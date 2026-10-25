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

---

## Update 2026-10-24 — the FAN command table is now decoded

The EC's own dispatcher `FUN_100C6610` enumerates every FAN CID. Two of
them are the ones that actually move the fan, and neither is the one
Windows' SMF layer makes look obvious:

| CID | dir | payload | what it does |
|-----|-----|---------|--------------|
| 0x01 | GET | — | measured RPM (u16) |
| 0x04 | GET | — | fan **profile record** by id (0x40 B) |
| 0x05 | GET | — | per-fan record |
| 0x06 | GET | — | the fan's 0x26 B config blob (holds the RPM quadratic) |
| 0x0B | **SET** | u16 rpm | the **forced-RPM cap** — this is the slider |
| 0x0D | GET | — | the applied target temperature (float32 °C) |
| 0x0E | **SET** | u8 profile | point the fan at a profile record (sets target °C) |
| 0x11 | GET | — | duty % |
| 0x80 | SET | u8 mode + u16 | "manual" mode — **inert on this firmware** |

**CID 0x80 looks like manual control and is not.** Mode 2 only stores the
RPM in `fan+0x6E`, which nothing consumes; mode 3's commit runs the curve
interpolator over the duty it just injected, so the duty is discarded.
The real model is

```
target_rpm = min( quadratic(target_temp), forced_rpm )
```

which is two writes: **CID 0x0B** (cap) and **CID 0x0E** (profile, to
raise the ceiling). Full derivation and the live proof are in
`decoded/FAN_SPEED.md`; the tool is `tools/fanctl/fanctl.py`.

The per-fan "Override" record (CID 0x05) has a *different* layout from the
profile records (CID 0x04) — temp at `+0x1C`, hot RPM 8500 at `+0x24`,
min RPM 3000 at `+0x22`, max RPM 8000 at `+0x20`. The profile records put
the target temperature at `+0x30` as a float32 in degrees C.

---

## Update 2026-10-24 (later) — the whole table, and the 0x80 wall

The complete CID list, from `FUN_100C6610`. **"Reachable" means the EC
actually dispatches it when the request comes from the Linux cdev.**

| CID | dir | payload | what it does | reachable |
|---|---|---|---|---|
| 0x01 | GET | — | measured RPM (u16) | yes |
| 0x02 | GET | — | 0x0E-byte float blob (2.0, 0.5, 0.2, u16) | yes |
| 0x03 | GET | — | fan count | yes |
| 0x04 | GET | — | fan **profile record** by id (0x40 B) | yes |
| 0x05 | GET | — | per-fan record (always the Override) | yes |
| 0x06 | GET | — | the fan's 0x26 B config blob (the quadratic) | yes |
| 0x07 | GET | — | runtime curve header (tail is racy) | yes |
| 0x08 | SET | u16 ×2 | raw registers A/B (`fan+0x78`/`+0x7A`) | yes |
| 0x09 | GET | u16 ×2 | the same two registers | yes |
| 0x0B | **SET** | u16 rpm | the **forced-RPM cap** (`fan+0x76`) | yes |
| 0x0C | **SET** | **f32 °C** | the **target-temperature override** (`fan+0x84` + `fan+0x88`) | yes |
| 0x0D | GET | — | the applied target temperature | yes |
| 0x0E | **SET** | u8 id | the fan's **profile record** (sets the target °C) | yes |
| 0x0F | SET | u8 | the policy byte (`DAT_30038C24`): 1 threshold, 2 curve | yes |
| 0x10 | GET | — | the policy byte | yes |
| 0x11 | GET | — | duty % | yes |
| 0x12 | SET | f32 °C | set the cap to the rpm the quadratic gives for that temperature (0.0 = cap off) | yes |
| 0x13 | GET | — | a temperature | yes |
| 0x80 | SET | u8 + u16 | "manual mode" | **no** |
| 0x81 | SET | 0x40 | the fan's curve params | **no** |
| 0x83 | SET | 0x40 | write a whole record into a fan | **no** |
| 0x84 | SET | 0x26 | write the fan's config blob | **no** |

### The 0x80 wall

The EC's dispatcher compares the command id as a **signed** char, so
0x83 and 0x84 are implemented properly — but the EC **drops every command
with bit 7 set** before the dispatcher runs. Measured:

* GET 0x80..0x85 → no response, the cdev times out (-110). GETs of the
  low CIDs answer normally.
* SET 0x83 with a 64-byte payload → the request is accepted and the
  fan's record is **byte-for-byte unchanged** afterwards.

The SET result is a trap worth spelling out: a SET is sent with
`flags = 0` (no `HAS_RESPONSE`), so **the kernel returns success as soon
as the request is queued**. A "status 0" from a SET is the *kernel's*
status, not the EC's — it says the request was queued, nothing more.
Only a GET (`flags = 1`, waits for the reply) proves the EC handled it.

So the top half of the fan command table (Windows' manual mode, the curve
params, the record write, the config write) is **EC-side code that Linux
cannot reach**. `decoded/FAN_SPEED.md` §11 has the details.

### The ceiling

Pushing the EC's own model as far as it goes:

```
target  55.0 C -> EC asks  11177 rpm | peak 7450 rpm, duty 100%
```

The duty saturates at 100 % at ~7500 rpm, so the 11000 in the config blob
is a guard on the EC's model rather than a speed the fan delivers.

