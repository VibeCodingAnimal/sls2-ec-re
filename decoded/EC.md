# EC Firmware — Surface Laptop Studio 2 ("Elmer")

The Embedded Controller (EC) on the SLS2 is a **NXP LPC54S001J** (Cortex-M4,
360 KB SRAM) running proprietary firmware. This document covers the static
analysis of the firmware blob shipped in the Microsoft Surface MSI
(SHA256: `352a206d25f9d964dd83946126d5c7ba968f31944bbddb569cd183272c9a471a`).

**Scope:** read-only. The firmware was never executed, written to flash, or
loaded into any analysis tool. All findings come from parsing the binary.

## Format

The EC firmware blob is **not encrypted**. It is a Microsoft XLFF (Surface
firmware) container with a PKCS#7 `signedData` signature wrapper and an
**MSS1/SAML** payload header. The payload is a **chunk-based** sparse image
format with two identical A/B copies for redundant storage.

### Container

```
0x0000  XLFF header + PKCS#7 signedData (cert chain only)
0x0780  MSS1 marker
0x0790  SAML marker
0x0900  Section table + Image A chunks (40,294 chunks × 21 bytes)
0xCF417 Image A chunks end
0xCF438 Image B chunks start (40,294 chunks × 21 bytes)
0x19DD95 Image B chunks end
0x19DDA0 Trailing cert/signature data
0x1A0000 End of file
```

PKCS#7 OID `1.2.840.113549.1.7.2` = `signedData` (not `envelopedData`), so
the firmware is signed but **not encrypted**. The two cert chains (root
+ accessory firmware CA) verify the signature, but the payload itself is
plaintext ARM Cortex-M machine code.

### Chunk format

Each chunk is a 21-byte record:
```
struct chunk {
    u32 offset;    // LE — destination offset in the image
    u8  len;       // = 16 (always)
    u8  payload[16]; // data to write at that offset
};
```

40,294 chunks × 16 bytes = 644,704 bytes of payload per image. Chunks
span offsets `0x0000` to `0x9D660`. Bytes outside the chunk range are
`0xFF` (uninitialized flash).

### Image layout (assembled from chunks)

```
0x0000..0x005F  Region 1: 48 bytes (boot header, 1 short chunk + 2 zero chunks)
0x0060..0x05FF  Gap (zeros)
0x0600..0x066B  Region 2a: section table
0x066C..0x0767CF Region 2b: ARM vector table + firmware metadata
0x767D0..0x9D65F Region 2c: trailing metadata (mostly empty)
0x9D660..0x9D66F Region 3: small trailer
```

The non-`0xFF` payload ends at offset `0x767D0` (~484 KB of initialized
data). The vector table is at offset `0x66C` and the firmware runs at
memory address `0x10000000`.

## Architecture

* **ISA:** ARM Cortex-M4 (Thumb/Thumb-2 only)
* **Vector table** at image offset `0x66C`:
  * Initial SP: `0x30080000` (256 KB SRAM at `0x30000000` + 0x80000 offset)
  * Reset: `0x100F1E09` (Thumb)
  * NMI: `0x1008CBD1` (Thumb)
  * HardFault: `0x1008CBE9` (Thumb)
  * MemManage / BusFault / UsageFault: `0x1008CC01` / `0x1008CC19` / `0x1008CC31`
  * SVCall: `0x100BC1D7`
  * DebugMon: `0x100C25AF`
  * PendSV: `0x1009E191`
  * SysTick: `0x1009DFDB`
  * IRQ0..IRQ15+: `0x1008CBA1` .. `0x100F1FCD` (sample)
* **No `0x100xxxx` code bodies** are present in the chunk payload. The
  chunks only contain metadata, the vector table, configuration tables, and
  function pointer tables. The actual machine code that the vector and
  dispatch tables point to lives **outside the chunk-payload address range**
  — most likely in a separate code-only region of the flash that this
  extraction does not cover, or in a different overlay format not parsed
  here. **This is a major gap in the static analysis.**

### EC hardware interfaces (per string table)

| Reference designator | Component | Subsystem |
|---|---|---|
| `U6302` | TI BQ25713 | Battery charger (1S-4S buck-boost) |
| `U6601` | IMVP MP2964 | Intel IMVP9 voltage regulator (CPU VCORE) |
| `U7901` | TI TPS65994 | USB-PD controller (×2: Port 0, Port 1) |
| `U3901`–`U3904` | SN1608035 | Skin temperature sensors (×4) |
| `J5701` | M1036428-001 | Display backlight driver |
| `U3601`/`U3602` | PCAL6416A | I/O expanders (×2) |
| `U8302` | (custom) | dGPU temperature sensor |
| `J8101` | (custom) | Pen charger |
| `WiCas` | (Bluetooth SoC) | Magnetometer (MMC3630KJ), Accelerometer (BMI260), Ambient light (TCS34303) |
| `KMB` | Intel Keem Bay | VPU (Movidius Myriad X) |
| `ADC0Module` | on-chip | ADC |

### Power states and modes

The firmware references an Intel/AMD-style power state machine (D0/D3)
for the dGPU, plus a "Power Policy" framework that gets surfaced to
ACPI through the Surface `_DSM` at `\_SB.PC00.VMD0.NVDR`.

Strings in the firmware:
* `Power Policy Generic Notification`
* `Power Policy RSOC Notification`
* `SoC Power State Publisher`
* `Power State Entry Async Publisher`
* `Power State Pending Seq Publisher`
* `SLPowerContract`
* `TcssPwrDown` (Type-C subsystem power down)

### GPU power profiles

The firmware embeds **named power profiles** for both the iGPU and dGPU
(Intel + NVIDIA) at image offset `0x8520`–`0x8760`:

```
Nominal, Quiet, Perf, Max, Gaming, Mixed_Reality, Kiosk
```

— applied independently to the iGPU and the dGPU, giving 14 named
states. Each entry is a 32-byte record containing the profile name plus
encoded frequency/voltage/power-limit values (IEEE 754 doubles and
integer fields). This is the same vocabulary that shows up in the
`Surface.Power.Policy.Defaults.ppkg` Windows package, so the EC
firmware and the Windows SMF power-policy client share a vocabulary.

## SAM command dispatch table

The EC has a **SAM (Surface System Aggregator Module) command dispatch
table** at image offset `0xAAE0`–`0xABBC`. Each entry is a 12-byte
record:

```c
struct sam_dispatch_entry {
    u32 fn;       // LE — handler function address (0x10xxxxxx)
    u32 tc_flag;  // LE — (flags << 24) | target_category
                  //   flags: 0x00 = notification, 0x40 = response (GET),
                  //          0x80 = request (SET), 0xC0 = unhandled
    u32 cid;      // LE — command_id
};
```

49 entries are present. The handler function `0x100ABA52` is the most
common (12 entries) — it is the **generic SAM handler** that decodes the
incoming request, locates the right subsystem, and dispatches. Specialized
handlers exist for the more complex commands.

### Decoded dispatch (by target category)

| TC | Name | CIDs handled | Direction | Handler(s) |
|---|---|---|---|---|
| `0x00` | (untyped) | `0x00` | NOTIF | `0x100B41E2` |
| `0x01` | SAM | `0x00`/`0x01`/`0x02`/`0x03`/`0x04`/`0x05`/`0x07`/`0x08`/`0x09`/`0x0B`/`0x0C`/`0x0D`/`0x10`/`0x11` | mixed | mostly `0x100ABA52` (generic) + 4 specialized (`0x100AED9E`, `0x100AC18A`, `0x100B1BA6`, `0x1008A2B8`) |
| `0x02` | BAT | `0x03`/`0x06`/`0x08`/`0x0C`/`0x0D`/`0x0E`/`0x0F`/`0x12` | mixed | `0x100ABF1A`, `0x100B1BA6`, `0x100B8682`, `0x100B9A92`, `0x100AF50A`, generic |
| `0x03` | TMP | `0x00` (NOTIF) / `0x01`/`0x02`/`0x09` (SET) | mixed | `0x100ABA52`, `0x100ABC66`, `0x100ABF1A`, `0x100AC03E`, `0x100B1BA6`, `0x100AED9E`, `0x100AF50A`, `0x100B41E2` |
| `0x04` | PMC | `0x00` (NOTIF) / `0x0A` (SET) | mixed | `0x100ABA52`, `0x100ABC66` |
| `0x05` | FAN | `0x00` (NOTIF) | NOTIF | `0x1008A2B8`, `0x100AC03E`, `0x100B1BA6`, `0x100B5F56` |

**Notes on the gaps:**

* The Linux kernel only uses TC `0x01`/`0x02`/`0x03`/`0x05`/`0x0E`/`0x11`/`0x26`
  (per `decoded/SAM_TABLE.md`). Of these, only TC `0x01`/`0x02`/`0x03`/`0x05`
  appear in the EC dispatch table in this region. The other TCs the
  kernel uses (`0x0E` KIP, `0x11` BAS, `0x26` POS) must be handled
  through a different table or registration mechanism not visible here.
* The dispatch table is **only a partial list**. There are 90 function
  pointer tables in the extracted image; the most plausible SAM
  registration table is the 89-entry table at `0x76658` (not decoded —
  uses a different entry format, likely a function index plus a
  target_category mask).
* **The TC `0x06` (PoM) and TC `0x04` (PMC) candidates from Phase 3 are
  real and meaningful.** TC `0x04` PMC has a SET command at CID `0x0A`
  (handler `0x100ABC66`) — this is the most likely PCIe / power-limit
  SET command in the firmware. TC `0x06` PoM is **not** in this dispatch
  table; if it exists, its entries are in the larger 89-entry table or
  registered dynamically.

## Subsystem tables

### Temperature sensors (image offset `0x7D30`–`0x7E50`)

| Sensor name | Description |
|---|---|
| `BATT1_T` | Battery 1 thermistor |
| `TP_TMP` | Trackpad temperature |
| `I_RTS1`–`I_RTS6` | Intel skin sensors 1–6 (×6 on the chassis) |
| `I_RTS_TP` | Intel trackpad sensor |
| `I_RTS_CRIT` | Intel critical-limit sensor |
| `I_VTS1`/`I_VTS2` | Intel VR temperature sensors |
| `U3901`–`U3904` | Skin sensor ICs (×4) |
| `DGPU TEMP (U8302)` | dGPU temperature |
| `ACCEL_GYRO BMI260 kePrimaryAccelerometer` | Accelerometer / gyro SoC |

The thermal trip table at `0x7C90`–`0x7CC0` contains 12 trip-point
temperatures encoded as 0.01 K (LE u16): `0x0C09`, `0x0C09`, `0x0C3B`,
`0x0C3B`, `0x0C6D`, `0x0C6D`, `0x0C8B`, `0x0C8B`, `0x0CB3`, `0x0CB3`,
`0x0CC7`, `0x0CC7`. These are the `I_RTS*` skin-temperature trip points
in 0.01 K units (i.e. `0x0C09` = 3081 = 30.81 °C).

### Power profile lookup (image offset `0x765E0`–`0x76640`)

Two parallel lookup tables indexed by trip-point count and platform
profile. The trailing constant block at `0x76650` (the first 89-entry
function pointer table) is referenced by the power-policy subsystem.

### GPIO / Surflink pin map (image offset `0x7230`–`0x73C0`)

| Pin name | Purpose |
|---|---|
| `SAM_SWD_CLK` | SAM serial-wire-debug clock |
| `SAM_SWD_DIO` | SAM SWD data I/O |
| `SAM_SWD_SWO` | SAM SWO trace |
| `SAM_RESET_N` | SAM hardware reset |
| `SAM_PCH` | SAM ↔ PCH (chipset) interconnect |
| `SAMTX_PCHRX_DBG` | Debug echo of SAM→PCH |
| `PCHTX_SAMRX_DBG` | Debug echo of PCH→SAM |
| `SAM_TRACKPAD_SCL_DBG` / `SDA_DBG` | Trackpad I²C over SAM |

These are the Surflink (SSH — Surface Serial Hub) pin assignments. The
SAM uses a dedicated debug serial-wire bus to the host CPU and a
multiplexed trackpad I²C channel — explaining why the trackpad works
even when the EC is in a low-power state.

## Subsystem notifiers

Strings near `0x7DA0`–`0x7E00` reference a number of notifier subsystems
that the SAM EC supports:

* `Dispatcher` / `DispatchEvents` / `DispatcherWait` — generic event
  dispatcher
* `MagDirSample`, `MagDegaussing`, `MagDataAcquisition` — magnetometer
  (WiCas) subsystem
* `Sensor Power Notification` — sensor power state change events
* `SOC_GEN` — generic SoC (system-on-chip) notification publisher
* `Typec Generic Notification` — USB Type-C events
* `Test Data Size[…ACS_CALIBRATION_GENERIC…]` — factory calibration
  interface

## Comparison to the Linux kernel's known SAM table

The Linux kernel `surface_aggregator` module only knows how to talk to a
subset of TCs (`decoded/SAM_TABLE.md` — 23 (TC, CID, IID) entries from
the loaded modules). The EC dispatch table here has more, including the
TC `0x04` (PMC) `SET CID 0x0A` command which has **no Linux kernel
client at all** and is the most likely candidate for the PCIe / power
limit / dGPU behavior control.

The fan control SAM commands the Windows `SurfaceSmfClient.sys` issues
(`SmfFanSetProfile`, `SmfFanMinRpm`, `SmfFanSetActiveSones`,
`SmfFanSetMaxSones`, `SmfFanSetMcuMaxSones`, `SmfFanCsRequest`,
`SmfFanSplLimit`) are not visible in the dispatch table. They are either:

* Treated as `TC 0x05` (FAN) CIDs above `0x10` (above the range of this
  table) and registered elsewhere
* Sent through the Surflink (SSH) transport rather than the SAM
  command protocol
* Part of a higher-level wrapper that builds a SAM request from a
  Windows ioctl

## Phase 4 conclusions

1. **The EC is a Cortex-M4 with plaintext firmware**, in a chunk-based
   Microsoft XLFF format with two redundant A/B images. No encryption
   (the PKCS#7 wrapper is a signature only).

2. **The chunks only contain the metadata layer** (vector table, function
   pointer tables, configuration tables, strings). The actual function
   bodies that the dispatch table points to are **not** in the chunk
   payload — they are loaded from a separate code region of the flash
   that this extraction does not cover. This is the limit of what
   can be learned from static analysis of the EC.bin alone.

3. **The SAM command dispatch table is the firmware's "API surface".**
   The 49 entries mapped here cover SAM, BAT, TMP, PMC, and FAN. The
   generic handler at `0x100ABA52` is the one most Linux kernel calls
   would land on; the specialized handlers (`0x100ABC66`, `0x100ABF1A`,
   `0x100B1BA6`, etc.) handle the more complex SET commands.

4. **The TC `0x04` (PMC) `SET CID 0x0A` command is the most likely
   candidate for the "set power limit" / "set dGPU PCIe link speed"
   control** the Linux kernel does not expose. Handler `0x100ABC66`.
   This is **observational** — we have not called this command, do not
   know its payload format, and do not know its effect on link state.
   Calling it on a live system is **out of scope** for this project.

5. **The EC controls the PCIe link speed by hardware, not by SAM.** The
   SAM dispatch table has no `(PCIe, link_speed, …)` entry. The link
   training policy is part of the EC's reset/init sequence, baked into
   the firmware code region we cannot disassemble from this extraction.
   Updating EC firmware (currently `12.201.139`; MSI ships `13.101.139`)
   could change this — but is out of scope.

6. **Fan control is also EC-managed.** The dispatch table shows only one
   TC `0x05` (FAN) notifier entry; the Windows `SmfClient` issues
   multiple fan CIDs that don't appear here. The fan curves and acoustic
   limits (SPL) are likely in the EC code region or in a separate config
   region not covered by the chunks.

## Files

* `decoded/EC.md` — this file
* `decoded/SAM_TABLE.md` — Linux kernel side (ground truth for what's
  implemented on Linux)
* `decoded/TC_FAN.md` — fan-control findings (Windows vs Linux)
* `raw/ec/EC.bin` — original 1.69 MB firmware blob
* `raw/ec/image_full.bin` — properly reconstructed 644 KB image from
  both chunk streams (corrected from earlier `image1.bin` extraction
  that was truncated at 0x80010)
* `sessions/2026-08-19-phase3.md` — Phase 3 (driver RE) notes that
  produced the SmfClient findings
