#!/usr/bin/env python3
"""
fancurve.py - temperature-driven fan control for the Surface Laptop Studio 2.

Why the stock fan is bad on this machine
----------------------------------------
The EC's own fan curve never engages.  FUN_100C6084 builds a ramp in the
*fan module's own thermistor* against a start threshold that lives in the
fan record at +0x26 (3070 = 30.70 C), and the sensor reads about 21 C at
idle and 22 C under load.  So the curve sits pinned at its minimum
(3000 rpm) forever, and the only other limit is the applied profile's
target temperature.  Result: the CPU package idles at 57 C with both
fans at 3000 rpm.

What is left is the forced-RPM cap.  FUN_100C595E computes

    ceiling = fan+0x88 ? quadratic(fan+0x84) : 0xFFFF
    state 1 (cool): target = min(cap, ceiling)
    state 2 (hot):  target = min(max(curve(fan_temp), cap), ceiling)

and fan+0x88 is only set by FAN CID 0x0C.  Nobody sets it, so the
ceiling is 0xFFFF and the cap *is* the target RPM.  FAN CID 0x0B is
therefore a plain actuator: write a u16 and that is the RPM the EC
steers towards.

So this tool closes the loop in userspace instead: it reads the real
temperatures (CPU package, GPU), runs them through an editable curve,
and writes the resulting RPM to the cap.  The EC keeps its own thermal
protection on top - in state 2 the internal curve is a floor, and in
state 3 the EC drives 8500 rpm by itself.

    sudo python3 tools/fanctl/fancurve.py --gui
    sudo python3 tools/fanctl/fancurve.py --auto --preset gaming
    sudo python3 tools/fanctl/fancurve.py --rpm 6000 --seconds 30
    sudo python3 tools/fanctl/fancurve.py --status

The curve is a list of (temperature, rpm) points, piecewise linear, kept
in ~/.config/fancurve/curve.json so edits survive a restart.

Safety:
  * Every exit path hands the fans back: the cap goes to 3000 rpm, the
    value the EC had before we touched anything.
  * The command is never below 3000 rpm (the EC's configured minimum) and
    never above 7500 rpm, which is where the duty saturates.
  * A crash leaves the cap at the last commanded value.  That is the
    loud case, not the dangerous one - the EC's own minimum is what the
    machine ran at before, so the failure mode is "fan stays loud", not
    "fan stops".
"""

import argparse
import glob
import json
import os
import signal
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fanctl

RPM_MIN = 3000      # the fan config's minimum; the EC will not go lower
RPM_TOP = 7500      # the duty saturates here; 11000 is a model guard
CID_EC_TEMP = 0x13  # the fan module's own temperature

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "fancurve")
CURVE_PATH = os.path.join(CONFIG_DIR, "curve.json")
PRESET_PATH = os.path.join(CONFIG_DIR, "presets.json")

# A curve is a list of (temperature, rpm) points.  The presets below are
# calibrated against this machine: the CPU package idles at 53-57 C and
# only the GPU gets properly hot under load, so the quiet end of every
# curve has to reach past 55 C before it does anything.
PRESETS = {
    "balanced": {
        "desc": "quiet when idle, full blast by 85 C",
        "points": [[45, 3000], [58, 3200], [64, 3800], [70, 4600],
                   [75, 5400], [80, 6400], [85, 7500]],
    },
    "gaming": {
        "desc": "for an overclocked GPU, full blast by 84 C",
        "points": [[45, 3200], [58, 3600], [64, 4400], [70, 5200],
                   [75, 6000], [80, 7000], [84, 7500]],
    },
    "cool": {
        "desc": "coldest machine, full blast by 77 C",
        "points": [[40, 3600], [55, 4200], [62, 5000], [68, 5800],
                   [73, 6600], [77, 7500]],
    },
    "quiet": {
        "desc": "as silent as possible, still full blast by 84 C",
        "points": [[50, 3000], [62, 3200], [70, 4000], [77, 5200],
                   [84, 7500]],
    },
}


def clamp_rpm(rpm):
    return max(RPM_MIN, min(RPM_TOP, int(round(rpm))))


# ------------------------------------------------------------------ sensors

def hwmon_temp(driver, label):
    """A hwmon temperature in degrees C, or None."""
    for base in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(base, "name")) as f:
                if f.read().strip() != driver:
                    continue
        except OSError:
            continue
        for path in sorted(glob.glob(os.path.join(base, "temp*_input"))):
            lbl = path[:-len("_input")] + "_label"
            if os.path.exists(lbl):
                try:
                    with open(lbl) as f:
                        if f.read().strip() != label:
                            continue
                except OSError:
                    pass
            try:
                with open(path) as f:
                    return int(f.read().strip()) / 1000.0
            except (OSError, ValueError):
                return None
    return None


def cpu_temp():
    return hwmon_temp("coretemp", "Package id 0")


def skin_temp():
    """The hottest of the chassis sensors."""
    best = None
    for base in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(base, "name")) as f:
                if f.read().strip() != "surface_thermal":
                    continue
        except OSError:
            continue
        for path in sorted(glob.glob(os.path.join(base, "temp*_input"))):
            try:
                with open(path) as f:
                    val = int(f.read().strip()) / 1000.0
            except (OSError, ValueError):
                continue
            if best is None or val > best:
                best = val
    return best


class GpuTemp:
    """nvidia-smi is slow, so it is polled on its own clock."""

    def __init__(self, interval=5.0):
        self.interval = interval
        self.value = None
        self.next_poll = 0.0

    def get(self, now=None):
        now = time.monotonic() if now is None else now
        if now >= self.next_poll:
            self.next_poll = now + self.interval
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3)
                self.value = float(out.stdout.strip().splitlines()[0])
            except (OSError, IndexError, ValueError,
                    subprocess.SubprocessError):
                pass
        return self.value


def ec_temp(fd, fan=1):
    """The fan module's own temperature, in degrees C."""
    st, d = fanctl.fan_get(fd, CID_EC_TEMP, fan, 8)
    if st != 0 or len(d) < 4:
        return None
    return struct.unpack_from("<f", d)[0]


# -------------------------------------------------------------------- curve

class Curve:
    """Piecewise linear temperature -> rpm."""

    def __init__(self, points):
        self.points = sorted((float(t), float(r)) for t, r in points)

    def rpm(self, temp):
        pts = self.points
        if not pts:
            return RPM_MIN
        if temp <= pts[0][0]:
            return pts[0][1]
        if temp >= pts[-1][0]:
            return pts[-1][1]
        for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
            if t0 <= temp <= t1:
                if t1 == t0:
                    return r1
                return r0 + (temp - t0) * (r1 - r0) / (t1 - t0)
        return pts[-1][1]

    def as_json(self):
        return [[t, r] for t, r in self.points]

    @staticmethod
    def load(path):
        try:
            with open(path) as f:
                raw = json.load(f)
            pts = raw["points"] if isinstance(raw, dict) else raw
            return Curve(pts)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, path=CURVE_PATH):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"points": self.as_json()}, f, indent=1)
        os.replace(tmp, path)
        fix_owner(path)


def fix_owner(path):
    """pkexec runs us as root; leave the config files Otter's own."""
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if not uid or not gid:
        return
    try:
        os.chown(path, int(uid), int(gid))
        os.chown(os.path.dirname(path), int(uid), int(gid))
    except (OSError, ValueError):
        pass


def all_presets():
    """The built-in presets plus any the user saved."""
    presets = dict(PRESETS)
    try:
        with open(PRESET_PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return presets
    if not isinstance(raw, dict):
        return presets
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "points" not in spec:
            continue
        presets[name] = {"desc": spec.get("desc", ""),
                         "points": spec["points"]}
    return presets


def save_preset(name, points, desc=""):
    """Keep a tuned curve under its own name, next to the saved one."""
    try:
        with open(PRESET_PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[name] = {"desc": desc, "points": [[t, r] for t, r in points]}
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = PRESET_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(raw, f, indent=1)
    os.replace(tmp, PRESET_PATH)
    fix_owner(PRESET_PATH)


def resolve_curve(args):
    """The curve to run: --points, then a file, then the saved one, then
    the preset.  Returns the curve and a note of where it came from."""
    if args.curve_points:
        return Curve(args.curve_points), "command line"
    if args.curve:
        curve = Curve.load(args.curve)
        if curve is None:
            raise SystemExit("cannot read the curve file: %s" % args.curve)
        return curve, args.curve
    if args.preset is None:
        curve = Curve.load(CURVE_PATH)
        if curve is not None:
            return curve, CURVE_PATH
        return Curve(PRESETS["balanced"]["points"]), "balanced"
    preset = all_presets().get(args.preset)
    if preset is None:
        raise SystemExit("no such preset: %s" % args.preset)
    return Curve(preset["points"]), args.preset


# ---------------------------------------------------------------- controller

class Controller:
    """Writes the forced-RPM cap, and puts it back on the way out."""

    def __init__(self, fd, fans=(1, 2)):
        self.fd = fd
        self.fans = list(fans)
        self.commanded = {}

    def command(self, rpm):
        """Aim every fan at rpm.  Returns the value actually written."""
        rpm = clamp_rpm(rpm)
        for fan in self.fans:
            fanctl.set_cap(self.fd, fan, rpm)
        self.commanded = {f: rpm for f in self.fans}
        return rpm

    def restore(self):
        """Hand the fans back to the EC: its own minimum, as before."""
        for fan in self.fans:
            try:
                fanctl.set_cap(self.fd, fan, RPM_MIN)
            except OSError:
                pass
        self.commanded = {}


def deadman(ctrl):
    """SIGINT/SIGTERM -> put the fans back before the process dies."""
    def handler(signum, frame):
        ctrl.restore()
        print("\nfans restored to %d rpm" % RPM_MIN, file=sys.stderr)
        os._exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


# ----------------------------------------------------------------- auto mode

class AutoPilot:
    """Reads the temperatures, runs the curve, commands the cap."""

    def __init__(self, ctrl, curve, sources=("cpu", "gpu"), interval=1.0,
                 gpu_interval=5.0, alpha=0.25, deadband=50):
        self.ctrl = ctrl
        self.curve = curve
        self.sources = list(sources)
        self.interval = interval
        self.gpu = GpuTemp(gpu_interval)
        self.alpha = alpha
        self.deadband = deadband
        self.smoothed = None
        self.last = None

    def read_temp(self):
        """The temperature the curve is evaluated at: the hottest source."""
        vals = {}
        if "cpu" in self.sources:
            vals["cpu"] = cpu_temp()
        if "gpu" in self.sources:
            vals["gpu"] = self.gpu.get()
        if "skin" in self.sources:
            vals["skin"] = skin_temp()
        if "ec" in self.sources:
            vals["ec"] = ec_temp(self.ctrl.fd)
        vals = {k: v for k, v in vals.items() if v is not None}
        if not vals:
            return None, vals
        return max(vals.values()), vals

    def step(self):
        """One iteration.  Returns (commanded rpm or None, temps)."""
        temp, vals = self.read_temp()
        if temp is None:
            return None, vals
        if self.smoothed is None:
            self.smoothed = temp
        else:
            self.smoothed += (temp - self.smoothed) * self.alpha
        want = clamp_rpm(self.curve.rpm(self.smoothed))
        if self.last is None or abs(want - self.last) >= self.deadband:
            self.ctrl.command(want)
            self.last = want
        return want, vals


def run_auto(fd, args):
    curve, source = resolve_curve(args)
    ctrl = Controller(fd, fans_of(args))
    deadman(ctrl)
    pilot = AutoPilot(ctrl, curve, sources_of(args), args.interval,
                       args.gpu_interval)

    print("auto: %s" % ", ".join(s for s in pilot.sources))
    print("curve (%s): %s" % (source, " ".join("%gC=%d" % (t, r)
                                              for t, r in curve.points)))
    deadline = time.time() + args.seconds if args.seconds else None
    last_print = 0.0
    shown = None
    try:
        while deadline is None or time.time() < deadline:
            rpm, vals = pilot.step()
            if rpm is None:
                print("no temperature source is readable", file=sys.stderr)
                return 1
            now = time.monotonic()
            if rpm != shown or now - last_print > 10:
                last_print = now
                shown = rpm
                print("%s  ->  %d rpm" % (
                    "  ".join("%s=%.1f" % (k, v)
                              for k, v in sorted(vals.items())), rpm),
                    flush=True)
            time.sleep(args.interval)
    finally:
        if not args.keep:
            ctrl.restore()
            print("fans restored to %d rpm" % RPM_MIN)
    return 0


# ----------------------------------------------------------------------- gui

def run_gui(fd, args):
    try:
        import tkinter as tk
        from tkinter import ttk, simpledialog
    except ImportError:
        print("tkinter is not available; use --auto or --rpm instead",
              file=sys.stderr)
        return 1

    curve, source = resolve_curve(args)
    ctrl = Controller(fd, fans_of(args))
    deadman(ctrl)

    class Plot(tk.Canvas):
        """The curve, the live temperature, and the operating point."""

        T0, T1 = 25.0, 95.0
        LEFT, BOTTOM = 56, 26

        def __init__(self, master):
            super().__init__(master, width=430, height=250,
                             background="#11161b", highlightthickness=0)
            self.curve = curve
            self.temp = None
            self.rpm = None
            self.bind("<Configure>", lambda e: self.draw())

        def x(self, temp):
            w = self.winfo_width() - self.LEFT - 12
            return self.LEFT + (temp - self.T0) / (self.T1 - self.T0) * w

        def y(self, rpm):
            h = self.winfo_height() - self.BOTTOM - 12
            return self.BOTTOM + (1.0 - (rpm - RPM_MIN) /
                                  (RPM_TOP - RPM_MIN)) * h

        def draw(self):
            self.delete("all")
            if self.winfo_width() < 120:
                return
            bottom = self.winfo_height() - self.BOTTOM
            for temp in range(30, 96, 10):
                x = self.x(temp)
                self.create_line(x, 10, x, bottom, fill="#1d262e")
                self.create_text(x, bottom + 8, text="%d" % temp,
                                 fill="#5d707d", font=("TkFixedFont", 8))
            for rpm in (3000, 4500, 6000, 7500):
                y = self.y(rpm)
                self.create_line(self.LEFT, y, self.winfo_width() - 12, y,
                                 fill="#1d262e")
                self.create_text(self.LEFT - 6, y, text="%d" % rpm, anchor="e",
                                 fill="#5d707d", font=("TkFixedFont", 8))
            pts = [(self.x(t), self.y(r)) for t, r in self.curve.points]
            if len(pts) > 1:
                self.create_line(*sum([list(p) for p in pts], []),
                                 fill="#4fc3f7", width=2, smooth=False)
            for x, y in pts:
                self.create_oval(x - 3, y - 3, x + 3, y + 3,
                                 fill="#4fc3f7", outline="")
            if self.temp is not None:
                x = self.x(self.temp)
                self.create_line(x, 12, x, bottom, fill="#ffb74d", dash=(3, 3))
                if self.rpm is not None:
                    y = self.y(self.rpm)
                    self.create_oval(x - 4, y - 4, x + 4, y + 4,
                                     fill="#ffb74d", outline="")

    class App:
        def __init__(self, root):
            self.root = root
            self.root.title("SLS2 fan curve")
            self.root.geometry("1000x470")
            self.root.minsize(860, 400)
            self.mode = tk.StringVar(value="manual")
            self.rpm_var = tk.DoubleVar(value=RPM_MIN)
            self.preset = tk.StringVar(
                value="custom" if source == CURVE_PATH
                else (args.preset or "balanced"))
            self.status = tk.StringVar(value="")
            self.manual = None
            self.pilot = AutoPilot(ctrl, curve, sources_of(args),
                                   args.interval, args.gpu_interval)
            self.last_write = 0.0

            top = ttk.Frame(root)
            top.pack(fill="x", padx=10, pady=(10, 0))
            ttk.Radiobutton(top, text="Manual", variable=self.mode,
                            value="manual",
                            command=self.on_mode).pack(side="left")
            ttk.Radiobutton(top, text="Auto (curve)", variable=self.mode,
                            value="auto",
                            command=self.on_mode).pack(side="left", padx=8)
            ttk.Button(top, text="Restore EC policy",
                       command=self.restore).pack(side="right")
            ttk.Button(top, text="Quit", command=self.quit).pack(
                side="right", padx=6)

            srow = ttk.Frame(root)
            srow.pack(fill="x", padx=10, pady=(8, 0))
            self.slider = tk.Scale(
                srow, from_=RPM_MIN, to=RPM_TOP, resolution=50, orient="horizontal",
                variable=self.rpm_var, command=self.on_slide, showvalue=False,
                highlightthickness=0, bd=0, sliderrelief="flat",
                troughcolor="#2b3238", background="#f0f0f0",
                activebackground="#4fc3f7")
            self.slider.pack(side="left", fill="x", expand=True)
            self.rpm_label = ttk.Label(srow, text="%d rpm" % RPM_MIN, width=12,
                                       anchor="e")
            self.rpm_label.pack(side="left", padx=(8, 0))

            mid = ttk.Frame(root)
            mid.pack(fill="both", expand=True, padx=10, pady=8)

            left = ttk.Frame(mid)
            left.pack(side="left", fill="both", expand=True)
            self.plot = Plot(left)
            self.plot.pack(fill="both", expand=True)

            right = ttk.Frame(mid)
            right.pack(side="left", fill="both", expand=True, padx=(12, 0))
            row = ttk.Frame(right)
            row.pack(fill="x")
            ttk.Label(row, text="preset").pack(side="left")
            names = list(all_presets())
            if source == CURVE_PATH and "custom" not in names:
                names.insert(0, "custom")
            box = ttk.Combobox(row, values=names, textvariable=self.preset,
                               state="readonly", width=12)
            self.box = box
            box.pack(side="left", padx=6)
            box.bind("<<ComboboxSelected>>", self.on_preset)
            ttk.Button(row, text="Load", command=self.on_preset).pack(side="left")

            self.table = ttk.Treeview(right, columns=("temp", "rpm"),
                                      show="headings", height=8)
            self.table.heading("temp", text="temp C")
            self.table.heading("rpm", text="rpm")
            self.table.column("temp", width=70)
            self.table.column("rpm", width=70)
            self.table.pack(fill="both", expand=True, pady=6)
            self.table.bind("<Double-1>", self.on_edit)

            brow = ttk.Frame(right)
            brow.pack(fill="x")
            ttk.Button(brow, text="Add", command=self.on_add).pack(side="left")
            ttk.Button(brow, text="Remove",
                       command=self.on_remove).pack(side="left", padx=4)
            ttk.Button(brow, text="Save", command=self.on_save).pack(side="left")
            ttk.Button(brow, text="Keep",
                       command=self.on_keep).pack(side="left", padx=4)
            ttk.Button(brow, text="Reload",
                       command=self.on_reload).pack(side="left", padx=4)
            ttk.Label(right, textvariable=self.status, wraplength=380,
                      justify="left").pack(anchor="w", pady=(8, 0))
            self.refresh_table()
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
            self.root.after(300, self.poll)

        # ---------------------------------------------------------- actions
        def refresh_table(self):
            self.table.delete(*self.table.get_children())
            for temp, rpm in curve.points:
                self.table.insert("", "end", values=("%g" % temp, "%g" % rpm))

        def on_mode(self):
            if self.mode.get() == "auto":
                self.slider.state(["disabled"])
                self.manual = None
            else:
                self.slider.state(["!disabled"])
                self.on_slide(self.rpm_var.get())

        def on_slide(self, _value):
            if self.mode.get() != "manual":
                return
            self.rpm_label.configure(text="%d rpm" % self.rpm_var.get())
            now = time.monotonic()
            if now - self.last_write < 0.15:
                return
            self.last_write = now
            self.manual = ctrl.command(float(self.rpm_var.get()))

        def on_preset(self, _event=None):
            name = self.preset.get()
            if name == "custom":
                return
            preset = all_presets().get(name)
            if preset is None:
                return
            curve.points = sorted((float(t), float(r))
                                  for t, r in preset["points"])
            self.refresh_table()
            self.plot.draw()

        def on_add(self):
            last = curve.points[-1][0] if curve.points else 40.0
            temp = simpledialog.askfloat("Add point", "temperature (C)",
                                          initialvalue=last + 5.0)
            if temp is None:
                return
            rpm = simpledialog.askfloat("Add point", "rpm",
                                         initialvalue=RPM_MIN)
            if rpm is None:
                return
            curve.points.append((float(temp), clamp_rpm(rpm)))
            curve.points.sort()
            self.refresh_table()
            self.plot.draw()

        def on_edit(self, _event):
            sel = self.table.selection()
            if not sel:
                return
            item = sel[0]
            temp, rpm = self.table.item(item, "values")
            new_temp = simpledialog.askfloat("Edit point", "temperature (C)",
                                             initialvalue=float(temp))
            if new_temp is None:
                return
            new_rpm = simpledialog.askfloat("Edit point", "rpm",
                                            initialvalue=float(rpm))
            if new_rpm is None:
                return
            curve.points = [(t, r) for t, r in curve.points
                            if (t, r) != (float(temp), float(rpm))]
            curve.points.append((float(new_temp), clamp_rpm(new_rpm)))
            curve.points.sort()
            self.refresh_table()
            self.plot.draw()

        def on_remove(self):
            sel = self.table.selection()
            if not sel:
                return
            temp, rpm = self.table.item(sel[0], "values")
            curve.points = [(t, r) for t, r in curve.points
                            if (t, r) != (float(temp), float(rpm))]
            self.refresh_table()
            self.plot.draw()

        def on_save(self):
            try:
                curve.save()
            except OSError as exc:
                self.status.set("could not save: %s" % exc)
                return
            self.status.set("saved to %s - the auto loop uses this curve"
                            % CURVE_PATH)

        def on_keep(self):
            """Keep the curve under a name, so it can be picked again."""
            name = simpledialog.askstring("Keep this curve",
                                           "name for this curve",
                                           initialvalue="tuned")
            if not name or not name.strip():
                return
            name = name.strip()
            try:
                save_preset(name, curve.points)
            except OSError as exc:
                self.status.set("could not keep: %s" % exc)
                return
            names = list(all_presets())
            self.box["values"] = names
            self.preset.set(name)
            self.status.set("kept as %r in %s" % (name, PRESET_PATH))

        def on_reload(self):
            """Re-read the curve from disk, for hand-edited numbers."""
            saved = Curve.load(CURVE_PATH)
            if saved is None:
                self.status.set("no saved curve at %s yet" % CURVE_PATH)
                return
            curve.points = saved.points
            self.refresh_table()
            self.plot.draw()
            self.status.set("reloaded %s" % CURVE_PATH)

        def restore(self):
            ctrl.restore()
            self.mode.set("manual")
            self.slider.state(["!disabled"])
            self.status.set("fans restored to %d rpm" % RPM_MIN)

        # ------------------------------------------------------------- poll
        def poll(self):
            want = None
            if self.mode.get() == "auto":
                want, vals = self.pilot.step()
                self.plot.temp = self.pilot.smoothed
                self.plot.rpm = want
                self.plot.draw()
                if want is not None:
                    self.rpm_var.set(want)
                    self.rpm_label.configure(text="%d rpm" % want)
                temps = "  ".join("%s=%.1f" % (k, v)
                                   for k, v in sorted(vals.items()))
            else:
                temps = "cpu=%.1f  gpu=%.1f" % (
                    cpu_temp() or 0.0, self.pilot.gpu.get() or 0.0)
            fans = "  ".join(
                "f%d %drpm %d%%" % (f, fanctl.fan_rpm(fd, f) or 0,
                                    fanctl.fan_duty(fd, f) or 0)
                for f in ctrl.fans)
            self.status.set("%s   |   %s" % (temps, fans))
            self.root.after(500, self.poll)

        def quit(self):
            ctrl.restore()
            self.root.destroy()

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


# ------------------------------------------------------------------- helpers

def fans_of(args):
    return tuple(int(x) for x in args.fans.split(",") if x.strip())


def sources_of(args):
    return tuple(x.strip() for x in args.sources.split(",") if x.strip())


def cmd_status(fd, args):
    print("cpu   %.1f C" % (cpu_temp() or 0.0))
    print("gpu   %.1f C" % (GpuTemp().get() or 0.0))
    skin = skin_temp()
    if skin is not None:
        print("skin  %.1f C" % skin)
    for fan in fans_of(args):
        print("fan %d  rpm=%s  duty=%s%%  target=%s C  ec=%.1f C" % (
            fan, fanctl.fan_rpm(fd, fan), fanctl.fan_duty(fd, fan),
            fanctl.fan_target(fd, fan), ec_temp(fd, fan) or 0.0))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gui", action="store_true", help="slider and curve UI")
    ap.add_argument("--auto", action="store_true",
                    help="follow the curve")
    ap.add_argument("--rpm", type=int, help="hold this RPM (manual)")
    ap.add_argument("--preset",
                    help="curve preset (default: the saved curve)")
    ap.add_argument("--curve", help="curve file (JSON: [[temp, rpm], ...])")
    ap.add_argument("--sources", default="cpu,gpu",
                    help="temperature sources: cpu,gpu,skin,ec")
    ap.add_argument("--fans", default="1,2", help="which fans to drive")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="control loop period")
    ap.add_argument("--gpu-interval", type=float, default=5.0,
                    help="how often to poll nvidia-smi")
    ap.add_argument("--seconds", type=float, help="stop after this long")
    ap.add_argument("--keep", action="store_true",
                    help="do NOT restore the fans on exit")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="put the fans back to %d rpm" % RPM_MIN)
    ap.add_argument("--list-presets", action="store_true")
    args = ap.parse_args()

    if args.list_presets:
        for name, preset in sorted(all_presets().items()):
            print("%-10s %s" % (name, preset["desc"]))
            print("           %s" % " ".join("%gC=%d" % (t, r)
                                           for t, r in preset["points"]))
        return 0

    if not os.path.exists(fanctl.CDEV_PATH):
        sys.exit("%s missing - run: sudo modprobe surface_aggregator_cdev"
                 % fanctl.CDEV_PATH)
    if not os.access(fanctl.CDEV_PATH, os.O_RDWR):
        sys.exit("needs root (the cdev is mode 0600 root:root) - "
                 "use the desktop launcher, which asks for the password")

    args.curve_points = None

    fd = os.open(fanctl.CDEV_PATH, os.O_RDWR)
    try:
        if args.status:
            cmd_status(fd, args)
            return 0
        if args.restore:
            Controller(fd, fans_of(args)).restore()
            print("fans restored to %d rpm" % RPM_MIN)
            return 0
        if args.gui:
            return run_gui(fd, args)
        if args.auto:
            return run_auto(fd, args)
        if args.rpm is not None:
            ctrl = Controller(fd, fans_of(args))
            deadman(ctrl)
            ctrl.command(args.rpm)
            print("fan(s) %s: %d rpm" % (args.fans, clamp_rpm(args.rpm)))
            deadline = time.time() + args.seconds if args.seconds else None
            try:
                while deadline is None or time.time() < deadline:
                    time.sleep(0.5)
                    print("   " + "  ".join(
                        "f%d %drpm %d%%" % (f, fanctl.fan_rpm(fd, f) or 0,
                                            fanctl.fan_duty(fd, f) or 0)
                        for f in ctrl.fans), flush=True)
            finally:
                if not args.keep:
                    ctrl.restore()
                    print("fans restored to %d rpm" % RPM_MIN)
            return 0
        cmd_status(fd, args)
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
