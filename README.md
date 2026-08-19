# SLS2 EC Firmware Reverse-Engineering

**Goal:** Understand how the SLS2 Embedded Controller (EC) firmware and the Windows Surface driver stack control fan speeds, PCIe link speed, and power delivery, so that Linux can do the same.

## Status: All 5 phases complete — see `decoded/RECOMMENDATIONS.md`

The work was **observational only**. We read SAM frames, RE the drivers, and map the EC firmware — but we did **not** issue any SET commands, modify sysfs, or write a userspace tool. The deliverable is understanding, not action.

System state at session end is identical to session start: no SET issued, no ACPI call made, no EC firmware flashed, no `setpci` write, no kernel modules loaded/unloaded, `platform_profile` still `balanced`, dGPU PCIe link still Gen 1 ×8, EC firmware still `12.201.139`.

## Scope

- **PCIe link speed** is the first focus (currently Gen 1 x8 instead of Gen 4 x8 — a 6.4× bandwidth throttle)
- **Fan control** is gated behind ACPI `platform_profile` (4 curves: low-power / balanced / balanced-performance / performance)
- **Power limits** are controlled by the EC via proprietary SSAM commands documented in the Windows driver .sys files

## Glossary

| Term | Meaning |
|------|---------|
| **SAM/SSAM** | Surface System Aggregator Module — the protocol between the main CPU and the Surface EC |
| **EC** | Embedded Controller — a low-power microcontroller on the SLS2 motherboard that handles fans, thermals, power gating, battery, keyboard backlight, etc. |
| **TC** | Target Category — the top-level namespace in SAM (e.g. `0x03` = thermal, `0x06` = fan) |
| **CID** | Command ID — the operation within a TC (e.g. `0x01` = get, `0x02` = set) |
| **IID** | Instance ID — which sub-device of a TC (e.g. which fan, which sensor) |
| **cdev** | Character device — the `/dev/surface/aggregator` interface exposed by `surface_aggregator_cdev.ko` for userspace SAM access |
| **platform_profile** | The ACPI sysfs knob at `/sys/firmware/acpi/platform_profile` for switching between the 4 EC thermal curves |

## Layout

```
sls2-ec-re/
├── README.md               # this file
├── LICENSE                 # MIT
├── .gitignore              # excludes raw/, *.msi, *.sys, *.bin
├── acquisition/
│   ├── download_msi.sh     # one-shot MSI downloader
│   ├── extract_msi.sh      # MSI extraction wrapper
│   └── IDENTIFY.md         # SHA256 hashes + sources only
├── raw/                    # gitignored binaries
│   ├── sys/                # surface_*.sys driver files
│   └── ec/                 # EC firmware blob
├── decoded/                # RE deliverables (markdown)
│   ├── SAM_TABLE.md        # primary: TC × CID × IID table
│   ├── TC_FAN.md
│   ├── TC_BAT.md
│   ├── TC_TMP.md
│   ├── PCI.md
│   ├── EC.md
│   └── RECOMMENDATIONS.md  # final output of this session
├── docs/                   # protocol documentation
├── pcap/                   # SAM/strace captures
├── sessions/               # per-session notes
└── tools/
    ├── rizin/              # .r2 scripts for repeatable RE
    └── ssam-probe/         # (Phase 5 — NOT this session)
```

## Phase status

- [x] Phase 1: Setup & acquisition — MSI downloaded, 8 drivers + EC FW identified, IDENTIFY.md written
- [x] Phase 2: Live reconnaissance (read-only) — SAM frames captured, baseline recorded
- [x] Phase 3: Static analysis of Windows drivers — full TC × CID table, SmfClient fan commands identified, ACPI `_DSM` mapped
- [x] Phase 4: EC firmware static analysis — confirmed plaintext (not encrypted); chunk-based XLFF format decoded; SAM dispatch table mapped (49 entries); TC 0x04 PMC SET CID 0x0A flagged as likely PCIe/power-limit control
- [x] Phase 5: Recommendation report — 7 ranked options in `decoded/RECOMMENDATIONS.md`

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

The EC firmware is `SurfaceSAM_13.101.139.bin` (codename "Elmer"), PKCS#7/XLFF
signed and encrypted — not directly RE-able. RE focus is the .sys drivers.

## Sources

- Microsoft SLS2 driver/firmware MSI: https://www.microsoft.com/en-us/download/details.aspx?id=105610
- linux-surface kernel: https://github.com/linux-surface/linux-surface
- linux-surface aggregator docs: https://docs.kernel.org/driver-api/surface_aggregator/
- libsurfacesam: https://github.com/linux-surface/libsurfacesam
- update-tracker: https://update-tracker.surfacetip.com/surface-laptop-studio-2-update-2026-06-03/
- LKML SAM RFC: https://lkml.kernel.org/lkml/CAK8P3a0_i=wHnQmfbjCdMgFRSD0eGjEM2uMFft5FJ4M1GSUbHg@mail.gmail.com/T/

## License

MIT (consistent with the nvpl-release project).
