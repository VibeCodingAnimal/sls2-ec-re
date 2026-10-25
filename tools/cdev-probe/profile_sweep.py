#!/usr/bin/env python3
"""
profile_sweep.py — live map of the EC's platform-profile space.

Opens /dev/surface/aggregator once and, for each platform-profile ID, sets
TMP CID 0x03 (the platform profile) then reads back every profile-related CID
so the whole enumeration can be correlated in a single pass.

Only TMP CID 0x03 is written; every other access is a GET.

    sudo python3 tools/cdev-probe/profile_sweep.py [id ...]
"""

import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"
HAS_RESPONSE = 0x01


class SsamPayload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("data", ctypes.c_uint64),
                ("length", ctypes.c_uint16),
                ("__pad", ctypes.c_uint8 * 6)]


class SsamCdevRequest(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("target_category", ctypes.c_uint8),
                ("target_id", ctypes.c_uint8),
                ("command_id", ctypes.c_uint8),
                ("instance_id", ctypes.c_uint8),
                ("flags", ctypes.c_uint16),
                ("status", ctypes.c_int16),
                ("payload", SsamPayload),
                ("response", SsamPayload)]


def _iowr(t, nr, size):
    return (3 << 30) | (size << 16) | (t << 8) | nr


IOCTL = _iowr(0xA5, 1, ctypes.sizeof(SsamCdevRequest))
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int


def req(fd, tc, cid, iid=0, tid=1, payload=b"", resp_size=64, is_set=False):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0 if is_set else HAS_RESPONSE
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


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    ids = [int(a, 0) for a in sys.argv[1:]] or list(range(1, 9))
    try:
        for pid in ids:
            req(fd, 0x03, 0x03, payload=struct.pack("<I", pid), is_set=True)
            tmp = req(fd, 0x03, 0x02, resp_size=16)[2]
            fan2 = req(fd, 0x05, 0x02, resp_size=16)[2]
            fan3 = req(fd, 0x05, 0x03, resp_size=8)[2]
            fan10 = req(fd, 0x05, 0x10, resp_size=8)[2]
            rpm1 = req(fd, 0x05, 0x01, iid=1, resp_size=4)[2]
            rpm2 = req(fd, 0x05, 0x01, iid=2, resp_size=4)[2]
            t = struct.unpack("<IHH", tmp) if len(tmp) >= 8 else (0, 0, 0)
            r1 = struct.unpack("<H", rpm1)[0] if len(rpm1) == 2 else -1
            r2 = struct.unpack("<H", rpm2)[0] if len(rpm2) == 2 else -1
            print(f"id={pid}  TMP02: raw={t[0]:>2} tag={t[1]:>2} eff={t[2]:>2} | "
                  f"FAN02={fan2.hex(' ') or '-':<42} FAN03={fan3.hex() or '-':<4} "
                  f"FAN10={fan10.hex() or '-':<4} rpm {r1}/{r2}")
            sys.stdout.flush()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
