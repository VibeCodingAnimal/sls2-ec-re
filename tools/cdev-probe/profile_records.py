#!/usr/bin/env python3
"""
profile_records.py — read-only dump of every EC platform-profile record and
the per-fan record, straight off the live EC over /dev/surface/aggregator.

    sudo python3 tools/cdev-probe/profile_records.py
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


def fields(rec):
    """Decode the u16 field layout of a 0x40-byte platform-profile record."""
    u = lambda o: struct.unpack_from("<H", rec, o)[0]
    return {
        "id": u(0x00),
        "name": rec[2:16].split(b"\x00")[0].decode("ascii", "replace"),
        "minrpm": u(0x22),
        "f24": u(0x24),
        "duty1": u(0x26),
        "lo": u(0x28),
        "hi": u(0x2A),
        "f2c": u(0x2C),
        "f2e": u(0x2E),
        "f30": u(0x30),
        "f32": u(0x32),
        "duty2": u(0x34),
        "span_hi": u(0x36),
    }


def main():
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        print("=== platform-profile records (TC 5 / CID 4, IID = profile id) ===")
        for iid in range(0, 9):
            st, ln, data = req(fd, 0x05, 0x04, iid=iid, resp_size=64)
            if st != 0 or not data:
                print(f" id={iid}: status=0x{st & 0xffff:04x} (no record)")
                continue
            f = fields(data)
            print(f" id={iid}: name={f['name']:<12} minrpm={f['minrpm']:>5} "
                  f"[24]={f['f24']:>5} duty1={f['duty1']:>3} "
                  f"span={f['lo']}..{f['hi']} [2c]={f['f2c']:>5} [2e]={f['f2e']:>3} "
                  f"[30]={f['f30']:>5} [32]={f['f32']:>5} duty2={f['duty2']:>3} "
                  f"spanhi={f['span_hi']:>5}")
        print()
        print("=== per-fan record (TC 5 / CID 5) ===")
        for iid in range(0, 4):
            st, ln, data = req(fd, 0x05, 0x05, iid=iid, resp_size=64)
            if st != 0 or not data:
                print(f" iid={iid}: status=0x{st & 0xffff:04x} (no record)")
                continue
            u16 = [struct.unpack_from("<H", data, o)[0] for o in range(0, min(len(data), 64), 2)]
            nz = [f"{o:02x}:{v}" for o, v in
                  zip(range(0, len(data), 2), u16) if v]
            print(f" iid={iid}: len={ln} nonzero u16: {' '.join(nz)}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
