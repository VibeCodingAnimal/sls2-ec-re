#!/usr/bin/env python3
"""
resolve_base.py — recover the absolute load address of an EC flash image.

The EC image is a raw Cortex-M4 (Thumb) flash image with no relocations, so the
address it was linked for has to be recovered empirically. Method:

  1. Find every Thumb `LDR Rt, [PC, #imm]` literal-pool load in the image.
  2. Resolve the pool word; keep those that look like absolute addresses
     (0x10000000-0x10100000 — the LPC54S00x flash window).
  3. For each candidate base B, count how many of those absolute addresses
     land exactly on the START of a printable string in the image
     (absolute = B + image_offset).

The correct B produces a sharp peak: every code-referenced string resolves to
its first character. Wrong candidates sit on a plateau because the EC string
table is dense, so a plain "most hits" score is not enough — the *string-start*
constraint is what separates the true base from its neighbours.

Validated result for the SLS2 EC firmware (2026-10-23):

    B = 0x1007F994
    => vector table at image 0x66C  lands at absolute 0x10080000 (page aligned)
    => PMC handler 0x100ABC66      lands at image 0x2C2D2
"""

import argparse
import re
import struct
import sys

PRINTABLE = re.compile(rb"[\x20-\x7e]{5,}")


def literal_pool_refs(img):
    """[(insn_off, pool_off, value)] for Thumb LDR-literal loads."""
    out = []
    for i in range(0, len(img) - 2, 2):
        hw = struct.unpack_from("<H", img, i)[0]
        if (hw & 0xF800) != 0x4800:      # 01001 Rt imm8  -> LDR Rt,[PC,#imm]
            continue
        pool = ((i + 4) & ~3) + ((hw & 0xFF) * 4)
        if pool + 4 > len(img) or (pool & 3):
            continue
        out.append((i, pool, struct.unpack_from("<I", img, pool)[0]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=0x10000000)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=0x10100000)
    args = ap.parse_args()

    img = open(args.image, "rb").read()
    n = len(img)

    refs = [v for _, _, v in literal_pool_refs(img) if args.lo <= v < args.hi]
    refs = sorted(set(refs))
    if not refs:
        print("no absolute literal references found", file=sys.stderr)
        return 1
    print(f"image 0x{n:X}; {len(refs)} distinct absolute literal references")

    # mask of positions that begin a printable run
    starts = set()
    for m in PRINTABLE.finditer(img):
        s = m.start()
        if s == 0 or not (32 <= img[s - 1] < 127):
            starts.add(s)

    # candidate bases: any reference minus any string offset
    cands = set()
    strs = [m.start() for m in PRINTABLE.finditer(img)]
    for v in refs:
        for s in strs:
            cands.add(v - s)
    cands = sorted(cands)

    best = []
    for b in cands:
        hit = 0
        for v in refs:
            t = v - b
            if 0 <= t < n and t in starts:
                hit += 1
        best.append((hit, b))
    best.sort(reverse=True)

    print("\nTop base candidates:")
    for hit, b in best[:6]:
        print(f"  BASE=0x{b & 0xFFFFFFFF:08X}  string-start hits = {hit}/{len(refs)}")

    b = best[0][1]
    print(f"\nBEST BASE = 0x{b & 0xFFFFFFFF:08X}   (absolute = BASE + image_offset)")
    for name, off in (("vector table", 0x66C),):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
