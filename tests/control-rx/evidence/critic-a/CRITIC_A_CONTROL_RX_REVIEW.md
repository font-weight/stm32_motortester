# Critic-A: RX-001 byte/controller binding review

## Verdict

**BLOCKED_EVIDENCE; score null.** No demonstrated P0/P1 implementation defect found in the bounded final local candidate. Source, caller contract, native execution and target-object evidence agree. Immutable publication binding and applicable shared/physical integration gates remain pending; no USB or firmware-ready acceptance is claimed.

Frozen SHA-256 identities:
- RX C: `b9c067ad72e20d7fb0888a0eb3a4e49a4871359271ee155b2d8027481e0d0322`.
- RX header: `67fc3dfa8b41aae941b13f968e76fcd4d21bba8619ee38defbf4a22975223943`.
- Russian README: `6826701b9c8df36d183a0516c39dc180f74c89cfe8f8c7b858f6e3ad368f817c`.

Isolated source: `final-candidate/`; all source/doc/test Git blob and SHA-256 identities: `source-manifest.json`. All 20 dependency C/header files match prior personally reviewed controller/ADC, stream, session, command, acquisition, bridge and TX bytes exactly. RX is intended to stack on final IO PR39 `aad61d598aa9996b1cd2c0587e936e19118fc087`, preserving controller PR35 behavior.

## Source and contract assessment

- Only decoder MESSAGE events call the real controller. Rejected framing/length/CRC/version/reserved candidates cannot apply cached previous output. This matches the device's diagnostic-only codec-rejection rule; a later valid sequence gap can cause controller recovery.
- Feed consumes at most 64 bytes. For recovery/argument results from a dispatched frame it stops at that frame's delimiter and returns the exact processed prefix, leaving a coalesced suffix to the caller. A fresh HELLO suffix cannot negotiate until explicitly supplied again.
- Zero-length feed polls RX timeout. Invalid arguments do not consume or mutate framing; a valid separate consumed output is zeroed. Saturating diagnostic counters are local parser events, not wire counters or lost-command counts.
- Explicit transport loss resets RX framing and invokes controller recovery. It preserves diagnostics and delegates partial-TX/DMA ownership to the existing controller contract. Recovery return does not prove physical Stop success.
- The final comment-only clarification correctly states that aggregate OK includes ignored commands and discarded nonfatal parser input. Command acceptance is determined by its wire response, not by feed's return value. Supporting review suggested this clarification; no executable change was necessary.
- Caller must use initialized, nonoverlapping, lifetime-safe objects, a single foreground owner, no callback reentry/IRQ use, true monotonic ms timestamps, and preserve/retry only the unconsumed suffix. A byte bound is explicitly not a WCET or ADC service guarantee.

## Personally executed evidence

1. Full source/header/README and author/supporting harness inspection. No production code edited.
2. Final author runner strict C11 and ASan/UBSan passed. It runs unchanged controller regressions, all CONFIG split positions, encoded ARM/Stop, rejection isolation, saturation, timeout/wrap/overflow and recovery suffix; then the added author RX/ADC composition routes real encoded commands in seven-byte chunks through the actual register model, driver, controller and RX wrapper. See `author-final.log`.
3. My separately authored wrapper oracle (`independent_rx.c`, SHA-256 `ff115123e8d45882f44df6ccc12adf09dae0f86d417fb18e11c3b21d4a07acd7`) passes **147,442 scenarios** under strict and ASan/UBSan. It tests every two-piece split for every payload size 0–512, maximum frames at all chunk sizes 1–64, coalescing, both recovery/argument prefix propagation, rejection, timeout rollover, overflow, saturation, invalid arguments and explicit transport reset. It uses the real stream/codec and independently faked adjacent controller API. Production encoding creates its valid input fixtures; this is a wrapper segmentation/control-flow oracle, not a new independent codec oracle.
4. Personally reran the supporting independently authored RX suite against my final snapshot: **24/24 scenarios, 594,167 checks**, strict and ASan/UBSan exit 0 (`supporting-rx-final/`). This separate suite uses independent CRC/COBS input generation and real controller hooks. It covers 1,275 CRC-valid version/reserved negative vectors, all 65,535 inconsistent header lengths, 3,584 command-size rejection vectors, all 1,160 bounded recovery-prefix/suffix combinations, stale cached messages, session/sequence failures, diagnostic boundaries and failed physical-Stop simulation.
5. Personally reran the unchanged 39-scenario controller suite (576,994 checks) and its explicitly RX-adapted copy (577,333 checks), strict and ASan/UBSan (`old-control-regression/`, `rx-adapted-regression/`). The adapter changes exactly nine expected input returns from IGNORED to the documented aggregate OK while retaining all state/output/callback assertions; its diff was reviewed. Both 20-window transcripts are byte-identical: SHA-256 `f697347414bf21bdddfafe2c091215c147bc9946b6dff40b9e76bb47a2935caf`. The separate Python oracle confirms 344 frames, 98,281 bytes and 40,960 exact sample values for the RX route.
6. GCC 14.2.0 host flags include strict warnings as errors. Sanitizers use `detect_leaks=0`; no leak-check pass is claimed. Original supporting harness compile corrections are preserved separately and do not conceal production failures.
7. Personally reproduced the final ARM GCC 14.2.1 Cortex-M3/Thumb object with explicit `-fno-short-enums`; exact object SHA-256 `2070737496f4dda694277c8a1786544751a3859bc53d6066a3c8a361e204d8c7`, **296 bytes text**, no owned global data/bss. Context **1,128 bytes**, including stream 564 and message 544. Feed frame **48 bytes**, init 16, poll 8, transport loss 8. See `arm-final.log`.
8. The known RX-feed -> controller-receive -> bridge-Stop -> TX-Stop -> append chain is **2,992 bytes** of known own frames, before library/caller/IRQ/runtime. A separate Cortex-M3 ABI probe of the hypothetical combined controller + ADC driver + ADC binding + RX owner yields **10,904 bytes**; alignment makes this 24 bytes larger than the earlier 10,880-byte owner with separate stream/message storage. This is a partial ownership model, not a linked firmware image or proven free RAM.

## Remaining gates

- Exact final publication identity and changed-tree preservation must be verified; this local result does not attach to later source edits automatically.
- Real USB enumeration/endpoints/IRQ/FIFO loss detection, startup and coherent clock implementation are outside this wrapper. Explicit transport_lost is only useful if the real backend detects and calls it correctly.
- The full foreground path must satisfy the ADC service interval strictly below 2,560 us. A final byte may trigger complete decode and synchronous hardware callbacks. No target WCET, scheduling/IRQ budget or service deadline is proved by these tests.
- Full linker map, complete flash/RAM/callback/libc/stack analysis, measured watermark, actual Windows device interoperability, measured low-voltage windows and applicable Critic-B/shared acceptance remain open. The passing native and object checks cannot establish hardware or complete-device readiness.
