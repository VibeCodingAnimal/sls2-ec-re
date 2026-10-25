#!/usr/bin/env python3
"""
fan_plan.py — show what fanctl.py would do, without writing anything.

Prints the profile table, the target-RPM quadratic and, for a range of
requested RPMs, which profile the tool would switch to (and the ceiling
that results).  Read-only.

    sudo python3 tools/cdev-probe/fan_plan.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402


def main():
    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        profs = fanctl.profiles(fd)
        print("fan profile table (CID 0x04):")
        for pid, (name, temp) in sorted(profs.items()):
            print(f"  id {pid} {name:14} target {temp:5.1f} C")
        print()
        for fan in (1, 2):
            coeffs = fanctl.fan_coeffs(fd, fan)
            target = fanctl.fan_target(fd, fan)
            print(f"fan {fan}: target {target} C, ceiling {fanctl.ceiling(fd, fan)} rpm")
            print(f"  quadratic a={coeffs[0]} b={coeffs[1]} c={coeffs[2]}")
            for pid, (name, temp) in sorted(profs.items()):
                cap = fanctl.rpm_for_temp(coeffs, temp)
                print(f"    profile {pid} {name:14} {temp:5.1f} C -> ceiling {cap}")
            ctl = fanctl.FanControl(fd, fan)
            print(f"  active profile {ctl.orig_profile}, "
                  f"profile min rpm {ctl.orig_cap}")
            print("  requested rpm -> profile the tool would pick")
            for want in (3000, 4000, 4321, 5000, 6000, 7000, 7681, 11000):
                pid = ctl._profile_for(want)
                if pid is None:
                    print(f"    {want:6} -> keep profile {ctl.orig_profile}")
                else:
                    cap = fanctl.rpm_for_temp(coeffs, profs[pid][1])
                    print(f"    {want:6} -> switch to profile {pid} "
                          f"({profs[pid][0]}, ceiling {cap})")
            print()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
