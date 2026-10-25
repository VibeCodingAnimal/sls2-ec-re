#!/usr/bin/env python3
"""Dump the live 0x40-byte platform-profile record to a file for diffing."""
import ctypes
import os
import sys

CDEV_PATH = "/dev/surface/aggregator"


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

tc, cid, iid, tid = (int(a, 0) for a in sys.argv[1:5])
out = sys.argv[5]

r = SsamCdevRequest()
r.target_category, r.target_id = tc, tid
r.command_id, r.instance_id = cid, iid
r.flags = 0x01
buf = (ctypes.c_uint8 * 64)()
r.payload.data = 0
r.payload.length = 0
r.response.data = ctypes.addressof(buf)
r.response.length = 64

fd = os.open(CDEV_PATH, os.O_RDWR)
rc = libc.ioctl(fd, IOCTL, ctypes.byref(r))
os.close(fd)

data = bytes(buf[:r.response.length])
with open(out, "wb") as f:
    f.write(data)
print(f"status={r.status} len={len(data)} -> {out}")
