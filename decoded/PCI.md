# PCIe Link Speed — SLS2 dGPU "Elmer"

## TL;DR

- **The dGPU is at PCIe Gen 1 x8** (2.5 GT/s), **not** Gen 4 x8 (16 GT/s).
- **The link is hardware-downgraded** — `LnkSta: Speed 2.5GT/s (downgraded)`,
  with `DLActive-` reported by the kernel AER.
- **The bridge `LnkCtl2: Target Link Speed: 16GT/s`** — both endpoints are
  configured to negotiate Gen 4; the link is failing to train at anything
  higher than Gen 1.
- **AER counters are all zero** — no errors, no retries. The link is stuck
  at Gen 1 silently.
- **No ACPI method explicitly sets the link speed.** Search across all
  20+ SSDTs and DSDT yielded no `LnkCtl2`, `TargetLinkSpeed`, `SetLinkSpeed`,
  `EqualizationDone`, or related controls.
- **The cause is almost certainly the EC firmware**, not the OS or
  drivers. The EC's link-training policy is what's keeping the link at Gen 1.

## Live Hardware State (canonical)

```
0000:00:01.0  Intel Raptor Lake PCI Express 5.0 Graphics Port (PEG010)
  LnkCap:   Port #0, Speed 32GT/s, Width x8, ASPM not supported
  LnkCtl:   ASPM Disabled; RCB 64 bytes, LnkDisable- CommClk+
  LnkSta:   Speed 2.5GT/s, Width x8             <-- current
            TrErr- Train- SlotClk+ DLActive+ BWMgmt- ABWMgmt-
  LnkCap2:  Supported Link Speeds: 2.5-32GT/s, Crosslink- Retimer+ 2Retimers+ DRS-
  LnkCtl2:  Target Link Speed: 16GT/s, EnterCompliance- SpeedDis-  <-- target
            Transmit Margin: Normal Operating Range

0000:01:00.0  NVIDIA AD107M [GeForce RTX 4050 Max-Q / Mobile]
  LnkCap:   Port #0, Speed 16GT/s, Width x8, ASPM L1, Exit Latency L1 <16us
  LnkCtl:   ASPM Disabled; RCB 64 bytes, LnkDisable- CommClk+
            ExtSynch- ClockPM+ AutWidDis- BWInt- AutBWInt- FltModeDis-
  LnkSta:   Speed 2.5GT/s (downgraded), Width x8  <-- kernel marks downgraded
            TrErr- Train- SlotClk+ DLActive- BWMgmt- ABWMgmt-
  LnkCap2:  Supported Link Speeds: 2.5-16GT/s, Crosslink- Retimer+ 2Retimers+ DRS-
  LnkCtl2:  Target Link Speed: 16GT/s, EnterCompliance- SpeedDis-
  LnkSta2:  EqualizationComplete+ EqualizationPhase1+ EqualizationPhase2+ EqualizationPhase3+

ASPM:       disabled (system policy = default)
AER stats:  0 correctable, 0 fatal, 0 non-fatal
```

## Hardware Topology

| Item | Value |
|------|-------|
| CPU | Intel Raptor Lake-P (PCIe 5.0 capable) |
| Bridge | 00:01.0 (PEG010, ports 1×8 max 32 GT/s) |
| dGPU | NVIDIA AD107M (PCIe 4.0 capable, 16 GT/s max) |
| Common link width | x8 (good — full width) |
| Common link speed | 2.5 GT/s (Gen 1) — **the throttle** |
| LnkSpeed capability | 2.5/5/8/16 GT/s supported by both endpoints |

Both endpoints advertise **all** higher speeds but the link cannot train
above Gen 1. There are no AER errors logged and the equalization process
completed successfully, so this is a **deliberate** downgrade rather than
a training failure.

## ACPI Search Results

Dump-then-decompile of the full ACPI namespace yielded:

### `_DSM` UUIDs Discovered

| UUID | Owner | Function |
|------|-------|----------|
| `a3316317-e8b6-4adf-96e8-94d94a51a38e` | `\_SB.PC00.VMD0.NVDR` | **NVIDIA dGPU power management _DSM** |
| `e5c937d0-3553-4d7a-9117-ea4d19c3434d` | `\_SB.PC00.TCPU` (Microsoft) | Device Labeling Interface |

### `NVDR` `_DSM` (Microsoft dGPU Power Management)

```asl
Method (_DSM, 4, Serialized) {
    If ((Arg0 == ToUUID("a3316317-e8b6-4adf-96e8-94d94a51a38e")) {
        Switch (ToInteger(Arg2)) {
            Case (Zero) { return 0x03 }  // supports rev 1
            Case (One) {
                // Return 5-element Package: DPM0(u16), DPM1(u16), DPM2(u32), DPM3(u64), DPM4(u64)
                If (DPMS == 0) { return {0x02, 0, 0, 0, 0} }       // dGPU in D3
                If (DPMS == 1) { return {0, 0, 0, PMSA, PMRL} }   // dGPU in D0
            }
        }
    }
}
```

Where `DPMS`, `PMSA`, `PMRL` are **read-only** memory fields in the platform
NVS region (SystemMemory at 0x3DB5A018, size 0x037A). The EC writes these
fields; ACPI only reads them. The current values are:

- `DPMS = 1` (dGPU present and in D0)
- `PMSA = ?` (dGPU power state address — pointer/blob from EC)
- `PMRL = ?` (dGPU power reference value)

### What `_DSM` does **NOT** do

- ❌ No `Case` for `Arg2 = 2` (no power limit set)
- ❌ No `LnkCtl2` / `Target Link Speed` write
- ❌ No PCIe retrain trigger
- ❌ No `Equalization` control
- ❌ No `LinkSpeed` formula anywhere in the namespace

### Search for explicit link-speed controls

```
grep -nE 'Target.*Speed|LnkCtl|LinkSpeed|MaxSpeed|EQR|EqualizationDone|Retrain' *.dsl
```
→ **0 matches**.

## Driver Search Results

The 8 .sys files in the MSI have no explicit link-speed control either:

- `SurfaceAcpiPlatformExtensionDriver.sys` — AMDI PEP dispatcher, no PCIe refs
- `SurfaceIntegrationDriver.sys` — MSHW0360, mentions "Surface SMF Display
  Client" but no PCIe code (only the string for the SMF window)
- `SurfaceSmfClient.sys` — SmfFanInterface, SmfThermalInterface,
  SmfAmbientEstimation, SmfGenericInterface. **No SmfPcieInterface**
- `SurfaceSerialHubDriver.sys` — SSH/SAM transport, no PCIe
- `SurfaceThermalPolicy.sys` — platform_profile Windows analog
- `SurfacePowerStateDriver.sys`, `SurfacePowerMeterDriver.sys`,
  `SurfacePowerTrackerCore.sys` — battery/power telemetry only

The `SmfFanInterface` issues **6 different SAM commands** for fan control
(SAMFanSetProfile, SAMFanMinRpm, SAMFanSetActiveSones, SAMFanSetMaxSones,
SAMFanSetMcuMaxSones, SAMFanCsRequest) — all **fan-only**. None for PCIe.

`surface_hotplug.ko` (the Linux kernel module) has **0 SSAM symbols**.

## What the EC Firmware Probably Controls (Hypothesis)

The ELMER EC has a SAM command (likely TC=0x??, CID=??) that sets the PCIe
link training policy. The EC is the only thing that can write the
`LnkCtl2` register's `Target Link Speed` field reliably across warm reboots
(because the Linux kernel reads this each boot and uses the EC's value).

EC FW 12.201.139 is what's running. The newer EC FW 13.101.139 in the MSI
may have different link-training policy. **The end user might solve the
problem by EC firmware update** — but this is speculation and out of scope.

## Recommendations (Carry to Phase 5)

For the **PCIe Gen 1 x8** throttle:

1. **Check the EC firmware version** — already confirmed: 12.201.139
   (running) vs 13.101.139 (in MSI). Updating the EC is **out of scope**
   but could be a factor.
2. **Try `setpci` to force a retrain** — read the docs first; some kernels
   ignore writes to LnkCtl2 for non-privileged processes. **Out of scope**
   for this session.
3. **Try Linux kernel parameter `pcie_aspm=off`** — already off, no help.
4. **Try `pcie_aspm=performance`** — kernel config option, may unlock
   higher link training. **Outside this session.**
5. **Document the gap** — Phase 5 deliverable.

## Files

- `decoded/PCI.md` — this file
- `decoded/SAM_TABLE.md` — sibling: SAM command table
- `pcap/ssam-trace.txt` — ftrace capture of platform client traffic
- `sessions/2026-08-19.md` — Phase 2 session notes
