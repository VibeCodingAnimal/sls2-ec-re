# How the SAM talks to the dGPU (SLS2 / "Elmer") — 2026-10-23

**Verdict: it doesn't.** The Surface Aggregator Module has no electrical or
logical channel to the discrete GPU. Everything the SAM "knows" about the dGPU
it learned from the *board*, not from the *GPU*.

Three separate mechanisms carry dGPU-adjacent information, and none of them is
a command channel to the GPU:

| direction | mechanism | what it carries |
|---|---|---|
| board → SAM | **GPIO strap pins** | which GPU *policy set* to use (iGPU vs dGPU half) |
| board → SAM | **I2C sensors** | a proxy temperature next to the GPU ("DGPU TEMP (U8302)") |
| SAM → OS | **ACPI NVS (SANV)** + `NVDR._DSM` | dGPU power-management state for the OS |

---

## 1. The hardware argument

The SAM is an **NXP LPC54S001J** Cortex-M4 on the LPC bus. It has no PCIe
controller, no SMBus-to-PCIe bridge, and — decisive — **no GPU-related pins**.

The complete SAM signal-name table in the firmware
(`0x10087200`–`0x100873FF`) contains:

```
SAM_SWD  SAM_SWD_CLK  SAM_SWD_DIO  SAM_SWD_SWO  SAM_RESET_N
UEFI_LOGGING
PCH_DBG_TX  PCH_DBG_RX  PM_DBG_I2C_SCL  PM_DBG_I2C_SDA
SAM_PCH  SAMTX_PCHRX_DBG  PCHTX_SAMRX_DBG
TBT_LSX1_TXD/RXD  TOUCH_DIGI  TS_DBG_TX_P/N  TS_TCK_1V8  TS_TMS_1V8
TOUCH_SPI  TS_SPI_*_DBG  TRACKPAD_DBG  SAM_TRACKPAD_SCL/SDA_DBG
TRACKPAD_INT_N  TRACKPAD_RST_N  WLC_SWD  PN_SWDCLK  PN_SWDIO
```

A full-image scan for `GPU|PCIe|PERST|PWRGD|SMU|NVIDIA|power` finds **no
SAM-side signal names at all** — only the policy-table strings below and the
device name `DGPU TEMP (U8302)`.

So there is **no PERST#, no PWRGOOD, no auxiliary-rail enable, no PCIe link
training control** in the SAM. It cannot command the GPU.

## 2. What the SAM actually has: a proxy sensor

The EC enumerates a device literally named:

```
'DGPU TEMP (U8302)'   @ 0x1008291C
```

U8302 is an I2C designator — a **dedicated temperature sensor mounted near the
dGPU**, not the GPU itself. The EC reads it over I2C (the `Sensor I2C Module`
in the module table at `0x100829C8`, `0x38`-byte records, `Sensor I2C Module`
= index 4, I2C channels 0x15/0x16).

The SAM's board-sensor name table (`0x10087CE6`, 18-byte records
`{u16, char name[16]}`) is:

```
U3901  U3902  U3903  U3904   (skin sensors, SN1608035)
BATT1_T                   (battery temp)
TP_TMP                    (touchpad temp)
I_RTS1 I_RTS2 I_RTS3 I_RTS4 I_RTS5 I_RTS6   I_RTS_TP   I_RTS_CRIT
I_VTS1 I_VTS2 I_VTS3 I_VTS4
```

`I_RTS*` (6 rail/thermal channels + a trip point + a critical point) are the
GPU-adjacent board thermal channels the SAM uses to drive the fans.

## 3. The GPU "power profile" is a local policy table, not a query

`0x1008851C`, 14 records x 0x2A — an **iGPU half and a dGPU half** of the same
seven policies:

```
rec 0 Nominal_iGPU       fan ids 2,2        rec 7  Nominal_dGPU       fan ids 2,2
rec 1 Quiet_iGPU         fan ids 1,1        rec 8  Quiet_dGPU         fan ids 1,1
rec 2 Perf_iGPU          fan ids 3,3        rec 9  Perf_dGPU          fan ids 3,3
rec 3 Max_iGPU           fan ids 5,5        rec 10 Max_dGPU           fan ids 4,4
rec 4 Gaming_iGPU        fan ids 6,6        rec 11 Gaming_dGPU        fan ids 6,6
rec 5 Mixed_Reality_iGPU fan ids 6,6        rec 12 Mixed_Reality_dGPU fan ids 6,6
rec 6 Kiosk_iGPU         fan ids 3,3        rec 13 Kiosk_dGPU         fan ids 3,3
```

The fan profile IDs live at record `+0x24` / `+0x25` and are consumed by
`FUN_100C69A0(_DAT_30028cfc + 0x24, …)` — that is the hand-off into the fan
subsystem. See `FAN_CONTROL.md` §6b–6d for the full chain and its live proof.

**Which half is used is decided by a board strap, not by detecting the GPU:**

```
FUN_1008F010:  DAT_30038bca = (DAT_30038bcd != 2 && DAT_30038bcd != 5)
FUN_100C4726():  returns DAT_30038bca          (0 -> iGPU half, 1 -> dGPU half)
FUN_100C47C8(id):  0x1008851C + (half*7 + id) * 0x2A
```

`DAT_30038bcd` is read from the board identification straps via
`FUN_1009A7CE()`. **The SAM never learns whether a GPU is attached — it is
told what policy set the board wants.** Live proof that this machine takes the
dGPU half: platform profile 4 ("Max") produces a **45 °C** fan target
(`dGPU_Max`), not 40 °C (`iGPU_Max`) — `tools/cdev-probe/axis_verify.py`.

## 4. The GPU device table (read live)

`TMP CID 0x0D` returns the list of GPU device IDs; `TMP CID 0x0E` returns the
18-byte descriptor for a given ID (`tools/cdev-probe/gpu_descs.py`):

```
id 01..06 : I_RTS1 I_RTS2 I_RTS3 I_RTS4 I_RTS5 I_RTS6
id 07     : I_RTS_TP
id 08     : I_RTS_CRIT
id 09..0C : I_RTS1 I_RTS2 I_RTS3 I_RTS4
```

`TMP CID 0x0F` returns the two top-level blobs: instance 0 = `"dGPU"`,
instance 1 = `"iGPU"`.

Two oddities worth noting:

* `FUN_100C43EC` copies the record out of **table 1** even when the match was
  found in **table 2**, indexing it with the table-2 index — a latent EC bug.
  That is why ids 07/08 return `I_RTS_TP`/`I_RTS_CRIT` rather than a table-2
  record.
* The class byte the same function writes (`*param_2 = 1` or `2`) does not
  survive into the response; the cdev returns uninitialised buffer there.

## 5. The OS-facing side: ACPI SANV + the `NVDR._DSM`

This is the only real "dGPU interface" in the system, and it is
**read-only to the OS**.

The ACPI namespace declares three SystemMemory NVS blocks; the Surface one is:

```
OperationRegion (SANV, SystemMemory, 0x3DB5A418, 0x01BC)
```

`decoded/` map of the block is produced by `tools/acpi-map/sanv_map.py`
(parses the DSDT field list and annotates it with live `/dev/mem` values).
The dGPU-relevant fields:

| off | field | live | meaning |
|---|---|---|---|
| 0x0CC | `NVGA` (u32) | 0 | NVIDIA GPU (BAR) address |
| 0x0D0 | `NVHA` (u32) | 0 | NVIDIA host address |
| 0x0D4 | `AMDA` (u32) | 0 | AMD GPU address (unused here) |
| 0x014 | `NDID` (u8) | 7 | number of display device IDs |
| 0x015.. | `DID1..DID5` (u32) | 0x80000400, 0x80000300..3 | display device IDs (0x8000xxxx = "present") |
| 0x055.. | `NXD1..NXD8`, `NXDX` (u32) | 0 | NVIDIA display device data |
| 0x0D8.. | `LTRX/Y/Z/W`, `OBFX/Y/Z/A` (u8) | 1 | PCIe Latency Tolerance Reporting groups |
| 0x096.. | `P1GP` `P2GP` `P3GP` (u8) | 0 | per-PCIe-port GPU power flags |
| 0x08A.. | `HRE0..3` `HRG0..3` `HRA0..3` (u8/u32) | 0 | per-port GPU/rail state groups |
| 0x090.. | `PWE0..3` `PWG0..3` `PWA0..3` | 0 | per-port power groups |
| 0x0BD | `P3WK` (u32) | 0 | port 3 power word |
| 0x190 | **`DPMS`** (u8) | **0** | dGPU power-management state |
| 0x191 | **`PMSA`** (u64) | 0 | dGPU power-state address |
| 0x199 | **`PMRL`** (u64) | 0 | dGPU power reference level |
| 0x18E | `MPGN` (u8) | 3 | max GPU profile number |
| 0x1A4.. | `PPA0..PPA3` (u32) | 0x00060000, 0x00010000, 0x00060002, 0 | per-GPU power policy assignments |

`DPMS` / `PMSA` / `PMRL` are handed to the OS by the Microsoft dGPU power
management `_DSM` on `\_SB.PC00.VMD0.NVDR`:

```asl
Method (_DSM, 4, Serialized) {
  If (Arg0 == ToUUID("a3316317-e8b6-4adf-96e8-94d94a51a38e")) {
    Switch (ToInteger(Arg2)) {
      Case (Zero) { If (Arg1 == One) { Return (0x03) } }        // rev 1
      Case (One)  {                                            // 24-byte buffer
        CreateWordField (..., 0x00, DPM0);  CreateWordField (..., 0x02, DPM1)
        CreateDWordField(..., 0x04, DPM2);  CreateQWordField(..., 0x08, DPM3)
        CreateQWordField(..., 0x10, DPM4)
        If (DPMS == Zero) { DPM0 = 0x02; DPM1 = 0; DPM3 = 0; DPM4 = 0 }
        Else             { DPM0 = 0;    DPM1 = 0; DPM3 = PMSA; DPM4 = PMRL }
      }
    }
  }
}
```

and `NVDR._STA` returns `0x0F` (present) only when `DPMS == 1`, else `0`.

**Facts established:**

* The OS **never writes** `DPMS`, `PMSA`, `PMRL`, `NVGA`, `P1GP..P3WK` — a
  full scan of every assignment to every SANV field in the DSDT finds none of
  them. They are producer-owned.
* The DSDT contains **no `EmbeddedControl` region and no `EC0` device** — the
  AML never touches the embedded controller directly.
* **The EC does not write SANV either.** The EC image contains **no reference
  to any of the NVS base addresses** — `0x3DB5A418` (SANV), `0x3DB50000`
  (GNVS), `0x3DB5A018` (PNVS), `0x3DB4F000` (FNVS), `0x3DB48018` (ASLB) all
  score **zero hits**, and there is no immediate anywhere in the whole image in
  the `0x3DB40000`–`0x3DB60000` range. So the "the EC writes these fields"
  claim in `PCI.md` is **wrong**.
* No Surface kernel module references the SANV base either (scanned every
  `drivers/platform/surface/*.ko` for the little-endian `18 A4 B5 3D`).
* ⇒ **The producer is the SoC platform firmware (BIOS/AMI)**, filled at boot.
  That is the edge of what is reachable from Linux: the Surface UEFI is not
  in the MSI (only device firmware is — GPU GSP, WLAN, touch, pen, TPM, SSD).
* Live on this machine `DPMS = 0`, `PMSA = PMRL = 0`, and `DPMS` does **not**
  track the GPU's actual runtime power state (sampled 0 continuously while the
  GPU was poked between P8 and P0). So `DPMS = 0` is a static
  "no dGPU ACPI power management in play" state, not a live D3/D0 flag.
* **SANV is completely static**: 200 samples over 20 s with nothing else
  happening moved **0 of 444 bytes**. It is a boot-time configuration
  snapshot, not a live telemetry feed.
* On this machine the `_DSM` is **unreachable through normal ACPI
  enumeration**: `NVDR._STA` returns `0` (because `DPMS == 0`), so Linux has
  **no `NVDR`, `NVD1`, `PAGD` or `VMD` ACPI nodes at all** (verified in
  `/sys/bus/acpi/devices/`). Reading SANV directly out of `/dev/mem` is
  currently the only way to see `DPMS` at all.

Note the `_LSI` / `_LSR` / `_LSW` methods on `NVD1` are **Label Storage**
(NVIDIA GSP board-partition data), *not* link-speed controls — an easy
misread, and it explains why `PCI.md`'s "no ACPI link-speed control" search
came back empty for the right reason.

## 6. The SAM ↔ SoC channel (for contrast)

The SAM's only high-speed link to the SoC is `SAM_PCH` (with the debug
pairs `SAMTX_PCHRX_DBG` / `PCHTX_SAMRX_DBG` and `PM_DBG_I2C_SCL/SDA`). That
carries the **SSAM** request/response transport — the same protocol we drive
from Linux over `/dev/surface/aggregator`.

So the layering is:

```
  dGPU (PCIe, on the SoC root complex)
     |  (no SAM involvement — NVIDIA driver owns this)
     v
  SoC / OS  <--PCIe-->  NVIDIA driver
     |
     |  ACPI: _SB.PC00.VMD0.NVDR._DSM  reads SANV (DPMS/PMSA/PMRL)
     |  ACPI: platform_profile -> SSAM TC 0x03 CID 0x03
     v
  SAM_PCH transport  <--SSAM-->  SAM (this firmware)
                                       |
                                       +-- I2C: DGPU TEMP (U8302), I_RTS1..6, I_VTS1..4
                                       +-- board straps: iGPU/dGPU policy half
                                       +-- fan controller: applies the profile
```

The SAM influences the dGPU only indirectly: **it chooses the fan curve**.

## 7. What this means for the open PCIe questions

`PCI.md` hypothesised that "the EC's link-training policy is what's keeping
the dGPU link at Gen 1". The pin table above **kills that hypothesis**: the
SAM drives no PCIe signals at all, so it cannot be setting `LnkCtl2`'s target
link speed.

What remains plausible:

* The link-speed target (16 GT/s) is set by SoC firmware or the NVIDIA
  driver, not the SAM.
* The SANV per-port groups (`P1GP..P3GP`, `PWE*`, `HRG*`, `LTR*`/`OBF*`) are
  the plausible OS-side surface for GPU power policy on this platform. They
  are all zero/unset right now.

## 8. Reproducing this

```bash
sudo modprobe surface_aggregator_cdev          # -> /dev/surface/aggregator
cd ~/projects/sls2-ec-re

# GPU descriptor tables (live, read-only)
sudo python3 tools/cdev-probe/gpu_descs.py

# platform-profile -> fan profile -> applied target temp (live)
sudo python3 tools/cdev-probe/axis_verify.py

# ACPI SANV field map with live values
sudo python3 tools/acpi-map/sanv_map.py /tmp/acpidump/DSDT.dsl
```

## 9. Still open

1. **Whether SANV ever changes at all.** It is static in every observation so
   far (20 s of sampling, and the profile-diff experiment was blocked pending
   authorisation). The decisive test — snapshot SANV, change the platform
   profile 1→7, diff — has not been run. If SANV is truly a boot-time
   snapshot, it is far less useful as a debugging channel than it first
   looks.
2. **What `PMSA` / `PMRL` mean** — a pointer and a "power reference level"?
   Both are zero here, so there is no live sample to reverse.
3. **The `I_RTS*` / `I_VTS*` channels.** Six rail-thermal channels plus trip
   and critical points, plus four VTS channels. Which physical sensors, and
   where their values land in the fan curve, is still unmapped.
4. **The six u16 at GPU-profile `+0x18..+0x23`** (3261, 3341, 3161, 3191,
   3263, 3404) are identical across every profile except Kiosk (3401, 3470)
   and identical between the iGPU and dGPU halves — so they are per-platform
   constants rather than per-policy thresholds, but their meaning is unknown.
5. **The platform firmware.** SANV is BIOS/AMI-owned (§5). The Surface UEFI
   is not in the MSI, so it is not available to RE from this material — only
   device firmware is (GPU GSP `SurfaceGpu_39.0.65.bin`, `Smf_iGPU_88.0.1.41.bin`,
   WLAN, touch, pen, TPM, SSD).
