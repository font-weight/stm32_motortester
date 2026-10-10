# Independent supporting integration tests, INT-001

Date: 2026-10-10. Repository: font-weight/stm32_motortester, issue 18.
Scope: independent tests only. No implementation, host, root tests, remote repository, or hardware changes. This report does not issue Critic-A's required verdict or accept any draft dependency.

## Result

Final immutable `fixed-snapshot` passes all 24 test groups under both strict GCC compilation and ASan/UBSan. Three defects were reproduced against the immutable `revised-session-snapshot`, reported to the root author, fixed by the author, and reverified here:

1. An old-session partially sent STOP_ACK surviving HELLO cleared the new session's Stop barrier. The session-aware link then ignored the stale completion, while the new STOP_ACK could no longer generate STOP_FINISHED. TX's completion condition now checks event session equality.
2. Stop's emitted STATUS reported zero dropped_windows after aborting an allocated window. Counter accumulation and saturation are now covered; repeated Stop and committed END do not increment it.
3. A RELEASE arriving at the 5000 ms deadline before the next poll bypassed the timeout and rearmed acquisition. RELEASE now checks the deadline itself before admitting its ACK or changing acquisition state.

## Test method and coverage

`adapter.c` exposes the real C API through a small ctypes test adapter. Normal behavior uses public APIs; the adapter inspects state for exact boundary selection and injects near-saturation counter values without billions of iterations. Samples are supplied to the real acquisition implementation. Bytes are copied from the real TX callback, independently COBS-decoded in Python, header fields and frame lengths are verified, and both frame/window CRCs are recomputed by Python's zlib rather than the production CRC/decode implementation.

- 24 full windows: three modes, pretrigger 0/512/1024, rising/falling, and Auto timeouts. Every one of 2048 codes is checked against an independently constructed expected array.
- 20 more full windows in one continuous Normal session, preserving generation and incrementing window IDs/sequence through RELEASE/rearm.
- Exact BEGIN metadata: config/generation/window IDs, profile, sample count, pretrigger, trigger index/kind, source/edge/channel, level/quality, nominal 72 MHz timer with PSC 71 and ARR 19, zero external_edge_id. These are nominal software metadata, not measured hardware timing.
- DATA offsets/counts and original little-endian codes; END total/reserved/CRC; monotonic uninterrupted sequence.
- Small writes of 1/7/63/64 bytes, periodic zero-byte acceptance, at most one backend callback per link poll, maximum 64-byte writes, full bulk queue retry with stable CRC/offset/buffer.
- Frozen-buffer immutability, CONFIG/ARM rejection while retained, valid and invalid RELEASE, duplicate tombstone replay without rearm, replacement by next END, Single stopping, late tombstone after Stop.
- Stop at idle, armed-without-trigger, trigger-before-freeze, frozen-before-TX, queued/partial BEGIN, partial DATA, queued END, partial committed END, fully completed END. Terminal uniqueness and no phantom window are verified. Repeated Stop waits for its own ACK and does not duplicate ABORT.
- All six TX slots occupied by partial BEGIN, two earlier replies, ABORT, STOP_ACK, STATUS; previous replies stay ordered and retained.
- Reconnect: unreset link fails closed on nonce mismatch; old END with reused generation/window IDs after link reset cannot contaminate the new state; old STOP_ACK cannot release the new Stop barrier.
- Input-range fault, negative/oversized backend response, TX stall, sequence/window/dropped-counter saturation, full mandatory-reply queue, exact RELEASE timeout and wrapping clock.

## Reproduction

From this directory:

- `./run.sh` compiles the immutable final sources with GCC and runs all tests.
- `./run-sanitized.sh` runs the same tests with AddressSanitizer and UndefinedBehaviorSanitizer, halt-on-error.
- `SOURCE=revised-session-snapshot ./run.sh Sessions.test_old_stop_ack_does_not_finish_new_stop Stops.test_cancelled_window_status_counter Release.test_late_release_cannot_bypass_timeout` reproduces the three original defects.
- `SOURCE=/workspace/shared/capture-link-draft ./run.sh` can test later author revisions, but those results must get their own source manifest.

GCC: Debian 14.2.0-19. Python: 3.12.14. Sanitizers instrument every C implementation and adapter source. ASan leak detection is disabled because the host is the Python interpreter; the tested firmware modules have no heap allocation. Python itself is not ASan-instrumented.

Logs: `final-strict-tests.log`, `final-sanitizer-tests.log`. Final source hashes: `fixed-sha256.txt`. Earlier valid failure logs: `revised-session-tests.log`, `extra-boundaries.log`, `complete-initial-tests.log`. `initial-tests.log` is retained for transparency but includes corrected test-harness mistakes (header offsets and an END enqueue boundary assumption); it is not evidence of production defects.

## Source identity

Final link C SHA256: f01beedd3c77e4373a7e6778b5551e8e82f4b70b378ae0ec70991c75dda272bd
Final link H SHA256: d2260fff7b6a583a52b6a99f1d2951d9d9919e70def99266b4a4131b9133c9de
Final TX C SHA256: e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e

Failing link C SHA256: 1a7f935986dd5aa7996df1456b47ae4fa0747653f411134aead5e5d80cb424a1
Failing link H SHA256: d14e9eac5e56b65cf9400ba3dea1785283ae8e127d75dd554f86323de95eef0d
Failing TX C SHA256: 6f8bef11297116388a2f3cf91a0717d9f42e0762eae9615beddb37168ed907ac

## Limits

These are native synthetic integration tests. They do not establish USB hardware transmission/completion, timer/ADC/DMA operation, low-voltage measurements, target memory/linker fitness, Windows host compatibility, or final Critic-A acceptance. The top-level command/session dispatcher remains the caller's responsibility. No remote commit identity exists for these local snapshots; hashes pin the tested bytes.

The repository's current AGENTS and required planning/quality documents were fetched through GitHub. The draft wire contract was fetched from `dot-a/a004-session-draft`, blob SHA 8bb5e619355d6678582348f1202b70a07476ad52. No root test implementation was read or reused.
