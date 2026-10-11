# Critic-A local review: ADC003 final-start admission

Repository `font-weight/stm32_motortester`, [issue62](https://github.com/font-weight/stm32_motortester/issues/62). Date: 2026-10-10 UTC. Production author: Dot A. Reviewer: the existing independent Critic-A, with no production edits.

## Conclusion and exact subject

No remaining demonstrated in-scope implementation defect was found. The frozen candidate passes my personal source review, native/sanitizer regression reruns, negative controls, supplemental boundary assertions and ARM-object checks. **Local verdict: BLOCKED_EVIDENCE; score: null.** Package and immutable candidate commit/base/tree binding have not occurred. No external publication is authorized by this report.

The issue's baseline is PR61 `7a721f82b98676d6fd9ab1b58a1d9b7d617b47a5`, tree `0d3f1acec1750cc0c3c9102d56fa211ce7729ead`. Its original ADC/control inputs were checked against the independently fetched full base tree and existing blob identities. Any eventual stack base or package change needs a fresh binding. USB005's draft actor semantics are a dependency, not a substitute for the missing real owner/guard backend.

Frozen SHA-256 values:

| File | SHA-256 |
|---|---|
| `firmware/platform/adc_timer_dma.c` | `fc0edee4a0948047c76513f598580c419353752374f91c45d79bce2b2e724ba4` |
| corresponding header | `930bb13278dfaf422fc78d453a9796977496f3e0e6393d7e8b3cf17268b0da3d` |
| `firmware/app/mt_adc_control.c` | `b771d00bf6fac333fac326b82f1e120a1ef4e582e8583e0974956e969f127620` |
| corresponding header | `a69143a2b432ac7403fe2125066bbd4e999e768d0d109c469a1739ed8b4c0200` |
| root README | `692a275d8e33c5ba5d72bdff4031b80bac0b7bf338fb24aec85b31072b374b4f` |
| final root ARM runner | `03a0778839f6ffdf83a3f409759c0e3cacfd348ee458d595e4ab7f9b17d508b7` |
| supporting `test_admission.c` | `8eeeb676d26d9c247600db6280f94c538462061d3bbe3b54267918b10b9db90c` |
| supporting runner | `d24ebf7a24e46e37c55c00ba1d9c97f7acd0293d00543f085a32929f106d8474` |

## Scope and source findings

The bounded result is an optional final-commit admission guard after preparation/calibration, with existing unguarded APIs retained for isolated use. It includes forwarding a copied guard definition through the ADC/controller binding. It does not implement USB/clock admission callbacks, a PRIMASK backend, or a full owner loop.

I read all four production files and the exact baseline delta before relying on the supporting test outcomes. The ordinary driver path preserves its prior register/callback ordering. Preparation, stabilization and calibration remain before `enter`; pre-guard failures cannot call an unmatched `leave`. A valid guarded commit obtains the previous mask, observes time, checks admission, then enables DMA and ADC trigger, verifies clock/gates, writes TIM3 CEN, observes time again, checks admission again and verifies readbacks within the same guard. All exits after entry restore the saved mask before potentially polling cleanup.

A denied admission cannot perform any of the three final enable/start writes. A post-start admission/readback/time failure performs existing Stop cleanup after `leave`. Failed cleanup retains `driver.owns_peripherals` and live storage; later start remains busy until a successful Stop. In a failed initial start, ownership must be read from the driver and actual Stop outcome, not inferred from a controller flag which was never set by a successful start. The controller's recovery always retries Stop, including after failed start.

The binding copies the guard definition, and its original initializer explicitly remains unguarded. Callback context lifetime and non-overlap remain caller obligations. `allowed` must inspect the original command actor and fresh time health; the optional API cannot prevent an incorrectly implemented callback from laundering the current token. All callbacks are prompt/nonreentrant. Restoring previous mask1 does not unmask an outer critical section. A pending IRQ can run after `leave`; successful return proves only the commit's admission, not that the epoch is still current when the caller resumes.

Guarded `start_before_us` is now the lower edge of the measured final-commit interval, earlier than the CEN write; the README expressly distinguishes it from a measured aperture or exact CEN timestamp. The measured interval still has the 20us bound. No target worst-case duration is established. The appended internal ADMISSION fault does not change existing enum values or the wire contract.

## Personal executable verification

Environment: Python3.12.14, native GCC14.2.0, ARM GCC14.2.1, Cortex-M3/Thumb/soft-float/full-size enums. Native strict flags include C11, pedantic-errors, Wall/Wextra/Werror/Wconversion/Wshadow. ASan+UBSan use halt-on-error; LSan is disabled and is not claimed.

I independently copied the frozen source/harness, then ran:

1. Candidate: unchanged legacy driver suite, unchanged real ADC/controller/window/CRC/clock suite, and all 65 new admission scenarios, strict and ASan+UBSan.
2. Exact PR61 baseline: both unchanged legacy suites, strict and ASan+UBSan.
3. All three supporting negative controls in separate mutant copies. Missing first admission, missing second admission and cleanup before leave each pass the old regressions and are then rejected by the new admission assertions. Original production inputs remain unchanged; these deliberately failing binaries are controls, not production defects.
4. Complete candidate and baseline driver/binding ARM objects, with final author runner. All four objects exactly match root r2 and have no warnings.

The 65 new cases include invalidation before/during preparation, before guard, first fresh clock health failure, all three enable locations receiving a pending IRQ, mask0/1 restoration, post-clock failure, RCC change/readback/drop failures, too-wide/reversed time, failed Stop ownership/retry, preparation errors, invalid callbacks and the actual controller binding. The old tests were not edited to accommodate the new API.

Receipts retain compiler argv, sanitizer environment, outputs and before/after source hashes. Native input preservation passed in every successful or expected-failure run. The real controller, acquisition, session/command and codec/TX implementations are linked; hardware registers, time and interrupts are models. This evidence does not execute real target interrupt masking.

Reproduction with the selected frozen supporting harness:

```sh
python3 path/to/supporting/run.py --base /exact/pr61-inputs --candidate /frozen/candidate --out /fresh/native
python3 path/to/supporting/run.py --base /exact/pr61-inputs --candidate /frozen/candidate --out /fresh/sanitized --sanitize
python3 path/to/supporting/run.py --base /exact/pr61-inputs --candidate /exact/pr61-inputs --out /fresh/baseline --legacy-only
python3 path/to/supporting/negative_controls.py --base /exact/pr61-inputs --candidate /frozen/candidate --out /fresh/mutants
```

Repeat the baseline command with a new output and `--sanitize`. All output paths must be fresh and disjoint from inputs. Immutable blob checks in the supporting runner bind legacy tests and dependency inputs.

### Supplemental Critic-A timing cases

My `test_extra.c` and `run_extra.py` passed 20 cases under both native strict and ASan+UBSan: exact reported commit spans0/1/19/20 are accepted, 21 is rejected, each under mask0/1 and ordinary/wrapping microsecond clocks. Returned ownership, fault and restored mask are asserted. These checks reuse the supporting MMIO/guard fixture; its `main` is renamed in a generated copy, with no assertion removed. They are supplemental tests, not a second independent hardware model. A zero delta is a valid sampled-clock boundary in this fixture; it is not proof that a frozen real timebase is healthy.

```sh
python3 path/to/critic/run_extra.py --base-run /fresh/supporting-run --out /fresh/extra-run
```

Use the sanitized supporting receipt to repeat with sanitizers. The runner retains the exact compiler commands and verifies the original snapshot remains unchanged.

### ARM resources and ABI

Personally reproduced object SHA-256 values:

- Candidate driver: `f408e437bbd58f533d29261fd70382ccd2e3f41a24f36dd31666315461cf570b`
- Baseline driver: `0d198c531aa24d6a4aa2227e7875b3ea54cdef6b257f67cdff78d04cf8d72cc4`
- Candidate binding: `919df4e8001b1b37eda1d0b5e0270859ffd4b0b841ef620b4049d57969da6f16`
- Baseline binding: `41c5474a01da75c4c5d62313c0bc3b9f76f97cdeb2324c861f4411defa59aa40`

Driver text/rodata2476→2598 bytes; binding438→548. These are relocatable object sections, not firmware size. I inspected the guarded start branches and indirect callback sequence, including common leave before failure/cleanup. Object code is not an executed PRIMASK backend.

My additional Cortex-M3 ABI probe confirms driver context860 bytes unchanged, binding24→44 bytes (+20), guard definition16 bytes. Probe source/runner and receipt preserve exact commands; no executable target image is claimed. Candidate local frame maxima are driver read-block40 bytes, driver start24, binding service56, binding init40. These are individual frames, not transitive chains or IRQ reserve. `memset` remains unresolved; binding additionally calls the optional guarded start. No final link, startup, descriptors, linker map or complete RAM/Flash/stack result is provided.

## Documentation/tooling disposition and rubric

The final README accurately records prior-mask restoration, potentially waiting cleanup outside this guard, the post-leave IRQ window, the earlier nominal timestamp edge and all missing integration pieces. The final ARM runner checks output/source separation and required inputs before mkdir; it preserves its earlier version and root refusal evidence. I inspected that code and personally used the final runner; I do not claim to have independently rerun root's six path-refusal cases.

There are no new demonstrated P0/P1 or unresolved source findings in this local review. C1 correctness and C2 failure handling are supported by the source/delta and admission/readback/Stop tests; C3 by personal strict/sanitizer/baseline/negative/ARM reruns; C4 by the bounded optional API and explicit ownership contract; C5 by frozen hashes and recorded commands. A numeric aggregate is withheld until a final reproducible package and exact immutable candidate identity are independently checked. Test counts do not produce a score.

Still open: actual USB/clock admission implementation, original-actor propagation by the full foreground owner, prompt and correct target PRIMASK save/restore, vector/priority/latency, physical ADC/USB race behavior, measured sampling aperture, linked resources and device-level validation. Legacy unguarded APIs do not close integrated transport races. This report authorizes no external publication, merge, shared policy acceptance, software-ready release or physical operation.
