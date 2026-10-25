#!/usr/bin/env python3
"""restore_nominal.py — put a fan back on the Nominal profile with a 3000 rpm cap."""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fanctl"))

import fanctl  # noqa: E402


def main():
    fans = [int(a) for a in sys.argv[1:]] or [1, 2]
    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        for fan in fans:
            print(fanctl.set_profile(fd, fan, 2), "profile -> 2")
            time.sleep(0.2)
            print(fanctl.set_cap(fd, fan, 3000), "cap -> 3000")
        print()
        for fan in fans:
            print(f"fan {fan}: rpm={fanctl.fan_rpm(fd, fan)} "
                  f"duty={fanctl.fan_duty(fd, fan)}% "
                  f"target={fanctl.fan_target(fd, fan)} C "
                  f"ceiling={fanctl.ceiling(fd, fan)}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    import time
    main()
