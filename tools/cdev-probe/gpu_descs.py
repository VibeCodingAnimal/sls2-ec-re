#!/usr/bin/env python3
"""
gpu_descs.py — dump the EC's GPU descriptor tables live.

TMP CID 0x0D returns the list of GPU device ids.
TMP CID 0x0E returns the 18-byte descriptor for the device id given as the
instance id, plus a class byte (1 = table 1 / "integrated", 2 = table 2).
TMP CID 0x0F returns the iGPU / dGPU blobs (instance 0 / 1).

    sudo python3 tools/cdev-probe/gpu_descs.py
"""

import ctypes
import os
import struct
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


def req(fd, tc, cid, iid=0, tid=1, resp_size=64):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0x01
    r.status = 0
    buf = (ctypes.c_uint8 * max(resp_size, 1))()
    r.payload.data = 0
    r.payload.length = 0
    r.response.data = ctypes.addressof(buf)
    r.response.length = resp_size
    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, ctypes.get_errno(), b""
    return r.status, r.response.length, bytes(buf[:r.response.length])


def printable(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        st, ln, d = req(fd, 0x03, 0x0D, resp_size=64)
        ids = list(d[4:]) if st == 0 else []
        ids = [i for i in ids if i]
        print(f"TMP 0x0D -> {len(ids)} GPU device ids: "
              f"{' '.join(f'{i:02X}' for i in ids)}")
        print(f"    raw: {d.hex(' ')}")
        print()
        for i in ids:
            st, ln, d = req(fd, 0x03, 0x0E, iid=i, resp_size=32)
            if st != 0 or len(d) < 21:
                print(f"  id {i:02X}: status=0x{st & 0xffff:04x} len={ln}")
                continue
            cls = d[1]
            rec = d[3:21]
            print(f"  id {i:02X}: class={cls}  rec={rec.hex(' ')}")
            print(f"          name={printable(rec)!r}  "
                  f"tail={rec[10:].hex(' ')}")
        print()
        for iid in (0, 1):
            st, ln, d = req(fd, 0x03, 0x0F, iid=iid, resp_size=32)
            print(f"TMP 0x0F instance {iid}: status=0x{st & 0xffff:04x} "
                  f"len={ln}  {d.hex(' ')}")
            print(f"        {printable(d)!r}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
