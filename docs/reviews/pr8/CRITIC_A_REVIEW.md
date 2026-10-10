# Independent Critic-A review: protocol draft stack

Reviewed 2026-10-10. Reviewer: independent Critic-A worker; not the author/co-author of the implementation. No implementation files changed. Independent test artifacts were written only in this review workspace.

## Decision

**BLOCKED_EVIDENCE; score: null.** Two P1 shared-contract defects also require rework. No P0/P1 implementation defect was found in the isolated `mt_wire`, `mt_stream`, or `mt_command` modules within their documented preconditions. The listed host checks passed. This is not approval of the contract, PR stack, firmware, USB, or a hardware readiness level.

## Exact reviewed versions and scope

Repository: `font-weight/stm32_motortester`.

- PR #4: `124b3f32e1e7f7282bce283baa4c9406d4d72950`; base `3fe1a2d8c53f77e0510a3da9ddd125f68cb872fa`. Wire document, COBS/CRC codec, vector, progress.
- PR #7: `8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7`; base `124b3f32e1e7f7282bce283baa4c9406d4d72950`. Stream parser, author tests, explanation, two status-only wire-document edits, progress.
- PR #8: `0f285510c1b9b6bd21bb7b60ae6098f7584ae71a`; base `8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7`. Command parser/CONFIG validator, author tests, explanation, progress.

GitHub independently reported all three as open drafts and these exact base/head pairs. Sources were fetched at PR8's immutable SHA, not a moving branch; all six C/header blobs, both test blobs, wire document, and vector were locally verified against Git blob hashes. PR4's `mt_wire.c` and PR7's `mt_stream.c` were also re-read at their own SHAs and matched the tested blobs. The stacked diffs were reviewed. No `host/**` work was duplicated.

Instructions read: AGENTS.md, DOT_START.md, README.md, task.md, requirements.md, docs/decisions.md, docs/progress.md, docs/mvp-spec.md, docs/coordination.md, docs/quality-gate.md, and both parser explanations. Rubric: quality-gate revision 1.0, 2026-10-10. Isolated task scope is documentation/calculation and program/simulator; relevant requirements are MVP-F03–F07 and data integrity FR-05/FR-06, without claiming those whole-system requirements complete.

## Findings requiring changes

### CA-1 — P1: sequence consumption and END commitment have different boundaries

Location: `protocol/wire-v0.1.md` lines 68 and 131–133 at PR8 SHA; same substantive text in PR4.

Line 68 assigns TX sequence when serialization starts. Line 131 requires an END with no byte yet accepted by USB TX FIFO to be discarded without assigning/consuming sequence. These rules conflict if a frame is staged while the FIFO/transport is busy.

Reproduction trace for the specified behavior:

1. Serialize WINDOW_END with sequence n; no byte reaches USB FIFO.
2. Receive STOP for the current generation.
3. Discard the staged END according to the pre-commit rule.
4. Emit WINDOW_ABORT with sequence n+1.
5. The host expects n and must recover for a sequence gap.

Expected correction: define provisional serialization and an atomic first-positive-byte-acceptance commit (or an equally explicit mechanism). A discarded never-started frame must leave the next sequence available; no other frame may consume it concurrently. Test STOP before serialization, after serialization but before first byte, after first byte, and after complete END.

Parent acknowledged this finding and intends the first-positive-write acceptance rule. No corrected commit or executable TX regression was reviewed here.

### CA-2 — P1: Stop terminal rule lacks no-window and already-terminal branches

Location: `protocol/wire-v0.1.md` lines 129–135 at PR8 SHA; same substantive text in PR4.

The text orders exactly one WINDOW_ABORT before END commitment without conditioning this on an allocated, nonterminal active window. It separately permits STOP(0), STOP while waiting, and repeat STOP on the stopped generation. No END-commit point exists for an unallocated window or for a previously aborted window.

Counterexamples to resolve explicitly:

- Initial STOPPED, no ARM and window_id=0: STOP(0) must not invent ABORT(window=0).
- ARM then WAITING without a qualifying event: STOP must not abort an unallocated window or reuse an old completed window's ID.
- Active window aborted by an earlier STOP: a later STOP with a new request_id must not emit a second terminal outcome or increment dropped_windows again.
- CAPTURING before any WINDOW_BEGIN bytes: specify how the host handles ABORT for the allocated window when BEGIN was never delivered.

Expected correction: enumerate no-window, allocated/nonterminal, committed-END, committed-ABORT, and repeat-Stop cases. Only an allocated nonterminal window gets the new ABORT. Define response/counter behavior and add executable transition/ordering tests.

Parent acknowledged the missing branches and will amend the contract. No corrected commit was reviewed here.

## Personally executed checks

Environment: Linux host, GCC `cc (Debian 14.2.0-19) 14.2.0`, Python 3.12.14. Reproduction: from this source snapshot, run `sh review-artifacts/run_checks.sh`.

1. Both author test executables compiled and passed with `-std=c11 -Wall -Wextra -Wpedantic -Werror -O2`.
2. Both passed with `-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined`, running with `ASAN_OPTIONS=detect_leaks=0`.
3. Important environment limit: the first default sanitizer execution failed because LeakSanitizer cannot run under this environment's ptrace. The repeat disabled only leak checking. This is not a leak-test pass. These modules do not allocate heap memory, but that observation is not a substitute for a successful LSan run.
4. Independently written Python struct/zlib/COBS oracle checked the published vector, CRC check value and empty CRC; 2,565 byte-exact encode/decode cases cover every length 0..512 with zeros, ones, 0xff, sequential and seeded random bytes.
5. 544 insufficient-capacity refusals preserved output/written. Independently CRC-recomputed version/reserved/length failures, corrupt CRC, malformed COBS, oversize frames, every truncation of one maximum frame, and 20,000 random decoder inputs checked failure atomicity.
6. Independently packed wire-layout vectors checked all seven host commands, nontrivial endian values, 3,584 wrong payload lengths, and 9,216 CONFIG/support-mask combinations against a separately expressed Boolean oracle.
7. Authored stream regressions exercised split/coalesced input, empty delimiters, malformed COBS, overflow resynchronization, both timeout thresholds, rollover, NULL handling, and explicit reset.

Host ABI sizes observed: mt_wire_message 544, mt_stream 568, mt_config 16, mt_command 24, mt_command_support 4 bytes. These are **host**, not ARM sizes or whole-firmware memory evidence.

Evidence files:

- `run_checks.sh`: full compiler/test commands
- `full-test.log`: successful repeat run with versions and exact-SHA test summary
- `independent_checks.py`: independent oracle and tests
- `independent_checks.json`: independent check summary
- `source-manifest.json`: immutable remote source hashes and local verification

No positive test count is used as a quality score. These tests are finite evidence, not a proof of safety.

## Exact-SHA remote checks and missing evidence

GitHub check-runs endpoint returned zero check-runs for each reviewed PR head. PR8 legacy status list was empty. No green CI claim is supported. Review did not verify branch-protection enforcement. Earlier Critic-B evidence discussed older SHA `18f154f3...`; it is not silently promoted into a final exact-current-SHA approval.

Still required before accepting the full shared contract/stack:

- Correct CA-1 and CA-2, then review the amended exact SHA and relevant executable state/TX regressions.
- Applicable current Critic-B review; joint contract approval requires each critic separately.
- Independent positive/negative vectors for all device-side payloads and executable handshake/reconnect/Stop/rearm/twenty-window B round-trip evidence required by wire document line 150. These are not claimed as implemented by these three isolated modules.
- ARM ABI/stack evidence identified in the stream document; builder evidence may satisfy this but was not received during this review.
- A documented reproducible artifact/CI path tied to the reviewed final source and manual or enforced gate, without claiming an empty check list passes required CI.

USB driver, dispatcher, TX queue, acquisition, Windows packaging, physical sample rate, board behavior and safety measurements are not tested by this review.

## Rubric evidence and scores

All criterion scores and final score remain null while mandatory evidence and shared-contract defects block the requested aggregate result.

- C1 (30%): pure-module fields, endian/layout, lengths and CONFIG ranges match the current tables in tested cases; CA-1/CA-2 prevent a correct unambiguous shared state contract.
- C2 (20%): finite bounds, decode atomicity, timeout and malformed-input checks passed; Stop/TX ordering needs the stated corrections and regressions.
- C3 (25%): actual host execution and independent oracle exist; full contract/state/ARM and current B evidence remain incomplete.
- C4 (15%): small allocation-free modules, explicit separation from state/execution, clear failure output ownership. The terminal/sequence boundary must be made equally explicit.
- C5 (10%): reproducible local scripts and source hashes supplied; repository documentation accurately records its earlier unexecuted state and should receive a new factual checkpoint, not rewritten history, after evidence publication.

Required verdict: BLOCKED_EVIDENCE, score:null; preserve drafts. No merge/release recommendation. The absence of identified defects in isolated source does not clear shared-contract dependencies.

## Subsequent independent conformance reproduction

The separate conformance worker supplied a generator (Python struct/zlib), C reference implementation and runner. Critic-A read the generator, runner and complete C harness, then reproduced it against this independently materialized exact PR8 source snapshot. The final harness removed two assertions about untouched unused output tails on success because that behavior is unspecified by the API; no production change occurred. Final test_conformance.c SHA256: 5f9dad95bc629c2a23d1a6b767ca5f288f114c631c3978d179a886f53d2551e1.

Final strict and ASan+UBSan runs both passed 3,294,024 assertions. The runner verifies exact source blobs before compilation and checks fixture regeneration. Final reproducible logs and report are in review-artifacts/conformance-final-reproduction/. Earlier evidence in conformance-reproduction/ used the older harness and is retained only as history. This added coverage strengthens isolated-module evidence; the contract P1s and missing full-state/shared-review evidence still block the aggregate verdict.
