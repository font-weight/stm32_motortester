# RX-001 independent supporting verification

Date: 2026-10-10. Task: issue #37, root-authored `mt_control_rx.c/.h` binding on CTRL PR #35 base `0ddea1e5992caa6231203838c3956e19675867a8`.

## Result

No implementation defect was found in the tested scope. The final independent RX suite passes **24/24 scenarios and 594,167 runtime checks** in both strict native C11 and AddressSanitizer/UndefinedBehaviorSanitizer builds. All pre-existing controller regression scenarios also pass unchanged and when commands are routed through the new byte binding.

This is supporting test evidence, **not a Critic-A verdict or acceptance score**. The reviewer did not author or modify the implementation. Only the independent test/evidence directory was written. No host-app changes, GitHub writes, new cloud tasks, paid resources, firmware flashing or hardware operations were performed.

## Exact source identity

The final source snapshot is `snapshot/`. The author added two comment lines to the RX header after the first successful run; the exact final header was then copied and all relevant runs repeated. Earlier evidence/header are preserved under `history/`. All 17 existing files supplied with the draft match the candidate CTRL #35 base byte-for-byte. Their Git blob identities also match the pre-existing publication base-tree/additions manifests. The source identity basis is recorded explicitly in `source-manifest.json`: remote metadata was supplied by the parent publication evidence, rather than independently fetched again by this worker.

New source SHA-256:

- `firmware/app/mt_control_rx.c`: `b9c067ad72e20d7fb0888a0eb3a4e49a4871359271ee155b2d8027481e0d0322`
- `firmware/app/mt_control_rx.h`: `67fc3dfa8b41aae941b13f968e76fcd4d21bba8619ee38defbf4a22975223943`

`run-final/compiled-sha256.txt` binds the exact test and compiled sources. `deliverable.sha256` identifies scripts, report, manifests and selected final logs. The new RX source was unpublished when tested; no immutable RX commit is claimed. The author later stacked composition work on IO PR #39; this report still verifies the original 17 CTRL-base dependency blobs separately. Additional author ADC-binding/platform sources and composition tests copied for supplementary reproduction are identified in `supplemental-author-files.json`; they are not described as independently authored by this reviewer.

## New independently authored test coverage

Input vectors use an independently written CRC-32/ISO-HDLC and COBS encoder. Its known CRC check is `123456789 → cbf43926`; payload sizes 0 through 512 are cross-checked with production decoding. The suite does not use `mt_wire_encode` to create its input vectors.

- HELLO, CONFIG, ARM and STOP: all 156 two-piece split positions, including empty first/last chunks; all chunk limits 1 through 64 across concatenated command sequences; two whole commands in one 60-byte read.
- API validation: null RX/control/consumed; null bytes with zero/nonzero length; every valid length 0–64; oversized 65–4096, UINT32_MAX and SIZE_MAX classes. Invalid calls preserve a pending partial frame even when their supplied clock would otherwise time it out. Overlap, dangling pointers, uninitialized objects and unsatisfied initialized-controller preconditions are outside the documented contract and were not invoked.
- Codec rejection: framing, short/long decoded length, header length mismatch, oversize payload, bad CRC, both unsupported version bytes and each reserved header byte. Every invalid byte value in the two version/three reserved positions is covered, totaling 1,275 CRC-valid negative vectors. The exact `last_decode` category is checked for representative vectors.
- All 65,535 mismatched 16-bit header payload-length values are tested against a four-byte STOP payload. No rejected frame changes any byte of the controller or causes a callback. The next correctly sequenced valid STOP still executes exactly once.
- All seven known command types with every wire-valid payload size 0–512 are exercised at mismatched lengths, totaling 3,584 semantic rejection vectors. Codec-valid payload failures reach the controller error/HELLO rules without overflow or framing-counter contamination.
- Cached decoded output remains unchanged after codec rejection; empty delimiters and repeated malformed frames cannot redispatch a previous command. Malformed and valid commands coalesced in the same call are processed correctly.
- The stream accepts exactly 543 nonzero accumulation bytes; byte 544 overflows. Discard continues until delimiter with only one overflow count; idle polling while discarding adds no timeout. Valid commands resume after delimiter.
- Interbyte timeout is checked at 999/1000 ms, by explicit poll, zero-length feed, and byte feed; both discarded nonzero bytes and delimiter resynchronization are covered. Whole-frame timeout is checked at 2999/3000 ms while interbyte gaps remain 900 ms. Both clocks cross UINT32 wrap.
- Rejected/overflow/timeout counters reach UINT32_MAX and remain saturated through repeated faults. They are local diagnostics: no capture recovery/state change and no wire-counter mutation. Transport loss preserves them; explicit RX initialization resets them.
- Session mismatch, exact duplicate and conflicting duplicate are exercised through actual encoding. Sequence gap, wrong request, wrong direction and exhausted sequence enter controller recovery at exactly the triggering delimiter. Retained fresh HELLO suffix is not consumed until explicitly serviced.
- Recovery consumed-prefix semantics are additionally tested at all 1,160 combinations of triggering-frame prefix and fresh-HELLO suffix that fit the 64-byte bound. This includes zero suffix and partial/full next HELLO, with exact consumed counts and no premature negotiation.
- CRC corruption alone leaves capture running, followed by a valid sequence gap that performs fail-closed recovery.
- Explicit transport loss clears partial framing, deactivates the session, stops logical capture and attempts Stop exactly once per loss call. Persistent hardware Stop failure retains the running flag while blocking control output; a later successful Stop/new HELLO recovers. No successful Stop ACK is emitted on failure.
- A Stop-command failure stops feed at its delimiter and retains a coalesced HELLO suffix for explicit service. The controller's retryable Stop callback behavior is checked.
- Partially accepted TX is retained across explicit loss and completed in order before the fresh HELLO_ACK. RX timeout polling does not drive TX or service ADC/capture.

## Existing-controller regression results

`run-regressions.sh` reproduced the following against the same snapshot:

1. Original author strict C11 and ASan/UBSan suite: passed, including 20 encoded window loops, stale Stop and hardware-failure recovery.
2. Unchanged independent controller suite: **39/39 scenarios, 576,994 checks**, native and ASan/UBSan passed.
3. The same 39 scenarios routed through `mt_control_rx_feed`: **39/39 scenarios, 577,333 checks**, native and ASan/UBSan passed.
4. Separate Python wire oracle: **344 frames, 98,281 bytes, 20 full windows and 40,960 exact sample values**, passed for each route.
5. Both unchanged Critic-A reproducer sources, native and ASan/UBSan: passed. Old tombstone release keeps start count 2 and hardware stopped; stale Stop retains Stop count 1, readiness and running capture.

Supplementary author composition check: `tests/control-rx/test_rx_io.c` replaces the original register-model command receiver with seven-byte encoded RX chunks and reuses the unchanged ADC-control author harness. This is additional author-authored integration coverage, kept distinct from the independently authored RX suite. Results are in `root-composition.log`; the worker personally reran strict/sanitized builds against copied inputs. It remains simulated register-model evidence, not a board test.

The original and RX-routed 20-window transcripts are byte-identical, SHA-256 `f697347414bf21bdddfafe2c091215c147bc9946b6dff40b9e76bb47a2935caf`.

The RX adapter is confined to a separate copy of the older test harness. It adds RX storage/initialization, routes encoded bytes in chunks of at most 64 and changes exactly nine expected input-return checks from `MT_CONTROL_IGNORED` to the wrapper's aggregate `MT_CONTROL_OK`. All controller state, output, ordering and callback assertions are retained. The full generated change is `old-controller-rx/adapter.diff`; `prepare_old_rx.py` reproduces it. This does not modify the old source or implementation.

## Reproduction

From this directory:

```sh
bash run.sh "$PWD/snapshot" "$PWD/reproduced-rx"
python3 prepare_old_rx.py
bash run-regressions.sh
```

The second runner writes `regression-run/`; change its OUT variable if preserving a prior run. The first runner accepts a source-root and output-root. Both source and output must be readable/writable under the executor's normal permissions.

Compiler: GCC 14.2.0, Debian package 14.2.0-19. New suite flags: `-std=c11 -O1 -g -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror`. Sanitizer run adds `-fsanitize=address,undefined -fno-omit-frame-pointer`; `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`, `UBSAN_OPTIONS=halt_on_error=1`. LeakSanitizer is disabled and no leak-check pass is claimed.

Final result paths:

- `run-final/native.log`, `run-final/sanitizer.log`, `run-final/exit-status.txt`
- `regression-run/author.log`
- `regression-run/independent-unchanged/`
- `regression-run/rx-adapted/`
- `regression-run/tombstone_hardware_repro*.log`
- `regression-run/stale_stop_capacity_repro*.log`
- `root-composition.log` (supplementary root-authored RX and RX/ADC composition tests)

## Findings and limits

No runtime/sanitizer implementation failure was observed. The author resolved the documentation suggestion: the final tested header explicitly says OK includes ignored commands and discarded nonfatal parser input, diagnostics report decoder faults, and OK is not command acceptance. The change was comments only; the C source was unchanged.

The first test-only build rejected two misleading-indentation constructs and an unused helper; an expanded test-only build rejected an unsigned-to-byte conversion. These harness issues were fixed under the same strict flags, and their original sources/build logs remain in `run-initial/` and `run-expanded-build-failure/`. The initial adapter generator's assertion expected five ignored-return sites, found nine and stopped before creating its output. Inspection confirmed all nine were relevant input-return assertions; the generator was corrected to expect nine. There were no observed implementation-test failures concealed by these harness corrections.

This evidence is native synthetic verification. It does not establish MCU memory fit, full linker map, target stack safety, timing/WCET or an ADC-service deadline; a 64-byte bound is not a measured time bound. No physical USB endpoint, native USB enumeration, Windows interoperability, actual ADC/DMA hardware, measured low-voltage windows or ignition behavior was tested. The wrapper's foreground serialized ownership, non-overlap, initialized controller, bounded callbacks and monotonic clock-service preconditions still apply. Existing quality-gate and broader integration blockers remain unchanged.
