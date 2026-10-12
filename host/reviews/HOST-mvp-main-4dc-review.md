# HOST current-main reconciliation: independent Critic-B review

## Verdict and scope

**BLOCKED_EVIDENCE — score: null.** The exact local composition, sole provenance-
document delta and fresh Linux host regression are verified. No P0/P1 was found
in this bounded reconciliation. Exact remote publication/source binding is now
verified for draft PR108. The separate fresh Windows-wheel resolver verdict,
applicable Windows validation and common-protocol acceptance are not established
by this report. No earlier component score is
transferred and no release or `software-ready` status is asserted.

This continues the same independent Critic-B review of the 16c40 union and its
6a3 current-main reconciliation. The reviewer authored no production or author-test
change. New writes are limited to this report and
`host/reviews/evidence/mvp-main-4dc/`, plus temporary local reviewer probes;
prior reports and recovery archives remain
unchanged. No publication, installation, dependency download, resolver retry, PR95 runtime
or reviewer action, new worker or external write was performed by this reviewer.
PR95 remains outside this review and receives no substituted verdict.

The declared level is Linux synthetic host-software regression and exact local
source composition. The affected inherited scope is the host portion of
MVP-01/04/06, including command admission, retained capture ownership and safe Qt
shutdown. This document-only reconciliation introduces no new functional feature
or measurement claim; LV-001 and physical requirements remain separate.

## Exact candidate and references

- Repository: `font-weight/stm32_motortester`
- Tested local candidate: `33696f8a41d70196d7870a92b24d01def44a1eb2`
- Published candidate: `1af7c7c27e7eaa6559138b055ed40c7e3114abb1`
- Draft PR: [108](https://github.com/font-weight/stm32_motortester/pull/108)
- Published branch: `dot-b/host-mvp-main-delivery`
- Tree: `2ed75f6d5d9993af47a79cbfa0eb1e6b3568b2e9`
- Sole parent/named current main: `4dc8389ed2cb9bfbc89504f81d253972dd003d6b`
- Previous reviewed composition: `6a3bfef0a5e74e5370c912c0fc45f8cebba2ddd8`
- Previous main: `e75e8c78e7042c63dd4c4e152cc4b7b40db7045b`
- Original frozen host reference: `16c40a8434c33b4e02bb71bf21294d58a90c5a18`
- Original main: `da85d0aed68828214057797a5bd78f7b9f49ed35`
- Sole changed host file: `host/docs/mvp-union.md`
- Document Git blob: `ed0d6ae0e22356e2cdab87623d76d1fd65316324`
- Document SHA-256: `fb0f87ddb52fb597a1e30b76f80ae1a033f15ca667cfe6c05fe671c0e4cfb677`

These identities were independently checked against local Git objects, raw
working bytes and read-only GitHub connector observations. The published commit
identity differs from the local commit because it was created through connector
publication; its full tree and sole parent are exact matches. The remote binding
below closes the publication-identity gap for this snapshot, not for future
branch changes. The source document remains the reviewed pre-publication snapshot.

## Independent composition and document findings

The [reviewer-owned audit](evidence/mvp-main-4dc/composition_audit.py) reads Git
objects directly, rather than adopting the author's composition proof.
[Audit output](evidence/mvp-main-4dc/composition-audit.log) and the
[290-file inventory](evidence/mvp-main-4dc/composition-audit.json) establish:

1. HEAD, tree and exactly one parent match the declared tuple.
2. All **76 non-host entries** match 4dc main by Git mode/type/blob and exact raw
   bytes. BOM and CRLF values are recorded without normalization.
3. All **213 other host entries** match both 6a3 and 16c40. Production code,
   author tests, dependency pins, component sources and historical logs are
   unchanged. The complete host delta is the single provenance document.
4. All **290 working files** equal their committed bytes and applicable source
   reference. A final source recheck after the test runs found no tracked change.
5. The e75-to-4dc main advance comprises **38 paths**: 37 additions under
   `tests/native-host-reproducer/`, including the four ADC-fault package files,
   and `docs/progress.md`. There is no active host, firmware, CI or shared-contract
   change. Historical firmware/host/protocol snapshots occur only inside the
   reproducer package; they are not substituted for the active implementation.
   Both mains' host subtree is `c6539e2d3fc588ddad972334c5e1e5f054054aa3`.

The [complete sole-host patch](evidence/mvp-main-4dc/sole-host-doc.patch) contains
four replacement paragraphs. It accurately identifies the new immediate parent,
preserves 6a3 and 16c40 provenance, and distinguishes inclusion of historical
native/ADC reproducer sources from acceptance of the latest runtime policy.
It keeps historical failures and independent-review requirements explicit.

The publication/download permission update is an attributed coordination status,
not itself evidence that publication or resolver acceptance passed. The document says
that the author reports successful wheel download and that independent review is
pending. This reviewer did not inspect those wheel bytes or issue a resolver
verdict. Subsequent resolver evidence must be assessed by its responsible
independent reviewer. This reconciliation does not reactivate the paused PR95
review or run either historical reproducer.

The new document delta passes `git diff --check` with exit 0. The complete
main-relative check exits 2 with **10 inherited trailing-whitespace warnings** in
unchanged historical host logs. [Raw output](evidence/mvp-main-4dc/main-relative-diff-check.log)
and [classification](evidence/mvp-main-4dc/inherited-whitespace.json) preserve that
negative result and prove all affected files are unchanged. No log was rewritten
to obtain a clean diff claim. An unrelated initial directory-discovery permission
warning is also retained in the evidence; it did not affect candidate access.

## Independent remote publication and source-byte binding

At approximately 05:36 UTC on 2026-10-12, this reviewer directly read the
[PR metadata](evidence/mvp-main-4dc/remote-pr108.json),
[remote commit](evidence/mvp-main-4dc/remote-commit.json) and the complete,
non-truncated [recursive tree](evidence/mvp-main-4dc/remote-tree.json) through the
read-only GitHub connector. PR108 was open, draft and unmerged, with the declared
remote head and base. The commit has exactly one parent, 4dc, and exactly the
tested local tree, 2ed75.

The [binding audit](evidence/mvp-main-4dc/remote_binding_audit.py) independently
compares all **341 Git entries**, including directories. For every one of the
**290 tracked files**, it recomputes the Git blob hash from the actual tested raw
working bytes and matches that hash and size to the remote tree entry. This binds
all source bytes through Git content addressing, rather than trusting a supplied
commit label. In addition, **five remote blobs were fetched directly**: the sole
changed document, acquisition bridge, main GUI, runtime and device emulator. All
five decoded UTF-8 payloads equal the local bytes exactly, including length and
Git/SHA-256 checks. No other blob is claimed as a separate direct download.

[Raw direct payloads](evidence/mvp-main-4dc/remote-direct-blobs.json),
[verification output](evidence/mvp-main-4dc/remote-binding.log) and
[full binding results](evidence/mvp-main-4dc/remote-binding-result.json) are retained.
The initial reader incorrectly expected REST JSON for the blob endpoint, while
the connector returns decoded UTF-8 source; that parser-only failure is preserved
in `remote-fetch-parser-negative.log`. It was corrected before byte comparison.
No production code or remote state was modified.

The local pre-binding report draft is preserved as
`local-review-before-remote.md`; it is historical and is superseded by this
report only for the newly established remote binding. Prior frozen 6a3/16c40
reports remain untouched. Full tests are not counted a second time against the
remote commit: the exact unchanged tree and parent bind the local executions.

## Fresh checks on this exact composition

Linux, CPython 3.12.14, PySide6 6.12.0, pyqtgraph 0.14.0, NumPy 2.5.3 and
pyserial 3.5 were used from the existing dependency directories. Qt used the
offscreen platform; bytecode writes were disabled. Full details are in
[environment.json](evidence/mvp-main-4dc/environment.json), and exact commands,
exit codes and log hashes are in [run-results.json](evidence/mvp-main-4dc/run-results.json).

| Check | Result | Evidence |
|---|---|---|
| Complete host suite, normal | 1,068 passed, 56.541 s | [log](evidence/mvp-main-4dc/full-normal.log) |
| Complete host suite, optimized | 1,068 passed, 55.268 s | [log](evidence/mvp-main-4dc/full-optimized.log) |
| Independent gated idle-read Start admission, duplicate rejection, Stop/shutdown cancellation | 3 normal + 3 optimized passed | [normal](evidence/mvp-main-4dc/start_poll_repro-normal.log), [optimized](evidence/mvp-main-4dc/start_poll_repro-optimized.log) |
| Independent queued Disconnect/incomplete-result exact ACK recovery | 1 normal + 1 optimized passed | [normal](evidence/mvp-main-4dc/incomplete_disconnect_repro-normal.log), [optimized](evidence/mvp-main-4dc/incomplete_disconnect_repro-optimized.log) |
| Native thread completion boundary | 100 completions; zero public finished events before native join | [log](evidence/mvp-main-4dc/native-join.log) |
| Final source tuple and byte recheck | All 290 tracked files unchanged | [record](evidence/mvp-main-4dc/final-source-recheck.json) |

Both aggregate runs executed the complete suite without skipped tests and exited
0. Their `intentional child failure` text belongs to a passing negative subprocess
test, not a suppressed aggregate failure. Raw Qt offscreen warnings are retained.
The independent probes are unchanged copies of this reviewer's earlier probes;
they import and exercise the current checkout's source.

Although software bytes are identical, fresh normal/optimized aggregate runs
were chosen to bind evidence to the new working tree and check path/environment
integration. Targeted real-Qt probes additionally recheck the two earlier union
P1 corrections and the native-shutdown guarantee. The prior 65-case GC stress,
mode-specific 120-window matrix and rendered size/scale evidence were not rerun
as independent matrices for this document-only reconciliation; their historical
results remain separate and are not counted as new executions here.

These checks do not validate a Windows desktop or installer, Windows serial
drivers, real USB, waveform timing, physical reset, calibration, low-voltage
measurements, ignition, vehicle operation or electrical safety. The newly
included historical native/ADC sources and analog hardware are preserved,
not independently accepted through this host review.

## Prior evidence preserved

Both earlier source worktrees retain their exact HEADs and have no tracked
changes. Their reports and archives retain all four previously delivered hashes,
verified in [the preservation record](evidence/mvp-main-4dc/prior-evidence-preservation.json):

| Prior artifact | SHA-256 |
|---|---|
| 6a3 report | `a98a4892b67fa03dbb765badaeb694df43676b4470faaecc47d61268bff01e93` |
| 6a3 archive | `559fe1adc9df5af950e88f93f96574f945f3f5fe5e9489f8c608a316bebe829c` |
| 16c40 report | `4c2db4e0b66fd019c3ba5450df3fceef9f9c0a19a1b707e685164125c23919fb` |
| 16c40 archive | `028c87dfeec1755e4a7dac42d6bfed9d7fcf10aa79d7a157532bae1f87f9335c` |

Earlier failed runs, corrected implementations and the DATA-gating stress-oracle
explanation remain in the original frozen artifacts. They were not rewritten,
removed or retroactively relabeled by this continuation.

## Fixed rubric and acceptance boundary

Quality-gate v1.0 (2026-10-10) retains all criteria and weights:

| Criterion | Weight | Basis within this reconciliation |
|---|---:|---|
| C1 Correctness/requirements | 30% | Exact tuple, 76 current-main files, 213 inherited host files and truthful provenance delta verified |
| C2 Reliability/failure handling | 20% | Fresh aggregate suites and explicit Qt command/ownership/native-completion probes passed |
| C3 Verification/evidence | 25% | Independent inventory, raw logs and post-test source recheck; mandatory external/platform gates not established here |
| C4 Clarity/maintainability | 15% | One bounded explanatory document change; historical/source-only packages clearly distinguished from active runtime acceptance |
| C5 Reproducibility/documentation | 10% | Commands, probes, independent remote source binding, preserved prior artifacts and separate recovery archive; resolver/platform/common-protocol evidence still needs its own binding and review |

**C1–C5 and weighted total: null. Verdict: BLOCKED_EVIDENCE.** Passing test counts
do not manufacture a score. No P0/P1 remains open within this local reconciliation,
but the required missing evidence prevents acceptance of the broader candidate.

Next: obtain and bind applicable independent resolver, Windows and common-protocol
results. Any later source/tree change requires impact review. This report
establishes the exact publication/source binding only and does not close another
critic's gate.
