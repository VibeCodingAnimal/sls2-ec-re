# SLS2 EC Firmware Reverse-Engineering

**Goal:** understand how the SLS2 Embedded Controller (EC) firmware and the
Windows Surface driver stack control fan speed, GPU power profile and power
delivery, so that Linux can do the same.

## Status: manual fan control solved, live and proven

The EC's ARM code is in the MSI and it is **not** encrypted. It was loaded
into Ghidra (2983 procedures) and the fan subsystem was decoded down to the
register writes.

The fan model, from `FUN_100C595E` and `FUN_100C5DFE`:

```
target_rpm = min( quadratic(target_temp), forced_rpm )
```

Two SSAM commands drive it:

| Command | Payload | Effect |
|---|---|---|
| `FAN CID 0x0B` | u16 rpm | the forced-RPM cap (`fan+0x76`) — this is the slider |
| `FAN CID 0x0E` | u8 profile id | point the fan at a profile record, which sets the target °C |
| `FAN CID 0x0C` | f32 °C | set the target temperature directly (sets the override flag, so the profile cannot overwrite it) |

Live proof on this machine:

```
3026 rpm → CID 0x0B = 11000        → 4808 rpm / 71 %
         → CID 0x0E = 6 "Extreme"   → 7508 rpm / 100 % / 45 °C
         → restore                  → 3200 rpm
```

The ceiling is **~7500 rpm, not 11000**. Driving the EC's own quadratic as
far as it goes (CID 0x0C → 55 °C, so the EC asks for 11177 rpm) yields
7450 rpm with the duty already at 100 %. The `11000` in the fan's config
blob is a guard on the EC's *model* rpm, not a speed the fan delivers.

### The cap is a plain speed command

`FAN CID 0x0B` turns out to be an accurate actuator on its own. The
quadratic is only the ceiling if the CID 0x0C override flag (`fan+0x88`) is
set, and nothing sets it in normal operation — so the cap *is* the
commanded RPM. Measured, both fans, 25 s to settle:

| commanded | fan 1 | duty | fan 2 | duty |
|---|---|---|---|---|
| 3000 | 3001 rpm | 30 % | 2999 rpm | 31 % |
| 5500 | 5567 rpm | 65 % | 5576 rpm | 67 % |
| 7500 | 7318 rpm | 97 % | 7270 rpm | 100 % |

### Why the stock fan is useless here, and what fixes it

The EC's internal curve is driven by the **fan module's own thermistor**
(21-23 °C idle, 45 °C at 6600 rpm) and its span is 40 centi-degrees — a
step, not a ramp. It never engages, and the profile's quadratic pins what
it would give to 4321 rpm (Nominal) or 7681 rpm (dGPU_Max). Linux's
platform profile only emits ids 1-4. So the fans sit at their 3000 rpm
minimum while the CPU package idles at 53-57 °C.

The fix is to close the loop in userspace on the real temperatures, which
is what `tools/fanctl/fancurve.py` does.

Details: `decoded/FAN_SPEED.md` (§13 has the whole state machine),
`decoded/FAN_CONTROL.md`, `decoded/TC_FAN.md`, `decoded/CURVE.md`.

### Tooling

```
sudo modprobe surface_aggregator_cdev     # gives /dev/surface/aggregator
tools/fanctl/fancurve --gui               # slider + editable fan curve
tools/fanctl/fancurve --auto              # follow the saved curve
python3 tools/fanctl/fanctl.py --status
python3 tools/fanctl/fanctl.py --rpm 6000
python3 tools/fanctl/fanctl.py --gui      # Tk slider
```

`fancurve.py` drives the cap from a temperature→rpm curve (CPU package,
GPU, chassis and the EC's own sensor), with a slider for manual control,
presets, an editable curve saved to `~/.config/fancurve/curve.json`, and a
deadman that puts the fans back to 3000 rpm on every exit path.

**Each fan has its own curve and its own set of temperatures to watch.**
The EC itself does not split the fans — both run off the same fan-module
thermistor and the same profile — but the cap (`FAN CID 0x0B`) is written
per fan, so the userspace loop can. Pick a fan in the GUI's *fan* box, give
it the sensors you want it to watch, and give it a curve.

That matters because the EC's own reaction to heat is one-sided: it follows
the CPU side and never sees the GPU. With the cap at 3000 rpm and a 4096²
matmul running, the GPU reached 69 °C and the fans never left 3000 rpm.
Under a CPU load the same cap gave 4130-5696 rpm. So the EC already covers
the CPU side (badly, and late), and nothing covers the GPU side at all —
which is exactly the fan the GPU curve belongs on.

`tools/fanctl/fancurve` is the launcher: the SSAM cdev is `0600 root:root`,
so it goes through `pkexec` and hands the GUI the live XWayland cookie and
the user's config directory. `tools/fanctl/sls2-fan-curve.desktop` puts it
on the desktop and in the launcher.

### Tuning the curve

The curve is a list of (°C, rpm) points, and both the GUI and the auto
loop read the same one:

| where | what |
|---|---|
| `~/.config/fancurve/curve.json` | the curves the GUI's **Save** button writes, and the default for `--auto` |
| `~/.config/fancurve/presets.json` | named curves the GUI's **Keep** button writes, listed in the preset box next to the built-in ones |
| `PRESETS` in `fancurve.py` | the built-in ones, checked into this repo |

In the GUI, double-click a row to change its °C or rpm, then **Save** to
make it the curve the auto loop follows, or **Keep** to give it a name.
Points above 85 °C are unreachable — the duty saturates at 7500 rpm — and
the CPU package idles at 53-57 °C, so the quiet end of the curve has to
reach past 55 °C before it does anything.

Two things the numbers have to live with:

- **The cap is a floor, not a command.** The EC keeps a demand of its own
  that rises with the fan module's temperature (~4000 rpm at 29.8 °C,
  ~5700 rpm at 41.5 °C), and the fans run at `max(cap, that)`. A curve
  point under the EC's demand has no effect; a curve point over it buys
  the extra cooling.
- **The sensors are not interchangeable.** `cpu` is the CPU package,
  `gpu` the NVIDIA die, `skin` the chassis, `ec` the fan module's own
  thermistor. The GPU-side chassis sensors (`I_RTS3`, `I_RTS4`) are the
  ones that move with a GPU load.

`tools/cdev-probe/` holds the raw probes used to decode the command table;
`tools/cdev-probe/fan_temp_boost.py` is the CID 0x0C door.

## Also answered

- **The EC never dispatches commands with bit 7 set.** CIDs 0x80 (manual
  mode), 0x81, 0x83 and 0x84 are implemented in the firmware but
  unreachable from Linux: a GET times out (-110) and a SET's "status 0" is
  the *kernel's* — a SET carries no `HAS_RESPONSE`, so it only proves the
  request was queued. → `decoded/FAN_SPEED.md` §11.
- **The SAM does not talk to the dGPU.** Its pin table has no GPU, PCIe or
  PERST signals; it sees board straps, a proxy I2C sensor labelled
  `DGPU TEMP (U8302)`, and publishes dGPU state to the OS over ACPI SANV.
  → `decoded/DGPU.md`.
- **The platform-profile cap is the Linux driver, not the EC.** The EC
  accepts platform IDs 1–7 without rejection; `surface_platform_profile`
  only ever emits 1–4 and hard-rejects anything higher with `-EINVAL`.

## Scope

- **PCIe link speed** — currently Gen 1 x8 instead of Gen 4 x8, a 6.4×
  bandwidth throttle. The old "EC link-training policy" hypothesis is dead
  (`decoded/PCI.md`).
- **Fan control** — solved, see above.
- **GPU power profile** — two axes: wire platform-id → GPU power profile →
  per-fan profile ids → fan profile records.

## Glossary

| Term | Meaning |
|------|---------|
| **SAM/SSAM** | Surface System Aggregator Module — the protocol between the main CPU and the Surface EC |
| **EC** | Embedded Controller — a low-power microcontroller on the SLS2 motherboard that handles fans, thermals, power gating, battery, keyboard backlight, etc. |
| **TC** | Target Category — the top-level namespace in SAM (`0x03` = thermal, `0x05` = fan) |
| **CID** | Command ID — the operation within a TC (`0x01` = get RPM, `0x0B` = set forced-RPM cap) |
| **IID** | Instance ID — which sub-device of a TC (e.g. which fan, which sensor) |
| **cdev** | Character device — `/dev/surface/aggregator`, exposed by `surface_aggregator_cdev.ko` for raw SSAM access from userspace |
| **platform_profile** | the ACPI sysfs knob at `/sys/firmware/acpi/platform_profile` |
| **XLFF** | Microsoft's firmware container format — a PKCS#7 `signedData` wrapper around a chunk-based MSS1/SAML payload |

## Layout

```
sls2-ec-re/
├── README.md
├── LICENSE                 # MIT
├── .gitignore              # excludes raw/, *.msi, *.sys, *.bin, *.ko
├── acquisition/
│   ├── download_msi.sh     # one-shot MSI downloader
│   ├── extract_msi.sh      # MSI extraction wrapper
│   └── IDENTIFY.md         # SHA256 hashes + sources only
├── raw/                    # gitignored — 4.6 GB of extracted firmware
├── decoded/                # RE deliverables (markdown)
│   ├── SAM_TABLE.md        # primary: TC × CID × IID table
│   ├── CODE_MAP.md         # the EC's address map and its procedures
│   ├── EC.md               # the EC firmware container and its code region
│   ├── FAN_CONTROL.md      # the fan subsystem, end to end
│   ├── FAN_SPEED.md        # the RPM model and the 11000-rpm question
│   ├── CURVE.md            # the curve interpolation machinery
│   ├── TC_FAN.md           # the FAN command table
│   ├── DGPU.md             # what the SAM does and does not know about the dGPU
│   ├── PCI.md              # PCIe link speed (the old hypothesis, corrected)
│   └── RECOMMENDATIONS.md  # the original phase-5 report
├── pcap/                   # SAM/strace captures
├── sessions/               # per-session notes
└── tools/
    ├── fanctl/             # fanctl.py = CLI+GUI, fancurve.py = the curve, fancurve = the launcher
    ├── cdev-probe/         # raw SSAM probes used to decode the tables
    ├── xlff/               # firmware container extraction
    ├── acpi-map/           # ACPI SANV / _DSM mapping
    └── bpftrace/           # ftrace SSAM tracer
```

The firmware binaries themselves are **not** in this repo. `raw/` holds the
extracted Microsoft firmware and is gitignored along with every `*.msi`,
`*.bin` and `*.ko`; `acquisition/IDENTIFY.md` carries the SHA256s and the
exact extraction commands so the environment can be rebuilt.

## Driver set discovered

The plan listed 3 drivers; reality is 8. See `acquisition/IDENTIFY.md` for
SHA256s and ACPI HW IDs. Sorted by subsystem:

**Transport / SAM/SSH**
- `SurfaceSerialHubDriver.sys` (ACPI\MSHW0084) — SSH/SAM transport
- `SurfaceSmfClient.sys` — modern SAM client (Linux: `surface_aggregator_cdev.ko`)

**Integration / ACPI / Thermal**
- `SurfaceIntegrationDriver.sys` (ACPI\MSHW0360) — integration root, loads `Surface.Power.Policy.*.ppkg`
- `SurfaceAcpiPlatformExtensionDriver.sys` (ACPI\MSHW0299) — **boot-start** ACPI `_DSM` provider
- `SurfaceThermalPolicy.sys` (ACPI\MSHW0187) — platform_profile

**Power / Battery**
- `SurfacePowerStateDriver.sys`
- `SurfacePowerMeterDriver.sys` — reads MAX34407/34417 hardware AND SAM
- `SurfacePowerTrackerCore.sys`

The EC firmware is `SurfaceSAM_13.101.139.bin` (codename "Elmer"), a
PKCS#7/XLFF container around a chunk-based MSS1/SAML payload. The ARM code
region maps as `absolute = 0x1007F994 + image_offset`, vector table at
`0x10080000`.

## Reproducing

```bash
./acquisition/download_msi.sh
./acquisition/extract_msi.sh
python3 tools/xlff/xlff_extract.py      # → the EC ARM image
```

Then load the image into Ghidra 12.1.4 (base `0x10080000`) or drive the EC
directly through the cdev.

## Sources

- Microsoft SLS2 driver/firmware MSI: https://www.microsoft.com/en-us/download/details.aspx?id=105610
- linux-surface kernel: https://github.com/linux-surface/linux-surface
- linux-surface aggregator docs: https://docs.kernel.org/driver-api/surface_aggregator/
- libsurfacesam: https://github.com/linux-surface/libsurfacesam
- update-tracker: https://update-tracker.surfacetip.com/surface-laptop-studio-2-update-2026-06-03/
- LKML SAM RFC: https://lkml.kernel.org/lkml/CAK8P3a0_i=wHnQmfbjCdMgFRSD0eGjEM2uMFft5FJ4M1GSUbHg@mail.gmail.com/T/

## License

MIT.
