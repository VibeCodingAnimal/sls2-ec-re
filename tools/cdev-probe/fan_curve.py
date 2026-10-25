#!/usr/bin/env python3
"""
fan_curve.py — dump the fan's config blob and profile table.

The fan's target RPM is a quadratic in the target temperature
(FUN_100C5DFE):

    rpm = (int)(t*t * cfg[0x22] + t * cfg[0x1a] + cfg[0x1e]) & 0xffff

with the three coefficients living in the fan's 0x26-byte config blob
(fan+0x64, readable with FAN CID 0x06 and writable with CID 0x84).

CID 0x04 returns a fan *profile* record by id; CID 0x05 returns the
per-fan "Override" record.  Both are 0x40 bytes.

Read-only.  sudo python3 tools/cdev-probe/fan_curve.py
"""

import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"
TC_FAN = 0x05


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


def req(fd, cid, iid=0, n=64):
    r = SsamCdevRequest()
    r.target_category, r.target_id = TC_FAN, 0x01
    r.command_id, r.instance_id = cid, iid
    r.flags = 0x01
    r.status = 0
    buf = (ctypes.c_uint8 * max(n, 1))()
    r.payload.data = 0
    r.payload.length = 0
    r.response.data = ctypes.addressof(buf)
    r.response.length = n
    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, b""
    return r.status, bytes(buf[:r.response.length])


def u16(d, o):
    return struct.unpack_from("<H", d, o)[0] if len(d) >= o + 2 else None


def f32(d, o):
    return struct.unpack_from("<f", d, o)[0] if len(d) >= o + 4 else None


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        for fan in (1, 2):
            st, d = req(fd, 0x06, fan, 0x26)
            if not d:
                print(f"fan {fan}: no config blob")
                continue
            print(f"=== fan {fan} config blob (CID 0x06), {len(d)} bytes ===")
            for i in range(0, len(d), 16):
                print(f"  {i:04x}  " + " ".join(f"{b:02x}" for b in d[i:i + 16]))
            print(f"  minRPM  [0x04] = {u16(d, 4)}")
            print(f"  maxRPM  [0x06] = {u16(d, 6)}")
            print(f"  [0x08] = {u16(d, 8)}   [0x0a] = {u16(d, 10)}   "
                  f"[0x0c] = {u16(d, 12)}")
            print(f"  quadratic coefficients:")
            print(f"    a = cfg[0x22] = {f32(d, 0x22)!r}")
            print(f"    b = cfg[0x1a] = {f32(d, 0x1a)!r}")
            print(f"    c = cfg[0x1e] = {f32(d, 0x1e)!r}")
            for t in (20, 25, 30, 35, 40, 45, 50, 60):
                rpm = int(t * t * f32(d, 0x22) + t * f32(d, 0x1a)
                          + f32(d, 0x1e)) & 0xFFFF
                print(f"      t={t:3d} C -> {rpm:6d} rpm")
            print()

        print("=== fan profile records (CID 0x04) ===")
        for pid in range(1, 9):
            st, d = req(fd, 0x04, pid, 0x40)
            if not d:
                print(f"  id {pid}: (none)")
                continue
            name = d[2:0x12].split(b"\x00")[0].decode("ascii", "replace")
            print(f"  id {pid} {name!r:14} temp[0x32]={f32(d, 0x32)}  "
                  f"min[0x22]={u16(d, 0x22)} rpm[0x24]={u16(d, 0x24)} "
                  f"thr[0x26]={u16(d, 0x26)} hyst[0x28]={u16(d, 0x28)} "
                  f"duty[0x32..]")
            print("        " + " ".join(f"{b:02x}" for b in d[0x20:0x40]))
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
