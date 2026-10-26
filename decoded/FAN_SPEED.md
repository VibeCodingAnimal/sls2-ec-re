# Manual fan control — SOLVED (2026-10-24)

**The SAM will take a manual RPM command from Linux.** The mechanism is two
SSAM writes, both already implemented in `tools/fanctl/fanctl.py`, and both
verified live on this machine.

```
  target_rpm = min( quadratic(target_temp), forced_rpm )
```

| term | where it lives | what writes it |
|---|---|---|
| `quadratic(t) = a·t² + b·t + c` | the fan's 0x26-byte config blob (`fan+0x64`) | fixed in firmware |
| `target_temp` | `fan+0x84`, a float | the fan's profile record, via **CID 0x0E**, or directly via **CID 0x0C** |
| `forced_rpm` | `fan+0x76`, a u16 cap | **CID 0x0B** |

The cap can only *lower* the fan below what the profile's quadratic asks
for, so a high RPM needs a profile with a high target temperature first.

**How high can it go? ~7500 rpm.** The quadratic can be pushed to ask for
11177 rpm (§10) but the duty saturates at 100 % there, and 11000 rpm in
the config blob is a guard on the EC's model, not a speed the fan
delivers. Also: the EC never dispatches commands with bit 7 set (§11).

## 1. Live proof

`sudo python3 tools/cdev-probe/fan_cap_test.py --fan 1`:

```
fan 1 — baseline
  baseline                     rpm= 3026  duty= 30%  target=30.0 C

fan 1 — CID 0x0B: forced RPM cap -> 11000
  write status = 0x0000
  after cap raise              rpm= 3030  duty= 30%  target=30.0 C
  after cap raise              rpm= 3032  duty= 30%  target=30.0 C
  after cap raise              rpm= 4808  duty= 71%  target=30.0 C   <-- moved

fan 1 — CID 0x0E: profile -> 6 (Extreme, 45 C)
  write status = 0x0000
  after profile change         rpm= 7434  duty=100%  target=45.0 C   <-- moved
  after profile change         rpm= 7508  duty=100%  target=45.0 C
  after profile change         rpm= 7505  duty=100%  target=45.0 C

fan 1 — restoring
  profile -> 2 (Nominal): 0x0000
  cap -> 3000:           0x0000
  restored                 rpm= 7509  duty=100%  target=30.0 C
```

After the restore the fan spun back down to **3202 rpm / 32 %** (checked
separately). The fan has real inertia and the closed loop is slow, so it
takes several seconds to settle.

The numbers line up with the model exactly:

* 30 °C → quadratic = 4321 → the fan settled at 4808 (the loop's gain)
* 45 °C → quadratic = 7681 → the fan settled at 7508

## 2. The quadratic

`FUN_100C5DFE(temp, fan#)` — the target RPM at a given temperature:

```c
uVar2 = (int)(temp*temp * cfg[0x22] + temp * cfg[0x1a] + cfg[0x1e]) & 0xffff;
```

`cfg` is the fan's config blob (`fan+0x64`), readable with **CID 0x06** and
0x26 bytes long. The three coefficients are IEEE-754 floats at `+0x22`,
`+0x1A` and `+0x1E`.

Live values (`tools/cdev-probe/fan_curve.py`):

| | fan 1 | fan 2 |
|---|---|---|
| a (`cfg[0x22]`) | 5.022900 | 4.482900 |
| b (`cfg[0x1A]`) | −152.690002 | −132.070007 |
| c (`cfg[0x1E]`) | 4381.5 | 4221.8999 |
| min RPM (`cfg[0x04]`) | 3000 | 3000 |
| max RPM (`cfg[0x06]`) | 11000 | 11000 |
| start duty (`cfg[0x08]`) | 16383 (25 %) | 16383 |

```
fan 1:  t=20 -> 3336    t=25 -> 3703    t=30 -> 4321
        t=35 -> 5190    t=40 -> 6310    t=45 -> 7681
        t=50 -> 9304    t=60 -> 13302
fan 2:  t=20 -> 3373    t=25 -> 3721    t=30 -> 4294
        t=35 -> 5091    t=40 -> 6111    t=45 -> 7356
```

## 3. The fan profile table — `CID 0x04` (record id) and `CID 0x0E` (set)

Each profile record is 0x40 bytes. `+0x00` is the id (u16) and `+0x02` is
the name; `+0x30` is the **target temperature** as a float32 in degrees C.

| id | name | target °C | `+0x22` min RPM | `+0x24` hot RPM | `+0x26` thr | `+0x28` hyst | `+0x2C` temp thr | `+0x2E` hyst |
|---|---|---|---|---|---|---|---|---|
| 1 | Quiet | 25.0 | 3000 | 0 | 3071 | 20 | 3251 | 40 |
| 2 | Nominal | 30.0 | 3000 | 0 | 3071 | 20 | 3251 | 40 |
| 3 | Performance | 32.0 | 3000 | 0 | 3071 | 20 | 3251 | 40 |
| 4 | dGPU_Max | 45.0 | 3000 | 0 | 3071 | 20 | 3251 | 40 |
| 5 | iGPU_Max | 40.0 | 3000 | 0 | 3071 | 20 | 3251 | 40 |
| 6 | Extreme | 45.0 | 3000 | 0 | 3071 | 20 | 3331 | 40 |

Reading the record is full of traps: the temperature is a float32 at
**`+0x30`**, not at `+0x32`, and it is plain degrees (25.0), not
milli-degrees (25000.0) and not a half float.  Getting that wrong makes
every profile look like it targets 0 °C, which silently collapses the
whole fan curve — the tool then thinks the maximum reachable RPM is the
constant term 4381 and happily drops the fan onto the Quiet profile.

`+0x24..+0x2F` is an undecoded ramp table; `+0x26`/`+0x28` and `+0x2C`/`+0x2E`
look like (rpm, temp) pairs — (3071, 20) and (3251, 40) — and the Extreme
profile differs from the rest at `+0x2A` (3331 vs 3081) and `+0x36`
(3131 vs 3121).

`+0x24` is the RPM used when the fan is in the hot state; it is 0 for all
six built-in profiles, and 8500 for the per-fan "Override" record that
**CID 0x05** returns.

**CID 0x0E** (payload = one byte, the profile id) points the fan at a
profile. `FUN_100C64B4` → `FUN_100C6084(fan, record)` copies the profile's
fields into the fan, including `fan+0x84` = the target temperature.

## 4. The cap — `CID 0x0B`

`FUN_100C5F9C(fan, value)`:

```c
if (value == 0) { fan+0x7F = 0; return; }      // clear
fan+0x76 = value; fan+0x7F = 1;                 // set
```

`fan+0x76` is a **cap**, and `FUN_100C595E` uses it as one:

```c
uVar6  = FUN_100C5DFE(target_temp, fan#);        // the quadratic
uVar10 = (fan+0x7F && policy == 1) ? fan+0x76 : 0;
// state 1 (cool):
uVar9 = uVar10; if (uVar6 < uVar10) uVar9 = uVar6;   // = min(quadratic, cap)
```

That is why the fan idles at exactly **3000 rpm**: the cap is 3000 (the
profile's `+0x22` minimum RPM) even though the quadratic asks for 4321 at
30 °C. Raising the cap to 11000 let the fan reach the quadratic's value.

**The cap is the slider.** `fanctl.py` writes it directly and restores it
to 3000 on exit.

> **Correction (2026-10-25).** The `uVar6` line above is only half the
> story: the quadratic is the ceiling *only if* `fan+0x88` is set, and
> `fan+0x88` is set by nothing except FAN CID 0x0C. In normal operation
> the ceiling is **0xFFFF** and the cap is the commanded RPM outright.
> §13 has the whole state machine.

## 5. The two commands

```
FAN CID 0x0B   payload: u16 rpm       -> the forced-RPM cap
FAN CID 0x0E   payload: u8  profile   -> the fan's profile record
```

Both are `is_set` requests (no response expected). Instance id = the fan
number.

## 6. `FAN CID 0x80` is a dead end — do not use it

> **CORRECTION (2026-10-24, later the same day):** everything below
> describes what CID 0x80 *would* do if the EC ever saw it. **It never
> does.** The EC silently drops every command with bit 7 set (0x80-0xFF)
> before the FAN dispatcher runs — see §11. So the two failure modes
> below are unreachable from Linux, and "inert" was the wrong word: the
> command is not handled badly, it is not handled at all.

CID 0x80 takes a mode byte + u16 and looks like the obvious manual-control
command. It is what Windows' fan driver uses. **On this firmware it does
not move the fan**, and this was measured, not assumed:

```
fanctl.py --rpm 11000 --fan 1 --seconds 15 --interval-ms 50
   rpm=3000 duty=30%
   ... 75 samples, rpm never left 2994..3002, duty never left 30 %
```

Two separate reasons, both traced in the decompiler:

1. **Mode 2 (fixed RPM) sets `fan+0x6E` and nothing consumes it.** The only
   reader is the periodic tick's state-2 branch:

   ```c
   fan+0x20 = fan->rpm_target;      // measured RPM
   fan+0x22 = fan->rpm_cmd;         // the commanded RPM
   FUN_100D1BDC(fan+4, 1, &fan->x);  // mode 1
   FUN_100C5CE0(fan, fan->x);
   ```

   `FUN_100D1BDC` in **mode 1** computes `fVar8 = param_1[6]` = `fan+0x1C`
   = the duty *itself* — the output is a **no-op**. Mode 1 only advances the
   integrator stored at `fan+0x10`; **mode 0** is what actually recomputes
   the duty, and only the state-3 branch calls it. So one write advances
   the integrator once and the output never moves.

   Re-asserting the command every 50 ms (to force the state back to 2 and
   keep the integrator advancing) still changed nothing, so the tick's
   per-fan mask (`DAT_30038C23`, filled from the GPIO read in
   `FUN_1008B2F8`) evidently does not include the fan while it is in a
   manual mode.

2. **Mode 3 (percent) is self-defeating.** Its commit path is
   `FUN_100C5CE0(fan, duty)` followed by `FUN_100C5D0A(fan)`, and
   `FUN_100C5D0A` calls `FUN_1009FAC2(curve)` — which runs the
   **interpolator** (`(**(code **)(curve+0x18))(curve, 1)`) over the value
   that was just injected. The injected duty is destroyed by the same
   commit that published it.

`FUN_100C62B0` also refuses to do anything while the fan is in the
ramping state (`if (fan+0x7C == 1) return;`), so a mode write issued
during the ramp is silently dropped.

## 7. The fan object, corrected

The fan object is 0x8C bytes. The fields that matter:

| offset | meaning |
|---|---|
| `+0x58` | per-fan descriptor pointer (`+0x84` enable callback, `+0x88` fan number) |
| `+0x5C` | curve pointer (the duty curve) |
| `+0x64` | **config blob pointer** — the quadratic coefficients live here |
| `+0x68` | tach period |
| `+0x6E` | commanded RPM (only CID 0x80 mode 2 writes it — and nothing reads it) |
| `+0x70` | commanded duty |
| `+0x72` | measured RPM (raw) |
| `+0x74` | measured RPM (smoothed) — this is what **CID 0x01** returns |
| `+0x76` | **forced-RPM cap** — CID 0x0B |
| `+0x7C` | drive state (0 stopped, 1 ramping, 2 settled, 3 manual) |
| `+0x7D` | hysteresis state (1 cool, 2 hot, 3 latched) |
| `+0x7E` | mode (1 policy, 2 fixed RPM, 3 percent, 4 auto) |
| `+0x7F` | "the cap is valid" flag |
| `+0x84` | **target temperature** (float) — CID 0x0D returns it |
| `+0x88` | "the fan has a speed source" flag |

`FUN_100C595E` is the sensor-update handler (not the tick): it takes the
measured RPM and the temperature, runs the hysteresis against the profile's
thresholds, and calls `FUN_100C5C76(fan, rpm)` to set the commanded RPM.
**It only acts on fans whose mode is 1.**

`FUN_100C5C76(fan, rpm)` sets `fan+0x6E` and calls `FUN_100C62B0(fan, 1)`,
and it is also gated on mode 1.

## 8. The complete FAN command table

From `FUN_100C6610`:

| CID | dir | payload / response | function |
|---|---|---|---|
| 0x01 | GET | u16 RPM | `FUN_100C5DF8` — `fan+0x74` |
| 0x02 | GET | 0x0E bytes | `FUN_100C65B4` |
| 0x03 | GET | u8 count | — |
| 0x04 | GET | 0x40 profile record | `FUN_100C6DAE` |
| 0x05 | GET | 0x40 per-fan record | `FUN_100C6DE4` |
| 0x06 | GET | 0x26 config blob | `fan+0x64` |
| 0x07 | GET | 0x34 curve header | `FUN_100CB92E` |
| 0x08 | SET | u16 ×2 | `fan+0x78`, `fan+0x7A` hysteresis thresholds |
| 0x09 | GET | u16 ×2 | the same two thresholds |
| 0x0B | SET | u16 | **`FUN_100C5F9C` — the forced-RPM cap** |
| 0x0C | SET | **f32 °C** | **`FUN_100C5FDC` — the target-temperature override, see §11** |
| 0x0D | GET | f32 | `fan+0x84` — the target temperature |
| 0x0E | SET | u8 | **`FUN_100C64B4` — the fan profile id** |
| 0x0F | SET | u8 | `DAT_30038C24` — 1 = threshold policy, 2 = curve |
| 0x10 | GET | u8 | the same policy byte |
| 0x11 | GET | u8 duty % | `FUN_100C5CEA` — `FUN_1009FD50(curve)` |
| 0x12 | SET | f32 °C | set the **cap** to the rpm the quadratic gives for this temperature (0.0 = cap off) |
| 0x13 | GET | f32 | temperature |
| 0x80 | SET | u8 + u16 | `FUN_100C61C8` — **never dispatched, see §11** |
| 0x81 | SET | 0x40 | `FUN_100C64F4` — the fan's curve params — **never dispatched** |
| 0x83 | SET | 0x40 | copy a record to a fan — **never dispatched** |
| 0x84 | SET | 0x26 | copy the payload over the fan's config blob — **never dispatched** |

## 9. Using it

```bash
sudo modprobe surface_aggregator_cdev     # not auto-loaded

sudo python3 tools/fanctl/fanctl.py --status
sudo python3 tools/fanctl/fanctl.py --rpm 6000 --fan 1      # holds 10 s, then restores
sudo python3 tools/fanctl/fanctl.py --rpm 7000 --fan 1 --seconds 3600 --keep
sudo python3 tools/fanctl/fanctl.py --auto --fan 1
sudo python3 tools/fanctl/fanctl.py --gui                    # the slider
```

The slider spans 3000 .. 11000 rpm. When the requested RPM is above the
current profile's ceiling the tool switches to a 45 °C profile first
(7681 rpm ceiling) and puts the original profile back when it exits.

The deadman (SIGINT/SIGTERM, the CLI timeout, the GUI close handler) always
restores the cap to 3000 and the profile to the one that was active.

## 10. Can the fans reach 11000 rpm? **No.**

Short answer: **the EC will happily be asked for 11000 rpm, but the fan
will not do it.** The ceiling is the duty curve, not the quadratic, and
the curve tops out at ~7500 rpm on this machine.

### 10a. The third door: `FAN CID 0x0C` writes the target temperature

The quadratic is evaluated at `fan+0x84`, the **target temperature** — and
that is writable on its own:

```c
// FUN_100C5FDC(float temp, int fan)
if (fabs(temp) < 0.1) {
    fan->0x88 = 0;                       // clear the override
    fan->0x84 = fan->0x54->0x30;         // back to the profile's temperature
} else {
    fan->0x88 = 1;                       // override flag
    fan->0x84 = temp;                    // <- our temperature
}
```

The override flag matters: `FUN_100C6084` refreshes `fan+0x84` from the
applied record **only while `fan+0x88 == 0`**, so a CID 0x0C value
survives profile changes. Writing `0.0` to CID 0x0C puts it back.

With the cap (CID 0x0B) at 11000 and a target temperature of 55 °C the
quadratic asks for

```
5.0229 * 55^2 - 152.69 * 55 + 4381.5 = 11177 rpm
```

which the cap clamps to 11000. **The EC does exactly that.**

### 10b. Live proof — the fan says no

```
$ sudo python3 tools/cdev-probe/fan_temp_boost.py --fan 1 --temp 55 --rpm 11000
fan 1: quadratic 5.0229t^2 -152.69t 4381.5, target temp 30.0 C
  target  55.0 C -> EC asks  11177 rpm | fan reads 55.0 C | peak   7450 rpm, duty 100%, target 55.0 C
fan 1: override cleared, cap back to 3000, target 30.0 C
```

The override took (the fan's own temperature readback went to 55.0 °C),
the EC's model asked for 11177 rpm — and the fan settled at **7450 rpm
with the duty at 100 %**. The duty was already saturated, so more RPM
could not be delivered.

Three independent commands all land on the same wall, all at 100 % duty:

| what was commanded | source | fan did |
|---|---|---|
| 8500 rpm | the Override record's hot-rpm field, state 3 | 7238 rpm |
| 7681 rpm | the Extreme profile (45 °C) | 7508 rpm |
| 11177 rpm | CID 0x0C target 55 °C + cap 11000 | 7450 rpm |

So the **11000 in the config blob is not a speed, it is a guard**: in
`FUN_100C6134` the EC computes the fan's model RPM as `30e6 / curve_x`
and *reverts* it if it exceeds `config+6` (11000). It is a sanity limit
on the model, and the model never gets anywhere near it.

**The real ceiling is the duty curve.** The duty is the curve's evaluated
output (`FUN_100C5CEA` → `FUN_1009FD50(curve)`, 0..0xFFFF) and the curve
is what the PWM is published from; 100 % duty is the top of the curve,
and the top of the curve on this machine is ~7500 rpm.

### 10c. What would have to change to get more

The three doors that could rewrite the curve or bypass it are all CIDs
≥ 0x80, and the EC never dispatches those (§11). The remaining lever is
the curve itself, whose breakpoint tables are filled into EC SRAM at boot
and are not writable from Linux.

**11000 rpm is therefore not reachable on this machine from Linux.**

## 11. Why CIDs ≥ 0x80 never arrive

Everything in the EC's FAN dispatcher uses a *signed* comparison on the
command byte, so 0x80-0xFF are implemented correctly:

```c
cVar2 = *(char *)(header + 7);        // the command id, signed
...
else if (cVar2 == -0x7d) {            // 0x83 — write a fan record
    FUN_100C6DE4(fan, &rec);
    FUN_10096BD4(rec, payload, 0x40); // memcpy
    FUN_100C64B4(fan, rec);           // apply
}
else if (cVar2 == -0x7c) {            // 0x84 — write the config blob
    FUN_10096BD4(0x30029608, payload, 0x26);
    fan->0x64 = 0x30029608;
}
```

But none of them can be reached from Linux. Measured behaviour:

* **GET 0x80 .. 0x85 → no response at all** (the cdev times out with
  -110), while GETs of 0x01/0x03/0x04/0x05/0x06/0x0D/0x11/0x13 answer
  normally.
* **SET 0x83 with a 64-byte payload → accepted, and nothing happens.**
  The fan's record is byte-for-byte unchanged afterwards. Same for 0x84
  and the config blob.

The SET case is the instructive one. A SET is sent with `flags = 0` (no
`HAS_RESPONSE`), so **the kernel returns success as soon as the request
is queued** — the "status 0" that `fan_record_boost.py` printed was the
*kernel's*, not the EC's. The EC never answered because it never ran the
handler.

Note that the request *does* reach the EC: a GET that the EC does not
implement times out rather than being rejected, so a timeout means "sent,
not answered", not "not sent". The EC's transport drops commands with
bit 7 set before the FAN dispatcher sees them — the mechanism is in the
IPC unpack (`FUN_1008DF7A` → the message header at `message+0x24`, whose
byte 7 is the command id), and is **not yet decoded**.

The practical consequence: the fan command table's top half
(0x80 manual mode, 0x81 curve params, 0x83 record write, 0x84 config
write) is **EC-side code that Linux cannot reach**. Everything in §10 is
the end of the road from the cdev.

## 12. Still open

1. The `I_RTS*` board thermal channels never enter this chain — they are
   read but do not feed the quadratic.
2. ~~The per-fan "Override" record (CID 0x05) has a **different layout**
   from the profile records~~ — **resolved 2026-10-25**, see §13.2. The
   Override record is the same layout with `+0x20` non-zero, which selects
   the other branch of `FUN_100C6084`. The record actually applied at
   runtime is the **Nominal profile record**: CID 0x0D reports the applied
   target temperature as 30.0 °C, and only the profile records carry a
   target temperature at `+0x30`.
3. `FAN CID 0x0F` (policy = 1 threshold / 2 curve) is 1 on this machine.
   It gates whether the cap is consulted at all; behaviour under policy 2
   is unverified. Under policy 2 the cap is ignored entirely and the EC's
   own curve is the only input, so it would be the one way to get *below*
   the state-2 floor of §13.7.
4. **Why the EC drops CIDs ≥ 0x80** (§11). The unpack is in the IPC layer
   around `FUN_1009EE0C`; until it is decoded we do not know whether the
   Linux cdev could express those commands in some other shape.
5. **Whether the fan can physically do more than ~7500 rpm.** The duty
   saturates at 100 %, so the EC never asks for more; the hardware's real
   limit is above the curve's, but nothing on the Linux side can reach it.
6. The curve's breakpoint tables (filled into EC SRAM at boot) and the
   numeric meaning of the three unlabelled floats at `config+0x0E`,
   `+0x12`, `+0x16`.

## 13. The fan state machine, decoded (2026-10-25)

Everything above was read off the decompiler and then confirmed live. This
section is the reconciled model, and it explains why the stock fan on this
machine is useless.

### 13.1 The two temperatures

`FUN_100C595E(fan_list, ?, ?, param_4)` takes a 32-bit word that holds
two centi-degree temperatures:

| | source | used for |
|---|---|---|
| `local_1c` | **overwritten per fan** by `FUN_100C5924` → `FUN_100C488C`, which reads the **fan module's own thermistor** | the curve, and the cool→hot transition |
| `local_1a` | the caller | the hot→latched transition |

FAN CID 0x13 returns the same number the curve sees, as a float32: 21-23 °C
at idle, rising to 45 °C when the fan is at 6600 rpm. **The fan's thermistor
is heated by the fan itself.**

### 13.2 The record layout

The profile records (CID 0x04) and the per-fan Override record (CID 0x05)
share one layout, and every threshold is in **centi-degrees**:

| offset | profile record | Override record | meaning |
|---|---|---|---|
| `+0x20` u16 | 0 | 8000 | non-zero selects the other branch of `FUN_100C6084` |
| `+0x22` u16 | 3000 | 3000 | minimum RPM |
| `+0x24` u16 | 0 | 8500 | the latched-state RPM |
| `+0x26` u16 | 3071 | 3131 | cool → hot threshold (30.71 °C) |
| `+0x28` u16 | 20 | 20 | threshold hysteresis (0.20 °C) |
| `+0x2A` u16 | 3081 | 3141 | the curve's start temperature |
| `+0x2C` u16 | 3251 | 3331 | hot → latched threshold (32.51 °C) |
| `+0x2E` u16 | 40 | 40 | latched hysteresis (0.40 °C) |
| `+0x30` f32 | 30.0 | 0 | the profile's target temperature |
| `+0x34` u16 | 100 | — | duty for the latched state |
| `+0x36` u16 | 3121 | — | the curve's end temperature |

The applied record is the **Nominal profile record**: CID 0x0D returns the
applied target temperature as 30.0 °C, and only the profile records have a
target temperature there (the Override record's `+0x30` is 0).

### 13.3 The curve is a step, not a ramp

`FUN_100C6084` builds the curve when a profile is applied:

```c
fan+0x46 = record+0x2A;                             // start temperature
span     = record+0x36 - record+0x2A;               // 40 for every profile
fan+0x28 = (quadratic(record+0x30) - config+4) / span;   // the slope
fan+0x38 = quadratic(record+0x30);                  // the curve's maximum
fan+0x3C = record+0x22;                             // the curve's minimum
```

and `FUN_100D1BDC` evaluates it:

```c
rpm = min_rpm + (fan_temp - start_temp) * slope;    // clamped to [min, max]
```

**The span is 40 centi-degrees — 0.40 °C.** The EC's "fan curve" is not a
ramp: it is a step from the minimum to the maximum within four tenths of a
degree of the fan's own thermistor. Below the record's `+0x26` threshold
the fan gets 3000 rpm; above it, `quadratic(target_temp)` — 4321 rpm on the
Nominal profile.

### 13.4 The ceiling, and why the cap is the command

`fan+0x88` is the "the target temperature is overridden" flag. Only
`FUN_100C5FDC` (FAN CID 0x0C) sets it, and it is cleared again by writing
0.0 to that CID:

```c
ceiling = fan+0x88 ? quadratic(fan+0x84) : 0xFFFF;
cap     = (fan+0x7F && policy == 1) ? fan+0x76 : 0;
```

Nothing in normal operation sets `fan+0x88`, so **the ceiling is 0xFFFF** —
the cap is the commanded RPM on its own. The whole model:

```
state 1 (cool,  fan thermistor < record+0x26):
    target = min(cap, ceiling)

state 2 (hot):
    target = min(max(curve(fan_thermistor), cap), ceiling)

state 3 (latched, local_1a > record+0x2C):
    target = record+0x24              // 8500 rpm on the Override record
```

`FUN_100C5C76` writes the target and the EC ramps the duty towards it.

### 13.5 Live characterisation (2026-10-25)

Commanded cap against the measured steady state, fans 1 and 2, 25 s to
settle:

| commanded | fan 1 | duty | fan 2 | duty |
|---|---|---|---|---|
| 3000 | **3001 rpm** | 30 % | 2999 rpm | 31 % |
| 5500 | **5567 rpm** | 65 % | 5576 rpm | 67 % |
| 7500 | **7318 rpm** | 97 % | 7270 rpm | 100 % |

The cap is an accurate RPM command — within 2.4 % — once the EC has ramped
there. The EC's duty map on this machine is 3000 → 30 %, 5500 → 65 %,
7500 → 97 %.

**The ramp is slow**, and that is why a short test lies:

```
cmd 7500:  +5s 4209 (55%)   +10s 6069 (78%)   +15s 6821 (88%)   +25s 7318 (97%)
cmd 3000:  +5s 4938 (50%)   +10s 3742 (37%)   +20s 3096 (31%)   +40s 3001 (30%)
```

About 25 s up, 35 s down. A test that samples for 6 s sees a fan that is
still on its way and looks like it under-delivers by 15 %.

The descent also shows the state machine working: at cmd 3000 the fan was
still at 4938 rpm with the thermistor at 36.7 °C (state 2, where the curve
is a floor of 4321 rpm), and it only dropped through 4321 once the
thermistor cooled below the 30.71 °C threshold and the EC went back to
state 1.

### 13.6 Why the stock fan never helps this machine

1. The curve's input is the **fan's own thermistor**, not the CPU or the
   GPU. It reads 21-23 °C idle and 36-48 °C only when the fan is already
   spinning hard.
2. The curve's span is 0.40 °C, so it is a step, not a ramp — and the
   step's *top* is `quadratic(profile target temp)`, which the Linux
   platform profile pins to 4321 rpm (Nominal) or 7681 rpm (dGPU_Max).
3. Linux's `surface_platform_profile` only ever emits profile ids 1-4 and
   the driver rejects anything higher, so the fan cannot be pointed at the
   45 °C profiles that would raise the step's top.

So the EC sits at its 3000 rpm minimum while the CPU package idles at
53-57 °C. Measured on this machine with the stock setup:

```
cpu 57.0 C   gpu 48.0 C   fan 1 3000 rpm   fan 2 3000 rpm
```

**The fix is to close the loop in userspace**, which is what
`tools/fanctl/fancurve.py` does: read the real temperatures, run them
through an editable curve, and write the resulting RPM to the cap. The EC
keeps its own protection on top — in state 2 the curve is a floor, and in
state 3 the EC drives 8500 rpm by itself.

### 13.7 The remaining sharp edge

In state 2 the target is `max(curve, cap)`, and the curve's maximum is
`quadratic(profile target temp)` — **4321 rpm on the Nominal profile**. So
once the fan's thermistor is above 30.71 °C, the cap cannot bring the fan
*below* 4321 rpm. In practice the fan escapes, because slowing it cools its
own thermistor and drops the EC back into state 1 (§13.5) — but a hot
ambient means the equilibrium sits at 4321 rpm and no slider can go
quieter. Applying the Quiet profile (FAN CID 0x0E, id 1) lowers that floor
to `quadratic(25 °C)` = 3704 rpm.
