#!/usr/bin/env python3
"""
fan_diff.py — which fan profile does each platform profile actually apply?

Reads the per-fan record (TC 5 / CID 5) and the 0x26-byte profile blob the
per-fan record points at, for every platform-profile ID, and diffs them.

    sudo python3 tools/cdev-probe/fan_diff.py
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


def req(fd, tc, cid, iid=0, tid=1, payload=b"", resp_size=64, is_set=False):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0 if is_set else 0x01
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
    base = None
    try:
        for pid in range(1, 9):
            req(fd, 0x03, 0x03, payload=struct.pack("<I", pid), is_set=True)
            st, ln, fan = req(fd, 0x05, 0x05, iid=1, resp_size=64)
            tmp = req(fd, 0x03, 0x02, resp_size=16)[2]
            eff = struct.unpack_from("<H", tmp, 6)[0] if len(tmp) >= 8 else -1
            if st != 0 or not fan:
                print(f"platform {pid}: eff={eff}  (no per-fan record)")
                continue
            if base is None:
                base = fan
                print(f"platform {pid}: eff={eff}  per-fan record (baseline):")
                for o in range(0, len(fan), 8):
                    print(f"    +0x{o:02x}  {fan[o:o+8].hex(' ')}")
            else:
                diff = [o for o in range(len(fan)) if fan[o] != base[o]]
                u16 = {o: struct.unpack_from("<H", fan, o)[0]
                       for o in range(0, len(fan) - 1, 2)}
                bu16 = {o: struct.unpack_from("<H", base, o)[0]
                        for o in range(0, len(base) - 1, 2)}
                changed = [(o, bu16[o], u16[o]) for o in sorted(bu16)
                           if bu16[o] != u16[o]]
                print(f"platform {pid}: eff={eff}  changed u16: "
                      + " ".join(f"+0x{o:02x} {a}->{b}" for o, a, b in changed))
            sys.stdout.flush()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
