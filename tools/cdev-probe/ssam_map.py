#!/usr/bin/env python3
"""
ssam_map.py — map the SAM's SSAM command surface by probing it live.

For every target category (and a couple of command ids) we send a
read-only GET and record the status the SAM returns. Categories that answer
with something other than "unsupported"/"invalid argument" are implemented
in this firmware.

    sudo python3 tools/cdev-probe/ssam_map.py
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


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        print(f"{'tc':>4} {'tid':>4} {'cid':>4}  {'status':>10}  len  data")
        for tc in range(0x00, 0x40):
            for cid in (0x00, 0x01, 0x02):
                st, ln, d = req(fd, tc, cid, resp_size=64)
                if st is None:
                    print(f"0x{tc:02X}   0x01 0x{cid:02X}  ioctl-errno {ln}")
                    continue
                s = st & 0xFFFF
                # 0x54020006 = unsupported target category / command
                # 0x54020000 = invalid parameter
                interesting = s != 0x0006 and s != 0 and s not in (0x54020006, 0x54020000)
                if interesting or ln:
                    show = d[:24].hex(" ")
                    print(f"0x{tc:02X}   0x01 0x{cid:02X}  0x{s:04X}  {ln:>3}  {show}")
                sys.stdout.flush()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
