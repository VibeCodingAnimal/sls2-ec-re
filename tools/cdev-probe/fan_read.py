#!/usr/bin/env python3
"""
fan_read.py — read-only dump of the EC's fan state and profile records.

Exercises the fan property API (TC 0x05) and the TMP property API (TC 0x03)
to pull the 0x40-byte profile records and the per-fan curve state, then
optionally compares them across platform-profile IDs.

    sudo python3 tools/cdev-probe/fan_read.py            # dump current state
    sudo python3 tools/cdev-probe/fan_read.py --sweep     # per platform ID
"""

import argparse
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


def hexdump(data, width=16):
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {off:04x}  " + " ".join(f"{b:02x}" for b in chunk).ljust(width * 3)
              + "  |" + txt + "|")


def profile_record(fd, tid):
    """Pull a 0x40-byte platform-profile record via the property API."""
    for tc, cid, iid in ((0x03, 0x05, 0x04), (0x05, 0x05, 0x04), (0x05, 0x04, 0x04)):
        st, ln, data = req(fd, tc, cid, iid=iid, tid=tid, resp_size=64)
        if st == 0 and ln and any(data):
            return (tc, cid, iid), data
    return None, b""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--ids", default="1,2,3,4,5,6,7")
    args = ap.parse_args()

    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        if args.sweep:
            for pid in [int(x, 0) for x in args.ids.split(",")]:
                req(fd, 0x03, 0x03, payload=struct.pack("<I", pid), is_set=True)
                tmp = req(fd, 0x03, 0x02, resp_size=16)[2]
                f2 = req(fd, 0x05, 0x02, iid=1, resp_size=16)[2]
                f2b = req(fd, 0x05, 0x02, iid=2, resp_size=16)[2]
                t = struct.unpack("<IHH", tmp) if len(tmp) >= 8 else (0, 0, 0)
                print(f"platform {pid}: eff={t[2]}  fan1state={f2.hex(' ')}  "
                      f"fan2state={f2b.hex(' ')}")
                sys.stdout.flush()
        else:
            print("== per-fan state (FAN CID 0x02) ==")
            for iid in (1, 2):
                st, ln, data = req(fd, 0x05, 0x02, iid=iid, resp_size=32)
                print(f" fan iid={iid} status={st} len={ln}")
                hexdump(data)
            print("== fan property API (TMP CID 0x05, IID 0x04) profile records ==")
            for tid in range(1, 9):
                where, data = profile_record(fd, tid)
                if not data:
                    print(f" TID={tid}: (no response)")
                    continue
                pid = struct.unpack_from("<H", data, 0)[0]
                name = data[2:16].split(b"\x00")[0].decode("ascii", "replace")
                print(f" TID={tid} via {where}: id={pid} name={name!r}")
                hexdump(data)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
