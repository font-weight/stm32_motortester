# Critic-A: TIME-001 bounded TIM2 clock review

Date: 2026-10-10. Scope: issue [#40](https://github.com/font-weight/stm32_motortester/issues/40), the isolated TIM2 timebase module, its caller contract, Russian documentation and executable evidence.

**Verdict: BLOCKED_EVIDENCE; score: null.** No additional in-contract implementation defect was demonstrated in this frozen local candidate. The author tests, separately authored supporting tests and my supplemental oracle pass the executions recorded below. This is not shared-contract acceptance, hardware acceptance, approval to merge or a complete-firmware timing/resource claim. An immutable publication binding is still required, and the application health integration and physical evidence remain open.

## Exact reviewed candidate

All seven source/document/test/script inputs are recorded with SHA-256, Git blob identity and byte length in `source-manifest.json`. I copied them into an isolated snapshot and verified that the original draft still matched after testing.

| Input | SHA-256 |
| --- | --- |
| `firmware/platform/mt_timebase.c` | `b403cc8f5e60cc2568b59d9fc5de6cf2cf1f116c03291876e7a05a5dc19f2740` |
| `firmware/platform/mt_timebase.h` | `613135965695f6e1eb4e24edea9e2873a33b60ee4b44865a5db1ef1730b5b3d2` |
| `firmware/platform/TIMEBASE_README.md` | `9882be7c3de2a3f9c48ee3804f5d0919fda6977ad9dc35ef4afc5379a73b0786` |
| `tests/timebase/test_timebase.c` | `264ccbb8aa85ddd2cd1d798e9c8e33f7e1d5e4317ebce363f4d908307f382f52` |

No production implementation was edited by this reviewer. No published commit is inferred from these local hashes.

## Source and contract findings

The implementation uses a 16-bit counter difference, then independently carries the microsecond remainder into modulo-32 millisecond state. It does not derive milliseconds from an already wrapped `us32`. The configured PSC=71, ARR=65535 and initial UG are consistent with a caller-verified nominal 72 MHz TIM2 input. The relevant hardware description and preload semantics were checked against [RM0008 Rev21, §§15.1, 15.3.1 and 15.4.10–12](https://www.st.com/resource/en/reference_manual/rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf); the 16-bit device scope was also checked against [DS5319, Table4 and general-purpose timers](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf).

The accepted contract requires zero-initialized, nonoverlapping storage; foreground-only, nonreentrant use; startup-established clock/reset state; exclusive TIM2 and internal trigger/compare consumers; and actual service between counter snapshots. The 72 MHz argument is an assertion supplied by startup, not a measurement or a decoded proof of crystal frequency. The selected RCC signatures detect relevant observed changes but cannot prove actual elapsed time during hidden faults.

New timestamps commit only after the pre/post observations and observed-delta check succeed. Detected faults retain the previous complete accumulator state and ownership. Busy/owned reinitialization is refused. Successful Stop requires gate/reset observations before and after the CEN write/readback. The previously reported Stop readback race is corrected in these bytes; I reviewed and reproduced the final regression behavior, without claiming a personal execution of an unavailable earlier TIME-001 snapshot.

There is no demonstrated new P0/P1 defect inside those conditions. The following are material, explicit limitations rather than silently assumed guarantees:

- A complete missed 65536-tick revolution can alias a small delta. The `<=32000` observed threshold cannot establish the actual service interval. Stuck counters, plausible external CNT changes and completely hidden clock transients can also evade detection.
- `healthy()` reports latched state. The callbacks return last-good values after fault; those values are invalid for continued application operation. This module does not itself stop ADC/controller activity or withhold TX.
- Sleep and debug-halt continuity are outside the contract. The documented debug-register caveat is supported by [ES096 Rev15, §2.2.2](https://www.st.com/resource/en/errata_sheet/es096-stm32f101x8b-stm32f102x8b-and-stm32f103x8b-mediumdensity-device-limitations-stmicroelectronics.pdf).
- The MMIO callback interface and register model do not prove physical write reliability, MCU revision, startup correctness, oscillator accuracy or bus timing.

## Executable evidence personally reproduced

Host compiler: GCC 14.2.0 on Linux x86_64. Strict C11 warnings are errors. Native and ASan/UBSan executions are separate; LeakSanitizer is disabled and no leak-check result is claimed.

1. **Author suite:** `snapshot/tests/timebase/run.sh`, strict `-O2` and ASan/UBSan. Both completed successfully. Log: `author.log`.
2. **My supplemental oracle:** `independent_timebase.c`, SHA-256 `b7de9f435411a01bdace225d732ba2359f3715ada69a1b94e350f3d13f6f2c14`. Both executions passed **462,147 scenarios and 32,222,386 checks**. Log: `independent.log`.
   - Exhausts every observed delta from 0 through 65535 at four coherent epochs, including us32 and ms32 rollover boundaries; checks acceptance through 32000 and atomic rejection above it.
   - Uses a separate PSC preload/active-divider model, UG load and input-cycle phase; verifies the 71+1 cycle boundary.
   - Advances the counter during individual MMIO reads across 200,000 mixed callback operations. The oracle compares the returned state to the actual CNT snapshot, allowing later hardware progress to be included by the next update.
   - Checks coherent remainder carry and preservation of the last-good epoch on a fault introduced at CNT read. Long ms32 edge states are seeded consistently from a uint64 history; this is not a 49-day physical run.
   - This supplemental harness was written separately after source/author-test review. It is not represented as a blind test or complete MCU emulator.
3. **Supporting independent suite:** I read and personally reran the frozen test `630b3e6b002b22fd8bfcf616d08421a5224b911dd8f745143f3076e53db230af`. All **16 groups** passed strict and ASan/UBSan; GCC `-fanalyzer` was clean. Log: `supporting-rerun.log`; supporting report: `REPORT-ru.md` in the independent test evidence.
   - Exact 13-write ordering and unrelated-register preservation; 646 monitored-bit injections; initial failures; ownership/reinit refusal; Stop gate/reset races and retries.
   - A 150,000-update sequence crosses us32 wrap without epoch seeding; separate 140,000-interval us-only and ms-only runs also cross that boundary. Mixed callbacks and coherent seeded ms32 boundaries are covered.
   - Deliberately violated-contract tests demonstrate missed-wrap/stuck/transient ambiguity. Their successful update results are not mistaken for fault detection.

## ARM resource evidence

I personally reran `snapshot/tests/timebase/arm-check.sh` with ARM GCC 14.2.1, Cortex-M3/Thumb, `-Os`, freestanding and explicit `-fno-short-enums`. Results match the author evidence:

- 1008 bytes text; no module data/bss; context 52 bytes.
- Only unresolved runtime symbol: `memset`.
- Object SHA-256: `7219ce425cb16a8ec91af9e6ba17d1a49cddb5d7040e935e5bb21cb3119f02c1`.
- Reported static frames include update/init/Stop at 16 bytes and callback entry at 8 bytes. These are individual compiler frames, not a complete caller/MMIO/IRQ stack bound.

Log: `arm.log`. Object reproducibility does not establish a complete firmware ELF/map, stack margin or MCU execution time. A normal update performs 27 MMIO reads. Hardware tick resolution is not callback WCET; the several callbacks inside IO-001's 20-unit correlation bracket have not been timed on the target.

## Remaining gates and reproduction

Before integrated use, establish one common clock context for all RX/controller/ADC consumers; health checks before/after each time-using operation and before output; fail-closed cleanup with dependent epochs/deadlines invalidated; service in idle/backpressure/stopped states; actual ADC service below its separate 2560 µs deadline; and a measured complete-path timing/stack budget. No startup, volatile-MMIO adapter, USB, physical frequency or hardware test result is supplied here. Existing shared Stop scheduling ambiguity CA-CTRL-4 is unrelated and is not resolved by this module.

Reproduce from this review artifact's directory: `sh run_independent.sh`; author tests from the snapshot with `sh tests/timebase/run.sh`; ARM with `TC` set to the toolchain's `usr` directory and `sh tests/timebase/arm-check.sh`. The supporting runner's final comparison references its original workspace draft, as disclosed in its report; publication may adapt only source/output paths while preserving the tested logic and recording the adaptation.

The local review is complete. Publication must preserve these source bytes, read back the immutable commit and attach an explicit binding addendum. The verdict remains **BLOCKED_EVIDENCE, score null** until the applicable gates are actually met.
