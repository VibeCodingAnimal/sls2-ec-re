#!/usr/bin/env python3
"""
fan_record_boost.py — ask a fan for more RPM than any profile allows.

The profile ceiling comes from the RPM quadratic in the fan's config blob
(FAN CID 0x06), so the forced-RPM cap (FAN CID 0x0B) can never ask for
more than the hottest profile delivers -- 7681 rpm on this machine.  The
EC's fan state machine has a third path though:

    FUN_100C595E, state 3 ("critical"):
        if (record->0x24 == 0)   -> duty path (record->0x34 percent)
        else                     -> command exactly record->0x24 rpm

and the record is writable.  FAN CID 0x83 takes a whole 0x40-byte record,
copies it over the fan's per-fan slot and applies it (FUN_100C64B4 ->
FUN_100C6084).  A *profile* record has the right layout for this:

    +0x20 u16 = 0        "profile layout" marker (FUN_100C6084 branches on it)
    +0x22 u16 min rpm    +0x24 u16 hot rpm (the state-3 command)
    +0x26 u16 rpm threshold   +0x28 u16 rpm hysteresis
    +0x2C u16 temp threshold  +0x2E u16 temp hysteresis
    +0x30 f32 target temperature
    +0x34 u16 duty percent (state-3 duty path)

Zeroing both thresholds walks the fan straight into state 3, where the
commanded RPM is the hot-rpm field and neither the quadratic nor the cap
applies.  The fan's own record is snapshotted and written back afterwards.

Note: the per-fan slot is looked up by the record's first u16 (FUN_100C6DE4
treats it as the fan id), so the patched record keeps the fan id there --
otherwise the slot could not be found again to restore it.

    sudo python3 tools/cdev-probe/fan_record_boost.py --fan 1 --rpm 11000
    sudo python3 tools/cdev-probe/fan_record_boost.py --restore
"""

import os
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402

CID_RECORD = 0x05
CID_RECORD_SET = 0x83
CID_PROFILE_REC = 0x04

BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "override_backup.bin")

F_FAN_ID = 0x00
F_MIN_RPM = 0x22
F_HOT_RPM = 0x24
F_RPM_THRESH = 0x26
F_TEMP_THRESH = 0x2C
F_TARGET_TEMP = 0x30


def read_record(fd, fan):
    st, _, d = fanctl.request(fd, fanctl.TC_FAN, CID_RECORD, iid=fan,
                               resp_size=0x40)
    return d if st == 0 and len(d) == 0x40 else None


def read_profile(fd, pid):
    st, _, d = fanctl.request(fd, fanctl.TC_FAN, CID_PROFILE_REC, iid=pid,
                               resp_size=0x40)
    return d if st == 0 and len(d) == 0x40 else None


def write_record(fd, fan, rec):
    st, _, _ = fanctl.request(fd, fanctl.TC_FAN, CID_RECORD_SET, iid=fan,
                               payload=rec, is_set=True)
    return st


def show(rec, tag):
    u = lambda o: struct.unpack_from("<H", rec, o)[0]
    f = lambda o: struct.unpack_from("<f", rec, o)[0]
    print(f"  {tag}: id={u(F_FAN_ID)} min={u(F_MIN_RPM)} hot={u(F_HOT_RPM)} "
          f"rpm_thresh={u(F_RPM_THRESH)} temp_thresh={u(F_TEMP_THRESH)} "
          f"target={f(F_TARGET_TEMP)} C")


def restore(fd):
    if not os.path.exists(BACKUP):
        print(f"no snapshot at {BACKUP}", file=sys.stderr)
        return 1
    with open(BACKUP, "rb") as f:
        data = f.read()
    fan = data[0]
    rec = data[1:0x41]
    st = write_record(fd, fan, rec)
    print(f"fan {fan}: original record written back (status {st})")
    time.sleep(0.3)
    now = read_record(fd, fan)
    print(f"  record {'matches' if now == rec else 'DIFFERS FROM'} the snapshot")
    # put the fan back under the EC's own policy
    fanctl.set_profile(fd, fan, 2)
    time.sleep(0.2)
    fanctl.set_cap(fd, fan, fanctl.RPM_MIN)
    print(f"  fan {fan}: profile 2 (Nominal), cap {fanctl.RPM_MIN}")
    return 0


def main():
    fan = 1
    want = 11000
    base = 2
    seconds = 20.0
    args = sys.argv[1:]
    if "--fan" in args:
        fan = int(args[args.index("--fan") + 1])
    if "--rpm" in args:
        want = int(args[args.index("--rpm") + 1])
    if "--base" in args:
        base = int(args[args.index("--base") + 1])
    if "--seconds" in args:
        seconds = float(args[args.index("--seconds") + 1])

    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        if "--restore" in args:
            restore(fd)
            return 0

        orig = read_record(fd, fan)
        if orig is None:
            print(f"fan {fan}: no per-fan record to read", file=sys.stderr)
            return 1
        with open(BACKUP, "wb") as f:
            f.write(struct.pack("<B", fan))
            f.write(orig)
        print(f"fan {fan}: original record snapshotted to {BACKUP}")
        show(orig, "orig")

        rec = bytearray(read_profile(fd, base))
        struct.pack_into("<H", rec, F_FAN_ID, fan)   # keep the slot findable
        struct.pack_into("<H", rec, F_HOT_RPM, want)
        struct.pack_into("<H", rec, F_RPM_THRESH, 0)
        struct.pack_into("<H", rec, F_TEMP_THRESH, 0)
        rec = bytes(rec)
        print(f"fan {fan}: rewritten from profile {base}")
        show(rec, "new ")

        st = write_record(fd, fan, rec)
        print(f"fan {fan}: CID 0x83 write status {st}")
        if st != 0:
            print("  the record was not accepted -- leaving it alone")
            return 1
        back = read_record(fd, fan)
        print(f"  read back: {'our record' if back == rec else 'something else'}")
        if back != rec:
            show(back, "slot")
            return 1

        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(1)
            print(f"   rpm={fanctl.fan_rpm(fd, fan)} "
                  f"duty={fanctl.fan_duty(fd, fan)}% "
                  f"target={fanctl.fan_target(fd, fan)} C")
        return 0
    finally:
        if "--keep" not in sys.argv and "--restore" not in sys.argv:
            restore(fd)
        os.close(fd)


def _sigint(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sigint)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
