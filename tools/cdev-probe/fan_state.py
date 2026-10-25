#!/usr/bin/env python3
"""
fan_state.py — dump everything the SAM will tell Linux about the fans.

Covers the SSAM surface that maps onto the decoded fan object
(see decoded/CURVE.md §10):

  FAN CID 0x01  measured RPM (per fan, IID = fan number)
  FAN CID 0x02  12-byte float blob
  FAN CID 0x03  fan count
  FAN CID 0x04  fan profile record by ID
  FAN CID 0x05  per-fan record (always the Override record — it is the default)
  FAN CID 0x06  static config + curve blob (0x26 B, matches the flash)
  FAN CID 0x07  runtime curve header (tail is racy)
  FAN CID 0x08/0x09  raw registers A/B
  FAN CID 0x0D  applied target temperature (float32)
  FAN CID 0x10  active fan number
  FAN CID 0x11  duty %

The two writes that actually move the fan are not in that list:

  FAN CID 0x0B  SET u16  the forced-RPM cap (fan+0x76)
  FAN CID 0x0E  SET u8   the fan profile record, i.e. the target temperature

CID 0x80 (manual mode) is inert on this firmware -- see decoded/FAN_SPEED.md.
tools/fanctl/fanctl.py drives 0x0B + 0x0E.

    sudo python3 tools/cdev-probe/fan_state.py
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


def get(fd, cid, iid=0, n=64):
    r = SsamCdevRequest()
    r.target_category, r.target_id = 0x05, 0x01
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


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        st, d = get(fd, 0x03)
        print(f"fan count                : {d[0] if d else '?'}")
        st, d = get(fd, 0x10)
        print(f"active fan               : {d[0] if d else '?'}")

        print()
        for fan in (1, 2):
            st, d = get(fd, 0x01, fan, 4)
            rpm = struct.unpack_from("<H", d)[0] if d else None
            st, d = get(fd, 0x0D, fan, 8)
            tgt = struct.unpack_from("<f", d)[0] if d else None
            st, d = get(fd, 0x11, fan, 4)
            duty = d[0] if d else None
            st, d = get(fd, 0x07, fan, 64)
            c7 = struct.unpack_from("<5I", d) if len(d) >= 20 else None
            st, d = get(fd, 0x09, fan, 8)
            regs = struct.unpack_from("<HH", d) if len(d) >= 4 else None
            print(f"fan {fan}: rpm={rpm}  target={tgt} C  duty={duty}%")
            print(f"        curve hdr (CID 0x07): {c7}")
            print(f"        raw regs A,B (CID 0x09): {regs}")

        print()
        for fan in (1, 2):
            st, d = get(fd, 0x06, fan, 64)
            if len(d) >= 0x26:
                u = lambda o: struct.unpack_from("<H", d, o)[0]
                print(f"fan {fan} config blob (CID 0x06):")
                print(f"        xLO={u(0x00)} xHI={u(0x02)} minRPM={u(0x04)} "
                      f"maxRPM={u(0x06)} {u(0x08)} {u(0x0A)} duty%={u(0x0C)}")
                print(f"        curve tail: {d[0x0E:0x26].hex(' ')}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
