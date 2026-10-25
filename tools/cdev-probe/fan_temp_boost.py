#!/usr/bin/env python3
"""
fan_temp_boost.py — drive a fan past the profile ceiling.

Every profile tops out at 45 C, and the RPM quadratic turns 45 C into
7681 rpm, so the slider in fanctl.py cannot ask for more than that:

    target_rpm = min( quadratic(target_temp), cap )

There is a third door though.  The quadratic is evaluated at the fan's
*target temperature* (fan+0x84), and that value is writable on its own:

    FAN CID 0x0C   SET f32   the target temperature, and it sets the
                             override flag (fan+0x88) so the profile
                             record can no longer overwrite it
                             (FUN_100C6084 only refreshes fan+0x84 while
                             fan+0x88 == 0)

So with the cap (CID 0x0B) at the EC's own maximum of 11000 rpm and a
target temperature high enough for the quadratic to exceed it, the EC
asks for 11000 rpm:

    quadratic(55) = 5.0229*55^2 - 152.69*55 + 4381.5 = 11177 rpm
    quadratic(60) = 13419 rpm

Writing 0.0 to CID 0x0C clears the override and drops the target
temperature back to the applied profile record, which is how the fan is
put back where it was.

    sudo python3 tools/cdev-probe/fan_temp_boost.py --fan 1 --temp 55 --rpm 11000
    sudo python3 tools/cdev-probe/fan_temp_boost.py --fan 1 --sweep
"""

import os
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402

CID_TARGET_TEMP_SET = 0x0C
CID_CAP_SET = 0x0B

RPM_MIN, RPM_MAX = 3000, 11000


def set_target_temp(fd, fan, temp):
    st, _, _ = fanctl.request(fd, fanctl.TC_FAN, CID_TARGET_TEMP_SET, iid=fan,
                              payload=struct.pack("<f", temp), is_set=True)
    return st


def set_cap(fd, fan, rpm):
    st, _, _ = fanctl.request(fd, fanctl.TC_FAN, CID_CAP_SET, iid=fan,
                              payload=struct.pack("<H", rpm), is_set=True)
    return st


def sample(fd, fan, seconds, interval=0.5):
    """(max rpm, last duty, last target temp) over the window."""
    hi, duty, target = 0, None, None
    end = time.time() + seconds
    while time.time() < end:
        rpm = fanctl.fan_rpm(fd, fan)
        if rpm:
            hi = max(hi, rpm)
        duty = fanctl.fan_duty(fd, fan)
        target = fanctl.fan_target(fd, fan)
        time.sleep(interval)
    return hi, duty, target


def restore(fd, fan, cap):
    set_target_temp(fd, fan, 0.0)
    time.sleep(0.3)
    set_cap(fd, fan, cap)
    time.sleep(0.3)
    print(f"fan {fan}: override cleared, cap back to {cap}, "
          f"target {fanctl.fan_target(fd, fan)} C")


def main():
    args = sys.argv[1:]
    fan = 1
    temp = 55.0
    want = RPM_MAX
    seconds = 15.0
    sweep = False

    if "--fan" in args:
        fan = int(args[args.index("--fan") + 1])
    if "--temp" in args:
        temp = float(args[args.index("--temp") + 1])
    if "--rpm" in args:
        want = int(args[args.index("--rpm") + 1])
    if "--seconds" in args:
        seconds = float(args[args.index("--seconds") + 1])
    sweep = "--sweep" in args

    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        coeffs = fanctl.fan_coeffs(fd, fan)
        start_temp = fanctl.fan_target(fd, fan)
        print(f"fan {fan}: quadratic {coeffs[0]:.4f}t^2 {coeffs[1]:.2f}t "
              f"{coeffs[2]:.1f}, target temp {start_temp} C")
        if start_temp is None:
            print("  the fan did not answer", file=sys.stderr)
            return 1

        temps = [temp]
        if sweep:
            temps = [30.0, 40.0, 45.0, 50.0, 55.0, 60.0, 70.0]

        set_cap(fd, fan, want)
        time.sleep(0.3)
        for t in temps:
            ask = fanctl.rpm_for_temp(coeffs, t)
            set_target_temp(fd, fan, t)
            time.sleep(0.5)
            got = fanctl.fan_target(fd, fan)
            hi, duty, target = sample(fd, fan, seconds if not sweep else 4.0)
            print(f"  target {t:5.1f} C -> EC asks {ask:6d} rpm | "
                  f"fan reads {got if got is None else round(got, 1)} C | "
                  f"peak {hi:6d} rpm, duty {duty}%, target {target} C")
            if sweep:
                set_target_temp(fd, fan, 0.0)
                time.sleep(0.5)
        restore(fd, fan, RPM_MIN)
        return 0
    finally:
        os.close(fd)


def _sigint(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sigint)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
