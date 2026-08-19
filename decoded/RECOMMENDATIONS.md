# SLS2 EC Firmware RE — Recommendations

This report consolidates the findings from Phases 1–4 of the SLS2 EC
reverse-engineering project and lays out a ranked set of options for the
three issues observed on this machine. **No recommendation has been
acted on.** The system is in the same state it was at session start.

## Issues under investigation

1. **dGPU PCIe link is at Gen 1 ×8** instead of the supported Gen 4 ×8
   (a 6.4× bandwidth throttle on the RTX 4050 Mobile).
2. **Fan control is gated** behind the ACPI `platform_profile` knob
   (4 curves: low-power / balanced / balanced-performance / performance),
   with no direct RPM control exposed on Linux.
3. **Power limits and power policy are EC-controlled** through SAM
   commands the Linux kernel does not implement.

## Root cause analysis (from the RE)

### PCIe Gen 1 ×8

| Layer | What we know | What it implies |
|---|---|---|
| Bridge `00:01.0` (PEG010) `LnkCtl2` | Target = `16GT/s` | Bridge is *configured* to train at Gen 4 |
| dGPU `01:00.0` (AD107M) `LnkCtl2` | Target = `16GT/s` | dGPU endpoint is also *configured* for Gen 4 |
| Both `LnkSta2` flags | `EqualizationComplete+ Phase1+ Phase2+ Phase3+` | Both endpoints completed Gen 4 EQ |
| `LnkSta` actual speed | `2.5GT/s, DLActive-` | The link is **stable** at Gen 1 but with no AER errors |
| AER counters | `0 correctable, 0 fatal, 0 non-fatal` | The link didn't *fail* — it *downgraded* |
| ACPI `_DSM` search across all 28 SSDTs + DSDT | 0 `LnkCtl2`, `TargetLinkSpeed`, `SetLinkSpeed`, `EqualizationDone` | ACPI does not control the link |
| `surface_hotplug.ko` symbols | 0 `ssam_*` references | Linux kernel has no SAM path for PCIe link speed |
| EC firmware SAM dispatch table | 0 entries map to (PCIe, link_speed, …) | The EC exposes no SAM API for link speed |
| EC firmware chunk payload | 0 references to `PCIe` / `Link` / `Gen` / `ASPM` strings | The link-training policy is not in the EC metadata we can read |
| EC firmware vector table | Reset handler at `0x100F1E09` (not in chunk payload) | The link policy is in the EC code region we cannot disassemble |

**Verdict:** The PCIe link-speed cap is enforced by the EC firmware's
link-training policy, which runs at boot before the kernel loads and
is not surfaced to the OS. The cap is applied via the link-training
state machine, survives warm reboots, and is **not** the result of a
link failure (AER is clean). This is the textbook signature of a
deliberate hardware-side cap.

The Surface dGPU `_DSM` at `\_SB.PC00.VMD0.NVDR` (UUID
`a3316317-e8b6-4adf-96e8-94d94a51a38e`) is **not** a link-speed control.
It returns a 5-element package with the dGPU's current power state
address and reference, which Windows feeds to NVIDIA's Optimus power
manager. The EC writes those values; ACPI/Windows only read them.

### Fan control

| Layer | What we know |
|---|---|
| Linux kernel `surface_fan.ko` | 1 GET (RPM read) + 1 SET (fan profile) |
| Linux `surface_platform_profile.ko` | Maps 4 platform profiles → 1 u32 each (`low-power`=1, `balanced`=2, `balanced-performance`=3, `performance`=4). On every profile change, the kernel also sends a 1-byte SET to TC `0x05` FAN CID `0x0E` (IID `0x01`) when the `has_fan` device property is present. |
| Windows `SurfaceSmfClient.sys` | Issues 7 fan SAM commands: `SmfFanSetProfile`, `SmfFanMinRpm`, `SmfFanSetActiveSones`, `SmfFanSetMaxSones`, `SmfFanSetMcuMaxSones`, `SmfFanCsRequest`, `SmfFanSplLimit` |
| EC dispatch table | TC `0x05` (FAN) CID `0x00` NOTIF handlers (`0x1008A2B8`, `0x100AC03E`, `0x100B1BA6`, `0x100B5F56`). The Windows `SmfClient` fan CIDs (≥ `0x10`?) are not in the visible dispatch range. |

The Windows fan-control commands are either registered in the larger
89-entry table at image offset `0x76658` (not yet decoded) or sent over
a different transport.

### Power policy

The EC firmware embeds 14 named power profiles
(`Nominal`/`Quiet`/`Perf`/`Max`/`Gaming`/`Mixed_Reality`/`Kiosk`) for
both the iGPU and the dGPU at image offset `0x8520`–`0x8760`. The
Windows `Surface.Power.Policy.Defaults.ppkg` package uses the same
vocabulary.

The EC has a `PowerPolicyMsg`, `PowerPolicyTran`, `PowerPolicySS`
state machine, a `PowerPolicyBattData` telemetry feed, and an
`SLPowerContract` API. The platform-profile SAM command the Linux
kernel issues (TC `0x03` TMP CID `0x03`, payload u32) is the closest
Linux gets; the Windows `SurfacePowerPolicy.sys` driver exposes the
full set of 14 profiles.

## Risk register (all options)

| Option | Risk | Reversibility | Effect on state |
|---|---|---|---|
| **A. Do nothing** | None | N/A | System stays in current state |
| **B. `setpci` write to LnkCtl2** | Medium — kernel may ignore; link may not retrain | Fully reversible (reboot) | Link speed may or may not change |
| **C. `pcie_aspm=performance` kernel param** | Low — well-tested knob | Fully reversible (boot param) | May unlock higher EQ (but doesn't bypass EC cap) |
| **D. ACPI `_DSM` call** | High — could change dGPU D-state unpredictably | Reversible (D0/D3 cycle) | Likely no effect on link speed (wrong `_DSM`) |
| **E. EC firmware update (12.201.139 → 13.101.139)** | **High — EC flash corruption bricks the laptop** | **Partial — firmware-level, can be re-flashed but requires hardware programmer if boot block breaks** | Unknown; could improve or worsen |
| **F. SAM SET command to TC 0x04 PMC CID 0x0A** | **Very high — completely undocumented, could brick dGPU or EC state** | Reversible (EC reboot) but transient side effects on system | Unknown; this is the most likely "correct" path per the EC dispatch table |
| **G. Add Linux kernel client for the missing SAM commands** | Low (code-level) but depends on F | Reversible (unload module) | Required to call option F from userspace |
| **H. Add Linux kernel client for the missing fan CIDs** | Low–Medium — depends on EC accepting the unknown commands | Reversible (unload module) | Could expose fan control on Linux |

## Ranked recommendations

> **The order below is "least risk first, most upside last." The user
> is the decision-maker. No action is recommended in this report — only
> ranked options.**

### 1. Option A — Do nothing (RECOMMENDED baseline)

* **Effort:** zero
* **Risk:** zero
* **Result:** the dGPU runs at Gen 1 ×8 (≈ 250 MB/s each direction
  over PCIe). For a GPU-bound workload (gaming, CUDA) this is
  catastrophic. For typical desktop usage (display, browser, video),
  the dGPU is rarely used and the throttle is invisible.

* **When this is the right choice:** the user is happy with the current
  behavior and wants to lock the system state.

### 2. Option C — Try `pcie_aspm=performance` (SAFE, low upside)

* **Effort:** 1-line kernel parameter change, no rebuild required
* **Risk:** low; this is a well-tested knob
* **Result:** ASPM L1 is enabled, which sometimes allows the link
  to retrain to a higher speed. Does **not** bypass the EC cap.
* **Worth doing once as an experiment**, but do not expect this to
  fix the Gen 1 issue.

### 3. Option B — `setpci` write to LnkCtl2 (MEDIUM risk, MEDIUM upside)

* **Effort:** one command per boot
* **Risk:** the kernel's `pcie_aspm.c` may overwrite our write
  immediately; the EC may force a retrain back to Gen 1 on the next
  link-state change; we may interfere with the dGPU's own PM
* **Result:** link speed may move to Gen 2 or Gen 4 for the duration
  of the boot, but the EC will likely re-cap it on the next suspend
  / resume cycle
* **Worth doing once with dmesg capture** to confirm whether the
  EC's link policy overrides the OS. The result tells us whether
  the EC cap is "sticky" or "boot-only".

### 4. Option D — ACPI `_DSM` call to `\_SB.PC00.VMD0.NVDR` (HIGH risk, LOW upside)

* **Effort:** a few lines of `acpi_call` or a custom kernel module
* **Risk:** the `_DSM` does not return a link-speed control — it
  returns the dGPU power state. Calling it with the wrong arguments
  could move the dGPU into D3 and crash the display pipeline.
* **Result:** probably no effect on link speed
* **Not recommended** unless we are specifically debugging the dGPU
  D-state.

### 5. Option H — Add kernel client for missing fan CIDs (MEDIUM risk, MEDIUM upside)

* **Effort:** 50–200 lines of new code in a new `surface_fan_control.ko`
  module (or extension to `surface_fan.ko`), plus a userspace tool
* **Risk:** low–medium. Each unknown CID we send could be misinterpreted
  by the EC. Worst case: EC enters an unknown state until next reboot.
* **Result:** could expose per-fan RPM control and acoustic limits
  on Linux
* **Pre-requisite:** a **narrow, validated cdev probe** of TC `0x05`
  (FAN) CIDs `0x10`–`0x3F` to discover which ones the EC actually
  accepts. The cdev probe tool (`tools/cdev-probe/ssam_probe.py`)
  is already built and validated — only needs to be run in the
  right TC×CID range, with payload size = 0, and only the CIDs we
  have evidence for.

### 6. Option G + F — Add kernel client for TC 0x04 PMC CID 0x0A (HIGH risk, HIGH upside)

* **Effort:** 100–500 lines of code, plus extensive testing
* **Risk:** **highest** of all the options. We do not know the payload
  format, the direction flag, or the effect of the command. The
  handler at `0x100ABC66` could touch PCIe link training, dGPU D-state,
  CPU power limits, or any combination.
* **Result:** could unblock Gen 4, could expose power-limit control,
  or could break things
* **Pre-requisites (in order):**
  1. Read the function body at `0x100ABC66` — but this address is in
     the EC code region that our chunk extraction does **not** cover.
     This is currently **impossible** without the full firmware dump
     (or a different extraction method that captures the code region).
  2. Reverse-engineer the payload format (likely 4-byte u32 or u64).
  3. Probe with a known-safe payload (e.g. read current state) and
     observe what changes.
  4. **Only then** consider issuing a SET with a candidate payload.

### 7. Option E — Update EC firmware (HIGHEST risk, UNKNOWN upside)

* **Effort:** requires Windows-side firmware update tool or hardware
  programmer
* **Risk:** **bricking the EC** if the new firmware has a different
  flash layout or the update fails mid-write. The EC controls the
  keyboard, power button, fan, and battery charging — a bricked EC
  means a bricked laptop from the user's perspective.
* **Result:** unknown. The newer EC could fix the link speed, or it
  could change other behavior in unexpected ways.
* **Not recommended** unless the user has a hardware programmer
  (e.g. Dediprog) and a known-good backup of the current EC image.

## Action plan if the user wants to proceed

The next **observational** action (no SETs, no writes) that would
generate the most new information is:

1. **A narrow, targeted cdev probe** of TC `0x04` (PMC) CIDs `0x00`–`0x0F`
   with a 0-byte request, to discover which CIDs the EC will respond to.
   * Risk: **low** for GET commands (the response tells us "yes/no").
   * Risk: **none** if we never issue a SET.
   * Could be done in 5 minutes with the validated probe tool.

2. **A cdev probe of TC `0x05` (FAN) CIDs `0x10`–`0x3F`** to find any
   fan CIDs the Linux kernel doesn't implement.
   * Same risk profile.
   * Would tell us whether the Windows `SmfClient` fan commands are
     reachable via SAM at all.

3. **A `setpci` experiment** with dmesg capture, to see whether the
   link speed is sticky across suspend/resume or whether the EC
   re-applies the cap.
   * Risk: **medium** (one OS-level write, fully reversible).
   * Result determines whether the EC cap is "permanent" or
     "per-boot".

The cdev probes are read-only and would not change any state. The
`setpci` experiment is a single OS-level write and is fully reversible
by reboot.

## Files (this report)

* `decoded/RECOMMENDATIONS.md` — this file
* `decoded/SAM_TABLE.md` — Linux kernel-side SAM table (ground truth)
* `decoded/PCI.md` — PCIe hardware analysis
* `decoded/TC_FAN.md` — fan control analysis (Windows vs Linux)
* `decoded/EC.md` — EC firmware analysis
* `sessions/2026-08-19.md` — Phase 2 session notes
* `sessions/2026-08-19-phase3.md` — Phase 3 session notes
