#!/usr/bin/env python3
"""
ssam_probe.py — read-only enumeration of TC x CID via /dev/surface/aggregator.

Issues SSAM_CDEV_REQUEST IOCTLs with empty payload and HAS_RESPONSE flag.
Captures: status, response length, first 32 response bytes.

Strictly read-only: payload length is always 0. Even commands that *could*
be SETs with a payload will be no-ops with empty payload.

Usage:
  sudo python3 tools/cdev-probe/ssam_probe.py [--tc LO-HI] [--cid LO-HI]
                                             [--iid LO-HI]
                                             [--out FILE]

Defaults: tc=0x00-0x26, cid=0x00-0x1f, iid=0x00-0x00.
"""

import argparse
import ctypes
import errno
import os
import struct
import sys
import time

SSAM_CDEV_REQUEST_HAS_RESPONSE = 0x01

CDEV_PATH = "/dev/surface/aggregator"

# struct ssam_cdev_request is __attribute__((packed)) — _pack_ = 1 is mandatory.
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
        ("target_id",       ctypes.c_uint8),
        ("command_id",      ctypes.c_uint8),
        ("instance_id",     ctypes.c_uint8),
        ("flags",           ctypes.c_uint16),
        ("status",          ctypes.c_int16),
        ("payload",         SsamPayload),
        ("response",        SsamPayload),
    ]

# _IOWR(0xA5, 1, struct ssam_cdev_request)
def _iowr(t, nr, size):
    return (3 << 30) | (size << 16) | (t << 8) | nr

SSAM_CDEV_REQUEST = _iowr(0xA5, 1, ctypes.sizeof(SsamCdevRequest))

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
libc.ioctl.restype = ctypes.c_int

# TC names from kernel tracepoint print fmt
TC_NAMES = {
    1: "SAM",  2: "BAT",  3: "TMP",  4: "PMC",  5: "FAN",  6: "PoM",
    7: "DBG",  8: "KBD",  9: "FWU", 10: "UNI", 11: "LPC", 12: "TCL",
   13: "SFL", 14: "KIP", 15: "EXT", 16: "BLD", 17: "BAS", 18: "SEN",
   19: "SRQ", 20: "MCU", 21: "HID", 22: "TCH", 23: "BKL", 24: "TAM",
   25: "ACC0",26: "UFI", 27: "USC", 28: "PEN", 29: "VID", 30: "AUD",
   31: "SMC", 32: "KPD", 33: "REG", 34: "SPT", 35: "SYS", 36: "ACC1",
   37: "SMB", 38: "POS",
}

def parse_range(s):
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo, 0), int(hi, 0)
    v = int(s, 0)
    return v, v

def do_probe(fd, tc, cid, iid, resp_buf):
    """Issue a single SSAM_CDEV_REQUEST and return (status, resp_len, resp_bytes)."""
    req = SsamCdevRequest()
    req.target_category = tc
    req.target_id = 0x01          # SSH host
    req.command_id = cid
    req.instance_id = iid
    req.flags = SSAM_CDEV_REQUEST_HAS_RESPONSE
    req.status = 0
    req.payload.data = 0
    req.payload.length = 0
    req.response.data = ctypes.addressof(resp_buf)
    req.response.length = len(resp_buf)

    try:
        rc = libc.ioctl(fd, SSAM_CDEV_REQUEST, ctypes.byref(req))
    except OSError as e:
        return None, e.errno, b""
    if rc != 0:
        return None, ctypes.get_errno(), b""

    return req.status, req.response.length, bytes(resp_buf[:req.response.length])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tc", default="0x00-0x26")
    ap.add_argument("--cid", default="0x00-0x1f")
    ap.add_argument("--iid", default="0x00-0x00")
    ap.add_argument("--out", default=None)
    ap.add_argument("--per-tc-timeout-ms", type=int, default=2000,
                    help="Early-stop a TC after this many ms of consecutive errors")
    ap.add_argument("--max-consecutive-fail", type=int, default=4,
                    help="Stop TC after this many consecutive errors")
    args = ap.parse_args()

    tc_lo, tc_hi = parse_range(args.tc)
    cid_lo, cid_hi = parse_range(args.cid)
    iid_lo, iid_hi = parse_range(args.iid)

    fd = os.open(CDEV_PATH, os.O_RDWR)
    if fd < 0:
        print(f"open({CDEV_PATH}): {os.strerror(-fd)}", file=sys.stderr)
        sys.exit(1)

    resp_buf = (ctypes.c_uint8 * 4096)()

    out = sys.stdout
    if args.out:
        out = open(args.out, "w")

    out.write(f"# GET-only SSAM cdev probe\n")
    out.write(f"# cdev={CDEV_PATH} tc=0x{tc_lo:02x}-0x{tc_hi:02x} "
              f"cid=0x{cid_lo:02x}-0x{cid_hi:02x} iid=0x{iid_lo:02x}-0x{iid_hi:02x}\n")
    out.write("# All requests: empty payload, HAS_RESPONSE, TID=Host (0x01)\n")
    out.write("TC\tTCNAME\tCID\tIID\tSTATUS\tRESP_LEN\tRESP_HEX\n")

    consecutive_fail = 0
    total_lines = 0

    for tc in range(tc_lo, tc_hi + 1):
        tc_had_success = False
        per_tc_first_fail_ts = None

        for cid in range(cid_lo, cid_hi + 1):
            for iid in range(iid_lo, iid_hi + 1):
                status, err, resp = do_probe(fd, tc, cid, iid, resp_buf)
                resp_hex = " ".join(f"{b:02x}" for b in resp[:32])
                if status is None:
                    out.write(f"0x{tc:02x}\t{TC_NAMES.get(tc, '??')}\t0x{cid:02x}\t0x{iid:02x}\t"
                              f"ERR={err}({os.strerror(err) if err in errno.errorcode else '?'})\t-\t-\n")
                    consecutive_fail += 1
                elif status == 0:
                    out.write(f"0x{tc:02x}\t{TC_NAMES.get(tc, '??')}\t0x{cid:02x}\t0x{iid:02x}\t"
                              f"0\t{len(resp)}\t{resp_hex}\n")
                    consecutive_fail = 0
                    tc_had_success = True
                else:
                    out.write(f"0x{tc:02x}\t{TC_NAMES.get(tc, '??')}\t0x{cid:02x}\t0x{iid:02x}\t"
                              f"0x{status & 0xffff:04x}\t{len(resp)}\t{resp_hex}\n")
                    consecutive_fail += 1

                total_lines += 1
                out.flush()

                # Per-TC early exit
                if consecutive_fail >= args.max_consecutive_fail and cid > 0x05 and not tc_had_success:
                    out.write(f"# -- TC=0x{tc:02x} ({TC_NAMES.get(tc, '??')}): "
                              f"{consecutive_fail} consecutive failures, skipping rest --\n")
                    break
            else:
                continue
            break

    out.write(f"# Done. {total_lines} probes issued.\n")
    if out is not sys.stdout:
        out.close()
    os.close(fd)

if __name__ == "__main__":
    main()