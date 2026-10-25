#!/usr/bin/env python3
"""
fan_space.py — read-only map of the FAN target-category command space.

Probes every (CID, IID) of TC 0x05 and TC 0x03 and reports what answers.

    sudo python3 tools/cdev-probe/fan_space.py
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


def describe(data):
    if len(data) == 2:
        return f"u16={struct.unpack('<H', data)[0]}"
    if len(data) == 4:
        v = struct.unpack("<I", data)[0]
        return f"u32={v} f32={struct.unpack('<f', data)[0]:g}"
    if len(data) == 8:
        a, b = struct.unpack("<II", data)
        return f"u32x2={a},{b}"
    if len(data) >= 16:
        pid = struct.unpack_from("<H", data, 0)[0]
        nm = data[2:16].split(b"\x00")[0].decode("ascii", "replace")
        return f"rec id={pid} name={nm!r}"
    return data.hex(" ")


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        for tc in (0x05, 0x03):
            print(f"===== TC 0x{tc:02x} =====")
            for cid in range(0x00, 0x20):
                row = []
                for iid in range(0, 4):
                    st, ln, data = req(fd, tc, cid, iid=iid, resp_size=64)
                    if st == 0 and ln:
                        row.append(f"iid{iid}:{describe(data)}")
                if row:
                    print(f" CID 0x{cid:02x}: " + "   ".join(row))
            sys.stdout.flush()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
