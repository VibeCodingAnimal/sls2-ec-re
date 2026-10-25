#!/usr/bin/env python3
"""
profile.py — set the EC platform profile and show what it actually applied.

The Linux `surface_platform_profile` driver only speaks platform ids 1..4.
The EC accepts 1..7, so ids 5 (Gaming -> Extreme fans), 6 (Mixed_Reality ->
Extreme fans) and 7 (Kiosk -> Performance fans) are only reachable over the
raw SSAM cdev.

    sudo python3 tools/cdev-probe/profile.py            # show current
    sudo python3 tools/cdev-probe/profile.py 5          # set Gaming
    sudo python3 tools/cdev-probe/profile.py 1          # back to Nominal

Requires the cdev module:
    sudo modprobe surface_aggregator_cdev
"""

import ctypes
import os
import struct
import sys

CDEV_PATH = "/dev/surface/aggregator"

NAMES = {
    1: "Nominal", 2: "Quiet", 3: "Performance", 4: "Max",
    5: "Gaming", 6: "Mixed_Reality", 7: "Kiosk",
}

FAN_PROFILE = {
    1: "Nominal", 2: "Quiet", 3: "Performance",
    4: "dGPU_Max (45C) on a dGPU board / iGPU_Max (40C) on an iGPU-only board",
    5: "Extreme", 6: "Extreme", 7: "Performance",
}


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


def req(fd, tc, cid, iid=0, tid=1, payload=b"", resp_size=64, want_resp=True):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0x01 if want_resp else 0
    r.status = 0
    buf = (ctypes.c_uint8 * max(resp_size, 1))()
    pbuf = (ctypes.c_uint8 * max(len(payload), 1))()
    if payload:
        ctypes.memmove(ctypes.addressof(pbuf), payload, len(payload))
    r.payload.data = ctypes.addressof(pbuf) if payload else 0
    r.payload.length = len(payload)
    r.response.data = ctypes.addressof(buf)
    r.response.length = resp_size
    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, ctypes.get_errno(), b""
    return r.status, r.response.length, bytes(buf[:r.response.length])


def show(fd):
    st, _, d = req(fd, 0x03, 0x02, resp_size=8)
    idx = struct.unpack_from("<H", d, 6)[0] if st == 0 and len(d) >= 8 else None
    print(f"active GPU power-profile index: {idx} "
          f"({NAMES.get(idx, '?')})")
    for fan in (1, 2):
        st, _, d = req(fd, 0x05, 0x0D, iid=fan, resp_size=8)
        t = struct.unpack("<f", d[:4])[0] if st == 0 and len(d) >= 4 else None
        st, _, d = req(fd, 0x05, 0x01, iid=fan, resp_size=4)
        rpm = struct.unpack("<H", d[:2])[0] if st == 0 and len(d) >= 2 else None
        st, _, d = req(fd, 0x05, 0x11, iid=fan, resp_size=4)
        duty = d[0] if st == 0 and d else None
        print(f"  fan{fan}: target={t} C   rpm={rpm}   duty={duty}%")


def main():
    if not os.path.exists(CDEV_PATH):
        sys.exit(f"{CDEV_PATH} missing — run: sudo modprobe surface_aggregator_cdev")
    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        if len(sys.argv) > 1:
            pid = int(sys.argv[1], 0)
            if pid not in NAMES:
                sys.exit(f"platform id must be 1..7, got {pid}")
            st, _, _ = req(fd, 0x03, 0x03, payload=struct.pack("<I", pid),
                           want_resp=False)
            if st != 0:
                sys.exit(f"EC rejected the set: status=0x{st & 0xffff:04x}")
            print(f"set platform profile {pid} ({NAMES[pid]}) "
                  f"-> fan profile: {FAN_PROFILE[pid]}")
        show(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
