#!/usr/bin/env python3
"""
ssam_req.py — issue one specific SSAM request over /dev/surface/aggregator.

The cdev lives in the `surface_aggregator_cdev` module, which is NOT
auto-loaded on this kernel:

    sudo modprobe surface_aggregator_cdev     # -> /dev/surface/aggregator

GET requests (default) set the HAS_RESPONSE flag and use an empty payload.
A request with a payload is a SET and is only sent when --set is given
explicitly, so a stray --payload can never mutate the EC by accident.

Usage:
  sudo python3 tools/cdev-probe/ssam_req.py --tc 0x03 --cid 0x02
  sudo python3 tools/cdev-probe/ssam_req.py --tc 0x03 --cid 0x03 \
       --payload 05000000 --set
"""

import argparse
import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"

SSAM_CDEV_REQUEST_HAS_RESPONSE = 0x01


class SsamPayload(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("data", ctypes.c_uint64),
        ("length", ctypes.c_uint16),
        ("__pad", ctypes.c_uint8 * 6),
    ]


class SsamCdevRequest(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("target_category", ctypes.c_uint8),
        ("target_id", ctypes.c_uint8),
        ("command_id", ctypes.c_uint8),
        ("instance_id", ctypes.c_uint8),
        ("flags", ctypes.c_uint16),
        ("status", ctypes.c_int16),
        ("payload", SsamPayload),
        ("response", SsamPayload),
    ]


def _iowr(t, nr, size):
    return (3 << 30) | (size << 16) | (t << 8) | nr


SSAM_CDEV_REQUEST = _iowr(0xA5, 1, ctypes.sizeof(SsamCdevRequest))

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int


def request(fd, tc, tid, cid, iid, payload=b"", resp_size=64, is_set=False,
            want_resp=None):
    req = SsamCdevRequest()
    req.target_category = tc
    req.target_id = tid
    req.command_id = cid
    req.instance_id = iid
    if want_resp is None:
        want_resp = not is_set
    req.flags = SSAM_CDEV_REQUEST_HAS_RESPONSE if want_resp else 0
    req.status = 0

    resp_buf = (ctypes.c_uint8 * max(resp_size, 1))()
    pay_buf = (ctypes.c_uint8 * max(len(payload), 1))()
    if payload:
        ctypes.memmove(ctypes.addressof(pay_buf), payload, len(payload))

    req.payload.data = ctypes.addressof(pay_buf) if payload else 0
    req.payload.length = len(payload)
    req.response.data = ctypes.addressof(resp_buf)
    req.response.length = resp_size

    rc = libc.ioctl(fd, SSAM_CDEV_REQUEST, ctypes.byref(req))
    if rc != 0:
        return None, ctypes.get_errno(), b""

    return req.status, req.response.length, bytes(resp_buf[:req.response.length])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tc", required=True, type=lambda s: int(s, 0))
    ap.add_argument("--cid", required=True, type=lambda s: int(s, 0))
    ap.add_argument("--tid", default=0x01, type=lambda s: int(s, 0))
    ap.add_argument("--iid", default=0x00, type=lambda s: int(s, 0))
    ap.add_argument("--resp-size", default=64, type=int)
    ap.add_argument("--payload", default="", help="hex payload bytes")
    ap.add_argument("--set", action="store_true",
                    help="actually send a payload (a WRITE to the EC)")
    ap.add_argument("--read", action="store_true",
                    help="send a payload AND read the reply (query, not a write)")
    args = ap.parse_args()

    payload = bytes.fromhex(args.payload.replace(" ", "")) if args.payload else b""
    if payload and not (args.set or args.read):
        sys.exit("refusing to send a payload without --set or --read")

    fd = os.open(CDEV_PATH, os.O_RDWR)
    if fd < 0:
        sys.exit(f"open({CDEV_PATH}): {os.strerror(-fd)}")
    try:
        status, err, data = request(fd, args.tc, args.tid, args.cid, args.iid,
                                    payload, args.resp_size, args.set,
                                    want_resp=True if args.read else None)
    finally:
        os.close(fd)

    if status is None:
        sys.exit(f"ioctl failed: {err}")
    if status < 0:
        status &= 0xFFFF
    print(f"tc=0x{args.tc:02x} tid=0x{args.tid:02x} cid=0x{args.cid:02x} "
          f"iid=0x{args.iid:02x} "
          f"{'SET' if args.set else 'GET'} status=0x{status:04x} len={len(data)}")
    print("  " + " ".join(f"{b:02x}" for b in data))
    if len(data) >= 4:
        for n in (1, 2, 4):
            if len(data) % n == 0:
                vals = struct.unpack(f"<{len(data)//n}{'H' if n == 2 else ('B' if n == 1 else 'I')}",
                                     data)
                print(f"  u{n}: " + " ".join(str(v) for v in vals))


if __name__ == "__main__":
    main()
