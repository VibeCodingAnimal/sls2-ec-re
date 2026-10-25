# EC Fan Control Subsystem — Full Decode (2026-10-23)

Static analysis of the Surface Laptop Studio 2 EC firmware's fan subsystem.
All addresses are absolute (`absolute = 0x1007F994 + image_offset`, see
`decoded/CODE_MAP.md`). Nothing was executed; nothing was written to the EC.

---

## 1. Two fans

The SLS2 Studio has **two fans**, each with its own flash-resident config
block and its own curve slot.

`FUN_100C6D5C` (fan subsystem init) — decompiled:

```c
u32 FUN_100C6D5C(void) {
    u8  *ram_dst  = &DAT_3002E2C8;   // per-fan RAM records, 2 x 0x40
    u8  *flash_src= &DAT_10089680;   // per-fan flash blob, 0x40 each
    u32  ram_self = 0x3002E348;      // fan component #1
    u8  *cfg      = &DAT_100896C0;   // fan #1 config, 0x90 each
    for (i = 2; i; ) {
        if (FUN_100C56FE(ram_self, cfg) < 0) { FUN_100F1DC6(0x54020001, ...); return err; }
        FUN_10096BD4(ram_dst, flash_src, 0x40);        // copy 0x40 bytes
        cfg       += 0x90;
        ram_self  += 0x8C;
        flash_src += 0x40;
        ram_dst   += 0x40;
    }
}
```

| | fan #1 | fan #2 |
|---|---|---|
| config block | `0x100896C0` | `0x10089750` (cfg+0x90) |
| fan number (`cfg+0x88`) | 1 | 2 |
| curve slot A (`cfg+0x8B`) | 0 | 1 |
| curve slot B (`cfg+0x8C`) | 0 | 1 |
| enable/disable callback (`cfg+0x84`) | `0x100C6E35` | `0x100C6E35` |
| per-fan GUID (`cfg+0x60`, 16 B) | `01f43fff-0064-d845-703c-a24593c195bf` | `01f43fff-0064-8794-853c-3f35b8c1bd37` |
| curve blobs `cfg+0x10` / `cfg+0x34` | float32 `2.0, 0.5, 0.4, 0, …` / u16 `0…0,1` | identical |

Per-fan u16 limits at `cfg+0x58..0x5E` (both fans identical):
`[13107, 65535, 3000, 11000]` — the `0xFFFF` entry is a "not set" sentinel.

The enable/disable callback `FUN_100C6E34(fan#, enable)`:

```c
u32 FUN_100C6E34(int enable, u32 fan_no, ...) {
    if (fan_no == 0 || fan_no > 2) return FUN_100F1DC6(0x54020000, fan_no, ...);
    if (enable == 1) {
        FUN_100AEB86(FUN_100AEA1E(0), FUN_100AEA40(0));   // channel 0 on
        FUN_100AEBF8(FUN_100AEA1E(0), FUN_100AEA40(1), 1); // channel 1, value 1
        DAT_30038BBC[fan_no - 1] = 1;
        return 0;
    }
    DAT_30038BBC[fan_no - 1] = 0;
    if (DAT_30038BBC[0] || DAT_30038BBC[1]) return 0;   // other fan still on
    FUN_100AEBF8(FUN_100AEA1E(0), FUN_100AEA40(1), 0);
    return FUN_100AEBCA(FUN_100AEA1E(0), FUN_100AEA40(1), status);
}
```

`DAT_30038BBC` is a 2-byte per-fan "enabled" flag array.

---

## 2. The profile table — 7 records, `0x100894C0`

7 records × 0x40 bytes (0x1C0, ending at `0x10089680`). The records are **not
stored in ID order**, and record 3 is reserved (id 0, empty). Lookup is by
**ID**, not by slot:

```c
// FUN_100C6DAE — profile ID -> record
u32 FUN_100C6DAE(u32 profile_id, void **out, ...) {
    for (i = 0; i < 7; i++)
        if (profile_id == ((u16*)&DAT_100894C0)[i * 0x20])
            { *out = &DAT_100894C0 + i * 0x40; return 0; }   // 0x20 u16 == 0x40 bytes
    FUN_100F1DC6(0x54020013, profile_id);    // "unknown profile id"
    return err;
}
```

### Record layout (0x40 bytes)

**CORRECTED 2026-10-23** — the offsets below are now *live-verified* by reading
a profile record off the running EC over the SSAM cdev (`TC 0x05 / CID 0x04`)
and diffing it against the firmware image. The previous revision of this table
was off by 2 bytes from +0x24 onwards. The live `dGPU_Max` record is
**byte-identical** to the image bytes at `0x10089600`.

| offset | type | meaning |
|---|---|---|
| +0x00 | u16 | **profile ID** |
| +0x02 | char[14] | name, NUL-padded |
| +0x10..0x1F | — | explicit-curve fields (only used when mode != 0) |
| +0x20 | u16 | **mode**: 0 = auto (scale the curve), non-zero = explicit curve |
| +0x22 | u16 | **min RPM** = 3000 |
| +0x24 | u16 | 0 |
| +0x26 | u16 | 3071 |
| +0x28 | u16 | 20 (duty %) |
| +0x2A | u16 | 3081 — lower bound of the curve span |
| +0x2C | u16 | 3251 (Extreme: 3331) |
| +0x2E | u16 | 40 (duty %) |
| +0x30 | **float32** | **target temperature, °C** |
| +0x32 | u16 | 16948 (dGPU_Max) — a max-RPM-like field, *not* part of the span |
| +0x34 | u16 | 100 (duty %) |
| +0x36 | u16 | 3121 — upper bound of the curve span (Extreme: 3131) |
| +0x38..0x3F | — | zero |

> **+0x30 is a float32.** Reading it as a u16 yields a plausible-looking
> "max RPM" (16840/16880/16896/16928/16948) that is actually the u16 at
> **+0x32**. The float at +0x30 is the target temperature.

### Live proof of the layout (2026-10-23)

```
$ sudo python3 tools/cdev-probe/profile_records.py
 id=1: name=Quiet        minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3251 [2e]= 40 [30]=  0 [32]=16840 duty2=100 spanhi= 3121
 id=2: name=Nominal      minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3251 [2e]= 40 [30]=  0 [32]=16880 duty2=100 spanhi= 3121
 id=3: name=Performance  minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3251 [2e]= 40 [30]=  0 [32]=16896 duty2=100 spanhi= 3121
 id=4: name=dGPU_Max     minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3251 [2e]= 40 [30]=  0 [32]=16948 duty2=100 spanhi= 3121
 id=5: name=iGPU_Max     minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3251 [2e]= 40 [30]=  0 [32]=16928 duty2=100 spanhi= 3121
 id=6: name=Extreme      minrpm= 3000 [24]=  0 duty1=3071 span=20..3081 [2c]= 3331 [2e]= 40 [30]=  0 [32]=16948 duty2=100 spanhi= 3131
 id=7: status=0xff92 (no record)
```

(The `[30]` column prints the *u16* at +0x30; the float there is the
temperature: 25/30/32/45/40/45 °C for ids 1..6.)

### The six profiles

| ID | Name | target °C | curve span | d1/d2 duty | min RPM |
|---|---|---|---|---|---|
| 1 | **Quiet** | **25.0** | 3081..3121 | 20 / 40 | 3000 |
| 2 | **Nominal** | **30.0** | 3081..3121 | 20 / 40 | 3000 |
| 3 | **Performance** | **32.0** | 3081..3121 | 20 / 40 | 3000 |
| 4 | **dGPU_Max** | **45.0** | 3081..3121 | 20 / 40 | 3000 |
| 5 | **iGPU_Max** | **40.0** | 3081..3121 | 20 / 40 | 3000 |
| 6 | **Extreme** | **45.0** | 3081..**3131** | 20 / 40 | 3000 |

### Physical layout vs. ID — they differ

The ID column above is the **id field at record +0x00** (that is what
`FUN_100C6DAE` compares against). The *physical* order in flash is different:

| table index | record @ | id | name |
|---|---|---|---|
| 0 | `0x100894C0` | 2 | Nominal |
| 1 | `0x10089500` | 1 | Quiet |
| 2 | `0x10089540` | 3 | Performance |
| 3 | `0x10089580` | 0 | *(reserved, all zero)* |
| 4 | `0x100895C0` | 6 | Extreme |
| 5 | `0x10089600` | 4 | dGPU_Max |
| 6 | `0x10089640` | 5 | iGPU_Max |

An **8th record** named `Override` lives at `0x10089680` — *outside* the
7-record window that `FUN_100C6DAE` scans, so it is not reachable by the
ID lookup. It is a separate structure (a manual-override record), not a
seventh platform profile.

The "target temperature" is the ceiling the EC allows the system to reach under
that profile. Note the ladder: **Quiet 25 °C → Nominal 30 → Performance 32 →
iGPU_Max 40 → dGPU_Max 45 → Extreme 45 °C**. `dGPU_Max` and `Extreme` are the
two "let it run hot" profiles; `Extreme` additionally widens the curve span by
+10 (3131 vs 3121), i.e. a gentler ramp.

---

## 3. The fan property API — `FUN_100C6610`

This is the EC-side handler behind the SMF/SAM fan commands. It dispatches on a
**property code** (`*(u8*)(*(int*)(param_1+4) + 7)`) and returns a typed blob.

| code | action | reply |
|---|---|---|
| 0x01 | read u16 | 2 |
| 0x02 | read block | 0x0E |
| 0x03 | `*(u8*)out = 2` | 1 |
| **0x04** | **get profile record by ID** (`FUN_100C6DAE`) — copies the whole 0x40 record | 0x40 |
| 0x05 | get per-fan record by fan# (`FUN_100C6DE4`) | 0x40 |
| 0x06 | copy fan's 0x26-byte runtime curve | 0x26 |
| 0x07 | `FUN_100CB92E` | 0x34 |
| 0x08 | **write 2×u16 into fan+0x78/0x7A**, publish `FUN_100F164E(0x9B09000C, 3, fan#, val)` | 1 (bool) |
| 0x09 | read 2×u16 from fan+0x78/0x7A | 4 |
| 0x0A | `FUN_100C5F9C(fan, *(u16*)out)` — set with a u16 argument | — |
| 0x0B | `FUN_100C5FDC(*out, fan)` | — |
| 0x0C | read float `fan+0x84` | 4 |
| **0x0D** | **set profile by ID** (`FUN_100C6DAE(*(u8*)out)` → `FUN_100C64B4`) | — |
| 0x0E | set profile by ID (variant) | — |
| **0x0F** | **select active fan** (1 or 2) → `DAT_30038C24`; publishes `0x9B09000B` | 1 |
| 0x10 | read active fan → `DAT_30038C24` | 1 |
| 0x11 | `*(u8*)out = (u8)(value * 100 / 0xFFFF)` — u16 → percent | 1 |
| 0x12 | compare-and-apply (`FUN_100C5DFE`), publishes `0x9B09000D/0E` | — |
| 0x13 | read float `FUN_100C5EFC()` | 4 |
| **0x80** | **set fan** — `FUN_100C61C8(fan, *(u8*)out, *(u16*)(out+1))` | — |
| 0x81 | `FUN_100C64F4(fan, out)` | — |
| 0x83 | get per-fan record + apply | — |
| 0x84 | copy 0x26 bytes from `0x30029608` | — |

Notification IDs seen: `0x9B09000A`, `0x9B09000B`, `0x9B09000C`, `0x9B09000D`,
`0x9B09000E`, `0x265D0001`, `0x265D0002`, `0xD1FE001B`.

Property **0x0F** (select active fan) is how the API addresses one of the two
fans — that is the counterpart of Windows' `SmfFanSetActiveSones` /
`SmfFanSetMaxSones` family. Property **0x80** takes a byte + u16, which is the
shape of a "set fan duty/RPM" command.

---

## 4. Applying a profile — `FUN_100C6084`

```c
u32 FUN_100C6084(void *fan, void *profile, ...) {
    *(u32 *)(fan + 0x54) = profile;                       // profile ptr
    *(u16 *)(fan + 0x46) = *(u16 *)(profile + 0x2A);      // 3081
    *(u16 *)(fan + 0x48) = 0;
    *(u32 *)(fan + 0x34) = 0;
    if (*(s16 *)(profile + 0x20) == 0) {                 // AUTO mode
        u32 t   = FUN_100C5DFE(*(float *)(profile + 0x30),   // target °C
                               fan_number);                     // for THIS fan
        float span = 40.0f;
        if (*(u16 *)(profile + 0x2A) < *(u16 *)(profile + 0x36))
            span = (float)(*(u16 *)(profile + 0x36) - *(u16 *)(profile + 0x2A));
        float cur = (float)*(u16 *)(fan->cur + 4);       // fan's current value
        *(float *)(fan + 0x28) = ((float)t - cur) / span; // <-- SCALE FACTOR
        *(u32 *)(fan + 0x2C) = 0;
        *(u32 *)(fan + 0x30) = 0;
    } else {                                              // EXPLICIT curve
        *(u32 *)(fan + 0x28) = *(u32 *)(profile + 0x14);
        *(u32 *)(fan + 0x2C) = *(u32 *)(profile + 0x18);
        *(u32 *)(fan + 0x30) = *(u32 *)(profile + 0x1C);
    }
    *(u32 *)(fan + 0x38) = uVar1;
    uVar1 = *(u16 *)(profile + 0x22);                    // min RPM (3000)
    if (uVar1 == 0) uVar1 = *(u16 *)(fan->cur + 4);
    *(u32 *)(fan + 0x3C) = uVar1;
    *(u8  *)(fan + 0x4A) = 1;                            // "profile applied"
    if (*(char *)(fan + 0x88) == 0)
        *(u32 *)(fan + 0x84) = *(u32 *)(profile + 0x30);  // stash target °C
    return 0;
}
```

So the **fan curve is a scale factor**, not a static RPM table:

```
fan->scale = (per_fan_target_temperature - current_value) / curve_span
```

with `curve_span = profile[+0x36] - profile[+0x2A]` (40 for most profiles,
50 for Extreme). `FUN_100C5DFE(target°C, fan_number)` returns the per-fan
target — the two fans sit in different thermal zones, so the EC keeps a
per-fan temperature ceiling.

`fan+0x3C` = min RPM (3000) and `fan+0x38` = the mode-derived bound. These are
the values the fan controller is driven with.

---

## 5. Commit / state machine — `FUN_100C62B0`

`fan+0x7C` is a state byte: 0 = idle, 1 = ramping, 2 = settled, 3 = manual,
4 = disabled.

* `param_2 == 1` → start the ramp: `FUN_100C5CE0(fan, fan->cur[+8])`,
  `FUN_100C5D0A(fan)`, invoke the enable callback `(1, fan_number)`,
  `fan->cur[+8]` → `fan+0x1C`, state = 1, `fan+0x6C = 0`.
* `param_2 == 2` → force a target: `FUN_100C5CE0(fan, fan+0x70)`,
  `FUN_100C5D0A(fan)`, enable callback, state = 4.
* `param_2 == 0` → the periodic tick: `FUN_100C6134(fan)`; while state == 1 it
  counts up `fan+0x6C` until it reaches `fan->cur[+0xC]` (the settle timeout),
  then state = 2 and `FUN_100F1DC6(0x54020014, fan+0x72)` is logged. It also
  compares `fan+0x72` against `fan->cur[+0x10]` — another bound — and drops
  to state 2 when exceeded.
* State 3 (manual) re-applies `fan+0x20`/`fan+0x22` and calls
  `FUN_100D1BDC(fan+4, 1, fan+0x1C)` — **the manual-override path**.

`fan+0x6E`, `fan+0x70`, `fan+0x72`, `fan+0x74`, `fan+0x78`, `fan+0x7A` are
u16 command/target fields written by properties 0x08/0x09/0x0A/0x0B.

---

## 6. Applying a profile to every fan — `FUN_100C69A0`

```c
u32 FUN_100C69A0(u8 *profile_ids, int use_current, ...) {
    if (!_DAT_300295ec) return FUN_100F1DC6(0x54020015, 0x63C);
    for (i = FAN_1009A202(&DAT_300295ec); FUN_1009A220() == 0; i = FUN_1009A22A(i)) {
        if (!use_current)
            rc = FUN_100C6DAE(profile_ids[n++], &rec);      // profile by ID
        else
            rc = FUN_100C6DE4(fan->cfg->fan_number, &rec);   // this fan's own record
        if (rc >= 0)
            FUN_100C64B4(fan->cfg->fan_number, rec);        // apply to this fan
    }
}
```

`DAT_300295EC` is the **`Cmpnt fan` component registry** (registered by
`FUN_100C56FE` via `FUN_1009A190`). So:

* `FUN_100C69A0(ids_array, 0)` — **apply a list of profile IDs, one per fan**,
  to every fan in the registry.
* `FUN_100C69A0(NULL, 1)` — re-apply each fan's *current* profile.

`FUN_100C64B4(fan_number, profile_record)` = find the fan component by number
(`FUN_100C6464`), apply (`FUN_100C6084`), then commit (`FUN_100C62B0(fan, 1)`).

---

## 6b. TWO PROFILE AXES — the GPU power profile vs. the fan profile (NEW 2026-10-23)

This is the single most important correction to the earlier write-up. There
are **two independent enumerations**, and the earlier analysis conflated them.

### Axis 1 — the fan profile (7 records, `0x100894C0`)

Read live over the cdev via `TC 0x05 / CID 0x04` (property 0x04 = "get
profile record by ID"), one record per ID:

| ID | Name | target °C | curve span | min RPM |
|---|---|---|---|---|
| 0 | *(reserved, all zero)* | 0 | — | 0 |
| 1 | **Quiet** | 25.0 | 3081..3121 | 3000 |
| 2 | **Nominal** | 30.0 | 3081..3121 | 3000 |
| 3 | **Performance** | 32.0 | 3081..3121 | 3000 |
| 4 | **dGPU_Max** | 45.0 | 3081..3121 | 3000 |
| 5 | **iGPU_Max** | 40.0 | 3081..3121 | 3000 |
| 6 | **Extreme** | 45.0 | 3081..**3131** | 3000 |
| 7 | **Override** | — | — | 3000 |

`Override` is the **8th physical record** at `0x10089680` — *outside* the
7-slot window that `FUN_100C6DAE` scans (`uVar1 < 7`), so it is unreachable
by ID lookup. It is instead what the **per-fan record** resolves to when the
fan is in manual mode — confirmed live: `TC 0x05 / CID 0x05 / IID 1` returns
the `Override` record, byte-identical to the image at `0x10089680`.

### Axis 2 — the GPU power profile (14 records, `0x1008851C`)

The table at `0x1008851C` is **not** the fan table. It is the **GPU power
profile** table: 14 records × 0x2A bytes, split into an iGPU half and a dGPU
half, each with a name and a **fan profile ID to apply**.

| # | name | → fan profile | | # | name | → fan profile |
|---|---|---|---|---|---|---|
| 0 | Nominal_iGPU | **2** Nominal | | 7 | Nominal_dGPU | **2** Nominal |
| 1 | Quiet_iGPU | **1** Quiet | | 8 | Quiet_dGPU | **1** Quiet |
| 2 | Perf_iGPU | **3** Performance | | 9 | Perf_dGPU | **3** Performance |
| 3 | Max_iGPU | **5** iGPU_Max | | 10 | Max_dGPU | **4** dGPU_Max |
| 4 | Gaming_iGPU | **6** Extreme | | 11 | Gaming_dGPU | **6** Extreme |
| 5 | Mixed_Reality_iGPU | **6** Extreme | | 12 | Mixed_Reality_dGPU | **6** Extreme |
| 6 | Kiosk_iGPU | **3** Performance | | 13 | Kiosk_dGPU | **3** Performance |

Each record's tail bytes (`+0x24..+0x29`) hold the per-fan IDs:
`{fan1_id, fan2_id, 0x00, 0x09, 0x09, 0x00}`.

**This resolves the iGPU/dGPU split.** `Max_iGPU` maps to fan profile **5**
(iGPU_Max) while `Max_dGPU` maps to fan profile **4** (dGPU_Max) — the same
GPU "Max" policy, different fan ceiling depending on which GPU is active.
`FUN_100C47C8()` picks the half via `DAT_30038bca` (set from a board query) —
see §6c for the exact mechanism and §6d for the live proof that this machine
uses the **dGPU** half. (`FUN_100C43EC` is a *different* thing: it matches
the live GPU against the 12-entry device-id table reported by `TMP CID 0x0D`,
not against the profile pool.)

### The wire protocol: which axis is which

| SSAM command | axis | Linux value |
|---|---|---|
| `TC 0x03 / CID 0x02` GET (8 bytes) | **platform profile** | u32 = current, u16 = ?, u16 = effective |
| `TC 0x03 / CID 0x03` SET (u32 payload) | **platform profile** | Linux sends 1..4 |
| `TC 0x05 / CID 0x01` GET (u16) | **fan RPM**, per-fan by IID | hwmon `fan1_input` |
| `TC 0x05 / CID 0x04` GET (0x40 bytes) | **fan profile record**, by ID | — |
| `TC 0x05 / CID 0x05` GET (0x40 bytes) | **per-fan record** (Override) | — |
| `TC 0x03 / CID 0x0D` GET (55 bytes) | **12-ID list** `01..0C` | — |

The `TC 0x03 / CID 0x02` response is `u32 raw | u16 tag | u16 effective`.
Live sweep, setting `CID 0x03` to each value:

```
id=1  raw=1  tag=2  eff=1      (Linux: balanced)
id=2  raw=2  tag=2  eff=2      (Linux: low-power)
id=3  raw=3  tag=2  eff=3      (Linux: balanced-performance)
id=4  raw=4  tag=2  eff=4      (Linux: performance)
id=5  raw=5  tag=2  eff=5      (Linux: EINVAL)
id=6  raw=6  tag=2  eff=6      (Linux: EINVAL)
id=7  raw=7  tag=2  eff=7      (Linux: EINVAL)
id=8  raw=8  tag=2  eff=1      (clamped to 1!)
```

**Two live facts fall straight out of this:**

1. **The EC accepts 1..7 and clamps 8 → 1.** It does *not* reject high IDs.
   `FUN_100B6C6C`'s switch has `default: uVar2 = 0` — anything outside the
   known set lands on slot 0 rather than erroring.
2. **Linux's `platform_profile_get` rejects IDs > 4** with `-EINVAL`
   (`cmp $0x3,%eax; ja 499` on `ec_value - 1`), which is why the sysfs file
   returns `Invalid argument (os error 22)` while the EC is happily running
   profile 5. The Linux side is not reading the EC's limit — it has its own.

Note also `raw=8, eff=1`: the EC *stores* 8 but *reports* 1. The readback
does not simply echo what was written.

---

## 6c. The complete SSAM command tables (NEW 2026-10-23)

The request router is `FUN_100C524E`: it pops requests off the queue
`0x3001b468`, checks `req[0] == 1`, then dispatches on `req[1]` (target
category) — **`0x03` → `FUN_100C4D68` (TMP)**, **`0x05` → `FUN_100C6610`
(FAN)**. Inside each handler the dispatch is on `req[7]` (command id) and
`req[4]` (instance id).

Both handlers first run two generic pre-checks over the request buffer:
`FUN_100B6FAE` (the *property getter*) and `FUN_100BD7DA`. If either reports
"handled", the handler returns immediately — so several CIDs are served by
`FUN_100B6FAE`, not by the switch.

### `FUN_100B6FAE` — generic property getter

`param_2` is the **instance id** (not a payload length), `param_1` is the
buffer, `param_3` is the CID.

| CID | meaning | resp len |
|---|---|---|
| 0x02 | current state: u32 status word, u16 tag, u16 = **active GPU record index** (record `+0x02`) | 8 |
| 0x0F | GPU descriptor blob. instance 0 → 18 bytes from `0x30029092` (**"dGPU"**); instance 1 → `FUN_100C48E4(0)` (**"iGPU"**); instance ≥2 → error `0x54020006` | 0x14 |
| 0x10 | 7-byte record from `DAT_30028d2c` / `FUN_100C4786`, selected by `buf[3]` | 10 |
| 0x11 | 0x2B-byte record from `DAT_30028d64` / `FUN_100C47A0` | 0x2E |
| 0x14 | active GPU record, `u16[0x18 + i*2]`, i = 0..4 | 6 |
| 0x15 | active GPU record, `u16[0x20 + i*2]`, i = 0..1 | 6 |
| 0x16 | active GPU record, `byte[0x23 + i]`, i = 0..2 — **i=1 → fan1 profile id, i=2 → fan2 profile id** | 6 |
| 0x83 | 14-byte descriptor: u16 type, u16 count (**14** = records in the pool), u32 flags, u8 state | 0xE |
| 0x90 / 0x91 | raw 7-byte / 0x2B-byte record copies | 10 / 0x2E |
| 0x92 | copy 0x1A bytes of the custom record `0x30028d00` | — |
| 0x93 / 0x94 / 0x95 | write u16 / u16 / u8 into the custom record | — |
| 0xA4 / 0xA5 | read / write the three temperature offsets (biased by `0xAAB`) | 2 / 1 |

### TMP — target category `0x03` — `FUN_100C4D68`

| CID | action | resp len |
|---|---|---|
| 0x01 | `FUN_100C3A4C` | 2 |
| **0x03** | **SET platform profile** — u32 payload → `FUN_100B6C6C`. Payload 0 = query (`FUN_100E848` must be non-zero, else `0x5402000B`) | 0 |
| 0x04 | `FUN_100C3FAA` → current profile | 2 |
| 0x0C | `DAT_30039710 = payload[0]`; `FUN_100B6D6C` | 0 |
| 0x0D | 55-byte GPU id table (`FUN_100B6BFE`): u16 2, u16 2, then **12 device ids `01..0C`** + NUL | 0x37 |
| 0x0E | GPU info for the instance id → `FUN_100C43EC`: writes class (1 = table 1, 2 = table 2) + an 18-byte record | 0x15 |
| 0x17 | `FUN_100B6DC8` | 0x1A |
| 0x18 / 0x19 | `FUN_100C4618` / `FUN_100C44C0` | 2 / 2 |
| 0x1A | `FUN_100BD944` | 4 |
| 0x1B | raise internal flag `0x0D` | 0 |
| 0x1D | `FUN_100C39DE` | 2 |
| 0x8D | `FUN_100BD17C` | 0x800 |
| 0x8E | `FUN_100C4470` | 2 |
| 0x8F | `FUN_100C36FE` if `u16 payload` ∈ [0x8B7, 0xDCB], else echo 3 bytes | 3 |
| 0x96 / 0x97 | `FUN_100C4524` / `FUN_100C4578` | 1 / 1 |
| 0xA6 | `DAT_30039708 = (payload[0] == 0)` — enable/disable | 0 |
| 0xA7 / 0xA8 | set / clear the 16-byte block at `_DAT_300396F8` | 0 |
| 0xA9 | `FUN_100C432A` if `payload[0]` ∈ [0xB74, 0x1087] | 1 |
| 0xAA | `FUN_100C43C0` | 2 |

### FAN — target category `0x05` — `FUN_100C6610`

`req[4]` = fan number (1 or 2).

| CID | action | resp len |
|---|---|---|
| 0x01 | **fan RPM** (u16) — per fan | 2 |
| 0x02 | `FUN_100C65B4` → 12-byte float blob (2.0, 0.5, 0.2 + u16) | 0x0E |
| 0x03 | constant **2** — fan count | 1 |
| 0x04 | **fan profile record by ID** (0x40 bytes) | 0x40 |
| 0x05 | **per-fan record** (0x40 bytes) — resolves to `Override` | 0x40 |
| 0x06 | 0x26-byte fan config blob from `fan+0x64` | 0x26 |
| 0x07 | `FUN_100CB92E` → 0x34-byte thermal/curve blob | 0x34 |
| 0x08 | **SET** 4 bytes → `fan+0x78` (u16) and `fan+0x7A` (u16); notifies `0x9B09000C` | 1 |
| 0x09 | read back `fan+0x78` / `fan+0x7A` | 4 |
| **0x0B** | **SET the forced-RPM cap** — `FUN_100C5F9C(fan, u16)` → `fan+0x76`. **The manual fan-speed control.** | — |
| 0x0C | `FUN_100C5FDC` | — |
| 0x0D | **applied target temperature (float32)** — `fan+0x84` | 4 |
| **0x0E** | **SET profile by ID** — `FUN_100C6DAE(payload[0])` then `FUN_100C64B4(fan, rec)`. **Per-fan profile control with no Linux equivalent.** | — |
| 0x0F | **SET active fan** — `DAT_30038C24 = payload[0]` (1 or 2; anything else → 1) | — |
| 0x10 | read active fan (`DAT_30038C24`) | 1 |
| 0x11 | **duty %** = `u16 * 100 / 0xFFFF` | 1 |
| 0x12 | `FUN_100C5DFE` target compare, notifies `0x9B09000D/E` | — |
| 0x13 | `FUN_100C5EFC` → float | 4 |
| **0x80** | **SET fan** — `FUN_100C61C8(fan, byte, u16)`: mode byte + u16. **⚠ INERT on this firmware — it does not move the fan. See `FAN_SPEED.md` §6.** | — |
| 0x81 | `FUN_100C64F4` | — |
| 0x83 | get per-fan record **and apply it** | 0x40 |
| 0x84 | copy the 0x26-byte blob from `0x30029608` and point `fan+0x64` at it | 0x26 |

### The GPU power profile record layout (0x2A) — exact

```
+0x00  u16   flags (always 0)
+0x02  u16   profile index 1..7 (Nominal, Quiet, Perf, Max, Gaming, Mixed_Reality, Kiosk)
+0x04  char  name[18]      "Nominal_iGPU" / "Nominal_dGPU" / ... (NUL padded)
+0x16  u16   40
+0x18  u16   3261
+0x1A  u16   3341   (Kiosk: 3401)
+0x1C  u16   3161
+0x1E  u16   3191
+0x20  u16   3263
+0x22  u16   3404   (Kiosk: 3470)
+0x24  u8    fan 1 profile id   <-- consumed by FUN_100C69A0
+0x25  u8    fan 2 profile id   <-- consumed by FUN_100C69A0
+0x26  u8    0x00
+0x27  u8    0x09
+0x28  u8    0x09
+0x29  u8    0x00
```

`FUN_100C69A0(_DAT_30028cfc + 0x24, …)` reads the two fan profile IDs here and
applies them to the fan registry — this is the exact hand-off between the two
axes.

### Which half is active

`FUN_100C4726()` → `FUN_1008F000()` → `DAT_30038bca`, which
`FUN_1008F010` sets as `DAT_30038bcd != 2 && DAT_30038bcd != 5` from a board
query (`FUN_1009A7CE`). `FUN_100C47C8(id)` then indexes
`0x1008851C + (FUN_100C4726() * 7 + id) * 0x2A`:

* `DAT_30038bca == 0` → half 0 → **iGPU** records (0..6)
* `DAT_30038bca != 0` → half 1 → **dGPU** records (7..13)

**On this machine the dGPU half is active** — proven live (see below).

---

## 6d. LIVE VERIFICATION of the two-axis chain (2026-10-23)

`tools/cdev-probe/axis_verify.py` drives axis 1 with `TMP CID 0x03` and reads
the fan-side effect with `FAN CID 0x0D` (the applied target temperature):

```
plat name           fan1 target fan2 target  duty1  duty2   rpm1   rpm2
   1 Nominal               30.0        30.0     30     31   3000   2996
   2 Quiet                 25.0        25.0     30     31   2999   2995
   3 Performance           32.0        32.0     30     31   2999   2995
   4 Max                   45.0        45.0     30     31   2999   2995
   5 Gaming                45.0        45.0     30     31   2999   2995
   6 Mixed_Reality         45.0        45.0     30     31   2999   2996
   7 Kiosk                 32.0        32.0     30     31   2999   2996
```

Every platform id lands exactly on the fan profile the two-axis model
predicts:

| platform id | GPU power profile | fan profile | target °C | predicted | ✓ |
|---|---|---|---|---|---|
| 1 | Nominal | 2 Nominal | 30.0 | 30.0 | ✓ |
| 2 | Quiet | 1 Quiet | 25.0 | 25.0 | ✓ |
| 3 | Perf | 3 Performance | 32.0 | 32.0 | ✓ |
| **4** | **Max_dGPU** | **4 dGPU_Max** | **45.0** | 45.0 (40.0 would mean iGPU) | ✓ |
| 5 | Gaming | 6 Extreme | 45.0 | 45.0 | ✓ |
| 6 | Mixed_Reality | 6 Extreme | 45.0 | 45.0 | ✓ |
| 7 | Kiosk | 3 Performance | 32.0 | 32.0 | ✓ |

**Platform 4 giving 45.0 °C (not 40.0 °C) proves this machine resolves to the
dGPU half of the pool** — i.e. `DAT_30038bca != 0` → `FUN_100C4726()` returns
1 → `FUN_100C47C8` indexes records 7..13. The last open question from §9 is
closed.

The `Override` record was also confirmed to be the *per-fan* record (not the
applied profile): `FAN CID 0x05 / IID 1` returns it unchanged no matter which
platform profile is active.

---

## 7. What Linux cannot reach — the answer (PROVEN 2026-10-23)

**Short version: it is a Linux driver limitation, not an EC limitation.**
The EC happily accepts platform-profile IDs 1..7. The shipped Linux driver
can only ever put **1..4** on the wire, and additionally *refuses to read
back* anything the EC reports above 4.

### 7.1 EC side — the profile-ID switch

`FUN_100B6C6C` is the EC's "apply platform profile" routine. It is reached
from the SSAM TMP handler `FUN_100C524E` → CID 0x03 → IID 0x03 branch →
`FUN_100C4D68` → `FUN_100B6C6C(u32 profile_id)`:

```c
longlong FUN_100b6c6c(u32 profile_id) {
    u32 slot;
    switch (profile_id) {
    case 0:  /* special: synthesise a "custom" profile from DAT_30028d00 */
             _DAT_30028cfc = 0x30028d00; ... uVar3 = 1; goto apply;
    case 2:  slot = 1; break;
    case 3:  slot = 2; break;
    case 4:  slot = 3; break;
    case 5:  slot = 4; break;      // iGPU_Max
    case 6:  slot = 5; break;      // Extreme
    case 7:  slot = 6; break;      // Override
    default: slot = 0; break;      // ID 1 -> Quiet  (and ID >= 8 -> Quiet!)
    }
    _DAT_30028cfc = FUN_100C47C8(slot);
apply:
    FUN_100C5340(4, &profile_id);
    *_DAT_30028cf4 = profile_id;
    FUN_100C69A0(_DAT_30028cfc + 0x24, uVar3);   // push the ID(s) to every fan
    FUN_100f1dc6(0x58020005, *_DAT_30028cf4);
}
```

There is **no rejection path**. IDs 5, 6 and 7 are routed to real records;
IDs ≥ 8 silently fall back to Quiet (slot 0). So the EC does not care what
you send it — the whole of the restriction lives in Linux.

`FUN_100C47C8(slot)` resolves the slot through the **14-record GPU power
profile table** at `0x1008851C` (stride `0x2A`), with the iGPU/dGPU half
selected by `DAT_30038bca` — i.e. by which GPU is currently active. See §6b.
The `+0x24` tail of the selected record carries the **fan profile IDs** that
`FUN_100C69A0` then pushes to every fan.

So the platform-profile ID → fan-profile mapping is:

| platform ID | GPU power profile (active half) | → fan profile applied |
|---|---|---|
| 1 | Nominal | 2 Nominal |
| 2 | Quiet | 1 Quiet |
| 3 | Perf | 3 Performance |
| 4 | Max | **5 iGPU_Max** (iGPU half) or **4 dGPU_Max** (dGPU half) |
| 5 | Gaming | **6 Extreme** |
| 6 | Mixed_Reality | **6 Extreme** |
| 7 | Kiosk | 3 Performance |

**This is the key to "iGPU_Max and Extreme are unreachable on Linux":** they
are reached through platform IDs 4-on-iGPU, 5 and 6 — and the Linux driver
only ever emits 1..4.

### 7.2 Linux side — the driver that caps it at 4

`drivers/platform/surface/surface_platform_profile.ko` (linux-surface,
`srcversion 14D2D28ED2CC50BA88FC519`) registers exactly **four** choices:

```
/sys/bus/surface_aggregator/devices/01:03:01:00:01/platform-profile/platform-profile-0/choices
  low-power balanced balanced-performance performance
```

The driver works in terms of the kernel's `enum platform_profile_option`
(`include/linux/platform_profile.h`):

```
PLATFORM_PROFILE_LOW_POWER = 0,  COOL = 1,  QUIET = 2,  BALANCED = 3,
BALANCED_PERFORMANCE = 4,  PERFORMANCE = 5,  MAX_POWER = 6,  CUSTOM = 7
```

`ssam_platform_profile_set()` maps that enum to the EC wire value. Disassembly
of the ladder (`cmp $0x4,%esi / ja / test / cmp $0x3,%esi / jne __SCT__WARN_trap`):

| enum | Linux profile | TMP CID 0x03 payload (u32) | FAN CID 0x0E payload (u8) |
|---|---|---|---|
| 0 | low-power | **2** | 1 |
| 1 | COOL | *rejected* ("invalid performance profile: %d") | *rejected* |
| 2 | QUIET | *rejected* | *rejected* |
| 3 | balanced | **1** | 2 |
| 4 | balanced-performance | **3** | 3 |
| 5 | performance | **4** | 4 |
| 6 | MAX_POWER | *rejected* | *rejected* |
| 7 | CUSTOM | *rejected* | *rejected* |

The inverse table lives in `.rodata+0x100` of the module — four `int32`s
`{ 3, 0, 4, 5 }` — indexed by `EC_profile_id - 1`, mapping the EC's answer
back to the kernel enum. It is exactly the inverse of the SET ladder, which
makes the mapping self-verifying.

> **The apparent "inversion" is not an inversion at all.** An earlier
> revision of this document read the platform-profile ID as a *fan* profile
> ID and concluded that Linux's `balanced` selected "Quiet" and `low-power`
> selected "Nominal" — a suspicious off-by-one. The two-axis model (§6b)
> resolves it: the platform-profile ID indexes the **GPU power profile**
> table, and the *fan* profile is derived from that record's `+0x24` tail.
>
> | Linux profile | platform ID | GPU power profile | → fan profile |
> |---|---|---|---|
> | `low-power` | 2 | Quiet | 1 Quiet (25 °C) ✓ sensible |
> | `balanced` | 1 | Nominal | 2 Nominal (30 °C) ✓ sensible |
> | `balanced-performance` | 3 | Perf | 3 Performance (32 °C) ✓ sensible |
> | `performance` | 4 | Max | 5 iGPU_Max (40 °C) or 4 dGPU_Max (45 °C) |
>
> The Linux names now line up perfectly with the fan behaviour. There was no
> bug in the driver — the earlier analysis had picked the wrong table.

### 7.3 The second, harder wall

Even if something *else* set the EC to profile 5 or 6, Linux could not report
it. `ssam_platform_profile_get()` reads TMP CID 0x02 and does:

```c
eax = ec_value - 1;
if ((unsigned)eax > 3) { dev_err("unsupported platform profile"); return -EINVAL; }
*profile = rodata_table[eax];
```

So the Linux driver **hard-rejects any EC profile ID above 4** with
`-EINVAL` and logs *"unsupported platform profile"*. Even Windows switching
the machine to `Extreme` would leave Linux's `platform-profile` sysfs reading
as "not supported".

**Confirmed live** — with the EC set to platform ID 5, the sysfs file reads:

```
$ cat /sys/bus/surface_aggregator/devices/01:03:01:00:01/platform-profile/platform-profile-0/profile
cat: ...: Invalid argument (os error 22)
```

while the EC itself reports `eff=5` on a direct `TC 0x03 / CID 0x02` read.
The EC is running `Extreme`; Linux just refuses to say so.

### 7.4 Summary

| | EC | Linux |
|---|---|---|
| Platform IDs accepted on SET | 1..7 (0 = custom, ≥8 → clamps to 1) | 1..4 only |
| Platform IDs reported by GET | 1..7 | 1..4 only; >4 → `-EINVAL` |
| `iGPU_Max` (fan 5) | fully supported | unreachable (needs platform ID 4 on the iGPU half, or 5 on the dGPU half) |
| `Extreme` (fan 6) | fully supported | unreachable (needs platform ID 5 or 6) |
| Per-fan addressing | yes (property 0x0F, per-fan enabled flags, per-fan curve slots) | no |

So the accurate statement is **not** "iGPU_Max and Extreme are Windows-only
EC features". It is: *the EC supports them; the Linux `surface_platform_profile`
driver implements a 4-profile subset and neither emits nor accepts the
higher IDs.* Reaching them from Linux requires a new path — either patching
the module's ladder + `.rodata` table, or writing the raw SSAM request
(TMP CID 0x03, u32 = 5 or 6) once the cdev is exposed.

There is also a per-fan dimension Linux has no concept of at all: the EC
addresses fans individually (property 0x0F selects fan 1 or 2; the
`DAT_30038BBC` per-fan enabled flags; per-fan curve slots 0 and 1 in the
SRAM tables). Linux's `surface_fan` module exposes a single combined RPM
reading and a single 4-state profile, and cannot address one fan.

### 7.5 What Linux *already* has

`surface_fan` is bound to `ssam:d01c05t01i01f01` (TC 0x05 = FAN, CID 0x01)
and exposes the live tachometer through hwmon:

```
/sys/class/hwmon/hwmon1/name        = surface_fan
/sys/class/hwmon/hwmon1/fan1_input  = 3032        # RPM, live
```

plus nine thermals via `hwmon4` (`surface_thermal`, e.g. `temp3_input = 42300`
= 42.3 °C). So **fan RPM is already readable on Linux today** — what is
missing is fan *control* (only `enable` = auto), per-fan addressing, and the
profile IDs above 4.

---

## 8. Tachometer and the closed-loop controller (NEW 2026-10-23)

The fan-controller layer is now decoded. Three layers:

### 8.1 Hardware descriptor table — `FUN_100AEA40`

```c
void *FUN_100AEA40(int i) {
    if (i < 11) return &DAT_10082698 + i * 8;   // 11 descriptors, 8 bytes each
    FUN_100f1dc6(0x54000008); return 0;
}
```

The 11 descriptors at `0x10082698` are packed `{u32, u32}` pairs, e.g.
`{0x00008000,0}`, `{0x20,0}`, `{0x30,0x04}`, `{0x60,0x04}`, `{0x8070,0}`,
`{0x01,0x04}`, `{0x8051,0x00080000}`, `{0x8061,0}`, `{0x8031,0x00080000}`,
`{0x8041,0}`, `{0x8071,0}` — GPIO/port identifiers for the fan outputs and
inputs. Only `FUN_100AEA40`/`FUN_100AEA1E` touch this table (2 xrefs).

### 8.2 Per-fan records — `FUN_100AEA1E`

```c
u32 FUN_100AEA1E(int fan) {
    if (fan < 2) return 0x30008BE4 + fan * 0x28;   // 2 fan records, 0x28 bytes
    FUN_100f1dc6(0x54000009); return 0;
}
```

`0x30008BE4 + 0x20` is the **vtable pointer** of the fan controller object.

### 8.3 The three dispatchers

```c
int FUN_100AEB86 (obj, desc)  { return vtable[1](obj); }  // vtable + 0x04
int FUN_100AEBF8 (obj, desc, flag) { return vtable[4](obj); }  // vtable + 0x10
int FUN_100AEBCA (obj, desc)  { return vtable[2](obj); }  // vtable + 0x08
```

They are thin virtual-method wrappers; the concrete implementations live in
the per-fan controller class's vtable.

### 8.4 The tachometer read — `FUN_100A6B60`

```c
u32 FUN_100A6B60(void) {                 // current tach value for the active fan
    if (DAT_3003c90d == 0) {
        uVar1 = *(u8 *)(_DAT_3003c8fc + DAT_3003c901);   // per-fan tach byte array
        FUN_100f15da(0x265d0002, 4, DAT_3003c901, uVar1); // tracepoint
    } else {
        uVar1 = DAT_3003c90c;             // alternate/cached source
        FUN_100f15da(0x265d0001, 1, uVar1);
    }
    return uVar1;
}
```

`DAT_3003c901` is the **fan index**; `_DAT_3003c8fc` is a per-fan byte array
of raw tach measurements.

### 8.5 The closed loop — `FUN_1009CB6C`

```c
u32 FUN_1009CB6C(int fan, ...) {
    FUN_100f15da(0xd1fe0019, 1, fan);
    FUN_1009189e(0x3002c044);             // lock
    u32 target  = (fan == 1) ? 0xCE4 /*3300 RPM*/ : 500;
    u32 publish = DAT_3003c906;          // "curves published" flag
    if (DAT_3003c90d == 0) {
        cur = FUN_100A6B60();             // tach
    } else {
        d = (u8)(FUN_100A6B60() - DAT_3003c902);
        cur = |d|;
        if (cur == 0) { duty = publish * target; goto apply; }
    }
    duty = publish * (target / cur);     // <-- proportional control
apply:
    if (duty == 0) duty = 1;
    FUN_100f15da(0xd1fe001a, 2, fan, duty);
    FUN_1009cc58();
    FUN_1009190c(0x3002bffc, duty, 1);    // publish PWM duty
    DAT_3003ce7b = 0;
}
```

Two PWM output channels are published:

* `FUN_1009CAA6(v)` → `0x3002C044` = `DAT_3003c906 * v / DAT_3003c902`
* `FUN_1009CB14(v)` → `0x3002C0D4` = `DAT_3003c906 * v / DAT_3003c904`
* `FUN_1009CC98()` → `0x3002BFFC` = **20000** (full-scale direct drive)

`DAT_3003c902` and `DAT_3003c904` are the **per-fan duty scale divisors**.

### 8.6 The curve publication — `FUN_100A7CF6` → `FUN_100A6AF8`

```c
void FUN_100A7CF6(u32 *out) {             // SRAM curve slots
    out[2] = FUN_10095924(2);
    out[1] = FUN_10095924(3);
    out[0] = FUN_10095924(4);
}

void FUN_100A6AF8(void) {
    u32 a[3];
    FUN_100A7CF6(a);
    if (a[2] == 0) FUN_100f1dc6(0x5813000a, 0x83);
    if (a[1] == 0) FUN_100f1dc6(0x5813000b, 0x87);
    if (a[0] == 0) FUN_100f1dc6(0x5813000c, 0x8b);
    _DAT_3003c8f8 = a[2];                 // curve slot 2  (Nominal)
    _DAT_3003c8f4 = a[1];                 // curve slot 3  (Performance)
    _DAT_3003c8f0 = a[0];                 // curve slot 4  (dGPU_Max)
    DAT_3003c901  = DAT_3003cc7c;         // active fan index
    DAT_3003c906  = 1;                    // "curves published"
}
```

`FUN_10095924(i)` returns the SRAM curve slot `0x300099C8 + i*0x20`
(valid for `i < 5`), i.e. a 5-slot × 32-byte table.

The three published slots map onto the three profile IDs that have a thermal
curve: **2 = Nominal, 3 = Performance, 4 = dGPU_Max**. Curve slots 1 (Quiet)
and 5+ (iGPU_Max / Extreme) are **not** published into the hot path — they are
reached through the per-fan profile path instead.

### 8.7 The curve evaluator — `FUN_1009FC8C`

```c
void *FUN_1009FC8C(void *curve, u32 x) {
    if (!curve) { FUN_100f1dc6(0x4070008, 0); return &DAT_0300001f; }
    FUN_1009f56a();                                   // lock
    if ((int)((u8)*(u8*)(curve + 0x14) << 31) < 0) {  // error bit set
        FUN_100f1dc6(0x4070002, 0); FUN_1009f59c(curve); return (void*)0x3080002; }
    if (*(char *)(curve + 0x17) != 1) {
        FUN_100f1dc6(0x4070005, 0); FUN_1009f59c(curve); return (void*)0x3080006; }
    if (*(int *)(curve + 4) == 0) {
        FUN_100f1dc6(0x4070007);    FUN_1009f59c(curve); return (void*)0x3080005; }
    *(u32 *)(curve + 0x1c) = x;                       // new x coordinate
    FUN_1009fd00(curve, *(int *)(curve + 4), *(uint *)(curve + 0x1c) & 0xffff);  // interpolate
    FUN_1009f59c(curve);                              // unlock
    return 0;
}
```

Curve record layout: `+0x04` = point count, `+0x14` = status byte,
`+0x17` = "type" (must be 1), `+0x1C` = current x. The `0x308000x` return
codes are **SSAM status codes**, which means the curve evaluator is reachable
over SSAM — a client can ask the EC "at this temperature, what duty?".

The scaler `FUN_100A6BA6(param_1)` reprograms the active curve with a
fixed-point x coordinate:

```c
lVar1 = (u64)(param_1 * 0xffff) * 0xa3d70a3e;      // Q32 fixed point
FUN_1009fc8c(_DAT_3003c8f8, ((u32)(lVar1 >> 32) & 0x3fffff) >> 6, (int)lVar1);
...
DAT_3003c902 = (char)param_1;                       // new fan-0 scale
```

`DAT_3003c902` is both the fan-0 duty divisor and the "which curve is
active" index — changing it reprograms the curve.

---

## 9. Remaining unknowns / next steps

> **Status 2026-10-24 — manual fan control is SOLVED.** `FAN CID 0x0B`
> (the forced-RPM cap) + `FAN CID 0x0E` (the fan profile id) move the fan
> from Linux, verified live: 3026 rpm → 4808 rpm (cap) → 7508 rpm (profile
> "Extreme", 45 °C). The target RPM is
> `min(quadratic(target_temp), cap)` with the quadratic in the fan's
> 0x26-byte config blob. **Full decode, live proof and the tool:
> `decoded/FAN_SPEED.md` and `tools/fanctl/fanctl.py`.**
>
> That closes items 3, 5 and 6 below.

1. **Units of `profile[+0x26]`, `+0x2A`, `+0x2C`, `+0x36`** (3071 / 3081 / 3251 /
   3121) are still not certain. They behave like a value-and-span pair (span
   is always exactly 40 = 3121-3081), not like RPM.
2. **The concrete fan-controller vtable** (what `vtable[1]/[2]/[4]` actually
   point at) has not been located — the object at `0x30008BE4` is populated
   at init, and the writer has not been identified.
3. ~~**`FUN_1009FD00`** (the actual curve interpolation) is not yet
   decoded.~~ **CLOSED** — it is a Q16 scaler with an inversion flag that
   writes into the owner's shared slot table at `shared[slot] + 0x18`; the
   real interpolation is a per-slot function pointer at `curve+0x18`.
4. **The SRAM curve tables** (`0x300099C8`, 5 × 32 bytes; `0x30009A98`,
   2 × 32 bytes) are populated at boot; fill code at `0x10095888` /
   `0x100959B6`.
5. ~~**What the `Override` per-fan record's `+0x20` = 8000 and `+0x24` = 8500
   mean.**~~ **PARTLY CLOSED** — `+0x24` is the hot-state target RPM (it is
   what `FUN_100C595E` hands to `FUN_100C5C76`), `+0x22` = 3000 is the
   minimum RPM and `+0x20` = 8000 the maximum. The Override record has a
   *different* layout from the profile records (its target temperature is a
   float at `+0x1C`, not `+0x32`).
6. ~~**`FUN_100C61C8`** (FAN CID 0x80 — the manual fan write) has not been
   decoded.~~ **CLOSED — and it is inert.** It is a mode byte + u16
   (1 off / 2 fixed RPM / 3 percent / 4 auto), but mode 2's `fan+0x6E` has
   no live consumer and mode 3's commit destroys the duty it injects. See
   `FAN_SPEED.md` §6 for the measurement and the two reasons.
7. **Windows has never been disassembled.** `SurfaceSmfClient.sys` and
   `SurfaceIhvGpuPowerControllerDriver.sys` are present under `raw/` and would
   confirm that Windows actually issues profile IDs 5/6/7 — the EC side is
   proven to accept them, but nothing yet proves Windows sends them.
8. **The `I_RTS*` board thermal channels** never enter the fan chain.
9. **Which record a fan actually uses** (Override vs. a named profile) is
   not exposed by any CID.
