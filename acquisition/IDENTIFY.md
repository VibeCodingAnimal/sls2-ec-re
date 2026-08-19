# Acquisition Identification

**Goal:** Record SHA256 hashes, versions, and ACPI hardware IDs for every artifact
extracted from the SLS2 MSI. No binary content in this file — only metadata.

## Source MSI

| Field | Value |
|-------|-------|
| Filename | `SurfaceLaptopStudio2_Win11_22631_26.071.9397.0.msi` |
| Size | 1,082,820,096 bytes (~1.08 GiB) |
| **SHA256** | `7f01b70e0e88995636374e72b70af0157f5e360269e6fda0aa192d785957f41d` |
| Source | `https://www.microsoft.com/en-us/download/details.aspx?id=105610` |
| Build | 26.071.9397.0 (Surface Laptop Studio 2 Win11 22631, driver package) |
| Downloaded | 2026-08-18 |
| Extracted to | `raw/extracted/SurfaceUpdate/` |

## EC firmware (`raw/ec/`)

EC firmware is **PKCS#7/CMS signed ("Surface XLFF Firmware")** with an X.509 chain
issued by `Microsoft Surface XLFF Firmware CA 2021` and signed by the
`Microsoft Surface Elmer Firmware Signer`. "Elmer" is the SLS2 EC codename.

Both blobs are DER-encoded PKCS#7 SignedData structures (first byte `0x30 0x82`
indicating an ASN.1 SEQUENCE; we see `0xED` in the surface-byte due to the
PKCS#7 outer ContentInfo wrapper). The firmware payload inside is encrypted
and not directly reversible without per-device keys held by Microsoft.

| File | Original | Version | SHA256 | Notes |
|------|----------|---------|--------|-------|
| `EC.bin` | `SurfaceSAM_13.101.139.bin` | EC FW 13.101.139 | `352a206d25f9d964dd83946126d5c7ba968f31944bbddb569cd183272c9a471a` | SLS2 EC. SAM/SSH subsystem. Signed by `Surface Elmer Firmware Signer`. |
| `PD.bin` | `SurfacePD_5.15.0.bin` | PD FW 5.15.0 | `91ccc4fc5b2db6338840ea79c2ea34e1ee9530c14c489b797a142feaf380d174` | USB-C PD controller FW (ITE IT5570VG companion, likely). Same XLFF/Surface-Elmer signer chain. |

The EC firmware **cannot be RE'd statically**. RE focus is the .sys drivers,
which are PE32+ plaintext.

## Drivers (`raw/sys/`)

All drivers are PE32+ x86-64 (Windows 10 native). Sorted by subsystem role.

### Transport / SAM/SSH

| File | DriverVer | ACPI HW ID | Role |
|------|-----------|------------|------|
| `SurfaceSerialHubDriver.sys` | `04/06/2023,9.165.4.0` | `ACPI\MSHW0084` | SSH/SAM transport — speaks raw SAM frames over UART to the EC. Linux analog: `surface_serhub.ko` + `surface_aggregator.ko`. |
| `SurfaceSmfClient.sys` | `04/17/2024,8.163.8.0` | (varied, includes SAM GUID) | SMF (System Management Framework) client — modern SAM client. Linux analog: `surface_aggregator_cdev.ko`. |

### Integration / ACPI / Thermal

| File | DriverVer | ACPI HW ID | Role |
|------|-----------|------------|------|
| `SurfaceIntegrationDriver.sys` | `05/06/2026,89.0.7.0` | `ACPI\MSHW0360` | Integration root. Loads `Surface.Power.Policy.Defaults.ppkg` (and Win10 variant). Sets SAM service flags (`PlatformKipSupported=0`, `SamHibernateNotificationEnabled=1`). Adds the **Surface Overlay Power Settings** subgroup (see below). |
| `SurfaceAcpiPlatformExtensionDriver.sys` | `04/18/2023,3.63.3.0` | `ACPI\MSHW0299` | **Boot-start** driver (`StartType=0`, `LoadOrderGroup=Core Platform Extensions`). Provides `_DSM` ACPI methods that the rest of the stack calls. This is where the missing `_DSM` for dGPU power gating likely lives. |
| `SurfaceThermalPolicy.sys` | `04/18/2023,8.96.2.0` | `ACPI\MSHW0187` | Thermal policy / `platform_profile`. Linux analog: `surface_platform_profile.ko`. |

### Power / Battery

| File | DriverVer | Device ID | Role |
|------|-----------|-----------|------|
| `SurfacePowerStateDriver.sys` | `04/18/2023,3.26.2.0` | `{4047F442-B3B0-4517-8C6F-9EEA770022B1}\SurfacePowerStateDriver` | Modern power state — child of integration. |
| `SurfacePowerMeterDriver.sys` | `04/20/2023,3.117.3.0` | `ACPI\MAX34407`, `ACPI\MAX34417`, `{5AE6B5A3-26AE-4FCF-B774-6F7EB76FD072}\SurfacePowerMeter` (SAM), `{B2910995-AAF3-4498-9C0C-261ADB788FEA}\SurfaceVirtualPowerMeter` | Power meter. Reads from MAX34407/34417 shunt monitors AND from SAM. Provides battery/power telemetry. |
| `SurfacePowerTrackerCore.sys` | `03/18/2025,2.149.19.0` | (?) | Power tracker core — long-term power history. |

### Firmware delivery (no .sys; included for completeness)

These directories in the MSI contain only `.inf`+`.cat`+`.bin` — they install
firmware blobs, not drivers. They use `Class=Firmware` and a NULL driver
service.

| Package | Firmware version | Notes |
|---------|------------------|-------|
| `surfacesam/` | EC FW 13.101.139 | Delivered via `Class=Firmware`, `AddService=,%NULL_DRIVER%`. EC update path. |
| `surfacepd/` | PD FW 5.15.0 | Delivered via `Class=Firmware`. PD controller update path. |
| `smfdgpu/` | SMF dGPU 88.0.2.68 | NVIDIA dGPU SMF firmware (the modern replacement for older power management) |
| `smfigpu/` | SMF iGPU 88.0.1.41 | Intel iGPU SMF firmware |
| `surfacepen*/`, `surfacetouchcfu/`, `surfacewlc/`, `surfacetcon/`, `surfacedock*/`, `surfacestoragefwupdate*/`, `surfaceretimer/`, `surfacecamera/`, `surfaceoempanelcustomization/`, `surfaceacpiplatformextension/`, etc. | various | Pen, touch, WLC, TCON, dock, SSD, retimer, camera, panel calibration — out of scope for EC RE. |

## SHA256 (drivers, alphabetical)

See `acquisition/SHA256.txt` for the canonical hashes. Snapshot at time of
this writing:

```
be660eacfb8e5c540dcbb95a265c2303e6070a90b8e78f66dc957ecd8cf915eb  SurfaceAcpiPlatformExtensionDriver.sys
bd53b1575be57564102f449514e8683e16bd8e248099690578f700a616109264  SurfaceIntegrationDriver.sys
c79c390ae7436e69f910e88735045f9686d8921b13674ec0658d1e57ee8e2c31  SurfacePowerMeterDriver.sys
aa50aaaf982ac4b7c21655be534e43537c52d5d8793645f22b42217f4369681c  SurfacePowerStateDriver.sys
90ecc3be8b97a57c2ef160fc06a13787078e37ece7757bcf1f1f0f9750c59ca2  SurfacePowerTrackerCore.sys
a9fb214f925ebcfefd4119d0b31741ab7b04eae312e99d100891af508d8a6d11  SurfaceSerialHubDriver.sys
0f18750c79aed0e4425e371e37e45eccd9179c22ba8aa2ddf3549eff4f74df41  SurfaceSmfClient.sys
19c20628c254e95dce57caf71486157d2705a9fcf461aa0d25dc54607abda042  SurfaceThermalPolicy.sys
352a206d25f9d964dd83946126d5c7ba968f31944bbddb569cd183272c9a471a  EC.bin
91ccc4fc5b2db6338840ea79c2ea34e1ee9530c14c489b797a142feaf380d174  PD.bin
```

## Key GUIDs harvested (Phase 1)

### Surface Overlay Power Settings (the "platform_profile" equivalent on Windows)

From `SurfaceIntegrationDriver.inf` (`AddPowerSetting`):

| Name | GUID |
|------|------|
| Surface Overlay Power Settings Subgroup | `{bfeba2e4-fdcc-41d9-93f4-3f6991c1f730}` |
| Surface Overlay Power Setting | `{a4a0ab02-11f1-4584-bc52-cbb98eabdb0b}` |

Overlay power schemes (per `Default = …`):

| Scheme | GUID |
|--------|------|
| GUID_TYPICAL_POWER_SAVINGS (Balanced) | `{381b4222-f694-41f0-9685-ff5bb260df2e}` |
| GUID_POWER_POLICY_OVERLAY_SCHEME_HIGH_PERFORMANCE | `{3af9B8d9-7c97-431d-ad78-34a8bfea439f}` |
| GUID_POWER_POLICY_OVERLAY_SCHEME_MAX_PERFORMANCE | `{ded574b5-45a0-4f42-8737-46345c09c238}` |

### EC FW / PD FW firmware device GUIDs

| Device | GUID |
|--------|------|
| EC FW (XLFF) | `{FE8944FA-53E7-46B0-AE19-15BBAE2705D7}` |
| PD FW (XLFF) | `{68242FEA-649F-4722-9894-3E4A5AA9B82B}` |

### SSAM child device GUIDs (from `SurfacePowerMeterDriver.inf`)

| Device | GUID |
|--------|------|
| SAM PowerMeter | `{5AE6B5A3-26AE-4FCF-B774-6F7EB76FD072}` |
| Virtual PowerMeter | `{B2910995-AAF3-4498-9C0C-261ADB788FEA}` |

### Event / ETW provider GUIDs

| Driver | Provider GUID |
|--------|---------------|
| SurfaceSerialHubDriver | `{8DDB06D0-A54B-4014-B119-5A09FB550D18}` |
| SurfaceAcpiPlatformExtensionDriver | `{ABA759C2-2F7B-41C2-8CFC-71C370E17276}` |

These let us correlate ETW traces (when available) with subsystem activity.

## Codename discovery

| Subsystem | Codename |
|-----------|----------|
| SLS2 EC | **Elmer** (from XLFF signer CN) |
| EC FW build | 13.101.139 |
| PD FW build | 5.15.0 |
| Integration driver build | 89.0.7.0 |

## PDB GUIDs (PE debug directory, format GUID + age)

If Microsoft ships public PDBs for any of these, the GUID + age can be used
to fetch from `https://msdl.microsoft.com/download/symbols/<file>/<GUID>/<file>`
— but production driver PDBs are typically private. Listed here anyway for
Phase 3 cross-reference.

| File | PDB GUID + age |
|------|----------------|
| SurfaceSerialHubDriver.sys | `FF3285F1494A790F38822C5119EEA71E1` |
| SurfaceSmfClient.sys | `0B04934014ED4E83F0988EDE7E861B3C1` |
| SurfaceThermalPolicy.sys | `1113DE03683667060A62F3E98ACBB3531` |
| SurfaceIntegrationDriver.sys | `BF674DEB3EE9DC283D1A6C0E4CC9A07D1` |
| SurfaceAcpiPlatformExtensionDriver.sys | `E0EC9041766D605FBEEE24598B4B45671` |
| SurfacePowerStateDriver.sys | `632AFB7EE88DF26BB68ACC7F67E704261` |
| SurfacePowerMeterDriver.sys | `D09D070091ABDAECEBDE6EE1DDA70CE71` |
| SurfacePowerTrackerCore.sys | `605DF4CBFA75602D71F65F9BF022EBB31` |

## What's intentionally NOT here

- The MSI itself (gitignored — 1 GiB of Microsoft proprietary data)
- Any `.sys`, `.bin`, `.dll`, `.cat`, `.inf` outside of `raw/` (also gitignored)
- ACPI tables (`/sys/firmware/acpi/*`) — captured in Phase 2 live recon
- Live SAM frame captures — captured in Phase 2 strace

## Reproducibility

```bash
# 1. Download MSI (cached in raw/, see download_msi.sh)
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
curl -sL -A "$UA" -o raw/SurfaceLaptopStudio2_Win11_22631_26.071.9397.0.msi \
  "$(curl -sL -A "$UA" https://www.microsoft.com/en-us/download/details.aspx?id=105610 \
    | grep -oE 'https://download\.microsoft\.com/[^"]*\.msi' | head -1)"
sha256sum raw/*.msi
# → 7f01b70e0e88995636374e72b70af0157f5e360269e6fda0aa192d785957f41d

# 2. Extract
msiextract -C raw/extracted raw/SurfaceLaptopStudio2_Win11_22631_26.071.9397.0.msi
find raw/extracted -name "*.cab" -exec sh -c 'mkdir -p "{}.x" && cabextract -d "{}.x" "{}"' \;

# 3. Copy targets
cp raw/extracted/SurfaceUpdate/surfaceserialhub/SurfaceSerialHubDriver.sys raw/sys/
cp raw/extracted/SurfaceUpdate/surfacesam/SurfaceSAM_13.101.139.bin          raw/ec/EC.bin
# … (see acquisition/extract_msi.sh)

# 4. Hash everything
sha256sum raw/sys/*.sys raw/ec/*.bin > acquisition/SHA256.txt
```
