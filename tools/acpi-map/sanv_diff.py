#!/usr/bin/env python3
"""
sanv_diff.py — snapshot the ACPI SANV block, change the SAM's platform
profile, and diff the block.

SANV is the Surface Aggregator NVS: the OS-readable snapshot of what the SAM
knows. If the SAM publishes its own GPU/fan policy state there, a profile
change must move bytes in it.

    sudo python3 tools/acpi-map/sanv_diff.py
"""

import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"
SANV = 0x3DB5A418
SANV_SIZE = 0x01BC


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


def set_profile(fd, pid):
    r = SsamCdevRequest()
    r.target_category, r.target_id = 0x03, 0x01
    r.command_id, r.instance_id = 0x03, 0
    r.flags = 0
    r.status = 0
    payload = struct.pack("<I", pid)
    pbuf = (ctypes.c_uint8 * 4)()
    ctypes.memmove(ctypes.addressof(pbuf), payload, 4)
    r.payload.data = ctypes.addressof(pbuf)
    r.payload.length = 4
    r.response.data = 0
    r.response.length = 0
    return libc.ioctl(fd, IOCTL, ctypes.byref(r)) == 0


def main():
    mem = os.open("/dev/mem", os.O_RDONLY)
    cdev = os.open(CDEV_PATH, os.O_RDWR)
    try:
        snap = lambda: os.pread(mem, SANV_SIZE, SANV)

        base = snap()
        results = {}
        for pid in (1, 2, 3, 4, 5, 6, 7, 1):
            set_profile(cdev, pid)
            results[pid] = snap()

        names = {}
        try:
            import re
            dsl = "/tmp/acpidump/DSDT.dsl"
            off = 0
            in_sanv = False
            for line in open(dsl, errors="replace"):
                if "Field (SANV," in line:
                    in_sanv = True
                    continue
                if in_sanv:
                    m = re.search(r"Offset\s*\((0x[0-9A-Fa-f]+)\)", line)
                    if m:
                        off = int(m.group(1), 16)
                        continue
                    m = re.match(r"^\s*([A-Z][A-Z0-9_]*),\s*(\d+),\s*$", line)
                    if m:
                        names[off] = m.group(1)
                        off += (int(m.group(2)) + 7) // 8
        except OSError:
            pass

        print("SANV diff across platform-profile changes")
        print("(baseline = profile 1 / Nominal)\n")
        changed_any = False
        for pid, blob in results.items():
            if pid == 1 and blob == base:
                continue
            diffs = [(i, base[i], blob[i]) for i in range(SANV_SIZE)
                     if base[i] != blob[i]]
            if not diffs:
                print(f"  profile {pid}: (no change)")
                continue
            changed_any = True
            print(f"  profile {pid}: {len(diffs)} byte(s) changed")
            for i, a, b in diffs:
                print(f"      SANV+0x{i:03X} {names.get(i, '?'):<6} "
                      f"0x{a:02X} -> 0x{b:02X}")
        if not changed_any:
            print("  -> the SAM does NOT publish its platform profile in SANV")

        # ASLB points at a second NVS block
        aslb = struct.unpack_from("<I", base, 0)[0]
        print(f"\nASLB -> 0x{aslb:08X}")
        try:
            os.pread(mem, 0x20, aslb)
            print("  (readable)")
        except OSError as e:
            print(f"  unreadable: {e}")
    finally:
        os.close(cdev)
        os.close(mem)


if __name__ == "__main__":
    main()
