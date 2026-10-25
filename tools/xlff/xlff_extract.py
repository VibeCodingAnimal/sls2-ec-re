#!/usr/bin/env python3
"""
xlff_extract.py — unpack a Surface XLFF EC firmware container.

The container (Microsoft "XLFF", used for Surface EC firmware and PD blobs)
holds a PKCS#7 signedData wrapper around an MSS1/SAML payload whose body is a
chunk-based sparse image with two redundant A/B copies of the same content.

    struct chunk {          // 5-byte header + variable payload
        u32 offset;         // LE — destination offset in the image
        u8  len;            // payload length (16 for full chunks)
        u8  payload[len];
    };

Both chunk arrays in an EC firmware describe the *same* offset range, so the
container is fully redundant: A and B are byte-identical images.

Output: the assembled flash image, plus the vector table location.

The image is a complete Cortex-M4 (Thumb) firmware image. See decoded/CODE_MAP.md
for how the absolute address mapping is recovered.
"""

import argparse
import hashlib
import struct
import sys

MSS1_MARK = b"MSS1"
SAML_MARK = b"SAML"


def find_chunk_array(blob, lo=0, hi=None):
    """Locate the first 21-byte chunk-record run starting at or after `lo`."""
    hi = len(blob) if hi is None else hi
    best = None
    for start in range(lo, hi):
        p, prev, n = start, None, 0
        while p + 21 <= len(blob):
            off, ln = struct.unpack_from("<IB", blob, p)
            if ln > 16 or (prev is not None and off != prev + 16):
                break
            prev, n, p = off, n + 1, p + 21
        if n >= 32 and (best is None or n > best[1]):
            best = (start, n)
    return best


def parse_chunks(blob, start):
    """Parse a chunk array at `start` into [(offset, payload)] honouring len."""
    out, p, expect = [], start, 0
    while p + 5 <= len(blob):
        off, ln = struct.unpack_from("<IB", blob, p)
        if off != expect or ln > 16 or p + 5 + ln > len(blob):
            break
        out.append((off, blob[p + 5:p + 5 + ln]))
        p += 5 + ln
        expect += 16
    return out, p


def assemble(chunks):
    end = chunks[-1][0] + len(chunks[-1][1])
    img = bytearray(b"\xff" * end)
    for off, payload in chunks:
        img[off:off + len(payload)] = payload
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("container")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--base", type=lambda s: int(s, 0), default=None,
                    help="absolute base address to report (default: auto)")
    args = ap.parse_args()

    blob = open(args.container, "rb").read()
    print(f"{args.container}: {len(blob)} bytes  sha256={hashlib.sha256(blob).hexdigest()}")

    m1 = blob.find(MSS1_MARK)
    m2 = blob.find(SAML_MARK)
    print(f"  MSS1 @ 0x{m1:X}   SAML @ 0x{m2:X}")

    start, n = find_chunk_array(blob, m2 if m2 >= 0 else 0, m2 + 0x200 if m2 >= 0 else len(blob))
    if start is None:
        print("no chunk array found", file=sys.stderr)
        return 1
    print(f"  chunk array A @ 0x{start:X}  ({n} records)")

    chunks, end = parse_chunks(blob, start)
    img = assemble(chunks)
    print(f"  image: 0x{len(img):X} bytes ({len(img)}), "
          f"offsets 0x{chunks[0][0]:X}..0x{chunks[-1][0]:X}")

    # vector table: search for a plausible Cortex-M vector table
    vt = None
    for off in range(0, len(img) - 0x100, 4):
        sp = struct.unpack_from("<I", img, off)[0]
        if 0x30000000 <= sp < 0x31000000:
            rst = struct.unpack_from("<I", img, off + 4)[0]
            if 0x10000000 <= rst < 0x10200000:
                vt = off
                break
    if vt is not None:
        print(f"  vector table @ image 0x{vt:X}  (SP=0x{struct.unpack_from('<I', img, vt)[0]:08X}, "
              f"Reset=0x{struct.unpack_from('<I', img, vt + 4)[0]:08X})")

    open(args.out, "wb").write(img)
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
