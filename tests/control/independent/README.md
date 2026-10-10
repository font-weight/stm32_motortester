# CTRL-001 independent supporting tests

Repository: `font-weight/stm32_motortester`, issue 28. Owner of production code: root / Dot A. These tests do not edit production code. Final review and acceptance remain with Critic-A (`review_protocol_drafts`); this report supplies evidence, not an acceptance verdict or score.

## Current result

Final controller C `0ef2f27441c41817867bd91336964aa65daa10732c7203fa85d97650253622ed`: **39/39 scenarios pass** under strict native and ASan/UBSan, with 576,994 runtime checks. The independent Python wire/sample oracle passes 344 frames, 20 windows and 40,960 exact sample values. Final evidence: `run-final/`; full source hashes: `source-final-sha256.txt`. No acceptance verdict or hardware result is claimed.

## Scope and oracle

- C11 harness replaces synchronous profile/start/stop callbacks and the irrevocable USB FIFO-acceptance boundary with deterministic fakes. Every incoming command passes actual COBS encode/decode before dispatch. Every accepted output is accumulated byte by byte and decoded as a complete COBS frame, independent of writes of at most 64 bytes.
- Separate Python standard-library oracle independently implements COBS decoding and uses `zlib.crc32`, without importing the production codec. It validates header, reserved bytes, exact payload layout, session/sequence/request identity, 20 complete windows, per-window CRC, all 40,960 sample values, and state transitions.
- Test source and dependencies are snapshotted and hashed. Files under `snapshot`, `snapshot-corrected`, and `snapshot-final` are frozen copies, not the live draft.
- Contract copy: `contract/wire-v0.1-c54bd95.md`, PR 14 commit `c54bd955aaf3cb863ef3df043ea237ea62e74ad4`. Original connector fetch evidence is preserved alongside it.

## Coverage

39 named scenarios, including:

1. HELLO/CAPS byte accuracy and all 18 advertised mode × edge × pretrigger combinations through real acquisition and transfer.
2. CONFIG echo/config-ID allocation, invalid profile/mode/source/edge/level/pretrigger/timeout/reserved/length and unknown type rejection.
3. ARM without config, stale config, busy ARM/CONFIG, duplicate CONFIG/ARM/STOP requests without second execution or ACK.
4. Stale generation STOP with and without pending state backlog; confirmed physical stop before terminal/STOP_ACK; initial STOP and repeated current-generation STOP; no phantom terminal; old replies before stop.
5. Failed HELLO stop, HELLO profile, ARM profile/start, capture stop, explicit stop, RELEASE profile/start and TX callback failures. Successful output is withheld and the same nonce cannot recover the session; a fresh nonce resets configuration/counters.
6. Old partial CAPS and old partial STOP_ACK frames drain across HELLO without resetting new-session link state or corrupting its response sequences.
7. Ordered PREPARING/WAITING/CAPTURING/TRANSFERRING/WAIT_RELEASE status events; GET_STATUS and RELEASE boundaries; Stop discards stale pending observation states; ordinary reply and state queue bounds.
8. Exact RELEASE CRC, duplicate/tombstone RELEASE, Single rearm, Auto timeout metadata and rearm, Normal continuous captures, release timeout.
9. Tombstone replay while the next window is held in BEGIN/DATA/END/END_QUEUED, including blocked FIFO. No profile/start invocation or hardware restart is permitted; completing the next END invalidates the previous tombstone.
10. Twenty synthetic COBS windows: 98,281 bytes, 344 frames, 40,960 exact sample values, 20 ENDs, 180 DATA blocks, and 20 RELEASE_ACKs; nominal fake hook totals are 21 starts and 21 stops.

The status queue's capacity guard is additionally exercised by controlled corruption of its public storage fields. That subcheck is explicitly a defensive-guard test; it is not claimed to be a naturally reachable application trace.

## Results and source identity

### Initial baseline

- Controller C SHA-256: `0c20b280f53348e93b2cbbfce22f1d3cdc040cff865d8ad0f75ce4f04db94f2d`.
- Header SHA-256: `ce45cf1fa34e4c82d256c8873fee0f2901256714c91a49cca83a90c0a4868574`.
- Full file hashes: `source-sha256.txt`.
- Final preserved baseline run: `run-baseline-preserved`: 34/39 pass; native and ASan/UBSan have the same five assertion failures; independent 20-window wire oracle passes.
- Four failing scenarios demonstrate two production defects: historical STATUS events can follow a newer state reply/rearm ACK; replaying an old RELEASE tombstone can restart hardware for a different frozen window. The fifth failing scenario, `status_reply_capacity_recovery`, verifies the later author's flush-before-reply policy and is NOT a separate independently established baseline defect.

Concrete baseline traces:

- GET_STATUS: PREPARING event (sequence 4), CAPTURING reply (sequence 5), stale WAITING event (sequence 6), CAPTURING event (sequence 7).
- RELEASE: END (sequence 17), RELEASE_ACK (sequence 18), stale WAIT_RELEASE event (sequence 19), WAITING event (sequence 20).
- Old RELEASE of window 1 while window 2 is frozen in BEGIN: start callback count 2 → 3, hardware stopped → running.

### First correction

- Controller C SHA-256: `07031e789a6a594d2b85b2dcdcfc10ce375d50b59ef31aeadf6739b2b304c74b`.
- Full file hashes: `source-corrected-sha256.txt`.
- Initially passed all then-current 38 tests in `run-corrected-asan-ubsan`; independent Python oracle passes.
- Expanded stale-STOP backlog regression: `run-corrected-stale-stop` is 38/39, with matching native/ASan/UBSan assertion failure. A held ARM_ACK plus three pending transitions leaves room for the mandatory stale-generation ERROR, but the correction first tries to flush the whole observation backlog, fails admission, and stops current hardware through recovery. Critic-A independently identified and reproduced this regression.

### Final correction

- Controller C SHA-256: `0ef2f27441c41817867bd91336964aa65daa10732c7203fa85d97650253622ed`; header unchanged.
- Full file hashes: `source-final-sha256.txt`; immutable copy: `snapshot-final`.
- `run-final`: 39/39 scenarios pass with 576,994 checks, native exit 0, ASan/UBSan exit 0, independent wire oracle exit 0.
- No assertions were weakened for the final correction. Genuine GET_STATUS reply-capacity exhaustion still requires fail-closed recovery. A stale STOP with held ARM_ACK and three pending states now returns ERROR without invoking the stop hook; existing transitions remain available to transmit. All tested old RELEASE tombstones remain free of hardware start/profile effects across the four pre-END-commit phases.
- Only the earlier ordering/tombstone defects and the intermediate stale-STOP regression are claimed resolved by these synthetic tests. Their original failing snapshots and logs remain preserved.

## Reproduction

Run from any working directory:

`/workspace/shared/control-independent-tests/run.sh /workspace/shared/control-independent-tests/snapshot-final /workspace/shared/control-independent-tests/a-new-run-directory`

The runner preserves its harness/oracle copies, compiled source hashes, compiler version, native log, ASan/UBSan log, independent transcript report, binaries and exit statuses. It exits nonzero for an assertion, sanitizer, build, or independent-oracle failure. Individual scenarios can be run as `native scenario_name` from their run directory.

Flags: `-std=c11 -O1 -g -Wall -Wextra -Werror -Wpedantic`, plus `-fsanitize=address,undefined -fno-omit-frame-pointer` for instrumented execution.

ASan/UBSan run with leak detection disabled. The first full-success corrected run with LeakSanitizer enabled encountered the execution environment's ptrace-related fatal error on process exit; that exact output remains in `run-corrected/sanitizer.log`. No leak-check pass is claimed. Production code uses fixed storage, but that is not substituted for a LeakSanitizer result.

## Limits

These are native synthetic software tests, not physical USB completion, DMA interrupt timing, MCU resource verification, nominal clock measurement, analog safety testing, Windows app interoperability, or low-voltage/ignition validation. They do not establish software-ready or hardware-ready status. No host source, production source, cloud task, remote repository state, purchase, merge, or acceptance action was changed by this supporting tester.
