# SAM Command Table — Surface Laptop Studio 2 ("Elmer")

Extracted **statically** from the loaded kernel modules on this machine
(`linux-surface 6.19.8-surface-3`). No SET commands were issued during this
extraction; the SET rows are documented from disassembled `movb`/`movabs`
immediates in the module code, not from live execution.

## Method

Two passes over each `.ko` in `/lib/modules/6.19.8-surface-3/kernel/`:

1. **`movabs $imm64, %rN`** — single-instruction packed `ssam_cdev_request`
   headers. The 8-byte immediate packs `[tc, tid, cid, iid, flags_lo,
   flags_hi, len_lo, len_hi]` little-endian.
2. **`movb $imm, off(%rbp)`** sequences in `ssam_*` functions — kernel
   functions build the struct on the stack; reconstruct from consecutive
   `-0xNN(%rbp)` writes.

Validation: `python3 -c "import ctypes; print(ctypes.sizeof(ssam_cdev_request))"`
returns 40 (matches the `__attribute__((packed))` struct). One live probe
(`TC=0x01 CID=0x13`, SAM firmware version) returns `8b c9 00 0c` which
**exactly matches** `dmesg`'s `SAM firmware version: 12.201.139` decoded as
LE u32 `0x0c00c98b` → (12, 201, 139). Decode pipeline verified.

## TC/CID coverage (kernel-side, ground truth)

| TC | Name | CIDs the kernel touches | Direction | Purpose |
|----|------|--------------------------|-----------|---------|
| 0x01 | SAM | 0x13, 0x15, 0x16, 0x33, 0x34 | GET (RESP) | EC firmware version, display/D0 notification control |
| 0x02 | BAT | 0x01, 0x02, 0x03, 0x0d | GET (RESP) | Battery static info, BST, AC status |
| 0x02 | BAT | 0x04 | **SET (1B)** | Battery alarm threshold |
| 0x03 | TMP | 0x01, 0x02, 0x0e | GET (RESP) | Per-sensor temps, current platform profile |
| 0x03 | TMP | 0x03 | **SET (4B)** | Platform profile set (perf mode) |
| 0x05 | FAN | 0x01 | GET (RESP) | Current fan RPM |
| 0x05 | FAN | 0x0e (IID=0x01) | **SET (1B)** | Fan profile (called on platform_profile transitions when `has_fan` property present) |
| 0x0e | KIP | 0x1d, 0x2c | GET (RESP) | Cover/connection state, hub state |
| 0x11 | BAS | 0x0c, 0x0d, 0x11 | GET (RESP) | Base/latch state, hotplug (DTX) |
| 0x26 | POS | 0x01, 0x02 | GET (RESP) | Tablet-mode posture (laptop/studio/stage) |

## Platform profile value mapping (TMP CID 0x03 payload u32)

Discovered in `ssam_platform_profile_set` by tracing the `cmp/je` ladder
on `%esi` (the `profile` argument):

| ACPI name | payload u32 | also sets FAN CID 0x0e? |
|-----------|-------------|-------------------------|
| `low-power` | 1 | yes (single-byte) |
| `balanced` | 2 | yes |
| `balanced-performance` | 3 | yes |
| `performance` | 4 | yes |

The `has_fan` device-property check at `surface_platform_profile.ko:0x1d9`
gates the second SET (FAN 0x0e, 1-byte payload) — when the property is
false, the SET is sent to TMP only. Both branches retry 3x on ETIMEDOUT
(`0xffffff92`) or EAGAIN (`0xffffff87`).

## What the kernel does NOT do via SSAM

| Subsystem | Kernel-side mechanism | SSAM used? |
|-----------|------------------------|------------|
| PCIe link speed | `pcie_aspm.c` / `setpci` only | **no** |
| PCIe dGPU power gating | `surface_hotplug.ko` → ACPI `_PR0`/`_PR3` | **no** (0 SSAM symbols in `surface_hotplug.ko`) |
| dGPU SMU / power limits | nvidia proprietary | **no** |
| Battery charge control | sysfs + `surface_charger` (BAT 0x01/0x0d only) | no SET for charge current |
| Fan RPM control | sysfs `enable` only (1=auto) | no direct RPM set |
| Keyboard backlight | `surface_kbd.ko` (not in this extract) | unknown |
| Pen / Touch / HID | surface_hid_core.ko | unknown |

This is the critical finding: **the Linux kernel has no SAM path to PCIe link
speed, dGPU power gating, or fan RPM control**. The only way to affect those
on Linux is either (a) ACPI `_DSM` calls, or (b) commands in a TC the kernel
doesn't implement (and which would need a Phase 2 probe to discover).

## Out-of-scope TCs (not touched by any loaded module)

The remaining 14 TCs the EC implements (per its own tracepoint enum) have
**zero callers** in any loaded `surface_*.ko`:

- 0x04 PMC, 0x06 PoM, 0x07 DBG, 0x08 KBD, 0x09 FWU, 0x0a UNI, 0x0b LPC
- 0x0c TCL, 0x0d SFL, 0x0f EXT, 0x10 BLD, 0x12 SEN, 0x13 SRQ
- 0x14 MCU, 0x15 HID, 0x16 TCH, 0x17 BKL, 0x18 TAM, 0x19 ACC0
- 0x1a UFI, 0x1b USC, 0x1c PEN, 0x1d VID, 0x1e AUD, 0x1f SMC, 0x20 KPD
- 0x21 REG, 0x22 SPT, 0x23 SYS, 0x24 ACC1, 0x25 SMB

Of these, the most likely to be relevant to the PCIe Gen 1 x8 question are:

- **0x11 BAS** (already mapped, 3 CIDs) — base/hotplug, low value for PCIe
- **0x06 PoM** (Power Mode) — possible candidate
- **0x04 PMC** (Performance / Power Mgmt Controller) — strong candidate
- **0x09 FWU** (Firmware Update) — definitely not what we want
- **0x15 HID** (Human Interface Device) — could carry keyboard/pen events

## Recommended Phase 3 lead

The **SurfaceAcpiPlatformExtensionDriver.sys** (the boot-start driver, ACPI
HW ID `MSHW0299`) is the most likely home for a PCIe power-gating `_DSM` —
it's the only file in the SLS2 stack that bridges the kernel's ACPI surface
to the rest of the driver tree. Phase 3 should disassemble it for `_DSM` UUID
strings and `\_SB.PEG0` / `\_SB.PCI0.GPP0` / similar PCIe-port object
references before any cdev probing of PMC or PoM.

## PCIe path forward

1. ACPI table: dump and decompile DSDT/SSDT, look for `_DSM` under
   `\_SB.PEG0`, `\_SB.PCI0`, `\_SB.GPU0`, etc.
2. Phase 3 disassembly of `SurfaceAcpiPlatformExtensionDriver.sys` for the
   UUIDs the driver passes to `_DSM` and the integer args (which encode
   `SetPciSpeed` / `SetPowerLimit` etc.).
3. **Only after** the above, consider a narrow cdev probe of TC 0x04/0x06
   with `CID 0x00..0x10` and a small request length, to find any
   power-related commands the Linux kernel doesn't implement.

## Files

- `decoded/SAM_TABLE.tsv` — same table in TSV
- `decoded/SAM_TABLE.md` — this file
- `pcap/ssam-trace.txt` — ftrace capture confirming live TMP/FAN/BAT traffic
- `tools/cdev-probe/ssam_probe.py` — read-only cdev probe (validated,
  not yet run in sweep mode)
