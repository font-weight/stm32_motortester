# Critic-A: separate conditional whole-stack review

## Conclusion

**The conditional calculation is independently verified for the exact frozen `-Os` composition.** I found no demonstrated omitted call target, unaccounted local SP decrement, accumulating call cycle or additional admitted exception nesting within its stated execution profile.

The resulting conservative MSP coverage is:

- foreground, including the persistent main frame: **2640 bytes**;
- one USB IRQ's software frames: **264 bytes**;
- normal foreground plus one USB exception: **2940 bytes**;
- additionally allowing terminal HardFault followed by NMI: **3012 bytes**;
- that last quantity rounded upward to eight bytes: **3016 bytes**.

This is a verified conditional upper-bound calculation, not a measured high-water or a reserve selection. It may inform that subsequent engineering decision. It does not change the completed BOOT004 source verdict or supply whole-device acceptance. Formal project status remains **BLOCKED_EVIDENCE; score null** pending the required final-image/board/physical and immutable evidence gates. No production or remote state was changed.

## Exact subject

Supporting directory: `/workspace/shared/device-stack-bound`.

- `REPORT_RU.md`: `33a27d1ce2b9d9361d1bf95eeaea14764f63c99a07743cb7161aa8eee58a954c`
- `ARTIFACTS_SHA256.json`: `c85b25382a2e79a2c588d1fc10cf1092f364e902d9f298165a9fd667fed85aaf`
- `INPUTS_SHA256.json`: `a5086666280e0ea07468190a35e835733e1b07a68b49edff1d9014a11475e521`
- linked ELF: `0450e7ee104e19d6a1c018c2f6d1bdcb6f4b74544063044e14c41c01b586c2ae`
- main C: `f42d80a8c5c999819252d71d9cda9ce8797db3c22cd59e9dfeac7f55f8bcdc9b`
- CPU C: `4640364e1fdd44cd224552547f164e3bf4838556c6623d866cfbb02b8ae98b95`
- application config: `341fd5a09fe5594c9e6dbee0c14427c485476e58bd8efb640091a717dd657c9e`

The ELF is the expressly invalid all-zero-board static-placement fixture under `/workspace/shared/device-main-arm/run-r2`. Its bytes also equal the full ARM rebuild I personally performed during BOOT004 source review at `device-main-arm/critic-a-replay-r1`. I did not repeat that unchanged full compilation merely to analyze the stack. The invalid board's values were never used to prune initialization, runtime or USB branches. No LTO or section-GC inference from the invalid board is credited.

I verified all 344 supporting input hashes and all 28 artifact entries before completing this review. The replay scripts were copied byte-for-byte into my own `replay/` directory, and all replay output was generated there. `final-input-check.json` records final preservation and equality checks.

## Independent reconstruction

`audit_independent.py` is my separate subject-specific checker, not an alteration of the supporting implementation. It reconstructs the graph and SP calculation from the frozen ELF, map and disassembly. It uses the explicitly source-reviewed callback mapping as an input; it does not pretend to infer arbitrary C function-pointer targets automatically.

Checks completed:

1. Every linked executable byte is accounted for by text input ranges, declared rodata or zero linker padding. Every decoded instruction/data encoding in the text inputs was checked against actual ELF bytes. The independent parser handles wrapped linker-map entries and `.byte` switch data.
2. There are **363 text input functions**, **11,773 decoded instructions**, and 360 unique final `STT_FUNC` addresses. The three additional inputs are retained, overridden weak fallback bodies for `dcd_deinit`, `dcd_edpt_xfer_fifo` and `dcd_edpt0_status_complete`; they are not silently omitted. Duplicate local function names remain address-distinct. All real function symbols fit their containing input range and have Thumb addresses.
3. Direct transfers include `BL`, `B`, all observed conditional branches, and `CBZ/CBNZ`. Every interfunction target is a function entry. The resulting edges exactly equal the supporting graph; the additional CBZ/CBNZ examination found no missing cross-function edge.
4. The **nine switch tables** were discovered from actual instruction forms. Their counts and bases were reconstructed from the adjacent compare/unsigned-high branch and PC-relative instructions, then their entries were decoded from ELF bytes. Every target is an instruction in the same function. An additional graph check removed each bounds-check fallthrough edge and proved its dispatch then unreachable; this checks the guard's dominance, not just its nearby appearance.
5. Push/pop, SP add/sub, SP-based multiple-register writeback and post-index return effects were propagated through each local machine CFG. Every reachable instruction has a consistent SP depth, all returns/tail transfers restore entry SP, and each local maximum equals the corresponding static `.su` value. No accumulating SP cycle was found.
6. All **236 instructions covered by IT blocks** were checked for hidden conditional SP or control-transfer effects. No unclassified stack switching, PC assignment, stack writeback or SVC exists in this subject. PRIMASK is the sole MSR destination. No register-list transfer loads SP, and no below-SP predecrement store was omitted.
7. All **53 indirect sites** are accounted for. Independent topological longest-path evaluation over machine-derived local frames completes for all 363 nodes. No recursive component remains after the documented, source-supported exclusions. Tail calls deliberately retain the caller's full frame in this calculation, yielding a conservative sum.

The independent result is `independent-machine-result.json`; the successful run is `independent-machine-r4.log`. Early checker-development attempts rejected incomplete handling of wrapped map lines, byte table entries and a post-index PC return. Those failure logs remain present and carry no success credit. They were checker construction errors, not production defects.

## Callback completeness and exclusions

I reviewed all 53 site contexts against the immutable sources and code-pointer relocations, including structure offsets and the actual initializer/copy chain:

- main supplies the sole MMIO read/write implementation and IRQ guard enter/leave;
- runtime preserves those bindings and supplies ADC read/write/time, the three commit callbacks, real TIME now-ms, and CDC `stable`;
- ADC-control installs exactly profile/start/stop into control;
- the TX writer is the actual macro-selected `mt_usb_cdc_write_impl`, confirmed by its address relocation, not a hypothetical extra wrapper call;
- OS_NONE queue/spin callbacks are `usbd_int_set`;
- the only built-in class is CDC and the actual weak application-driver callback returns NULL/count zero;
- EP0 completion assignments are NULL, CDC control or the actual weak vendor control callback. Test-mode code is excluded by the exact compile profile.

The CDC table has the six expected function relocations and null `sof`/`xfer_isr` slots. The two conditional indirect sites using those null slots cannot execute in this profile. The deferred-function site is separately excluded because the sole `USBD_EVENT_FUNC_CALL` producer, `usbd_defer_func`, has no caller or address-taking relocation and is unreachable from actual roots. Actual DCD event constructors and core SOF/resume producers use fixed event tags 1–7; the queue does not obtain an arbitrary callback from host payload data under the assumed memory-safe execution. The existing code-pointer relocation inventory contains only ABS32 literals/tables plus direct-call/tail relocations; no separate MOVW/MOVT function-address construction was missed.

These are whole-composition source arguments, not a general claim that every public TinyUSB API can safely accept arbitrary callbacks. Adding a deferred producer, class, strong callback override, pointer mutation or external caller invalidates this closure and requires a new analysis. The phrase “all graph components are acyclic” applies to the resolved, qualified graph, not arbitrary future API uses.

## Exception model and arithmetic

I verified actual vectors 2/3 both target the assembly `Default_Handler`; vectors 35/36 target the strong USB wrappers. Default_Handler is CPSID plus a nonreturning branch and has no software frame. Reset uses MSP, sets STKALIGN and enters main without a local stack allocation.

Main's final admission requires exactly IRQ19/20 enabled and equal implemented priorities. The F103 FSDEV enable/disable helpers only change those two IRQ enables; runtime adds no other enabled interrupt or priority writer. The IRQ wrapper guards the complete DCD call and restores its saved mask. Equal-priority USB handlers cannot preempt each other even in the short interval before masking or after restoration. Repeated pending IRQs therefore do not accumulate software frames. SVC/PendSV/SysTick and configurable faults remain outside the reset-only admitted profile; no code in the subject activates them.

For the hardware model, Cortex-M3 exception entry saves eight words, with an additional alignment word allowed here. Equal-priority exceptions wait; HardFault and NMI have fixed higher priorities. Late arrival/tail chaining do not add another unbounded layer. Fault recurrence inside terminal NMI/HardFault leads to lockup rather than a software recursion claim. These semantics were checked against [ST PM0056 Rev 7, §§2.3.5–2.3.7, 2.4.4 and CCR.STKALIGN](https://www.st.com/resource/en/programming_manual/pm0056-stm32f10xxx20xxx21xxxl1xxxx-cortexm3-programming-manual-stmicroelectronics.pdf).

Thus 2640 + 264 + 36 = 2940, and 2940 + 36 + 36 = 3012. The 4-byte alignment allowance is retained for each terminal entry even where alignment would make it unnecessary. The startup bound, including main, is 288 and remains below the post-admission bound. IRQ cost is added even at foreground points where PRIMASK might actually block it. No mask/path feasibility optimization is used to lower the result.

The longest foreground sum is:

`main88 → runtime16 → CDC wrapper24 → CDC impl40 → RX48 → control640 → capture Stop1696 → TX Stop64 → append16 → memcpy8 = 2640`.

The longest IRQ sum is:

`IRQ wrapper0 → dispatch8 → DCD72 → event48 → queue48 → FIFO64 → hwfifo16 → memcpy8 = 264`.

The generic hardware-FIFO branch is retained even though the actual queue uses normal access mode. The small memory primitive implementations and all compiler/library dependencies are within the linked graph; final `nm -u` is empty and no libc/libgcc, allocator or hidden helper is linked.

## Conditions that must accompany any later use

- Exact target/compiler/options, runtime callback bindings, permitted API use, startup and exception profile remain fixed; the eventual valid-board image must be checked again and bound to its own identity.
- The estimate assumes intact SP, code/data/function pointers and valid accesses. It is not a memory-safety proof or coverage of hardware stacking failure, corrupt-state execution or arbitrary debugger modification.
- No PSP, RTOS task, extra IRQ, custom fault handler, dynamic stack, recursion, LTO or new external callback is covered.
- This provides no hardware timing, ISR latency, stack-canary measurement, real board fit, USB enumeration or fault-recovery demonstration.
- 3016 is rounded mathematical coverage, with no separately chosen engineering margin. The prior test-only 4096 linker parameter was not an input to the derivation.
- Both analysis scripts are exact-subject local tools with absolute pinned inputs. They are not a qualified generic disassembler, configurable arbitrary-image prover or portable publication package.

## Reproduction

All writes are confined to this review directory. With the pinned inputs present:

```sh
python3 -I -B /workspace/shared/critic-a-device-stack-review/replay/replay.py
python3 -I -B /workspace/shared/critic-a-device-stack-review/audit_independent.py
```

The first command replays unchanged supporting logic. The second runs the independent machine/graph calculation. `REVIEW_MANIFEST.json` binds this report and the selected review artifacts. The separate BOOT004 source report is unchanged.
