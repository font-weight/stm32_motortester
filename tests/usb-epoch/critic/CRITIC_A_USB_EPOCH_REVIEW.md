# Critic-A: USB-002 serialized epoch metadata

Date: 2026-10-10. Scope: [issue54](https://github.com/font-weight/stm32_motortester/issues/54), the portable metadata transition module, its contracts and its native/ARM evidence. This reviewer did not author or modify the implementation.

**Current verdict: BLOCKED_EVIDENCE; score null pending final reproducible package and immutable publication binding.** No remaining transition defect has been demonstrated in the reviewed C/header. The bounded software checks below pass. This report does not accept a production USB bridge, a shared full-lifecycle policy or the device.

## Reviewed source identities

- `mt_usb_epoch.c`: `c3f70c9937359d263cc78c668bd2a9cd91f8d384d82d958d6587a777b6947159`.
- `mt_usb_epoch.h`: `0846781e5688642fc7ffb59910d3acc2492b3dd9afbef406a4b4671dc2d8be0b`.
- Final Russian `USB_EPOCH_README.md`: `5d17b747b40dacde3e07369fa3c2fa550c6c513883b2ccc09d32513b3f585a1c`.
- Author `run.sh`: `6b83e11b506fadac6e41a21b6f06924afbacd595d9843c4253f5a4872f3e0e37`; `test_epoch.c`: `1b982457f54db9ef17a20eaddbaca70036ac6f0a72e5efa9bcbbb1957f3cd525`.
- Author `arm-check.sh`: `0cb6ababaf9d5651487afd6725474aeefdd20c7488fdfceafd2b7544f5552c1d`; `arm_probe.c`: `35173444a1119024b424d4a89a302b167f631de1d6802c2c7effbfb0ad561bc8`.
- Supporting independent test: `4064fe5801df86fb762ba0b80eaf4622f0e33bca2cc820a23e7a02af30698a93`.

The issue's original scope is metadata only. It expressly excludes TinyUSB hooks, queue/IRQ implementation, target locking and physical timing. None is silently removed from a broader acceptance task here; those later integration requirements remain open.

## Contract and source findings

The implementation assigns nonzero hard epochs and invalidates earlier receipts before recording the new queue outcome. Ordinary permissions require current identity, successful current reset queueing/processing, foreground cleanup, no poison and no pending soft pause; application permission additionally requires current configuration. Old outcomes cannot clear or poison a later epoch. Queue, submission, completion and deconfiguration failures remain sticky until a new real reset. Disconnect alone never supplies a processed-reset receipt.

Soft serials distinguish multiple pauses within a hard epoch; an old acknowledgment cannot clear a newer pause. Reset dispatch can proceed while soft-paused, but ordinary dispatch and core/app I/O remain denied until matching resume. A false ordinary dispatch during soft pause means defer current work, not discard it. The module deliberately does not classify or retain the event itself. Hard exhaustion is permanent for the live object; soft exhaustion poisons only that epoch; diagnostic counters saturate and survive reset.

These properties rely on explicit caller obligations: truthful physical-reset/queue/cleanup/configuration receipts, original operation tokens, serialization of every access, and one guard covering permit-check through DCD action. The API supplies no IRQ masking, memory barrier, atomic primitive, queue envelope, hardware reset detector or physical Stop confirmation. Reinitializing with old tokens/events still alive violates its contract. Permits do not imply TinyUSB readiness, endpoint validity, TIME001 health, DTR policy or successful controller Stop.

Two evidence/documentation issues were identified:

1. **P2, corrected:** the updated README attributed detection of ten injected faults to the reference model. The runner invokes each mutant with `--directed`, bypassing its reference walks. Root corrected the final wording to directed tests; I inspected the delta and verified the final README hash above. A later wording-only amendment links the supplied supporting report and describes its completed clean-directory reproduction; executable inputs are unchanged. The combined-package check must verify that relative link resolves.
2. **Reproduction packaging dependency:** the first supporting runner unconditionally hashed an absolute external research-report path at the end of an otherwise successful run. I reported this to its author and root. The supporting author preserved the first version and supplied the exact research report, issue snapshot and provenance locally in inputs/. Its corrected runner (`e79b31ed4d29a021d1f8ca36f81af829fc2f260a3767fbe09749725e261cec37`) changes only those final-manifest paths. I inspected the delta, confirmed unchanged model/C/header/ABI inputs and reran its complete suite from a fresh directory initially containing only tests/ and inputs/. Every stage passed. This dependency issue is closed for the relocated supporting package; the combined publication package still needs its own integrity check. No firmware correction was involved.

The supporting tester reported the earlier ordinary-dispatch-during-soft-pause risk before source freeze. Root corrected it before my review snapshot. I did not execute or preserve that earlier source and claim no personal pre-fix reproduction. The final behavior and required defer semantics are covered below.

## Personally executed evidence

I read the implementation and header before writing supplemental tests, then inspected the separately authored reference suite. I ran everything in isolated copies without changing the root candidate.

- Author strict C11 and ASan/UBSan tests both pass.
- My `test_api_boundaries.c` and runner pass strict GCC 14.2.0 and ASan/UBSan: four supplemental groups / 1,839 checks. They examine NULL and malformed API values, stale no-mutation behavior, repeated read-only permits, duplicate/late acknowledgments, soft pause across reset processing and explicitly seeded arithmetic limits. These overlap some supporting directed cases and are not claimed as wholly novel coverage.
- I personally reran the supporting suite: 12 directed groups; 2,628,485 model transitions; 86,740,537 checks. Its phase/evidence reference representation differs from the implementation's token-valued receipts. Exhaustive enumeration is bounded to all action prefixes of length at most five over 15 actions from cold/configured origins. The remaining 1,000 reproducible 1,000-step walks use a fixed seed. Extra setup transitions are included in the published total. Near-maximum origins are field-seeded fixtures, not billions of naturally observed resets.
- Supporting strict and combined ASan/UBSan executions pass. Its gcov run reports 74/74 lines, 88/88 branch edges and 14/14 calls exercised. All ten selected safety mutants compile and fail the directed tests. Coverage and those mutations do not prove all possible states or defects.
- A separate GCC `-fanalyzer` compilation of the unchanged source completes without a diagnostic.

LeakSanitizer is disabled. The supporting original unsupported-ptrace failure is retained; no leak check or hidden successful leak run is claimed. The module uses no heap allocation. The model and all tests are serial software executions, not interrupt/concurrency or actual USB tests.

## ARM and resource evidence

I reran the author's Cortex-M3/Thumb `-Os -fno-short-enums` command using the restored official ARM GCC 14.2.1 toolchain. Result: **470 B text, 0 module data/bss, 48 B state**, only unresolved `memset`. Object SHA-256 is exactly **`2b63118f5e871c51c6675a4203f18dbba7f0d16239dbd2715623c1ef90154159`**. The independent ABI probe validates the 48-byte structure, alignment 4 and every field offset. Its separate `-O2` object is 708 B text; different flags are explicitly distinguished rather than combined into one budget.

I also reran the supporting ARM compilation, disassembly/symbol checks and toolchain package-hash verification. These are relocatable objects, not a complete link, ARM execution, stack chain or timing proof. `memset`, caller and interrupt costs remain outside the local object/frame counts.

## Remaining boundaries and gate

The seven pre-existing real TinyUSB/fake-DCD counterexamples remain negative evidence. This module is not connected to those paths and does not fix them by itself. Production ingress-before-queue publication, rejected SETUP accounting, retained-event handling, every DCD submission path, hardware completion identity, critical-section nesting/restoration, time-health integration and ADC/USB latency remain future work. Config0→config1 without a new physical reset is deliberately unsupported by this first policy and is not a new promise about Windows behavior.

The mandatory portable tests specified by issue54 are now available and have been personally reproduced. The supporting-package relocation defect is closed. Final combined-package integrity and immutable source/tree readback are still needed. The quality-gate rubric retains C1–C5 and weights 30/20/25/15/10; all scores remain null until those pending checks are complete. Any eventual scoped conclusion applies only to the deterministic serialized metadata module and cannot transfer to its future production integration, USB001, host or hardware. No merge or MVP readiness claim is made.
