#!/usr/bin/env python3
"""prof_hex.py — hexdump the fan profile records (CID 0x04)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402


def main():
    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        for pid in range(1, 7):
            st, ln, d = fanctl.request(fd, fanctl.TC_FAN, fanctl.CID_PROFILE_REC,
                                       iid=pid, resp_size=0x40)
            print(f"=== profile {pid}  st={st} len={ln}")
            for off in range(0, len(d), 16):
                chunk = d[off:off + 16]
                hexs = " ".join(f"{b:02x}" for b in chunk)
                ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {off:04x}  {hexs:<47}  {ascii_}")
            print()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
