#!/usr/bin/env python3
"""
fan_cap_test.py — verify the manual fan-speed mechanism on a live fan.

The fan's target RPM is the smaller of two things (FUN_100C595E):

    rpm = min( quadratic(target_temp), forced_rpm )

  * quadratic(t) = a*t*t + b*t + c, with a/b/c in the fan's config blob
    (FAN CID 0x06) -> 5.0229t^2 - 152.69t + 4381.5 on this machine, i.e.
    4321 rpm at 30 C and 7681 rpm at 45 C.
  * forced_rpm is the cap written by FAN CID 0x0B (fan+0x76).  On this
    machine it sits at 3000, which is why the fan idles at 3000 rpm even
    though the curve asks for 4321.

FAN CID 0x0E sets the fan's profile record, which carries the target
temperature (0x32): Quiet 25, Nominal 30, Performance 32, iGPU_Max 40,
dGPU_Max 45, Extreme 45.

    sudo python3 tools/cdev-probe/fan_cap_test.py [--fan N]
"""

import argparse
import ctypes
import os
import struct
import sys
import time

CDEV_PATH = "/dev/surface/aggregator"
TC_FAN = 0x05

CID_RPM = 0x01
CID_TARGET_TEMP = 0x0D
CID_FORCED_RPM = 0x0B
CID_PROFILE = 0x0E
CID_DUTY = 0x11


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


def req(fd, cid, iid=0, n=16, payload=b"", is_set=False):
    r = SsamCdevRequest()
    r.target_category, r.target_id = TC_FAN, 0x01
    r.command_id, r.instance_id = cid, iid
    r.flags = 0 if is_set else 0x01
    r.status = 0
    buf = (ctypes.c_uint8 * max(n, 1))()
    pay = (ctypes.c_uint8 * max(len(payload), 1))()
    if payload:
        ctypes.memmove(ctypes.addressof(pay), payload, len(payload))
    r.payload.data = ctypes.addressof(pay) if payload else 0
    r.payload.length = len(payload)
    r.response.data = ctypes.addressof(buf)
    r.response.length = n
    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, b""
    return r.status, bytes(buf[:r.response.length])


def rpm(fd, fan):
    st, d = req(fd, CID_RPM, fan, 8)
    return struct.unpack_from("<H", d)[0] if st == 0 and len(d) >= 2 else None


def duty(fd, fan):
    st, d = req(fd, CID_DUTY, fan, 8)
    return d[0] if st == 0 and d else None


def target(fd, fan):
    st, d = req(fd, CID_TARGET_TEMP, fan, 8)
    return struct.unpack_from("<f", d)[0] if st == 0 and len(d) >= 4 else None


def set_forced_rpm(fd, fan, value):
    st, _ = req(fd, CID_FORCED_RPM, fan, payload=struct.pack("<H", value),
                is_set=True)
    return st


def set_profile(fd, fan, pid):
    st, _ = req(fd, CID_PROFILE, fan, payload=bytes([pid]), is_set=True)
    return st


def show(fd, fan, note):
    print(f"  {note:38} rpm={rpm(fd, fan):5}  duty={duty(fd, fan):3}%  "
          f"target={target(fd, fan):.1f} C", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fan", type=int, default=1)
    ap.add_argument("--hold", type=float, default=8.0)
    args = ap.parse_args()
    fan = args.fan

    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        print(f"fan {fan} — baseline")
        show(fd, fan, "baseline")

        print(f"\nfan {fan} — CID 0x0B: forced RPM cap -> 11000")
        print(f"  write status = 0x{set_forced_rpm(fd, fan, 11000):04x}")
        time.sleep(1.5)
        for _ in range(3):
            show(fd, fan, "after cap raise")
            time.sleep(args.hold / 3)

        print(f"\nfan {fan} — CID 0x0E: profile -> 6 (Extreme, 45 C)")
        print(f"  write status = 0x{set_profile(fd, fan, 6):04x}")
        time.sleep(1.5)
        for _ in range(3):
            show(fd, fan, "after profile change")
            time.sleep(args.hold / 3)

        print(f"\nfan {fan} — restoring")
        print(f"  profile -> 2 (Nominal): 0x{set_profile(fd, fan, 2):04x}")
        print(f"  cap -> 3000:           0x{set_forced_rpm(fd, fan, 3000):04x}")
        time.sleep(2.0)
        show(fd, fan, "restored")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
