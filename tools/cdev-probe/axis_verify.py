#!/usr/bin/env python3
"""
axis_verify.py — live end-to-end check of the EC's two-axis profile model.

Axis 1: wire platform-profile id  ->  GPU power profile record (14 x 0x2A)
Axis 2: GPU power profile record   ->  per-fan profile ids (record +0x24/+0x25)
         -> fan profile records (0x100894C0) -> target temp / curve

We drive axis 1 with TMP CID 0x03 (set platform profile) and observe the
fan-side effect with FAN CID 0x0D (applied target temperature, float32),
FAN CID 0x11 (duty %) and FAN CID 0x01 (RPM).

Read-only apart from the platform profile, which is restored to the value
it had at the start.

    sudo python3 tools/cdev-probe/axis_verify.py
"""

import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"

# platform id -> (name, expected fan profile name, expected target degC)
#
# Platform 4 ("Max") is the only ambiguous one: it resolves to iGPU_Max (40 C)
# on an iGPU-only board and dGPU_Max (45 C) on a dGPU board. This machine is
# a dGPU board (RTX 4050 Max-Q), so 45.0 is expected here.
PLATFORM = {
    1: ("Nominal", "Nominal", 30.0),
    2: ("Quiet", "Quiet", 25.0),
    3: ("Performance", "Performance", 32.0),
    4: ("Max", "dGPU_Max", 45.0),
    5: ("Gaming", "Extreme", 45.0),
    6: ("Mixed_Reality", "Extreme", 45.0),
    7: ("Kiosk", "Performance", 32.0),
}


class SsamPayload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("data", ctypes.c_uint64), ("length", ctypes.c_uint16),
                ("__pad", ctypes.c_uint8 * 6)]


class SsamCdevRequest(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("target_category", ctypes.c_uint8), ("target_id", ctypes.c_uint8),
                ("command_id", ctypes.c_uint8), ("instance_id", ctypes.c_uint8),
                ("flags", ctypes.c_uint16), ("status", ctypes.c_int16),
                ("payload", SsamPayload), ("response", SsamPayload)]


def _iowr(t, nr, size):
    return (3 << 30) | (size << 16) | (t << 8) | nr


IOCTL = _iowr(0xA5, 1, ctypes.sizeof(SsamCdevRequest))
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int


def req(fd, tc, cid, iid=0, tid=1, payload=b"", resp_size=64, want_resp=True):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0x01 if want_resp else 0
    r.status = 0
    buf = (ctypes.c_uint8 * max(resp_size, 1))()
    pbuf = (ctypes.c_uint8 * max(len(payload), 1))()
    if payload:
        ctypes.memmove(ctypes.addressof(pbuf), payload, len(payload))
    r.payload.data = ctypes.addressof(pbuf) if payload else 0
    r.payload.length = len(payload)
    r.response.data = ctypes.addressof(buf)
    r.response.length = resp_size
    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, ctypes.get_errno(), b""
    return r.status, r.response.length, bytes(buf[:r.response.length])


def set_platform(fd, pid):
    st, _, _ = req(fd, 0x03, 0x03, payload=struct.pack("<I", pid), want_resp=False)
    return st


def fan_target(fd, fan):
    st, _, d = req(fd, 0x05, 0x0D, iid=fan, resp_size=8)
    return struct.unpack("<f", d[:4])[0] if st == 0 and len(d) >= 4 else None


def fan_duty(fd, fan):
    st, _, d = req(fd, 0x05, 0x11, iid=fan, resp_size=4)
    return d[0] if st == 0 and d else None


def fan_rpm(fd, fan):
    st, _, d = req(fd, 0x05, 0x01, iid=fan, resp_size=4)
    return struct.unpack("<H", d[:2])[0] if st == 0 and len(d) >= 2 else None


def current_platform(fd):
    """TMP CID 0x02 -> 8 bytes: status word, u16, u16 (= GPU record +2)."""
    st, _, d = req(fd, 0x03, 0x02, resp_size=8)
    if st != 0 or len(d) < 8:
        return None
    return struct.unpack_from("<H", d, 6)[0]


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        start = current_platform(fd)
        print(f"start: platform record index = {start}")
        print(f"{'plat':>4} {'name':<14} {'fan1 target':>11} {'fan2 target':>11} "
              f"{'duty1':>6} {'duty2':>6} {'rpm1':>6} {'rpm2':>6}   expect")
        for pid in sorted(PLATFORM):
            name, fanprof, expect = PLATFORM[pid]
            st = set_platform(fd, pid)
            t1, t2 = fan_target(fd, 1), fan_target(fd, 2)
            d1, d2 = fan_duty(fd, 1), fan_duty(fd, 2)
            r1, r2 = fan_rpm(fd, 1), fan_rpm(fd, 2)
            ok = "OK" if (t1 is not None and abs(t1 - expect) < 0.01) else "MISMATCH"
            print(f"{pid:>4} {name:<14} {t1!s:>11} {t2!s:>11} "
                  f"{d1!s:>6} {d2!s:>6} {r1!s:>6} {r2!s:>6}   "
                  f"{fanprof} {expect}C  set_status={st} {ok}")
            sys.stdout.flush()
        if start is not None:
            set_platform(fd, start)
            print(f"\nrestored platform record index = {start} "
                  f"(fan1 target = {fan_target(fd, 1)})")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
