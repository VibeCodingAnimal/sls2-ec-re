#!/usr/bin/env python3
"""
fan_api.py — dump the Surface Aggregator's FAN command surface.

The FAN router is FUN_100C6610.  These are the commands it implements:

  0x01 GET  u16    measured RPM            (fan+0x74, smoothed)
  0x02 GET  0x0E   blob
  0x03 GET  u8     fan count
  0x04 GET  0x40   fan profile record
  0x05 GET  0x40   per-fan record
  0x06 GET  0x26   static config blob
  0x07 GET  0x34   runtime curve header
  0x08 SET  u16x2  hysteresis thresholds  (fan+0x78, fan+0x7A)
  0x09 GET  u16x2  hysteresis thresholds
  0x0B SET  u16    forced RPM              (fan+0x76, enabled by fan+0x7F)
  0x0C SET  u16    (FUN_100C5FDC)
  0x0D GET  f32    applied target temperature (fan+0x84)
  0x0E SET  u8     fan profile id
  0x0F SET  u8     fan control policy: 1 = threshold, 2 = curve
  0x10 GET  u8     fan control policy
  0x11 GET  u8     duty percent
  0x12 SET  f32    RPM override
  0x13 GET  f32    temperature
  0x80 SET  u8+u16 manual mode (1 off, 2 rpm, 3 percent, 4 auto)
  0x81 SET  0x40   raw record write
  0x83 SET         profile record write
  0x84 SET  0x26   restore the default config blob

Read-only.  sudo python3 tools/cdev-probe/fan_api.py
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


def req(fd, cid, iid=0, n=64, payload=b"", is_set=False):
    r = SsamCdevRequest()
    r.target_category, r.target_id = TC_FAN, 0x01
    r.command_id, r.instance_id = cid, iid
    r.flags = 0x01 if not is_set else 0
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


def u16(d, o=0):
    return struct.unpack_from("<H", d, o)[0] if len(d) >= o + 2 else None


def hexdump(d, width=16):
    for i in range(0, len(d), width):
        chunk = d[i:i + width]
        print(f"      {i:04x}  " + " ".join(f"{b:02x}" for b in chunk))


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        st, d = req(fd, 0x03)
        print(f"fan count (CID 0x03)   : {d[0] if d else '?'}")
        st, d = req(fd, 0x10)
        print(f"control policy (0x10)  : {d[0] if d else '?'}  "
              f"(1 = threshold, 2 = curve)")

        for fan in (1, 2):
            print(f"\n=== fan {fan} ===")
            st, d = req(fd, 0x01, fan, 8)
            print(f"  rpm (0x01)           : {u16(d)}")
            st, d = req(fd, 0x0D, fan, 8)
            print(f"  target temp (0x0D)   : {struct.unpack_from('<f', d)[0]:.1f} C")
            st, d = req(fd, 0x11, fan, 8)
            print(f"  duty pct (0x11)      : {d[0] if d else '?'}")
            st, d = req(fd, 0x09, fan, 8)
            print(f"  thresholds (0x09)    : {u16(d, 0)} {u16(d, 2)}")
            st, d = req(fd, 0x13, fan, 8)
            print(f"  temperature (0x13)   : {struct.unpack_from('<f', d)[0]:.1f} C")

            st, d = req(fd, 0x04, fan, 0x40)
            print(f"  profile record (0x04): {len(d)} bytes")
            hexdump(d)
            if len(d) >= 0x40:
                print(f"      [0x24] rpm = {u16(d, 0x24)}   "
                      f"[0x26] = {u16(d, 0x26)}   [0x28] = {u16(d, 0x28)}")
                print(f"      [0x2c] = {u16(d, 0x2c)}   [0x2e] = {u16(d, 0x2e)}   "
                      f"[0x34] = {u16(d, 0x34)}")

            st, d = req(fd, 0x05, fan, 0x40)
            print(f"  per-fan record (0x05): {len(d)} bytes")
            hexdump(d)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
