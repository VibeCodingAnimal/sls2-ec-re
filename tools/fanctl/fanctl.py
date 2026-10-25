#!/usr/bin/env python3
"""
fanctl.py — manual fan control for the Surface Laptop Studio 2.

How the EC actually decides the fan speed
------------------------------------------
The SAM computes a target RPM for each fan and steers the PWM towards it
with a closed loop.  The target comes from FUN_100C595E:

    target_rpm = min( quadratic(target_temp), forced_rpm )

  * quadratic(t) = a*t*t + b*t + c, with a/b/c stored in the fan's
    0x26-byte config blob (FAN CID 0x06).  On this machine:
    5.0229t^2 - 152.69t + 4381.5, i.e. 4321 rpm at 30 C, 7681 rpm at 45 C.
  * forced_rpm is a per-fan cap (fan+0x76) written by FAN CID 0x0B.
  * target_temp (fan+0x84) is the temperature the profile asks the fan to
    hold, and it is what the quadratic is evaluated at.  It comes from the
    fan profile record, selected with FAN CID 0x0E.

So there are exactly two knobs, and this tool drives both:

    FAN CID 0x0B   forced RPM cap       <- the slider
    FAN CID 0x0E   fan profile record   <- raises the ceiling

The ceiling for the current profile is quadratic(target_temp).  The
profiles are Quiet 25 C, Nominal 30 C, Performance 32 C, iGPU_Max 40 C,
dGPU_Max 45 C, Extreme 45 C; the ceiling is highest for the 45 C ones, so
the tool switches to a 45 C profile when the requested RPM is above what
the current profile can deliver, and puts the original profile back when
it exits.

FAN CID 0x80 (mode byte + u16) looks like the obvious manual-control
command and is what Windows' own fan driver uses, but it is inert on this
firmware: mode 2 only stores the RPM in fan+0x6E, and the fan state
machine copies it through a curve interpolator whose output is
independent of the commanded value.  Mode 3 is worse - the commit path
runs the interpolator after injecting the duty, so the duty is discarded.
Do not use CID 0x80.

    sudo python3 tools/fanctl/fanctl.py --gui
    sudo python3 tools/fanctl/fanctl.py --rpm 6000 --fan 1
    sudo python3 tools/fanctl/fanctl.py --auto --fan 1
    sudo python3 tools/fanctl/fanctl.py --status

The GUI hands control back to the EC when you close it, so a stray window
can't leave the fans pinned.

Safety notes:

* The EC's model clamps the fan's RPM to 11000, but that number is a
  guard, not a speed: the duty saturates at 100 % and the fan settles
  around 7500 rpm however hard it is asked (decoded/FAN_SPEED.md 10).
  The slider therefore tops out at the hottest profile's quadratic.
* Every path that pins a fan restores the original cap and profile on
  exit: SIGINT/SIGTERM, the CLI timeout and the GUI's close handler.
* --off is refused unless the machine is cool, and needs --force.
"""

import argparse
import ctypes
import json
import os
import signal
import struct
import sys
import time

CDEV_PATH = "/dev/surface/aggregator"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "profiles.json")

TC_FAN = 0x05

CID_RPM = 0x01
CID_PROFILE_REC = 0x04
CID_CONFIG = 0x06
CID_TARGET_TEMP = 0x0D
CID_CAP = 0x0B
CID_PROFILE = 0x0E
CID_DUTY = 0x11

RPM_MIN, RPM_MAX = 3000, 11000

# FAN CID 0x80, kept only so the docs can point at it
CID_MANUAL = 0x80
MODE_OFF, MODE_RPM, MODE_PERCENT, MODE_AUTO = 1, 2, 3, 4


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


def request(fd, tc, cid, iid=0, tid=1, payload=b"", resp_size=64, is_set=False):
    r = SsamCdevRequest()
    r.target_category, r.target_id = tc, tid
    r.command_id, r.instance_id = cid, iid
    r.flags = 0 if is_set else 0x01
    r.status = 0

    resp = (ctypes.c_uint8 * max(resp_size, 1))()
    pay = (ctypes.c_uint8 * max(len(payload), 1))()
    if payload:
        ctypes.memmove(ctypes.addressof(pay), payload, len(payload))
    r.payload.data = ctypes.addressof(pay) if payload else 0
    r.payload.length = len(payload)
    r.response.data = ctypes.addressof(resp)
    r.response.length = resp_size

    if libc.ioctl(fd, IOCTL, ctypes.byref(r)) != 0:
        return None, ctypes.get_errno(), b""
    return r.status, r.response.length, bytes(resp[:r.response.length])


# ------------------------------------------------------------- fan access

def fan_get(fd, cid, fan, n=64):
    st, _, d = request(fd, TC_FAN, cid, iid=fan, resp_size=n)
    return (st, d) if st == 0 else (st, b"")


def fan_rpm(fd, fan):
    st, d = fan_get(fd, CID_RPM, fan, 8)
    return struct.unpack_from("<H", d)[0] if len(d) >= 2 else None


def fan_duty(fd, fan):
    st, d = fan_get(fd, CID_DUTY, fan, 8)
    return d[0] if d else None


def fan_target(fd, fan):
    st, d = fan_get(fd, CID_TARGET_TEMP, fan, 8)
    return struct.unpack_from("<f", d)[0] if len(d) >= 4 else None


def fan_config(fd, fan):
    st, d = fan_get(fd, CID_CONFIG, fan, 0x26)
    return d if len(d) >= 0x26 else None


_coeff_cache = {}


def fan_coeffs(fd, fan):
    """(a, b, c) of the target-RPM quadratic.

    The config blob is firmware data and never changes, so it is read
    once per fan: every SSAM request costs the EC a round trip and the
    driver waits for it in uninterruptible sleep.
    """
    if fan in _coeff_cache:
        return _coeff_cache[fan]
    d = fan_config(fd, fan)
    if not d:
        return None
    coeffs = (struct.unpack_from("<f", d, 0x22)[0],
              struct.unpack_from("<f", d, 0x1A)[0],
              struct.unpack_from("<f", d, 0x1E)[0])
    _coeff_cache[fan] = coeffs
    return coeffs


def rpm_for_temp(coeffs, temp):
    a, b, c = coeffs
    return int(temp * temp * a + temp * b + c) & 0xFFFF


def ceiling(fd, fan):
    """Highest RPM the current profile can ask for."""
    coeffs = fan_coeffs(fd, fan)
    t = fan_target(fd, fan)
    if not coeffs or t is None:
        return None
    return rpm_for_temp(coeffs, t)


def max_ceiling(fd, fan):
    """Highest RPM any profile can ask for.

    The EC's own limit is 11000 rpm, but the target is
    min(quadratic(target_temp), cap) and the hottest profile is 45 C,
    which the quadratic turns into 7681 rpm on this machine.  Offering more
    than that would be a lie.
    """
    coeffs = fan_coeffs(fd, fan)
    if not coeffs:
        return RPM_MAX
    caps = [rpm_for_temp(coeffs, temp) for name, temp in profiles(fd).values()]
    return max(caps) if caps else RPM_MAX


def profile_record(fd, pid):
    """The raw 0x40-byte fan profile record, or None."""
    st, d = fan_get(fd, CID_PROFILE_REC, pid, 0x40)
    return d if len(d) >= 0x40 else None


def profile_temp(rec):
    """The target temperature of a profile record, in degrees C.

    It is a plain float32 at +0x30 (25.0, 30.0, 45.0 ...).
    """
    return struct.unpack_from("<f", rec, 0x30)[0]


def _profile_cache_load():
    try:
        with open(CACHE_PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    out = {}
    for pid, rec in raw.items():
        out[int(pid)] = (rec["name"], rec["temp"], rec["min_rpm"])
    return out or None


def _profile_cache_save(table):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump({str(pid): {"name": n, "temp": t, "min_rpm": m}
                       for pid, (n, t, m) in table.items()}, f, indent=1)
    except OSError:
        pass


def profiles(fd, refresh=False):
    """{id: (name, target_temp)} for the fan profile table.

    The table is firmware data, so it is cached on disk.  Reading it from
    the EC costs 15 requests, each of which can block for seconds.
    """
    if not refresh:
        cached = _profile_cache_load()
        if cached:
            return {pid: (n, t) for pid, (n, t, m) in cached.items()}
    out = {}
    mins = {}
    for pid in range(1, 16):
        d = profile_record(fd, pid)
        if not d:
            continue
        name = d[2:0x12].split(b"\x00")[0].decode("ascii", "replace")
        temp = profile_temp(d)
        if not name or not (0.0 < temp < 200.0):
            continue
        out[pid] = (name, temp)
        mins[pid] = struct.unpack_from("<H", d, 0x22)[0]
    if out:
        _profile_cache_save({pid: (n, t, mins[pid]) for pid, (n, t) in out.items()})
    return out


def profile_min_rpm(fd, pid):
    """The minimum RPM the profile record asks for."""
    cached = _profile_cache_load()
    if cached and pid in cached:
        return cached[pid][2]
    d = profile_record(fd, pid)
    if d and len(d) >= 0x24:
        return struct.unpack_from("<H", d, 0x22)[0]
    return RPM_MIN


def set_cap(fd, fan, rpm):
    """FAN CID 0x0B: the forced-RPM cap (fan+0x76)."""
    st, _, _ = request(fd, TC_FAN, CID_CAP, iid=fan,
                       payload=struct.pack("<H", rpm), is_set=True)
    return st


def set_profile(fd, fan, pid):
    """FAN CID 0x0E: point the fan at a profile record (sets target temp)."""
    st, _, _ = request(fd, TC_FAN, CID_PROFILE, iid=fan,
                       payload=bytes([pid]), is_set=True)
    return st


def set_manual(fd, fan, mode, value=0):
    """FAN CID 0x80.  Kept for reference -- it does not move the fan."""
    st, err, _ = request(fd, TC_FAN, CID_MANUAL, iid=fan,
                          payload=struct.pack("<BH", mode, value), is_set=True)
    if st is None:
        raise OSError(err, f"ioctl failed: {os.strerror(err)}")
    return st


# ------------------------------------------------------------- the control

class FanControl:
    """Drives a fan's target RPM through the cap + profile pair.

    The cap can only lower the fan below what the profile's quadratic
    asks for, so when the requested RPM is above the current ceiling the
    profile is switched to one with a higher target temperature first.
    """

    def __init__(self, fd, fan):
        self.fd = fd
        self.fan = fan
        self.orig_temp = fan_target(fd, fan)
        self.orig_profile = self._profile_for_temp(self.orig_temp)
        self.cur_profile = self.orig_profile
        # the cap the EC itself sets is the profile's minimum RPM
        self.orig_cap = RPM_MIN
        if self.orig_profile is not None:
            self.orig_cap = profile_min_rpm(fd, self.orig_profile)

    def _profile_for_temp(self, temp):
        """The profile whose target temperature is temp, if any."""
        if temp is None:
            return None
        for pid, (name, t) in sorted(profiles(self.fd).items()):
            if abs(t - temp) < 0.05:
                return pid
        return None

    def _profile_for(self, want_rpm):
        """A profile whose ceiling covers want_rpm, if we need to change.

        The current profile is left alone whenever it can already deliver
        the requested RPM -- switching it unnecessarily would quietly move
        the machine's thermal target (the profile's temperature) even when
        the user only wanted the fan slower.
        """
        coeffs = fan_coeffs(self.fd, self.fan)
        if not coeffs:
            return None
        cur_temp = fan_target(self.fd, self.fan)
        if cur_temp is not None and rpm_for_temp(coeffs, cur_temp) >= want_rpm:
            return None
        best = best_ceiling = None
        for pid, (name, temp) in profiles(self.fd).items():
            cap = rpm_for_temp(coeffs, temp)
            if cap >= want_rpm and (best_ceiling is None or cap < best_ceiling):
                best, best_ceiling = pid, cap
        if best is not None and best != self.cur_profile:
            return best
        return None

    def set_rpm(self, want_rpm):
        """Aim the fan at want_rpm.  Returns (status, ceiling_used)."""
        top = max_ceiling(self.fd, self.fan)
        want_rpm = max(RPM_MIN, min(top, want_rpm))
        target_profile = self._profile_for(want_rpm)
        if target_profile is not None:
            set_profile(self.fd, self.fan, target_profile)
            self.cur_profile = target_profile
            time.sleep(0.2)
        st = set_cap(self.fd, self.fan, want_rpm)
        return st, ceiling(self.fd, self.fan)

    def restore(self):
        """Hand the fan back to the EC's own policy."""
        orig = self.orig_profile
        if orig is None:
            # the profile table was unreadable when we started; look again
            orig = self._profile_for_temp(self.orig_temp)
        if orig is not None and self.cur_profile is not None \
                and self.cur_profile != orig:
            set_profile(self.fd, self.fan, orig)
            self.cur_profile = orig
            time.sleep(0.2)
        set_cap(self.fd, self.fan, self.orig_cap)


def install_deadman(ctrls):
    """SIGINT/SIGTERM -> hand the fans back to the EC policy."""
    def handler(signum, frame):
        for c in ctrls:
            try:
                c.restore()
            except OSError:
                pass
        print("\nrestored", file=sys.stderr)
        os._exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


# ---------------------------------------------------------------- headless

def cmd_status(fd):
    for fan in (1, 2):
        print(f"fan {fan}: rpm={fan_rpm(fd, fan)}  duty={fan_duty(fd, fan)}%  "
              f"target={fan_target(fd, fan)} C  ceiling={ceiling(fd, fan)}")


def cmd_set(fd, fan, want_rpm, seconds=10.0, restore=True):
    ctl = FanControl(fd, fan)
    top = max_ceiling(fd, fan)
    if want_rpm > top:
        print(f"note: {want_rpm} rpm is above what any profile can deliver "
              f"({top} rpm) — using {top}")
    st, ceil = ctl.set_rpm(want_rpm)
    if st != 0:
        print(f"EC rejected the write: status=0x{st & 0xffff:04x}", file=sys.stderr)
        return 1
    print(f"fan {fan}: target {min(want_rpm, top)} rpm (ceiling {ceil})")
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.25)
            print(f"   rpm={fan_rpm(fd, fan)} duty={fan_duty(fd, fan)}%")
    finally:
        if restore:
            ctl.restore()
            print(f"fan {fan}: restored to the EC policy")
    return 0


# -------------------------------------------------------------------- gui

def run_gui(fd):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("tkinter is not available; use the headless options instead",
              file=sys.stderr)
        return 1

    ctl = FanControl(fd, 1)
    ctl2 = FanControl(fd, 2)
    install_deadman((ctl, ctl2))

    class App:
        def __init__(self, root):
            self.root = root
            self.root.title("SLS2 fan control")
            self.fan = tk.IntVar(value=1)
            self.value = tk.IntVar(value=RPM_MIN)
            self.ctl = {1: ctl, 2: ctl2}
            self.applied = None
            self.last_apply = 0.0
            self.slider = ttk.Scale(root, from_=RPM_MIN, to=max_ceiling(fd, 1),
                                    orient="horizontal", variable=self.value,
                                    command=self.on_slide)
            self.slider.pack(fill="x", padx=12, pady=8)
            self.readout = tk.Label(root, text="", font=("TkFixedFont", 10),
                                    anchor="w")
            self.readout.pack(fill="x", padx=12, pady=4)

            ttk.Radiobutton(root, text="Fan 1", variable=self.fan,
                            value=1, command=self.on_fan).pack(side="left")
            ttk.Radiobutton(root, text="Fan 2", variable=self.fan,
                            value=2, command=self.on_fan).pack(side="left")
            ttk.Button(root, text="Apply", command=self.apply).pack(
                side="left", padx=4, pady=8)
            ttk.Button(root, text="Auto (EC policy)",
                       command=self.to_auto).pack(side="left", padx=4, pady=8)
            ttk.Button(root, text="Quit", command=self.quit).pack(side="left",
                                                                   padx=4, pady=8)
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
            self.root.after(500, self.poll)

        def on_fan(self):
            f = self.fan.get()
            self.slider.configure(to=max_ceiling(fd, f))
            self.apply()

        def on_slide(self, _):
            # dragging the slider drives the fan live
            self.update_readout()
            self.apply()

        def apply(self):
            f = self.fan.get()
            v = int(self.value.get())
            now = time.time()
            if (f, v) == self.applied or now - self.last_apply < 0.15:
                return
            self.last_apply = now
            self.applied = (f, v)
            st, ceil = self.ctl[f].set_rpm(v)
            self.readout.configure(
                text=f"fan {f}: {'applied' if st == 0 else f'rejected 0x{st:04x}'}"
                     f"  cmd={v}  ceiling={ceil}")

        def to_auto(self):
            f = self.fan.get()
            self.ctl[f].restore()
            self.applied = None
            self.readout.configure(text=f"fan {f}: back to the EC policy")

        def update_readout(self):
            f = self.fan.get()
            t = fan_target(fd, f)
            coeffs = fan_coeffs(fd, f)
            ceil = rpm_for_temp(coeffs, t) if coeffs and t is not None else None
            self.readout.configure(
                text=f"fan {f}: cmd={int(self.value.get())}   "
                     f"rpm={fan_rpm(fd, f)}  duty={fan_duty(fd, f)}%  "
                     f"target={t} C  ceiling={ceil}")

        def poll(self):
            self.update_readout()
            self.root.after(500, self.poll)

        def quit(self):
            for c in (ctl, ctl2):
                try:
                    c.restore()
                except OSError:
                    pass
            self.root.destroy()

    root = tk.Tk()
    root.geometry("780x200")
    root.minsize(780, 200)
    App(root)
    root.mainloop()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gui", action="store_true", help="slider UI")
    ap.add_argument("--rpm", type=int, help="target RPM (3000-11000)")
    ap.add_argument("--auto", action="store_true",
                    help="hand the fan back to the EC's own policy")
    ap.add_argument("--fan", type=int, default=1, choices=(1, 2))
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="how long to hold the target before restoring")
    ap.add_argument("--keep", action="store_true",
                    help="do NOT restore afterwards")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--refresh-profiles", action="store_true",
                    help="re-read the fan profile table from the EC and cache it")
    args = ap.parse_args()

    if not os.path.exists(CDEV_PATH):
        sys.exit(f"{CDEV_PATH} missing — run: sudo modprobe surface_aggregator_cdev")
    if os.geteuid() != 0:
        sys.exit("needs root (the cdev is mode 0600 root:root)")

    fd = os.open(CDEV_PATH, os.O_RDWR)
    try:
        if args.refresh_profiles:
            for pid, (name, temp) in sorted(profiles(fd, refresh=True).items()):
                print(f"  {pid} {name:14} {temp:5.1f} C")
            print(f"cached to {CACHE_PATH}")
            return 0
        if args.gui:
            return run_gui(fd)
        if args.status:
            cmd_status(fd)
            return 0
        if args.auto:
            ctl = FanControl(fd, args.fan)
            ctl.restore()
            print(f"fan {args.fan}: back to the EC policy")
            return 0
        if args.rpm is not None:
            if not RPM_MIN <= args.rpm <= RPM_MAX:
                sys.exit(f"--rpm must be {RPM_MIN}..{RPM_MAX}")
            ctl = FanControl(fd, args.fan)
            install_deadman((ctl,))
            return cmd_set(fd, args.fan, args.rpm, args.seconds,
                           restore=not args.keep)
        cmd_status(fd)
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
