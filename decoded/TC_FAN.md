# TC_FAN — Fan Control on SLS2

## Linux kernel surface (what we have)

From static extraction of `surface_fan.ko` and `surface_platform_profile.ko`:

| TC | CID | IID | Direction | Payload | Name |
|----|-----|-----|-----------|---------|------|
| 0x05 (FAN) | 0x01 | 0x00 | GET (RESP) | 0 | `surface_fan_hwmon_read` (current RPM) |
| 0x05 (FAN) | 0x0e | 0x01 | SET | 1 | companion to platform_profile_set (when `has_fan` property is true) |
| 0x03 (TMP) | 0x02 | 0x00 | GET (RESP) | 0 | `ssam_platform_profile_get` (current profile) |
| 0x03 (TMP) | 0x03 | 0x00 | SET | 4 | `ssam_platform_profile_set` (perf-mode u32) |

### `platform_profile` SET payload (u32)

| Linux profile | u32 | Fan profile SET issued? |
|---------------|-----|-------------------------|
| `low-power` | 1 | yes (1B payload = 1) |
| `balanced` | 2 | yes |
| `balanced-performance` | 3 | yes |
| `performance` | 4 | yes |

The `has_fan` device-property gate is checked via `device_property_read_bool`
in `surface_platform_profile.ko`. The kernel module retry loops 3× on
`ETIMEDOUT` (0xffffff92) or `EAGAIN` (0xffffff87).

## Windows SurfaceSmfClient fan commands (not in Linux)

`SurfaceSmfClient.sys` exposes **6 fan SAM commands** via the SMF
(System Management Framework) that have **no Linux-side implementation**:

| SmfClient command | Semantics | SAM TC/CID (from kernel) |
|-------------------|-----------|--------------------------|
| `SmfFanSetProfileSamCommand` | set fan profile (low/med/high/max) | `(FAN, 0x0e, 1)` — set with 1B |
| `SmfFanMinRpmSamCommand` | set minimum RPM floor | unknown |
| `SmfFanSetActiveSonesSamCommand` | set acoustic "active" sound level | unknown |
| `SmfFanSetMaxSonesSamCommand` | set max acoustic sound level | unknown |
| `SmfFanSetMcuMaxSonesSamCommand` | set max sones for fan MCU | unknown |
| `SmfFanCsRequestSamCommand` | request CS (cool/quiet state) | unknown |
| `SmfFanSplLimitSamCommand` | set SPL (sound pressure level) limit | unknown |

The 4 known profile values from the kernel match: when Windows sends
"set profile" the platform profile SET goes to both TMP CID 0x03
(u32 = profile) and FAN CID 0x0e (u8 = profile). The **other** fan
commands (min RPM, active sones, max sones, mcu max sones, SPL) are
likely different (TC, CID) combinations that the linux kernel doesn't
poll.

## Possible fan-related TCs to probe (Phase 5 risk)

The EC has 38 TCs (per kernel tracepoint). The Linux kernel only uses
TC 0x05 (FAN) for single-byte RPM reads. Other TCs that the SmfClient
might use for fan control:

- TC 0x05 (FAN) — same one, with different CIDs discovered statically
- TC 0x03 (TMP) — proven; the platform profile and temp sensors

If the kernel implements `surface_fan` as 1 RPM read + 1 profile set,
then most other FAN commands are **EC private** and would only show
up via cdev probing.

## Files

- `decoded/SAM_TABLE.md` — base SAM table
- `decoded/TC_FAN.md` — this file
- `pcap/ssam-trace.txt` — ftrace showing FAN CID 0x01, IID 0x01 polling
