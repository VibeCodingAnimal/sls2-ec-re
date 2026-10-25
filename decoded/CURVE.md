# The fan curve machinery — decoded (2026-10-24)

Companion to `FAN_CONTROL.md` §6b–6d (the *policy* chain). This file covers the
*actuation* chain: how the SAM turns a target temperature into a PWM value.

**Headline correction:** `FUN_1009FD00` — which earlier notes called "the
actual curve interpolation" — **is not an interpolator at all.** It is a
16-bit scaler with an optional inversion. The real interpolation lives behind a
**per-slot function pointer** at `curve+0x18`.

---

## 1. `FUN_1009FD00` — the scaler (fully decoded)

```c
void FUN_1009FD00(curve *c, owner *o, uint x16)
{
    uint scale = o->scale;                 // u32 at owner+0x10, Q16
    if (scale == 0) scale = 0xFFFFFFFF;    // "unity"

    if (c->flags & 0x80) {                 // INVERT
        if (x16 == 0)            goto out_zero;
        if (x16 != 0xFFFF) { x16 = 0xFFFF - x16; goto scale_it; }
        goto out_zero;                     // x == 0xFFFF -> 0
    }
    if (x16 != 0) {
        if (x16 == 0xFFFF) goto out_full;  // x == full -> scale unchanged
        goto scale_it;
    }
    goto out_zero;                         // x == 0 -> 0

scale_it:
    scale = (scale * x16 + 0x8000) >> 16; // Q16 * Q16 -> Q16, round-to-nearest
out_full:
out_zero:
    o->out_array[c->channel * 4 + 0x18] = scale;
}
```

So the output is simply `scale × x / 65536`, with `x` optionally mirrored
(`0xFFFF - x`) when the curve is marked inverted. **All five fan curve slots
have the invert bit set**, so for them `x` runs backwards.

## 2. The curve header (0x20 bytes)

The five fan curve slots live at `0x300099C8 + i*0x20` (accessor
`FUN_10095924(i)`, `i < 5`).

```
+0x00  ptr    next curve instance in the owner's linked list
+0x04  ptr    owner / points struct   (curve+4 is what FD00 calls "points")
+0x08  u32    descriptor word 1
+0x0C  u32    —
+0x10  u32    —
+0x14  u8     flags
               bit0 = "has curve"      bit1 = "pending"
               bit2 = "armed"         bit3 = INVERT
               bit7 = INVALID (evaluator rejects)
+0x15  u8     curve slot index (0..4)
+0x16  u8     output channel index
+0x17  u8     type — the evaluator requires exactly 1
+0x18  ptr    >>> INTERPOLATOR function pointer <<<
+0x1C  u32    current x (the input value)
```

## 3. The owner / points struct

```
+0x00  ptr    head of the linked list of curve instances
+0x08  ptr    shared u32 output array
+0x0C  u32    scale numerator
+0x10  u32    scale (Q16)  = numerator / descriptor[2]
+0x14  u8     device / channel id
+0x16  u8     flags: bit0 = "an active curve exists", bit1 = "armed"
+0x18  u8     sub-index
```

`FUN_1009F3B0(curve, owner)` appends the curve to `owner->list` and rejects
(`0x300001D`) a curve whose channel collides with a scaled one on the same
owner.

## 4. The per-slot descriptors

`FUN_10095962()` (the boot-time curve-table constructor) installs five
descriptors, one per fan curve slot:

```c
FUN_1009f936(0x300099c8, &DAT_10083068, 0x30009958);   // slot 0
FUN_1009f936(0x300099e8, &DAT_1008307c, 0x30009958);   // slot 1
FUN_1009f936(0x30009a08, &DAT_10083090, 0x3000993c);   // slot 2
FUN_1009f936(0x30009a28, &DAT_100830a4, 0x3000993c);   // slot 3
FUN_1009f936(0x30009a48, &DAT_100830b8, 0x3000993c);   // slot 4
```

Each descriptor is 5 words:

```
+0x00 u32  interpolator function pointer
+0x04 u32  -> curve+0x08
+0x08 u32  -> curve+0x1C   (initial x)
+0x0C u32  divisor for the owner scale
+0x10 u32  packed: [0x10]=slot id  [0x11]=channel  [0x12]=invert  [0x13]=—
```

| slot | descriptor | id | channel | invert | initial x | divisor | owner |
|---|---|---|---|---|---|---|---|
| 0 | `0x10083068` | 0 | 0 | yes | 25000 | 21626 | `0x30009958` |
| 1 | `0x1008307C` | 1 | 2 | yes | 25000 | 21626 | `0x30009958` |
| 2 | `0x10083090` | 2 | 0 | yes | 25000 | 65535 | `0x3000993C` |
| 3 | `0x100830A4` | 3 | 1 | yes | 25000 | 65535 | `0x3000993C` |
| 4 | `0x100830B8` | 4 | 2 | yes | 25000 | 65535 | `0x3000993C` |

Notes:

* **Slots 2/3/4 share one owner** (`0x3000993C`) and slots 0/1 another
  (`0x30009958`) — matching `FAN_CONTROL.md` §8.6, where `FUN_100A6AF8`
  publishes slot2 = Nominal, slot3 = Performance, slot4 = dGPU_Max.
* The **divisor** is the curve's max-x normalisation: `65535` = "no cap",
  `21626` = a scaled-down ceiling for the two lower slots.
* `initial x = 25000` is the curve's starting input.

## 5. The evaluator and the commit

```c
// FUN_1009FC8C(curve, x) — evaluate
if (!curve)                        return ERR;   // 0x4070008
if (curve->flags & 0x80)           return 0x3080002;  // INVALID
if (curve->type != 1)             return 0x3080006;
if (!curve->owner)                return 0x3080005;
curve->x = x;
FUN_1009FD00(curve, curve->owner, curve->x & 0xFFFF);
return 0;
```

```c
// FUN_1009FAC2(curve) — commit
curve->interpolator(curve, 1);          // <<< the real interpolation
FUN_1009FD00(curve, curve->owner, curve->x & 0xFFFF);
owner->out_array[0x3C] |= 1 << (channel*2);   // "value changed" bitmap
owner->out_array[0x4C] |= 1 << channel;
owner->out_array[0x00]  = 1 << channel;       // active-channel mask
```

So the **interpolator runs before the scaler** — it is what turns an input
(temperature) into the curve's own x value, which `FUN_1009FD00` then scales
into the output channel.

## 6. The interpolators

`curve+0x18` is a per-slot function pointer:

| slots | pointer | function |
|---|---|---|
| 0–4 (fan curves) | `0x10095887` | body at `0x10095886` |
| `0x30009A98` / `0x30009AB8` | `0x100958E5` | body at `0x100958E4` |

Both are small Thumb routines Ghidra did not promote to procedures (they are
reached only through the function pointer). Hand-decoded, `0x10095886` is:

```
PUSH {r7, lr}
LDR r2, [PC, #0x180]      ; r2 -> 0x300099C8 (curve slot 0)
LDR r0, [r2]
BNE  ...
CMP r1, #1
MOVS r0, #9
CMP r1, r2
...
MOVS r0, #10
...
MOVS r0, #100   (0x64)
...
MOVS r0, #101   (0x65)
...
MOVS r0, #102   (0x66)
```

i.e. a **keyed lookup** with keys **{1, 9, 10, 100, 101, 102}** — the shape of
a piecewise fan curve expressed as duty-step breakpoints (9 and 10 are the
neighbours of the "curve point count" 9 seen in `FAN CID 0x07`; 100/101/102 are
duty-percent-scale steps). The tables it consults live in EC SRAM
(`0x300099C8` and neighbours) and are populated at boot, so the numeric values
are not recoverable from the image alone.

## 7. The scale setter — `FUN_100A6BA6(percent)`

This is the function `FAN_CONTROL.md` §8.6 called "Scaler":

```c
void FUN_100a6ba6(uint percent)
{
    if (!curve_slot2)  error;                 // 0x58130007
    if (percent == DAT_3003c902) return;      // unchanged

    if (percent != 0) {
        uint64 l = (uint64)(percent * 0xFFFF) * 0xA3D70A3E;
        x = (((l >> 32) & 0x3FFFFF) >> 6);    // 16-bit x
        FUN_1009FC8C(curve_slot2, x);
        if (DAT_3003c902 == 0 || !FUN_1009F920(curve_slot2)) {
            FUN_100a7c94(1);                  // enable
            FUN_1008fdde(0x14);
            FUN_1009fac2(curve_slot2);        // commit (runs the interpolator)
        }
        DAT_3003c902 = (char)percent;
        return;
    }
    FUN_1009FC8C(curve_slot2, 0);             // scale 0 = fan off
    FUN_1009fba6(curve_slot2);
    FUN_100a7c94(0);
    DAT_3003c902 = 0;
}
```

The magic constant: `0xA3D70A3E` as a Q32 multiplier = **0.6401319**.
For `percent = 100` the computed x is **65515 ≈ 0xFFFF** — full scale.
So:

> **x = percent × 65535 × 0.640132 / 64**, i.e. `x ≈ percent × 655.4`,
> saturating at 0xFFFF for percent ≥ 100.

(The `& 0x3FFFFF` then `>> 6` in the code is the Q16 packing of that value.)
`percent` above 100 wraps, because the mask is applied after the multiply.

## 8. What the fan actually consumes

* `FAN CID 0x01` → fan RPM (u16)
* `FAN CID 0x0D` → **applied target temperature** (float32, `fan+0x84`)
* `FAN CID 0x11` → duty % (`u16 * 100 / 0xFFFF`)
* `FAN CID 0x06` → the fan's **static config + curve blob** (0x26 bytes) — a
  byte-exact slice of the image's fan-config block (`0x100896C0+0x58` for fan 1,
  `0x10089750+0x58` for fan 2):

```
+0x00 u16 13107   +0x04 u16 3000 (min RPM)   +0x0A u16 500
+0x02 u16 65535   +0x06 u16 11000             +0x0C u16 100 (duty %)
+0x08 u16 16383   +0x0E.. 24 bytes = per-fan curve data (differs per fan)
```

* `FAN CID 0x07` → 0x34-byte runtime curve header per fan:
  `{u32 type=1, u32 points=9, u32 ?, u32 ?, u32 ?, 0…}`
  — the first two words are **stable** (`type=1`, `points=9`). The three tail
  words are **not reliably returned**: repeated single-shot reads have produced
  `(84, 944, 368)`, `(17170, 34779, 12585)` and mostly `(0, 0, 0)` within
  seconds of each other, with no corresponding change in RPM, duty % or target
  temperature. Treat the tail as a race against the EC's curve update, not as
  a stable telemetry field.

## 9. The full chain, end to end

```
OS: platform profile
  → SSAM TMP CID 0x03
    → FUN_100B6C6C: id → GPU power profile (14 recs @ 0x1008851C)
      → record+0x24/+0x25 = per-fan profile IDs
        → FUN_100C69A0 → FUN_100C64B4 → FUN_100C6084 → FUN_100C62B0
          → sets fan->scale from (per-fan target − current) / profile span
            → publishes one of the 5 curve slots (0x300099C8 + i*0x20)
              → FUN_100A6BA6(percent)  → x = percent*65535*0.640132/64
                → FUN_1009FC8C → curve->x = x
                  → FUN_1009FAC2 → curve->interpolator(curve,1)   [the real curve]
                    → FUN_1009FD00 → out = scale × x / 65536
                      → owner->out_array[channel*4 + 0x18]  → PWM
```

## 10. The vtable hunt — RESOLVED (there were *two* objects, and one "vtable" wasn't one)

Earlier notes claimed the fan had a "code vtable" at `fan+0x20`. That was a
conflation of **two different structures**:

### 10a. The fan object — 0x8C bytes, at `0x3002E348 + fan*0x8C`

Built by `FUN_100C56FE(fanobj, cfg)` (the fan constructor), which is called
twice by `FUN_100C6D5C`:

```c
void FUN_100c6d5c(void) {
    for (fan = 0; fan < 2; fan++) {
        FUN_100C56FE(0x3002E348 + fan*0x8C, cfg);            // cfg += 0x90
        memcpy(0x3002E2C8 + fan*0x40, 0x10089680, 0x40);      // per-fan rec := Override
    }
}
```

**That also answers "why is `FAN CID 0x05` always `Override`?" — the init
literally copies the `Override` record into both fans' per-fan slots. It is
the default, not a mode.**

```
fan+0x04  0x24 B   copied from cfg+0x10
fan+0x14  u16      cfg+0x5A  = 65535   curve x upper bound
fan+0x18  u16      cfg+0x58  = 13107   curve x lower bound
fan+0x1C  u32      current curve x
fan+0x20  u16      RPM target copy
fan+0x22  u16      RPM command copy
fan+0x28  12 B     fan-profile-record header (Nominal) fields
fan+0x38  u16      cfg+0x5E  = 11000   max RPM
fan+0x3C  u16      cfg+0x5C  = 3000    min RPM
fan+0x54  ptr      *(cfg+0x80) = 0x100894C0   <-- the FAN PROFILE TABLE, *not* a vtable
fan+0x58  ptr      cfg (0x100896C0 / 0x10089750)
fan+0x5C  ptr      curve slot A   = FUN_10095924(cfg[0x8B])
fan+0x60  ptr      curve slot B   = FUN_10095950(cfg[0x8C])
fan+0x64  ptr      cfg+0x58  (the bounds block)
fan+0x68  u32      0
fan+0x6C  short    ramp counter
fan+0x6E  short    COMMANDED RPM          (mode 2)
fan+0x70  short    COMMANDED DUTY, Q16 %  (mode 3)
fan+0x72  u16      current RPM target
fan+0x74  u16      current measured RPM
fan+0x78  u16      0xFFFF   FAN CID 0x08/0x09 register A
fan+0x7A  u16      0xFFFF   FAN CID 0x08/0x09 register B
fan+0x7C  u8       STATE: 0 stopped, 1 ramping, 2 settled, 3 manual, 4 auto
fan+0x7D  u8       cfg[0x8A]
fan+0x7E  u8       MODE: 1 off, 2 fixed RPM, 3 percent, 4 auto
fan+0x80/0x81 u8   0
```

### 10b. The fan config block — 0x90 bytes, `0x100896C0` / `0x10089750`

```
+0x10  0x24 B  copied to fan+0x04
+0x34  0x24 B  copied to fan+0x28
+0x58  u16 13107   curve x lower bound   (= 20 % duty)
+0x5A  u16 65535   curve x upper bound   (= 100 % duty)
+0x5C  u16 3000    min RPM
+0x5E  u16 11000   max RPM
+0x60  u16 16383
+0x62  u16 500
+0x64  u16 100     duty %
+0x66  24 B        per-fan curve data (this is what FAN CID 0x06 returns)
+0x80  ptr 0x100894C0    fan profile table
+0x84  fn  0x100C6E35    ENABLE CALLBACK — called as cb(on/off, fan#)
+0x88  u8  fan# (1/2)
+0x89  u8  -> fan+0x7C initial state
+0x8A  u8  -> fan+0x7D
+0x8B  u8  curve slot A index
+0x8C  u8  curve slot B index
```

### 10c. The real code vtable — on a *different* object

The per-fan **descriptor** is 0x28 bytes at `0x30008BE4 + fan*0x28` and it
carries a genuine code vtable at `+0x20`:

```c
// FUN_100AEB86 — vtable[1]
iVar1 = (**(code **)(*(int *)(param_1 + 0x20) + 4))(param_1);
// FUN_100AEBCA — vtable[2]
iVar1 = (**(code **)(*(int *)(param_1 + 0x20) + 8))(param_1);
// FUN_100AEBF8 — vtable[4]
iVar1 = (**(code **)(*(int *)(param_1 + 0x20) + 16))(param_1);
```

`FUN_100C4A60()` is what wires it up:

```c
void FUN_100c4a60(void) {
    FUN_1008ABA0(0x40); FUN_1008CFE2();     // GPIO 0x40
    FUN_1008ABA0(0x14); FUN_1008CFE2();     // GPIO 0x14
    FUN_100AEBCA(FUN_100AEA1E(0), FUN_100AEA40(1));   // attach hw descriptor #1 to fan 0
}
```

`FUN_100AEA40(i)` = the hardware descriptor table: `0x10082698 + i*8`,
**11 entries**. So the *hardware* side is described by 11 GPIO/port records,
and the per-fan descriptor vtable is the driver interface to them.

### 10d. `FAN CID 0x80` — the manual fan write, fully decoded — **and INERT**

`FUN_100C61C8(fan, mode, value)` is the mode setter; the SSAM payload for
CID 0x80 is **one mode byte + one u16 value**:

| mode | meaning | value range | effect |
|---|---|---|---|
| 1 | **off** | — | `fan+0x6E = 0`, `fan+0x7E = 1`, fan stopped |
| 2 | **fixed RPM** | **3000 .. 11000** (`cfg+0x5C/0x5E`) | `fan+0x6E = value` |
| 3 | **percent** | **20 .. 100** (`cfg+0x58/0x5A`) | `fan+0x70 = value*65535/100` |
| 4 | **auto** | — | back under the curve/state machine |

Out-of-range → `0x5402000D` (fixed RPM) / `0x5402000E` (percent). Note mode 3
re-uses the *same* `0xA3D70A3E` Q32 constant as `FUN_100A6BA6` to compute the
curve's x, and range-checks it against `cfg+0x58/0x5A`.

> **⚠ CORRECTION (2026-10-24): CID 0x80 does not move the fan.**
> Measured: 15 s of `--rpm 11000` re-asserted every 50 ms left the fan at
> 2994–3002 rpm / 30 % throughout. Mode 2's `fan+0x6E` has no live consumer
> (`FUN_100D1BDC` mode 1 outputs `fan+0x1C` unchanged — it only advances an
> integrator), and mode 3's commit path runs the interpolator *after*
> injecting the duty, destroying it. `FUN_100C62B0` also drops mode writes
> while the fan is ramping (`fan+0x7C == 1`).
>
> **The working manual control is `FAN CID 0x0B` (the forced-RPM cap) plus
> `FAN CID 0x0E` (the fan profile id).** See `decoded/FAN_SPEED.md`.

### 10e. The PWM output path

```
FUN_100C62B0(fan, action)
 ├ action == 2  (auto / percent commit)
 │    FUN_100C5CE0(fan, fan->duty_q16)          -> FUN_1009FC8C(fan->curve_slot_A, x)
 │    FUN_100C5D0A(fan):
 │        FUN_1009FAC2(curve_slot_A)             -> interpolator + scaler + bitmaps
 │        if (fan->state == 0) FUN_1008ED50(2, fan+0x4C)    // publish the component
 │        FUN_100C5C88():                        // <<< PWM PUBLISH
 │            FUN_1009F6F4(FUN_10095930(0), 250000)          // 250 ms period
 │    fan->enable_cb(1, fan#)                   // cfg+0x84 = 0x100C6E35
 │    fan->state = 4
 ├ action == 1  (manual start)
 │    FUN_100C5CE0(fan, *(u16*)(cfg+0x58+8))    // initial duty
 │    enable_cb(1, fan#); FUN_100C5D0A(fan); FUN_100D1CE6(fan+4)
 │    fan->x = *(u16*)(cfg+0x58+8); fan->state = 1
 └ action == 0  (periodic tick)
      FUN_100C6134(fan)                         // compute the new target RPM
      ramp / settle logic (fan+0x6C vs cfg+0x58+0xC)
      FUN_100D1BDC(fan+4, 0|1, &fan->x)        // publish the curve point
      FUN_100C5CE0(fan, fan->x & 0xFFFF)
```

The PWM compare registers are shadowed in EC SRAM at **0x3002BFFC**,
**0x3002C044**, **0x3002C0D4**, written through a register-index dispatch
table at `0x1009CF40` (case 13 → 0x3002C164, 15 → 0x3002C044,
16 → 0x3002C08C, 17 → 0x3002C0D4, 20 → 0x3002BFFC).

### 10f. What you can read from Linux right now

| quantity | how |
|---|---|
| measured RPM | `FAN CID 0x01` (also `hwmon1/fan1_input`) |
| duty % | `FAN CID 0x11` |
| applied target °C | `FAN CID 0x0D` |
| static config + curve blob | `FAN CID 0x06` |
| runtime curve header | `FAN CID 0x07` (tail is racy) |
| active fan | `FAN CID 0x10` |
| raw registers A/B | `FAN CID 0x08` / `0x09` |

The **commanded** RPM / duty (`fan+0x6E` / `fan+0x70`) and the **state/mode**
bytes (`fan+0x7C` / `fan+0x7E`) live in EC SRAM and are *not* exposed by any
CID — there is no SSAM command that returns them. That is the one remaining
gap on the read side.

## 11. Still open

> **SOLVED 2026-10-24 — manual fan control.** `FAN CID 0x0B` writes a
> forced-RPM cap (`fan+0x76`) and `FAN CID 0x0E` sets the fan's profile
> record (which sets the target temperature). The target RPM is
> `min(quadratic(target_temp), cap)`, with the quadratic in the fan's
> 0x26-byte config blob. Full write-up and live proof in
> **`decoded/FAN_SPEED.md`**; the tool is `tools/fanctl/fanctl.py`.

> **SOLVED 2026-10-24 (later) — the ceiling.** `FAN CID 0x0C` writes the
> target temperature directly (`fan+0x84`, plus the override flag
> `fan+0x88` that stops the profile record from overwriting it). Asking
> for 55 °C makes the EC's own quadratic request 11177 rpm — and the fan
> still settles at **7450 rpm with the duty at 100 %**. The duty is the
> curve's output and it saturates at the top of the curve; the 11000 in
> the config blob is a guard on `FUN_100C6134`'s model RPM, not a speed
> the fan delivers. The curve is the wall, and the commands that could
> rewrite it (CID 0x81/0x84) are **not dispatched by the EC** — see
> `decoded/FAN_SPEED.md` §11.

1. **The numeric contents of the interpolator's breakpoint tables** — they live
   in EC SRAM and are filled at boot; the image only has the code. This is
   now also *why* the fan tops out: the curve's top end is ~7500 rpm.
2. **The meaning of the 24-byte tail in the fan-config blob** — **SOLVED for
   three of the six floats**: `+0x1A`, `+0x1E` and `+0x22` are the
   target-RPM quadratic's `b`, `c` and `a`. The other three (`+0x12`,
   `+0x16`, `+0x0E`) are still unlabelled.
3. **The `I_RTS*` sensors** still have no confirmed path into this chain.
4. **The per-fan descriptor's vtable *value*** — we know it lives at
   `0x30008BE4 + fan*0x28 + 0x20` and that slots 1/2/4 are dispatched
   through it, but the pointer is written by code we have not found (no
   literal reference to `0x30008C04` exists), and the 11 hardware descriptors
   at `0x10082698` (`FUN_100AEA40`) have not been decoded.
5. **Whether the fan hardware can do more than ~7500 rpm.** The EC never
   asks, so there is no way to find out from Linux — the duty would have
   to be driven past the curve's top.

5. **No SSAM CID returns the commanded RPM / duty / mode** (`fan+0x6E`,
   `fan+0x70`, `fan+0x7C`, `fan+0x7E`) — they are EC-SRAM-only. Adding a read
   would need a firmware change; today the closest proxies are `FAN CID 0x11`
   (duty %) and `0x01` (RPM).
6. **What `FUN_100C6134` computes** — the per-tick function that turns the
   curve into the target RPM. It is the last undecoded link between the curve
   and the fan.
7. **Which record a fan actually uses** — the per-fan "Override" record
   (CID 0x05) and the profile records (CID 0x04) have *different* layouts,
   and no CID reports the fan's active record.

