# Critic-A: TX002 retained-ABORT stack reduction, local review

Repository `font-weight/stm32_motortester`, issue 70. Critic-A is independent of the production author and made no production edits. Date: 2026-10-10 UTC.

## Current decision

**BLOCKED_EVIDENCE; score: null.** No remaining demonstrated in-scope production defect has been found. The source, native equivalence/oracle checks and ARM resource comparison below pass. The explicitly promised immutable app/control regression suites have now also been restored, rebound and personally rerun successfully. Only final package/base/immutable-head binding remains absent. Runtime integration is additional evidence, not a substitute for those named regressions.

This is a local version review. No candidate Git SHA or final package base is asserted. The prior TX implementation is pinned to PR19 `826bef255362b258d5dcde2100c90dc7d591edb3`; the change does not alter the frozen USB005 package now published separately as PR72.

## Exact subject

| Input | SHA-256 |
|---|---|
| Original `mt_tx.c` | `e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e` |
| Candidate `mt_tx.c` | `b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf` |
| Unchanged `mt_tx.h` | `68f013a8ffb6e946e9cd92f3e760bf593746afdc7097d11d837a101c5ab83dd9` |
| Root task README | `7a092e16000c372a706f8ce604fe97c886923cfdabcb4c36491304e48c08c976` |
| Optional component-link recipe | `b2dcdfa79cc8501275c3773cb4456ef6b7ed2477a3259237a9be4dc5d0f8aa41` |

The only production change is local temporary storage and reconstruction of a previously queued, unstarted matching ABORT in `mt_tx_stop`. ABI, queue capacity, protocol constants, timeout logic and headers are unchanged. The optional `--tx-stop` recipe is a separate validation extension over the earlier memory/descriptor closure; those local component dependencies are hash-bound evidence, not implicitly part of USB005 PR72.

## Independent source reasoning

- The old stack-local full message was normalized to session/sequence zero before shape validation and then appended with the active session. The new path selects the same type/current-session/generation/window, explicitly checks length 12 / request 0, and copies all 12 payload bytes before any queue mutation.
- Capacity and argument checks still precede compaction. Rejected calls leave the entire TX object unchanged. A pointer into the old queue is not carried across compaction; the final slot is reconstructed only after preflight succeeds.
- Reconstruction zeros sequence/request, supplies the current session, type 147 and length 12, preserves the complete payload including the existing failure reason and sets `reply=0`. A speculative sequence at zero acceptance is discarded rather than consumed.
- Existing partial head and earlier replies retain order. Committed END/ABORT suppresses a duplicate terminal. NULL supplied ABORT remains allowed when an eligible queued ABORT exists, or no terminal is needed. Old-session partial frames and the session-specific STOP_ACK completion check are untouched.
- Clearing unused successful-message tail/padding is observable only through private storage, not encoded wire data or documented API semantics. Success comparison must use meaningful fields and actual transmitted bytes; rejection comparison remains byte-for-byte. The tests explicitly preserve this distinction.

## Personally executed native evidence

GCC 14.2.0, strict C11 with conversion/shadow warnings treated as errors; ASan+UBSan in the sanitizer variants. Leak detection is disabled because of the execution environment's ptrace limitation. No LSan, MCU, timing or physical USB execution is claimed.

I copied the supporting harness without changing its assertions and ran:

- The unchanged original 14 TX suites on both original and candidate, strict and sanitized, plus the original shell runner. Original Git blob identities are verified before output creation.
- Eleven differential groups: 11,580 scenarios  / 480,361 operations per build. These comprise 11,577 API-created transport scenarios and three explicitly synthetic private-state defensive controls. Included are 1024 opaque payload representation probes, 24 legal-reason/reserved-zero cases, partial heads, staged zero acceptance, committed terminals, full reserved queue, byte-atomic refusal, deadlines/wrap,10,000 deterministic action sequences and 93 old-session cases. Counts are bounded coverage, not exhaustive equivalence.
- All 10 isolated compile-valid mutants were detected by assertions, not compiler failure. Production inputs remain unchanged.
- All 19 native runner checks pass, including unavailable compiler before mkdir, toolchain identity, source/evidence/script aliases, fresh-output separation, hostile environment and clean relocation.
- All 28 existing runtime integration scenarios pass in each strict/sanitized build with the candidate TX proven present in the compiled snapshot. This executes real runtime/controller/RX/acquisition/TIME/ADC/USB modules against modeled MMIO/DCD/critical sections, including full 2048-code capture/END CRC, Release replay, Stop, backpressure and short suspend/resume.

Personal receipts are `tx002-critic-native-{strict,sanitize}`, `tx002-critic-mutations`, `tx002-critic-native-paths` and `tx002-critic-runtime-{strict,sanitize}`. The supporting native runner is SHA-256 `dca6f13abdeef04c58fc688eabb81d2c52edcd5445541e318ba43ee140eb65bc`.

### Restored immutable app/control regressions

I independently fetched the complete immutable PR30 and PR35 Git trees, recomputed the 47 restored files' SHA-256 and Git blob identities, and verified every path against those trees. I then copied and personally executed the supplemental runner unchanged, SHA-256 `080d8e4d4e2b3118a8600d9e993d78daa9f6842655faa4d1bcb60fea0159d67a`.

- PR30 `a451e5a491439fcfed31f298a589643cf7050ec9`, tree `bd13f4768a93d0f3da0546846868a40ba79ae66d`: original author strict/sanitized tests and independent 24-group strict/sanitized suite, including 44 complete windows.
- PR35 `0ddea1e5992caa6231203838c3956e19675867a8`, tree `8b6bc0e66e2c06ef17c632dd9bd28ec21748e17f`: original author strict/sanitized tests, independent 39-group strict/sanitized suite with 576,994 checks, independent wire/sample oracle for 344 frames / 20 windows / 40,960 samples, and both unchanged tombstone-hardware and stale-Stop-capacity reproducers in each build.

Every suite ran on both original and candidate TX. All test scripts, source dependencies and fixtures remain byte-identical to the pinned commits, except the candidate snapshots' sole `firmware/transport/mt_tx.c` overlay. No later controller/runtime source was substituted into these historical regression snapshots. Input/toolchain hashes remain unchanged. This closes a real omission in the initial evidence list rather than silently treating the 28 runtime cases as equivalent coverage.

Personal evidence: `pinned-live-binding.json`, `pinned-live-trees.json`, and `tx002-critic-pinned-regressions/receipt.json` plus the actual command logs. Supporting provenance SHA-256: `098ff9805ade5cf38560af4303284ffff38cd0e3ac977057fd64b7c36fcd0623`.

### Supplemental independent oracle

My separately written `test_retained_abort.c`, built by `run_oracle.py`, passes 12,288 queue-compaction cases and 4,359,168 checks in strict and ASan/UBSan builds. The same oracle passes unchanged on the original implementation.

Three API-reachable layouts force original ABORT storage to be overwritten by earlier replies during compaction. The sweep varies all four opaque retained bytes through 256 patterns, positive FIFO chunk sizes 1/2/7/64, an optional staged zero-accepted frame and NULL versus supplied fallback. It verifies complete decoded COBS messages, exact session/sequence/request/order/payload, ACK barrier completion and whole-object atomicity for rejected/repeated Stop. Arbitrary reason/reserved patterns here test transport representation only; they are not declared valid protocol payloads. The supporting legal-reason cases cover the six documented reasons separately.

Receipts: `tx002-critic-oracle-{strict,sanitized}-final/receipt.json`, and `tx002-critic-oracle-old/receipt.json`. Initial successful runs precede a reviewer-runner-only addition protecting compiler input parents; their evidence remains distinct.

## Personally executed ARM and optional-link evidence

I copied the frozen supporting ARM runner and reproduced its complete comparison, including its 11 negative and two positive no-write preflights. All generated objects and the selected chain exactly match supporting r3. The runner binds 404 source/object/header/tool inputs before/after, checks actual GCC-selected cc1/as, and compiles under a clean environment. The old individual TX object and 25-object aggregate reproduce the original bytes; the new links replace only that individual TX object, never link both versions or a precombined aggregate.

Toolchain: ARM GCC 14.2.1, Cortex-M3/Thumb, C11, `-Os`, freestanding, full enum ABI, stack-usage output and strict warnings. Both TX objects are warning-free. The full inherited closure retains its seven disclosed FSDEV conversion warnings.

| Quantity | Original | Candidate |
|---|---:|---:|
| `mt_tx_stop` local compiler frame |592 B|64 B|
| TX text/rodata |1812 B|1932 B|
|25-unit pre-GC text/rodata |27903 B|28023 B|
|25-unit data/bss |122/986 B|122/986 B|

The measured local reduction is 528 B; code grows 120 B. Prologues independently show 36 B saved registers plus 556 B allocation becoming 36+28. The payload-size arithmetic alone was not used as a stack measurement. Public symbol definitions, undefined-symbol set, AEABI attributes, structure sizes/alignments/offsets and other TX local frames remain equal. `sizeof(mt_tx)=3920`, `mt_tx_slot=552`, `mt_wire_message=544`, `mt_tx_event=40`; enum sizes 4.

Exact new TX object: `e014288dc2d8ecdca6d17f9c14de74814c144ce4af7513c97befe53b4741dfb1`.
Exact new 25-unit aggregate: `39b9b0a725eee442ca543c39b5b700ec13dc41d0f260db02485093312dbbe3fe`.

The selected actual direct-BL chain is runtime_step 16 → usb_cdc_step 24 → usb_cdc_step_impl 40 → control_rx_feed 48 → control_receive 640 → capture_link_stop 1696 → tx_stop 592/64 → append 16 → memcpy 8. Its known subtotal is 3080→2,552 B. I checked the saved direct-call/relocation evidence and TX prologues. This is not a complete maximum stack bound or a measured watermark. It still exceeds 2 KiB by 504 B before caller/main, exceptions and IRQ costs. Sequential decoder and dispatcher frames are not summed as nested.

Separately my `check_overlay.py` executed 12 full component builds: old recipe, new recipe with overlay off, and new recipe with overlay on, in each of four descriptor/memory combinations. Every overlay-off individual object and aggregate equals the old recipe. Every overlay-on comparison changes only TX and aggregate; exact expected external-symbol sets are preserved. With memory+descriptors the sole pre-GC external remains `tusb_time_millis_api`; other combinations retain their expected descriptor/libc boundary. A later real entrypoint and section-GC link may remove unreachable externals, so this is not an assertion that every pre-GC external must be runtime-live.

Personal ARM receipt: `tx002-critic-arm/receipt.json`; all-object/chain equality is recorded in `arm-result-equality.json`. Four-mode recipe receipt: `tx002-critic-overlay/receipt.json`.

## Findings and review limits

No production P0/P1/P2 correction was requested. One early tooling observation was resolved before the supporting freeze: native runner now resolves the compiler and hashes driver/cc1/as/ld/collect2 before creating output, with tool paths included in protected input parents. I inspected the corrected delta and personally reproduced all 19 controls. This was inspection-driven hardening; I do not claim an executed pre-fix source-damage reproduction. The supporting initial harness compile warning and earlier tool versions remain labeled historical evidence.

The initial mandatory app/control regression gap is now closed by the exact restoration and personal rerun described above. No numeric C1–C5 aggregate is assigned until the final package and exact commit/base are checked. C1/C2 currently have source and assertion-based support; C3 has personally reproduced native/ARM checks; C4 is a narrow ABI-preserving change with an explicit text/stack tradeoff; C5 has pinned inputs and reproduction tools but no publication binding.

No full firmware/startup/linker placement, total stack, target IRQ safety, WCET, physical ADC/USB, host connectivity or hardware acceptance is inferred. No protocol policy changed and no merge is authorized.
