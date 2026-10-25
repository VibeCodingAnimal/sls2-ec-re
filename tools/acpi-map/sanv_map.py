#!/usr/bin/env python3
"""
sanv_map.py — parse the ACPI SANV ("Surface Aggregator NVS") field map out of a
decompiled DSDT and annotate it with the live values from /dev/mem.

SANV is the block the SAM/EC publishes to the OS. It is what the OS reads to
learn things the SAM knows that ACPI/DMI cannot express -- including the
dGPU power state (DPMS/PMSA/PMRL) that `\_SB.PC00.VMD0.NVDR._DSM` hands out.

    sudo python3 tools/acpi-map/sanv_map.py /tmp/acpidump/DSDT.dsl
"""

import os
import re
import struct
import sys

SANV_BASE = 0x3DB5A418
SANV_SIZE = 0x01BC

FMT = re.compile(r"^\s*([A-Z][A-Z0-9_]*),\s*(\d+),\s*$")
OFF = re.compile(r"Offset\s*\((0x[0-9A-Fa-f]+)\)")


def parse_fields(path):
    """Return [(name, offset, size_bits)] for the SANV Field block."""
    fields = []
    off = 0
    in_sanv = False
    with open(path, errors="replace") as f:
        for line in f:
            if "Field (SANV," in line:
                in_sanv = True
                continue
            if in_sanv:
                if re.match(r"^\s*}\s*$", line) and fields:
                    break
                m = OFF.search(line)
                if m:
                    off = int(m.group(1), 16)
                    continue
                m = FMT.match(line)
                if m:
                    fields.append((m.group(1), off, int(m.group(2))))
                    off += (int(m.group(2)) + 7) // 8
    return fields


def live_read(addr, n):
    try:
        fd = os.open("/dev/mem", os.O_RDONLY)
    except OSError as e:
        return None, str(e)
    try:
        return os.pread(fd, n, addr), None
    except OSError as e:
        return None, str(e)
    finally:
        os.close(fd)


def main():
    dsl = sys.argv[1] if len(sys.argv) > 1 else "/tmp/acpidump/DSDT.dsl"
    fields = parse_fields(dsl)
    if not fields:
        sys.exit(f"no SANV fields parsed from {dsl}")

    blob, err = live_read(SANV_BASE, SANV_SIZE)
    if blob is None:
        print(f"cannot read /dev/mem: {err}")
        blob = b""

    print(f"SANV  @ 0x{SANV_BASE:08X}  size 0x{SANV_SIZE:X}  "
          f"({'live' if blob else 'no live data'})")
    print(f"{'off':>6} {'sz':>4}  {'name':<10} value")
    print("-" * 52)
    for name, off, bits in fields:
        nbytes = (bits + 7) // 8
        if not blob or off + nbytes > len(blob):
            print(f"0x{off:04X} {bits:>4}  {name:<10} -")
            continue
        raw = blob[off:off + nbytes]
        if bits == 8:
            val = raw[0]
            txt = f"{val} (0x{val:02X})"
        elif bits == 16:
            val = struct.unpack_from("<H", raw)[0]
            txt = f"{val} (0x{val:04X})"
        elif bits == 32:
            val = struct.unpack_from("<I", raw)[0]
            txt = f"{val} (0x{val:08X})"
        elif bits == 64:
            val = struct.unpack_from("<Q", raw)[0]
            txt = f"{val} (0x{val:016X})"
        else:
            val = int.from_bytes(raw, "little")
            txt = f"{val} (0x{val:X}, {bits} bits)"
        print(f"0x{off:04X} {bits:>4}  {name:<10} {txt}")


if __name__ == "__main__":
    main()
