#!/usr/bin/env python3
"""
fan_boost_test.py — raise a fan's RPM ceiling above the profile limit.

The EC computes the commanded RPM as

    target = min( quadratic(target_temp), cap )

with quadratic(t) = a*t^2 + b*t + c read from the fan's 0x26-byte config
blob (fan+0x64): a at +0x22, b at +0x1A, c at +0x1E.  target_temp comes
from the fan profile record, and the hottest profile is 45 C, which the
quadratic turns into 7681 rpm -- so the cap can never lift the fan past
that, whatever it is set to.

FAN CID 0x84 (SET, 0x26 bytes) rewrites that blob:

    memcpy(0x30029608, payload, 0x26);
    fan->0x64 = 0x30029608;

so raising c lifts the whole ceiling.  This script snapshots the original
blob, rewrites it, drives the fan, and puts the original bytes back.

    sudo python3 tools/cdev-probe/fan_boost_test.py --fan 1 --rpm 11000
    sudo python3 tools/cdev-probe/fan_boost_test.py --restore
"""

import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "config_backup.bin")

CID_CONFIG_SET = 0x84


def read_config(fd, fan):
    st, _, d = fanctl.request(fd, fanctl.TC_FAN, fanctl.CID_CONFIG, iid=fan,
                               resp_size=0x26)
    return d if st == 0 and len(d) >= 0x26 else None


def write_config(fd, fan, blob):
    st, err, _ = fanctl.request(fd, fanctl.TC_FAN, CID_CONFIG_SET, iid=fan,
                                 payload=blob, is_set=True)
    return st, err


def coeffs(blob):
    return (struct.unpack_from("<f", blob, 0x22)[0],
            struct.unpack_from("<f", blob, 0x1A)[0],
            struct.unpack_from("<f", blob, 0x1E)[0])


def quad(c, t):
    a, b, cc = c
    return int(t * t * a + t * b + cc) & 0xFFFF


def snapshot(fd, fans):
    out = {}
    for fan in fans:
        blob = read_config(fd, fan)
        if blob:
            out[fan] = blob
    with open(SNAPSHOT, "wb") as f:
        for fan in fans:
            f.write(struct.pack("<B", fan))
            f.write(out[fan])
    return out


def restore(fd):
    if not os.path.exists(SNAPSHOT):
        print(f"no snapshot at {SNAPSHOT}", file=sys.stderr)
        return 1
    with open(SNAPSHOT, "rb") as f:
        data = f.read()
    i = 0
    while i + 0x27 <= len(data):
        fan = data[i]
        blob = data[i + 1:i + 0x27]
        i += 0x27
        st, err = write_config(fd, fan, blob)
        print(f"fan {fan}: wrote the original config back (status {st})")
        time.sleep(0.3)
        now = read_config(fd, fan)
        if now != blob:
            print(f"  fan {fan}: MISMATCH -- the EC did not keep our bytes")
            print(f"    want {blob.hex()}")
            print(f"    got  {now.hex() if now else None}")
        else:
            print(f"  fan {fan}: config matches the snapshot")
    return 0


def main():
    fan = 1
    want = 11000
    args = sys.argv[1:]
    if "--restore" in args:
        fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
        try:
            return restore(fd)
        finally:
            os.close(fd)
    if "--fan" in args:
        fan = int(args[args.index("--fan") + 1])
    if "--rpm" in args:
        want = int(args[args.index("--rpm") + 1])

    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        orig = snapshot(fd, (1, 2))
        for f, blob in orig.items():
            c = coeffs(blob)
            print(f"fan {f}: a={c[0]:.6f} b={c[1]:.6f} c={c[2]:.6f}  "
                  f"min_rpm={struct.unpack_from('<H', blob, 4)[0]} "
                  f"max_rpm={struct.unpack_from('<H', blob, 6)[0]}")

        blob = bytearray(orig[fan])
        a, b, c = coeffs(bytes(blob))
        target_temp = fanctl.fan_target(fd, fan)
        print(f"fan {fan}: target temp {target_temp} C, ceiling now "
              f"{quad(coeffs(bytes(blob)), target_temp)} rpm")

        # lift c so that the quadratic reaches `want` rpm at 45 C
        hot = 45.0
        need = want - (a * hot * hot + b * hot)
        new_c = c + need
        struct.pack_into("<f", blob, 0x1E, new_c)
        print(f"fan {fan}: c {c:.2f} -> {new_c:.2f}  (quadratic at 45 C = "
              f"{quad(coeffs(bytes(blob)), hot)} rpm)")

        st, err = write_config(fd, fan, bytes(blob))
        print(f"fan {fan}: CID 0x84 write status {st}")
        time.sleep(0.5)
        now = read_config(fd, fan)
        if now != bytes(blob):
            print(f"  fan {fan}: the EC did not keep our blob")
            print(f"    want {bytes(blob).hex()}")
            print(f"    got  {now.hex() if now else None}")
            return 1
        print(f"  fan {fan}: config verified")
        for other in orig:
            if other != fan:
                o = read_config(fd, other)
                print(f"  fan {other}: config {'unchanged' if o == orig[other] else 'CHANGED'}")

        # now drive it: 45 C profile (ceiling 11000 now) + cap 11000
        fanctl.set_profile(fd, fan, 4)
        time.sleep(0.3)
        fanctl.set_cap(fd, fan, want)
        print(f"fan {fan}: profile 4 (45 C), cap {want}")

        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(1)
            print(f"   rpm={fanctl.fan_rpm(fd, fan)} "
                  f"duty={fanctl.fan_duty(fd, fan)}% "
                  f"target={fanctl.fan_target(fd, fan)} C "
                  f"ceiling={quad(coeffs(read_config(fd, fan) or bytes(blob)), 45)}")
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
