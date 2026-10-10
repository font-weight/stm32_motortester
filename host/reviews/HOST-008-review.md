# Critic-B: HOST-008 verified-window release

Date: 2026-10-10. Repository: [font-weight/stm32_motortester](https://github.com/font-weight/stm32_motortester).
Task: [HOST-008 #48](https://github.com/font-weight/stm32_motortester/issues/48).
Publication: [draft PR #69](https://github.com/font-weight/stm32_motortester/pull/69).

**Verdict: PASS, 10.00/10 for the declared Linux/synthetic Normal + analog HOST-008 module scope only.**
No confirmed P0/P1 or missing mandatory evidence was found in that scope.
This is not acceptance of the shared protocol, the PR's eventual merge result, Windows, hardware, or a product release.

## Exact reviewed version and independence

- Head: **`45ef38792b8e824e2109e7bacc1c9dc605917d06`**.
- Tree: **`e39dfe24663d089e8ff9888101305a0e2af8a626`**.
- Implementation base and sole parent: **`ef2880c0655d155284c176424a12378b3b66adf4`**.
- Parent-to-head diff: **36 files, 6094 insertions, 32 deletions**. Production changes are confined to `host/motortester/session.py`; documentation, two new suites, fixtures/evidence and one existing provenance assertion make up the remainder.
- Personally compared **all 165 files** in the frozen `/tmp/motortester-host-session-release` checkout against the exact local Git objects of this head. Every byte matched; there were no extra source/evidence files outside Git, excluding `.git` and bytecode caches.
- Supplied `verified-manifest.json` SHA-256: **`a4596d0f5eea1318841390600bcc763d9aa02ad66649ec4a06fece3de37a0042`**. All 139 before-source entries, 139 after-source entries and 25 evidence hashes matched. Its publication lists exactly equal the 36-file diff.
- Critic: independent Critic-B, author only of this review and its separately owned probes/evidence. I did not implement or modify the controller, author tests, conformance tests, fixtures, or their historical logs. No external write or merge was performed.
- Rubric: `docs/quality-gate.md` revision 1.0, C1–C5 weights 30/20/25/15/10, threshold 9.5 before rounding. Scope and rubric were fixed before testing.

[Exact-source verification](evidence/release-host008/source-verification.json),
[diff scope](evidence/release-host008/diff-scope.txt),
[reproduction instructions](evidence/release-host008/README.md),
[evidence checksums](evidence/release-host008/SHA256SUMS).

The coordinator reports that PR #69 is stacked against `bd582…` and has a viewer-related merge conflict.
This review's implementation diff is explicitly against `ef288…`, not an untested merge with that PR base.
A conflict resolution, rebase, or integration result requires exact-SHA compatibility checks and an updated review; this score does not transfer automatically.

## Scope, sources, and acceptance

Read the project instructions, task/requirements, decisions/progress, MVP specification, coordination and quality gate,
the host/session/window/wire/emulator documentation, the changed production code, author/conformance suites,
independent reference vectors, and preserved initial/corrected/final evidence.

The reviewed outcome is a bounded, pure-controller release path:

1. `WindowReceipt.from_window` accepts only strictly typed, complete, semantically checked Normal/analog results; rechecks accepted context, BEGIN, all 2048 original samples, contiguous bounded fragments, CRC and rail metadata.
2. `release` requires a factory-bound receipt matching the admitted terminal, session, generation, window and CRC; it occupies the one ordinary-request slot.
3. Request allocation, first-positive-byte TX sequence commitment, immutable tickets and final-byte response timing remain correct under partial writes and Stop.
4. Only a matching RELEASE_ACK clears the prior window barrier; Normal continues with one ARM and an unchanged generation. Late release after Stop stays stopped.
5. Invalid results fail in the factory itself before release; corrupt/stale ACKs, transport uncertainty and errors fail closed without automatic retry or loss of the diagnostic terminal.
6. At least 20 complete windows travel through real byte-emulator/decoder/receiver/controller components, with independently checked samples and CRC.

These are bounded contributions to MVP-U02/U03/U07/U09, MVP-F03/F06/F07, FR-05/FR-06 and DATA-04.
They do not complete those product requirements. CON-12 and DOC-02 govern this independent review.

Source pins remain separate, with personally extracted and hashed source documents in the evidence directory:

- Session/controller draft: **`b6ae8025baf1a47e8173997029d2d8b858ada74c`**.
- Window receiver draft: **`124b3f32e1e7f7282bce283baa4c9406d4d72950`**.
- Emulator draft: **`c54bd955aaf3cb863ef3df043ea237ea62e74ad4`**.
- Implementation dependencies: host union `988f2cc094fa82c622f0b20276114e3159ae73d9` plus the 27-file emulator set `fd1d0ad6f131e1f08be64893da9c1d667dc15eed`, forming parent `ef288…`.

The newer PR #52 Stop-contract compatibility review is separate and is not accepted by this verdict.
CA-CTRL-4 remains the documented conservative host scheduling policy: do not create a new ordinary request while STOP_ACK is pending, but process earlier replies.
Seven inherited emulator hold-timeout cases remain **PROVISIONAL**. Their execution in the regression suites does not make the old 5000 ms interpretation normative.

## Personally executed verification

Environment: Linux x86-64, kernel 6.18.44, glibc 2.41, CPython 3.12.14;
PySide6 6.12.0, pyqtgraph 0.14.0 and numpy 2.5.3 from the pre-existing `/tmp/motortester-deps`.
Qt used `offscreen`. No package installation was needed.

| Check | Result | Evidence |
|---|---:|---|
| Imports and exact module locations | PASS | [imports.log](evidence/release-host008/imports.log) |
| New author + independent conformance suites | 54 PASS, 0 skips | [release-normal.log](evidence/release-host008/release-normal.log) |
| Same new suites under `python -O` | 54 PASS, 0 skips | [release-optimized.log](evidence/release-host008/release-optimized.log) |
| Existing session suites | 70 PASS, 0 skips | [existing-session.log](evidence/release-host008/existing-session.log) |
| Entire host suite, Linux/offscreen | 421 PASS, 0 skips | [full-normal.log](evidence/release-host008/full-normal.log) |
| Entire host suite under `python -O` | 421 PASS, 0 skips | [full-optimized.log](evidence/release-host008/full-optimized.log) |
| New critic-owned adversarial groups | 14 PASS, 0 skips | [critic-normal.log](evidence/release-host008/critic-normal.log) |
| Same critic groups under `python -O` | 14 PASS, 0 skips | [critic-optimized.log](evidence/release-host008/critic-optimized.log) |

All runs had zero failures, errors, expected failures and unexpected successes.
The critic-owned runner fails on empty discovery, skips or expected failures.
[Commands, environment, complete before/after file hashes and results](evidence/release-host008/suite-runs.json) establish no source drift across the repeated suites.
[Probe metadata](evidence/release-host008/probe-runs.json) binds the extra probe source, commands and closed logs.
The author's evidence-producing runner was not invoked; historical logs/manifests were preserved.

The [critic probes](evidence/release-host008/critic_probes.py) import production modules and Python's standard library only, never author/conformance helpers or their oracle. They add:

- Twenty real byte-emulator windows with falling edge and threshold 1, mixed receive chunk sizes and one-byte RELEASE writes. Every sample and a separately implemented bitwise CRC are checked; exactly one ARM and twenty releases occur.
- All **43 split positions** of the 42-byte encoded RELEASE, including zero and full acceptance, with Stop queued at each position. Request reservation, zero-write identity, single sequence commitment, stale ticket rejection, frame/reply order and stopped completion are checked.
- **104 exact scalar-type attacks** across every context/BEGIN field, tuple subclasses, wrong sample types, missing slot attributes, and malformed shapes. A maximum-size 2048-fragment valid result works; oversized/gapped fragments reject before sample inspection.
- Consistent changes to both sample representations at **every one of the 2048 sample positions**, retaining the old CRC. Every changed result is rejected, so equality between the two arrays cannot substitute for recomputing CRC.
- Real incomplete, reordered and corrupted receiver inputs rejected by the factory itself; no subsequent release call is included in the expected-exception block.
- Single-ordinary-request admission, direct/replaced receipt refusal, foreign nonce isolation, stale ACK from an earlier request, exact-last replay, new-sequence replay, 999/1000 ms deadline boundary, errors/disconnection/unknown writes, and final identifier exhaustion.
- Stop followed by a late valid release remains READY; old window traffic is barred. Ten Stop → late release → ARM cycles preserve increasing window IDs and reject prior-generation receipts.

The supplied conformance's bounded-object graph check also passed personally: the controller retains no sample/result/receipt history as repeated windows complete.
This is structural boundedness and a synthetic regression, not a hardware RAM or real-time measurement.

## Findings and resolved evidence issues

**No confirmed in-scope defect or open P0/P1. No mandatory evidence is missing.**

The historical failed runs were reviewed rather than erased:

- The first independent expectation incorrectly demanded a second host RELEASE for an already acknowledged receipt. The fixed test checks the documented bounded host policy, while device-side tombstone idempotence remains in the emulator suites.
- The old emulator suite's exact historical `session.py` digest initially conflicted with this new implementation. The narrowly changed assertion now checks the immutable historical manifest's before/after digest and base, preserves the session protocol pin and current wire digest, and leaves current HOST-008 bytes bound by their own manifest. No behavioral test was deleted or skipped.
- The negative-stream assertion was tightened so the factory itself must raise; a later rejection by `release` can no longer produce a false pass. The seven negative streams and test count were retained. The final bytes and final logs were independently reproduced.

### Explicit trust boundary and remaining work

`WindowResult` contains no session identity. The runtime must retain and supply the same trusted `ExpectedContext` used to construct its receiver.
The factory cannot authenticate a substituted session from the result alone; my probe confirms this limit, and `release` rejects a receipt carrying a foreign session.
The private scalar `_validated` binding guards ordinary direct construction and `dataclasses.replace`; it is not a security mechanism against deliberate Python object mutation.
Production context ownership/linkage belongs to the future runtime work (HOST-010) and is not accepted here.

This controller does not itself accumulate DATA or prove successful measurement from END alone.
Release readiness is the checked receiver/factory path; release acknowledgement means buffer release, not another measured window.
External code still owns transport reset, truthful byte-acceptance reporting, monotonically supplied time, regular ticks, context linkage and preservation of incomplete fragments.

Not verified or accepted: Windows 10 execution/packaging, native USB/serial, GUI runtime integration, firmware on a board,
physical sample/trigger timing, low-voltage LV-001, ignition, vehicle use, Auto/Single/external-mode operation,
new shared Stop semantics, or `software-ready`/`low-voltage-verified`/`ignition-verified` MVP status.

## Fixed rubric and decision

| Criterion | Weight | Score | Concrete basis within this scope |
|---|---:|---:|---|
| C1 Correctness and requirements | 30% | 10 | Complete strict admission and CRC verification, exact release/ACK matching, one ordinary slot, one-ARM multi-window continuation, Stop/recovery invariants demonstrated by source review and independently reproduced byte paths |
| C2 Reliability and failure handling | 20% | 10 | All release split positions, stale/foreign/replayed responses, sample-wide CRC mutations, shape/type limits, missing attributes, deadlines, uncertainty, exhaustion and old-generation barriers pass without uncontrolled state changes |
| C3 Evidence and verification | 25% | 10 | Exact 165-file/tree comparison; reproduced new, regression and complete suites normal/optimized with no skips; independent adversarial tests and direct factory failures; preserved earlier failure evidence and explicit synthetic limits |
| C4 Clarity and maintainability | 15% | 10 | Local immutable receipt and existing bounded request machinery; no transport, callback, sample/history ownership creep; trust boundary, time ownership and Stop policy are explicitly documented |
| C5 Reproducibility and documentation | 10% | 10 | Exact parent/head/tree and protocol pins, all supplied hashes verified, commands/environment/logs recorded, no source drift, historical corrections explained, untested integration and physical stages separated |

Unrounded total: **0.30×10 + 0.20×10 + 0.25×10 + 0.15×10 + 0.10×10 = 10.00**.
**PASS**: 10.00 ≥ 9.5; independent review and all mandatory scoped evidence are present; no open P0/P1.
This score is a scoped engineering judgement, not a 100% reliability probability, hardware guarantee, shared-contract acceptance or permission to merge an unreviewed integration tree.
