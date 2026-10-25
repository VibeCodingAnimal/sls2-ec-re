#!/usr/bin/env python3
"""
test_fanctl.py — offline checks for fanctl's control logic.

No EC, no root, no writes: the SSAM getters are stubbed so the profile
selection and the restore path can be exercised.

    python3 tools/fanctl/test_fanctl.py
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fanctl  # noqa: E402


def record(pid, name, temp, min_rpm=3000, hot_rpm=0):
    d = bytearray(0x40)
    struct.pack_into("<H", d, 0, pid)
    d[2:2 + len(name)] = name.encode()
    struct.pack_into("<H", d, 0x22, min_rpm)
    struct.pack_into("<H", d, 0x24, hot_rpm)
    struct.pack_into("<f", d, 0x30, temp)
    return bytes(d)


PROFILES = {
    1: record(1, "Quiet", 25.0),
    2: record(2, "Nominal", 30.0),
    3: record(3, "Performance", 32.0),
    4: record(4, "dGPU_Max", 45.0),
    5: record(5, "iGPU_Max", 40.0),
    6: record(6, "Extreme", 45.0),
}

# fan 1's real coefficients
COEFFS = (5.022900104522705, -152.69000244140625, 4381.5)


class FakeFd:
    """Stands in for the cdev file descriptor."""

    def __init__(self, target_temp=30.0, profile=2):
        self.target_temp = target_temp
        self.profile = profile
        self.cap = 3000
        self.writes = []

    # -- reads ------------------------------------------------------------
    def profile_rec(self, pid):
        return PROFILES.get(pid)

    def config(self, fan):
        d = bytearray(0x26)
        struct.pack_into("<H", d, 0x04, 3000)
        struct.pack_into("<H", d, 0x06, 11000)
        struct.pack_into("<f", d, 0x22, COEFFS[0])
        struct.pack_into("<f", d, 0x1A, COEFFS[1])
        struct.pack_into("<f", d, 0x1E, COEFFS[2])
        return bytes(d)

    # -- writes -----------------------------------------------------------
    def set_cap(self, fd, fan, rpm):
        self.cap = rpm
        self.writes.append(("cap", fan, rpm))

    def set_profile(self, fd, fan, pid):
        self.profile = pid
        self.writes.append(("profile", fan, pid))


class Logic(unittest.TestCase):
    def setUp(self):
        self.fd = FakeFd()
        self._orig = (fanctl.fan_get, fanctl.fan_target, fanctl.fan_coeffs,
                      fanctl.profile_record, fanctl.profiles,
                      fanctl.profile_min_rpm,
                      fanctl.set_cap, fanctl.set_profile)
        fanctl.fan_get = lambda fd, cid, fan, n=64: (
            (0, self.fd.profile_rec(fan)) if cid == fanctl.CID_PROFILE_REC
            else (0, b""))
        fanctl.fan_target = lambda fd, fan: self.fd.target_temp
        fanctl.fan_coeffs = lambda fd, fan: COEFFS
        fanctl.profile_record = lambda fd, pid: self.fd.profile_rec(pid)
        fanctl.profiles = lambda fd: {
            pid: (r[2:0x12].split(b"\x00")[0].decode(),
                  struct.unpack_from("<f", r, 0x30)[0])
            for pid, r in PROFILES.items()}
        fanctl.profile_min_rpm = lambda fd, pid: 3000
        fanctl.set_cap = self.fd.set_cap
        fanctl.set_profile = self.fd.set_profile

    def tearDown(self):
        (fanctl.fan_get, fanctl.fan_target, fanctl.fan_coeffs,
         fanctl.profile_record, fanctl.profiles,
         fanctl.profile_min_rpm,
         fanctl.set_cap, fanctl.set_profile) = self._orig

    def test_rpm_for_temp(self):
        self.assertEqual(fanctl.rpm_for_temp(COEFFS, 30), 4321)
        self.assertEqual(fanctl.rpm_for_temp(COEFFS, 45), 7681)

    def test_profile_temp_is_a_plain_float(self):
        # the record stores the target temp as a float32 of degrees C
        for temp in (25.0, 30.0, 32.0, 40.0, 45.0):
            rec = record(1, "x", temp)
            self.assertEqual(fanctl.profile_temp(rec), temp)
        self.assertEqual(struct.unpack_from("<f", record(1, "x", 30.0), 0x30)[0],
                         30.0)

    def test_ceiling(self):
        self.assertEqual(fanctl.ceiling(self.fd, 1), 4321)
        self.fd.target_temp = 45.0
        self.assertEqual(fanctl.ceiling(self.fd, 1), 7681)

    def test_low_request_keeps_the_profile(self):
        ctl = fanctl.FanControl(self.fd, 1)
        self.assertEqual(ctl.orig_profile, 2)
        self.assertIsNone(ctl._profile_for(3000))
        self.assertIsNone(ctl._profile_for(4321))

    def test_high_request_raises_the_profile(self):
        ctl = fanctl.FanControl(self.fd, 1)
        self.assertEqual(ctl._profile_for(7000), 4)     # dGPU_Max, ceiling 7681

    def test_max_ceiling_is_the_best_profile(self):
        # no profile reaches 11000 rpm, so that must not be offered
        self.assertEqual(fanctl.max_ceiling(self.fd, 1), 7681)
        ctl = fanctl.FanControl(self.fd, 1)
        self.assertIsNone(ctl._profile_for(11000))

    def test_set_rpm_switches_then_caps(self):
        ctl = fanctl.FanControl(self.fd, 1)
        ctl.set_rpm(7000)
        self.assertEqual(self.fd.profile, 4)
        self.assertEqual(self.fd.cap, 7000)

    def test_restore_puts_everything_back(self):
        ctl = fanctl.FanControl(self.fd, 1)
        ctl.set_rpm(7000)
        ctl.restore()
        self.assertEqual(self.fd.profile, 2)
        self.assertEqual(self.fd.cap, 3000)

    def test_rpm_is_clamped(self):
        ctl = fanctl.FanControl(self.fd, 1)
        ctl.set_rpm(999999)
        # clamped to the best profile's ceiling, not to the EC's 11000 limit
        self.assertEqual(self.fd.cap, 7681)
        ctl.set_rpm(1)
        self.assertEqual(self.fd.cap, fanctl.RPM_MIN)

    def test_restore_without_a_known_profile(self):
        # the EC's temp did not match any profile: restoring must not
        # try to write a profile id it does not have
        self.fd.target_temp = 27.5
        ctl = fanctl.FanControl(self.fd, 1)
        self.assertIsNone(ctl.orig_profile)
        ctl.restore()


if __name__ == "__main__":
    unittest.main(verbosity=2)
