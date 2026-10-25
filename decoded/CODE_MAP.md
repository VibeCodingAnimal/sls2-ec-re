# EC Firmware — Code Map (2026-10-23)

**Status: the EC firmware's ARM code IS present in the MSI container.**
The Phase 3 conclusion recorded in `decoded/EC.md` — *"No `0x100xxxx` code bodies
are present in the chunk payload… the actual machine code lives outside the
chunk-payload address range"* — **is wrong**. The chunk payload contains the
complete Cortex-M4 image. The earlier analysis failed only because the absolute
address mapping was never recovered, so every code address appeared to point
outside the extracted range.

Everything below was re-derived from scratch from `raw/ec/EC.bin` and verified
independently.

---

## 1. Container format (re-derived)

`raw/ec/EC.bin` — 1,695,143 bytes, sha256
`352a206d25f9d964dd83946126d5c7ba968f31944bbddb569cd183272c9a471a`.

```
0x00000  XLFF header (GUID + sizes + base 0x10000000)
         PKCS#7 signedData cert chain (signature only — payload is PLAINTEXT)
0x0A80   "MSS1" + base + 0x0D00658B
0x0A90   "SAML" + payload descriptor
0x0AB9   chunk array A  (40,295 records)
0xCF428  second SAML-style descriptor, chunk array B (40,294 records)
0x19DD95 end of chunk data; trailing cert/signature data to 0x19DDA7
```

Chunk record (5-byte header + variable payload):

```c
struct chunk { u32 offset; u8 len; u8 payload[len]; };  // len == 16 for full chunks
```

* Array A: file `0x0AB9` → image offsets `0x0000..0x9D660`
* Array B: file `0xCF438` → image offsets `0x0000..0x9D660` — **identical range**

A and B are a redundant pair. **Neither contains anything outside
`0x0000..0x9D660`.** Assembled image = 644,716 bytes (`0x9D66C`).

`raw/ec/PD.bin` is a second, much smaller XLFF container (same GUID header,
`MSS1`@`0xA1A`, `SAML`@`0xA2A`): a 12,560-byte *Platform Descriptor*, not code.

Tools: `tools/xlff/xlff_extract.py` (unpack + auto-locate vector table).

---

## 2. Address mapping — how the base was recovered

The image has no relocations, so the link address had to be recovered
empirically (`tools/xlff/resolve_base.py`):

1. Scan for Thumb `LDR Rt,[PC,#imm]` literal-pool loads (456 distinct
   absolute-looking pool values in `0x10000000–0x10100000`).
2. For each candidate base `B`, count how many of those absolute addresses
   land on the **start** of a printable string in the image.

```
BASE=0x1007F994   string-start hits = 184/456   <-- sharp peak
BASE=0x1007F9A4   string-start hits =  94/456
BASE=0x1007F984   string-start hits =  89/456
```

The plateau around the winner is the dense EC string table; requiring an
exact *string start* is what separates the true base from its neighbours.

### Result

```
absolute = 0x1007F994 + image_offset
```

Validation (three independent confirmations):

| Check | Result |
|---|---|
| Vector table image `0x66C` → absolute | **0x10080000** — page-aligned, the standard Cortex-M vector base |
| PMC handler `0x100ABC66` | → image `0x2C2D2` → disassembles to coherent Thumb-2 (`itt ne / ldrne / cbnz / bx lr`) |
| Literal-pool strings | `0x1008340C`→`IdleThread`, `0x10083424`→`DebugSS`, `0x10083418`→`DebugLedTmr`, `0x10087380`→`Write Data: ` — all exact string starts |

The vector table itself:

```
[0] 0x30080000   Initial SP (SRAM at 0x30000000 + 0x80000)
[1] 0x100F1E09   Reset        -> image 0x72475
[2] 0x1008CBD1   NMI          -> image 0xD23D
[3] 0x1008CBE9   HardFault    -> image 0xD255
[4] 0x1008CC01   MemManage    -> image 0xD26D
[5] 0x1008CC19   BusFault     -> image 0xD285
[6] 0x1008CC31   UsageFault   -> image 0xD29D
[7] 0x100B9F57   SVCall-ish   -> image 0x3A5C3
[11] 0x100BC1D7  DebugMon     -> image 0x3C843
[12] 0x100C25AF  PendSV       -> image 0x42C1B
[14] 0x1009E191  SysTick      -> image 0x1E7FD
[15] 0x1009DFDB  IRQ0         -> image 0x1E647
```

### Image layout

```
0x00000..0x0005F  Region 1  boot header (48 bytes)
0x00060..0x005FF  gap
0x00600..0x0066B  Region 2a section table
0x0066C..0x0767CF Region 2b vector table + firmware metadata
0x0767D0..0x9D65F Region 2c trailing metadata
0x9D660..0x9D66F Region 3  trailer
```

Section table words at image `0x600`:
`{0x30,0x02,0x01,0x04, 0x58,0x10,0x0D00658B,0x00, 0x630,0x48,0x00,
0x654,0x24,0x03,0x01,0x01,0x00,0x01, 0x66C,0x6000,0x9D000,0x18,0x04,0x01,0x01,0x02,0x14}`

---

## 3. Loading into Ghidra

The image was loaded as a flat **ET_EXEC** ELF32 with a single `PT_LOAD` at
`0x1007F994`, entry `0x10080000`, plus a `.ARM.attributes` section declaring
`Tag_CPU_arch = v7E-M` / `profile = 'M'` so Ghidra selects the Cortex-M
language. Built by `tools/xlff/mkelf.py`.

Result in Ghidra 12.1.4 (via REA MCP 6.4.0): **2,983 procedures, 435 defined
strings** — i.e. the code is fully analysable.

> Note: `objcopy --change-addresses` produces an `ET_REL` ELF, and Ghidra's
> ELF loader then relocates it by +0x10000, breaking the mapping. The ET_EXEC
> builder above avoids that.

---

## 4. The EC's SSAM command routing table

Located at absolute **`0x1008A2BC`** (image `0xA928`), referenced by the
dispatcher at `0x100B0640` → `FUN_100b09ee(..., &DAT_1008a2bc, 0x0F, ...)`.
`0x0F` = 15 output slots.

Record layout (12 bytes), confirmed from the decompiler of `FUN_100b09ee`
(`0x100B09EE`):

```c
struct cmd_entry {
    u32 flags;    // bits 0-5 = fragment TYPE, bit30 = "has response", bit31 = "notification"
    u32 index;    // output slot number (0..14; param_5 = 0x0F = 15 slots)
    u32 handler;  // pointer to a byte-tagged descriptor
};
```

> **CORRECTION (2026-10-23).** An earlier revision of this document read
> `flags` bits 0-5 as the SSAM *TC* and `index` as the *CID*, and produced a
> TC→CID map from it. **That was wrong.** The decompiler of `FUN_100b09EE`
> shows plainly that:
>
> * `flags & 0xFF` is a **fragment type**: `2` = fallback, `3`/`4` = push,
>   `5` = pop, `1`/`2` = consume-and-advance. The function keeps a small
>   8-deep stack of saved parse fragments (`local_7c[24]`).
> * `index` is an **output slot number** into the caller's 15-slot result
>   array (`param_6 + index*8`), gated by `param_5 > index`.
> * the third word is a pointer to a **NUL-terminated byte set of acceptable
>   request tags**: the dispatcher scans it for a byte equal to the first byte
>   of the incoming message, stopping at the NUL. Entries whose tag byte is
>   `2`/`4` also accept end-of-input as a fallback.
>
> So this table is a **request-field parser / result-slot routing table**,
> not a TC/CID command map. The real per-TC command handlers live elsewhere
> — e.g. the TMP handler at `0x100C524E` dispatches on the request's CID
> (0x03, 0x05) and then on the **IID** (0x01, 0x03, 0x04, 0x0C…0x1D) inside
> `FUN_100C4D68`. See `decoded/FAN_CONTROL.md` §7.

55 entries were decoded. The "TC-like" column below is just the low byte of
`flags` (the fragment type) and is **not** a TC:

| flags low byte | meaning |
|---|---|
| 0x01 | consume-and-advance |
| 0x02 | fallback / terminal |
| 0x03 | push fragment |
| 0x04 | push fragment (also accepts fallback) |
| 0x05 | pop fragment |

**PMC CID 0x0A → handler `0x100ABC66`** (image `0x2C2D2`) — this confirms the
Phase 3 lead from the other direction and shows the PMC handler *is* in the
image.

The FAN rows all carry a NULL handler, which suggests fan commands are routed
elsewhere (the fan lives behind the SA; the EC's own fan state machine is the
`Cmpnt fan` component, §5).

---

## 5. Fan component (`Cmpnt fan`)

String `Cmpnt fan` at absolute `0x10087C70` (image `0x82DC`), referenced from
`FUN_100c56fe` (`0x100C56FE`) — the fan component's constructor. Decompiled:

```c
longlong FUN_100c56fe(void *self, void *cfg, u32 a3, u32 a4) {
    if (!self || !cfg) { FUN_100f1dc6(0x54020000, &DAT_0300001f); ... }
    *(u32 *)(self + 0x68) = 0;
    *(u32 *)(self + 0x58) = (u32)cfg;
    *(u8  *)(self + 0x7c) = cfg[0x89];
    FUN_10096bf4(self + 0x28, cfg + 0x34, 0x24);
    *(u32 *)(self + 0x54) = *(u32 *)(cfg + 0x80);
    *(u32 *)(self + 0x28) = ((u32*)cfg[0x80])[3];      // +0x14
    *(u32 *)(self + 0x2c) = ((u32*)cfg[0x80])[4];      // +0x18
    *(u32 *)(self + 0x30) = ((u32*)cfg[0x80])[5];      // +0x1c
    FUN_10096bf4(self + 0x04, cfg + 0x10, 0x24);
    *(u32 *)(self + 100)   = cfg + 0x58;
    *(u32 *)(self + 0x18)  = *(u16 *)(cfg + 0x58);     // curve param A
    *(u32 *)(self + 0x14)  = *(u16 *)(cfg + 0x5A);     // curve param B
    *(u32 *)(self + 0x3c)  = *(u16 *)(cfg + 0x5C);     // curve param C
    *(u32 *)(self + 0x38)  = *(u16 *)(cfg + 0x5E);     // curve param D
    *(u8  *)(self + 0x7d)  = cfg[0x8A];
    *(u16 *)(self + 0x76)  = 0;
    *(u8  *)(self + 0x7f)  = 0;
    *(u16 *)(self + 0x78)  = 0xFFFF;
    *(u16 *)(self + 0x7A)  = 0xFFFF;
    *(u8  *)(self + 0x80)  = 0;
    *(u8  *)(self + 0x81)  = 0;
    *(u32 *)(self + 0x5c)  = FUN_10095924(cfg[0x8B]);  // profile -> curve lookup
    *(u32 *)(self + 0x60)  = FUN_10095950(cfg[0x8C]);  // profile -> curve lookup
    FUN_100c61c8(self, 4, 0);
    iVar2 = FUN_1008ecb4(self + 0x4c, "Cmpnt fan");
    ...
    FUN_1009a190(&DAT_300295ec, self);
}
```

So the fan curve is driven by four u16 config values at `cfg+0x58..0x5E` plus
two profile-indexed lookups (`FUN_10095924` / `FUN_10095950`) — the latter two
are the natural target for "what is the RPM at thermal profile N".

---

## 6. GPU power profiles

14 profile names, contiguous in the image, image `0x8B8C..0x8DB0`:

```
Nominal_iGPU  Quiet_iGPU  Perf_iGPU  Max_iGPU  Gaming_iGPU
Mixed_Reality_iGPU  Kiosk_iGPU
Nominal_dGPU  Quiet_dGPU  Perf_dGPU  Max_dGPU  Gaming_dGPU
Mixed_Reality_dGPU  Kiosk_dGPU
```

Plus `dGPU_Max` (image `0x9C6E`) and `iGPU_Max` (image `0x9CAE`).

Related EC strings: `SLPowerContract`, `PowerPolicyMsg`, `PowerPolicyTran`,
`PowerPolicySS`, `PowerPolicyBattData`, `PwrPolicyExec`, `PwrPolicyAcOk`,
`RsrcMgrPower`, `SoC Power State Publisher`,
`Power State Entry Async Publisher`, `Power State Pending Seq Publisher`,
`Power Policy Generic Notification`, `Power Policy RSOC Notification`.

None of the 14 profile-name strings is referenced by a literal-pool load, so
they are reached through a descriptor/pointer table rather than directly —
that table is the next thing to chase.

---

## 7. Live access on this machine

* Running EC firmware: **`SAM firmware version: 12.201.139`** (dmesg, from
  `surface_serial_hub serial0-0`) — **exactly the firmware extracted from the
  MSI**, so this analysis applies verbatim to the machine it runs on.
* Kernel `6.19.8-surface-3`; `surface_aggregator` is loaded (9 users:
  `surface_battery`, `surface_charger`, `surface_fan`, `surface_temp`,
  `surface_platform_profile`, `surface_hid_core`, `surface_hid`,
  `surface_aggregator_tabletsw`, `surface_aggregator_registry`).
* **There is no `/dev/surface_aggregator` (or `/dev/surface/aggregator`) node
  on this kernel**, and no misc device in `/proc/misc`. So
  `tools/cdev-probe/ssam_probe.py` cannot run as written — its cdev path
  does not exist here.
* Consequence: the Phase 3 "5-minute GET-only sweep" is blocked until the
  cdev is exposed. Options: (a) find the node name this kernel actually uses,
  (b) build a tiny kernel module against `surface_aggregator` to issue
  requests, (c) use the `surface_fan`/`surface_temp` sysfs paths that already
  work.

---

## 8. Next steps (ranked)

1. **Expose the SSAM cdev** — unblock all empirical verification.
2. **Decode `FUN_10095924` / `FUN_10095950`** — profile→RPM curve tables
   (direct fan-RPM access on Linux).
3. **Find the GPU-profile descriptor table** (the thing that references the
   14 profile names) and the code that applies a profile → the dGPU power /
   PCIe policy path.
4. **Decode the remaining SAM routing entries** and cross-check against the
   Windows `SurfaceSmfClient.sys` / `SurfaceIhvGpuPowerControllerDriver.sys`
   command vocabularies (both present in `raw/`).
5. Only then consider issuing any SET. Nothing in this session wrote to the EC.
