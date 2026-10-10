# Critic-A: IO-001 ADC/controller binding review

## Verdict

**BLOCKED_EVIDENCE; score null.** No remaining P0/P1 found in the bounded corrected local candidate. The independently reproduced clock-correlation defect below is closed in the frozen source and regression. Immutable publication binding remains pending. These tests do not establish real DMA/USB timing, full-firmware resource fit, clock accuracy or hardware acceptance.

- Adapter C: `f316523483171195d8f60bee38a3f716f3d887e506074f1038aafc14a99e64ce`.
- Adapter header: `85110d0698c322919ef2b8006a81e166b00cd11eea3a9b4cd972f550d244fbc7`.
- Russian README: `5946c39a000952e0739e14c8934ca93e0773028def161554a01c240cf86713f6`.
- Platform C with read-only preflight: `91906d403ffc787d1b7e6541302226347bf299f58b2949be47742d9768240d43`.
- Platform header: `3c1c175c5372a3a1a6ce3c00b97f674802a61b04a716097e105b947e7494a008`.

Full file/Git-blob manifest: `source-manifest.json`; isolated final copy: `final-candidate/`. Controller, bridge, acquisition, TX, session and codec source/header bytes match the previously reviewed PR35 snapshot. Platform delta from aligned PR27 `5215177100453c6f77e5c5a637197fd626e57aec` is confined to the read-only profile1 preflight function and declaration.

## P1 CA-IO-1: omitted interval between millisecond and microsecond epoch reads

Baseline adapter C `24bc1435f46845fd5da2d15a4478ccb24a3d69bfb13ec6f6bb85b0b1820e6013` sampled epoch_ms before its microsecond reference. Time spent between those reads was omitted from every derived sample timestamp, with no numerical bound in the old clock contract. In particular this can delay the Auto timeout measured from ARM when pretrigger is zero.

I independently reproduced the issue with a shared-clock callback model: the ms callback samples at 1 ms, then suffers a 5 ms preemption before returning; the old adapter accepts start and assigns its first sample 1 ms although the model's physical timer reference is at 6 ms. `correlation_gap_repro.c` and `correlation-gap-baseline.log` preserve the failing trace (exit 1). This is a controlled software time/preemption model, not a physical clock measurement.

The correction brackets the millisecond read between two microsecond reads, rejects unsigned reported width >20 before driver start, and uses the lower microsecond bracket as delay origin. My unchanged reproducer now rejects without invoking driver start (`correlation-gap-final.log`, exit 0); no sample is created. Supporting actual-register-model tests independently verify widths 0/20 accepted, 21/5000 rejected before any MMIO write, for ordinary and wrapping clocks. Subsequent delay until timer start is included. The header and Russian README now require one true monotonic clock, prompt serialized callbacks, the same ms source for controller receive/poll, nonoverlapping lifetime-safe objects and explicit quantization limitations.

Closure applies only to the frozen corrected bytes above. At <=1 us clock quantization, 20 reported units can span nearly 21 us of actual time, in addition to millisecond quantization. The chosen timestamp is nominal and is not guaranteed to precede the physical aperture. A 32-bit microsecond counter divided by 1000 is explicitly disallowed as the millisecond clock.

## Personally executed checks

1. Read complete adapter source/header, added platform preflight, caller README, author harness and supporting independent harness. No production implementation edited.
2. Strict GCC 14.2.0 C11 author combined register-model/adapter/controller suite and ASan/UBSan passed, including exact complete samples and partial final-block stop, clock wrap, stale Stop and transfer fault (`author-final.log`). The unchanged controller strict/sanitized suite, including 20 encoded windows, also passed (`controller-regression.log`).
3. My separate adapter-boundary harness (`independent_adapter.c`, SHA-256 `f031a979423cbb8dcaf6566ce7d5d03db1cb7da54253ab82b80c54debcf77568`) links the real adapter with independently written fake adjacent APIs. Strict and ASan/UBSan each pass **10,139 scenarios**, checking **1,280,000 full-block sample timestamps** against uint64 reference arithmetic, all 128 partial-stop positions, bad block metadata, error propagation, start rejection and rollover. Frozen snapshots are invalidated/poisoned at the stop callback and no later element is consumed. This isolates adapter arithmetic/control flow; it is not a replacement for the real-controller/register-model integration suite.
4. Personally reran the supporting independent scenario harness against my final snapshot: **313 complete 2048-sample windows (641,024 exact code values)**, strict and ASan/UBSan pass (`supporting-final/`). Includes 20 windows for each mode/pretrigger pairing, every final DMA-block offset, uint64 index boundaries, actual driver ownership on failed Stop, fresh-HELLO retry, canonical profile rejection without writes, mid-start/runtime clock change, release rearm, clock-correlation guard and coherent uint64 clock epochs across both 71-minute microsecond and 49.7-day millisecond rollovers. It reuses the existing author's register-bus fixture and real production COBS/CRC APIs; no separate independent codec oracle is claimed here. Code expectations and scenario construction are independent.
5. ASan/UBSan uses `detect_leaks=0`; the environment's ptrace-incompatible LeakSanitizer run is not counted as a pass.
6. Personal strict Cortex-M3 GCC 14.2.1 compile (`-Os -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -fno-short-enums`, freestanding, warnings as errors) passed (`arm-final/result.txt`). Adapter object text **438 bytes**, adapter context **24 bytes**; modified backend text **2,476 bytes**, driver context **860 bytes**; controller context remains **8,888 bytes**. These three owned contexts sum to **9,772 bytes**, excluding stream/USB/startup/runtime/stack and any other objects. Adapter service frame 56 bytes, start hook 24, init 32; backend read-block frame 40. Earlier controller's known 2,944-byte Stop chain remains relevant, with additional callers/runtime/IRQs still unproven. Object compilation is not a final linker fit or MCU execution result.

## Remaining evidence and contracts

- Exact published commit/tree binding, applicable second-critic/shared acceptance and later integration must still be established.
- All objects must remain alive through confirmed hardware stop; live-driver reinitialization violates the documented caller precondition. The API does not dynamically enforce arbitrary alias/lifetime misuse.
- The foreground service interval, including copy, RX, TX and other work, must remain strictly below 2,560 us while running. Calling only once per completed 128-code block is insufficient. No measured WCET, scheduler/IRQ bound or DMA bus-load proof exists.
- Startup must supply actual coherent wrapping ms/us clocks and reserved peripherals. Clock dividers/nominal HSE are configuration evidence, not measured oscillator frequency or sample aperture.
- Real startup/linker map, full stack budget/watermarks, USB adapter, Windows device interoperation and low-voltage physical windows remain open. Existing hardware and ignition safety exclusions remain in force.

## Final documentation/resource rebind

The final README adds the combined resource appendix; I reviewed its exact final hash above and personally reran the frozen ten-module resource builder in `combined-arm-reproduction/`. The relocatable object is byte-identical to the author's frozen run, SHA-256 `ae149ec3c091cf67d7ba3a94b5b3f16d2905b8b772f721ff106cf1957a9a9e2b`. The 11,281-byte text/rodata sum and 10,880-byte owner ABI match. That owner adds RX stream 564 and decoded message 544 to the 9,772-byte subtotal above. The README accurately labels 6,656 bytes as arithmetic remainder before missing runtime/platform/IRQ costs, not proven free RAM. No full firmware fit is claimed. Author test SHA-256 `dbf9dacf5287c3ef7a587602a3e46038a74da297a4da7f370c418ca659dae458`, including clock-correlation regression, was already the exact test in my personally executed final snapshot. No test rerun gap or executable-source change is introduced by this appendix.
