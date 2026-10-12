# HOST current-main reconciliation: independent Critic-B review

## Verdict and scope

**BLOCKED_EVIDENCE — score: null.** The exact local composition, sole provenance-
document change and fresh Linux software regression are verified. No P0/P1 was
found in this bounded reconciliation. Required remote binding, the cancelled
14-wheel resolution check, applicable Windows validation and common-protocol
acceptance remain open. This is not a transferred component score, release,
software-ready declaration or approval of the added analog hardware.

This is a continuation by the same independent Critic-B who reviewed the frozen
16c40 union. Reviewer authored no production/test-source change and did not edit
that prior report or evidence. Review-owned writes are limited to this new report
and `host/reviews/evidence/mvp-main-e75/`. No publication, installation, resolver
retry, PR95 reviewer/runtime action, new worker or external write was performed.
The PR95 work and its missing verdict are outside this review and are not replaced
by these composition/software checks.

## Exact candidate and references

- Repository: `font-weight/stm32_motortester`
- Candidate: `6a3bfef0a5e74e5370c912c0fc45f8cebba2ddd8`
- Tree: `5dc6aa691706ebb52c070d3df06aca3aaab6e40c`
- Sole parent/named current main: `e75e8c78e7042c63dd4c4e152cc4b7b40db7045b`
- Frozen host reference: `16c40a8434c33b4e02bb71bf21294d58a90c5a18`
- Its historical sole parent: `da85d0aed68828214057797a5bd78f7b9f49ed35`
- Sole changed host file: `host/docs/mvp-union.md`
- Document Git blob: `9b09867bf2694cfd8867b81930c689f2a17830e5`
- Document SHA-256: `64f1bd912c74c8fe6a1973fff39ef45ddc46f14238fcf33e225b17a59a220c71`

These identities were checked against local Git objects and working bytes. This
review does not independently establish that e75 is still the latest remote main
at a later time; no remote query/publication was performed.

## Independent composition findings

The [reviewer-owned audit](evidence/mvp-main-e75/composition_audit.py) reads Git
objects directly rather than adopting the implementation author's proof.
[Audit output](evidence/mvp-main-e75/composition-audit.log) and the
[253-file inventory](evidence/mvp-main-e75/composition-audit.json) establish:

1. HEAD, full tree and exactly one parent match the declared tuple.
2. All **39 non-host tracked entries** match e75 by Git mode/type/blob and exact
   raw bytes. Newly added analog-source material is preserved; BOM/CRLF values
   are recorded without normalization or rewriting.
3. All **213 other host files** match frozen 16c40 by Git entry and bytes. No
   production code, author tests, dependency pins, historical logs or component
   sources changed. The complete host diff is the single provenance document.
4. All **253 working files** match the candidate's committed bytes and their
   applicable reference. There are no unexplained composition substitutions.
5. The da85-to-e75 advancement comprises exactly 22 paths: 19 analog-source
   additions plus DOT_START.md, root README.md and docs/progress.md. There is no
   host, firmware, CI or shared-contract delta in that advancement. Both mains'
   host subtree is `c6539e2d3fc588ddad972334c5e1e5f054054aa3`.

### Provenance-document review

The [complete sole host patch](evidence/mvp-main-e75/sole-host-doc.patch) correctly
identifies e75 as this candidate's immediate parent and separately identifies
16c40/da85 as the historical host provenance. It distinguishes unchanged software
source evidence from the new full-tree/parent reconciliation check, preserves
historical failure discussion, and explicitly keeps remote, resolver, Windows,
protocol and physical gates open. No unsupported acceptance claim was introduced.
The new document delta passes `git diff --check`.

### Preserved historical whitespace

The complete main-relative `git diff --check` exits 2 with **10 inherited trailing-
whitespace warnings**, all in unchanged historical host logs. Those bytes were
not rewritten to produce a cleaner review. [Raw output](evidence/mvp-main-e75/main-relative-diff-check.log)
and [classification](evidence/mvp-main-e75/inherited-whitespace.json) prove every
reported path is an unchanged 16c40 host file. This is an inherited informational
finding, not a clean full-diff claim; the sole new document delta is clean.

## Fresh checks on this exact composition

Environment: Linux, CPython 3.12.14, existing pinned Qt/serial dependencies,
`QT_QPA_PLATFORM=offscreen`; detailed environment and command paths are in
[environment.json](evidence/mvp-main-e75/environment.json). No dependency was
downloaded or installed. Python bytecode writing was disabled for these runs.

| Check | Result | Evidence |
|---|---|---|
| Complete host suite, normal | 1,068 passed, 54.603 s | [log](evidence/mvp-main-e75/full-normal.log) |
| Complete host suite, optimized | 1,068 passed, 55.877 s | [log](evidence/mvp-main-e75/full-optimized.log) |
| Independent gated idle-read Start, duplicate rejection, Stop/shutdown cancellation | 3 normal + 3 optimized passed | [normal](evidence/mvp-main-e75/start_poll_repro-normal.log), [optimized](evidence/mvp-main-e75/start_poll_repro-optimized.log) |
| Independent queued Disconnect/incomplete-result ACK recovery | 1 normal + 1 optimized passed | [normal](evidence/mvp-main-e75/incomplete_disconnect_repro-normal.log), [optimized](evidence/mvp-main-e75/incomplete_disconnect_repro-optimized.log) |
| Native thread completion boundary | 100 completions; zero premature finished events | [log](evidence/mvp-main-e75/native-join.log) |
| Final full source recheck | All tracked bytes and exact tuple unchanged | [record](evidence/mvp-main-e75/final-source-recheck.json) |

Both aggregate runs executed the complete suite without skipped tests. Their
`intentional child failure` text comes from a passing negative subprocess test;
it is not a suppressed aggregate failure. Offscreen Qt limitations/warnings are
retained. These results do not validate Windows packaging or an actual desktop,
serial driver, device, waveform timing or electrical setup.

Full normal/optimized regression was chosen despite byte-identical software to
bind fresh aggregate evidence to the new working tree and check path/environment
integration. The targeted real-Qt probes additionally verify that the prior two
GUI ownership/admission corrections and native-shutdown guarantee remain active.
No new GUI layout, protocol or physical implementation is claimed by this review.

## Previous review and evidence preserved

The original 16c40 worktree still has that HEAD and no tracked changes. Its review
and recovery archive retain the previously delivered hashes:

- Report SHA-256: `4c2db4e0b66fd019c3ba5450df3fceef9f9c0a19a1b707e685164125c23919fb`
- Archive SHA-256: `028c87dfeec1755e4a7dac42d6bfed9d7fcf10aa79d7a157532bae1f87f9335c`

The [preservation check](evidence/mvp-main-e75/prior-evidence-preservation.json)
records that comparison. Earlier failures, their corrections and the prior
critic-oracle explanation remain in that separate frozen archive; this review
neither rewrites nor retroactively changes its verdict.

## Fixed rubric and acceptance boundary

Quality-gate v1.0 (2026-10-10) remains in force. All criteria and weights are kept:

| Criterion | Weight | Basis within this reconciliation |
|---|---:|---|
| C1 Correctness/requirements | 30% | Exact candidate/parent, 39 main files and 213 inherited host files verified; accurate sole provenance-doc delta |
| C2 Reliability/failure handling | 20% | Full regressions and explicit Qt command/ownership/native-completion probes passed |
| C3 Verification/evidence | 25% | Fresh exact-composition evidence, raw logs, independent inventory and unchanged-source recheck; external/platform gates absent |
| C4 Clarity/maintainability | 15% | One bounded explanatory document change; implementation boundaries and historical claims retained |
| C5 Reproducibility/documentation | 10% | Reproduction scripts, source hashes, preserved old artifacts and new recovery archive; required remote/resolver/Windows/protocol evidence still missing |

**C1–C5 and weighted total: null. Verdict: BLOCKED_EVIDENCE.** No score is carried
forward from a prior component or manufactured from passing test counts.

Next gate: authorized exact remote binding and the still-required resolver,
Windows and common-protocol evidence, with appropriate independent verdicts.
Any later source/tree change requires its own impact review. No new P0/P1 remains
open in this bounded local composition, but the analog-source material is merely
preserved here, not independently accepted. Real USB, physical reset, calibration,
low-voltage acquisition, ignition and electrical safety remain unverified by this
review.
