# HOST MVP union: independent Critic-B review

## Current verdict

**BLOCKED_EVIDENCE; score: null.** Final local composition and Linux software
regression are verified. Both newly identified P1 defects are corrected and
independently retested; no P0/P1 remains open in the tested local scope. Required
remote binding, Windows wheel resolution, applicable Windows validation and
common-protocol acceptance remain absent. No prior component score transfers to
this union, and local success is not a software-ready or hardware-ready claim.

## Exact final candidate

- Final source: `16c40a8434c33b4e02bb71bf21294d58a90c5a18`
- Final tree: `88afe1d41f3c24ef032e1e17a6fd12655d6f5bfa`
- Sole parent/main: `da85d0aed68828214057797a5bd78f7b9f49ed35`
- Corrected GUI source: `25db86d83e878dd6e9ffbdd287c2c0ab23a97d43`
- GUI source tree: `8270b7fa1695ee3bfddd0afb6eacf3588385455d`

The final source has exactly the same tree as historical integration checkpoint
`57d0b62668163806654cba3f5582beceb110e80e`, whose parent is the initially defective
union below. The final source is separately parented directly to main; that
history distinction was checked rather than assuming identical parentage from
identical files. No main merge or external publication is established here.

[Final composition checker](evidence/mvp-union/check_composition_final.py),
[log](evidence/mvp-union/composition-final.log) and
[full tree/hash result](evidence/mvp-union/composition-final.json) verify the final
HEAD/tree/sole parent, all 20 unchanged non-host entries, all 27 original overlay
entries, and the complete five-path corrected-GUI delta and its explicit hashes.
Only the same three union-specific documentation paths differ from that exact
GUI-plus-component construction. All tracked working bytes match final source.

## Exact initial composition

- Repository: `font-weight/stm32_motortester`
- Initial union: `a25285dc98ca0d49d3c8eb1ba0e5c4f847cc0556`
- Tree: `0e531eb2b57f4be31f656c2062aa82c1818968b6`
- Sole parent/main: `da85d0aed68828214057797a5bd78f7b9f49ed35`
- GUI source: `22eab1b4dce04f057ddb2c9654dfab176e166c23`
- Stop source: `a60a05c0e0b0ce9db0c907843a5be97be73e64e1`
- Early RELEASE source: `9a32271fcf40ac3b47fb0c9e81a66ec43ef8033a`
- Package source: `8ecaa1892d2894050d8490d4c1232df74b391cbd`

Reviewer is the same independent Critic-B who checked the GUI integration.
Reviewer has written no production or author-test code. This report and its
`host/reviews/evidence/mvp-union/` directory are the only review-owned writes.
The review applies the existing quality-gate v1.0 rubric and task-specific source
composition, asynchronous Qt, Stop/early-RELEASE, byte-mode and packaging-impact
checks. Scope remains Linux synthetic software; it is not Windows/USB/hardware
acceptance.

[Independent composition checker](evidence/mvp-union/check_composition.py) and
[full result](evidence/mvp-union/composition-check.json) prove:

- All 20 non-host tracked entries exactly match main, including Git mode/blob
  identities; no source outside `host/` is changed.
- All 27 listed overlay entries match their referenced component commit, size
  and SHA-256. There are 26 unique paths; the only overlapping component path is
  `host/motortester/device_emulator.py`, intentionally Stop then parity.
- Final emulator SHA-256 is
  `f733ab0fad14a443402a26f437a399fe69499ef1a68a8fa793a64d8d630daef5`.
- All other host blobs exactly match the GUI source except three reviewed
  union-specific documents: `acquisition-integration.md`, `mvp-union.md`, and
  `union-source-manifest.json`. README is byte-identical to GUI source.
- Exact HEAD, tree and sole parent match the declared tuple. Local working
  production bytes match those Git blobs.

An initial checker omitted the intentional `acquisition-integration.md` prose
update from its allowed documentation list. Its failed assertion is retained in
`composition-initial-oracle.log`; inspecting that one-paragraph source diff
justified correcting only the checker. It was not a production mismatch.

## Personally executed initial evidence

Commands use existing dependencies only:
`PYTHONPATH=/tmp/motortester-deps:/tmp/motortester-serial-r2-deps:host`,
`QT_QPA_PLATFORM=offscreen`, `XDG_CACHE_HOME=/tmp/motortester-cache`.
Environment is recorded in [environment.json](evidence/mvp-union/environment.json).
No dependency download, installation, Windows resolver retry or external write
was performed.

- [Full normal](evidence/mvp-union/full-normal.log): 1,065 tests passed, 54.220 s.
- [Full optimized](evidence/mvp-union/full-optimized.log): 1,065 tests passed,
  55.321 s. Logged `intentional child failure` is an exercised negative subprocess
  path within passing package tests, not an ignored failed aggregate.
- Real Qt bridge/MainWindow impact: [13 normal](evidence/mvp-union/gui-normal.log)
  and [13 optimized](evidence/mvp-union/gui-optimized.log) passed. Covers no ARM on
  connection, staged CONFIG/ARM and Stop, mode/edge file roundtrips, immutable
  retention, partial windows, file-operation interlocks, hidden panel, reconnect,
  failed close retry and completed native shutdown.
- [Native completion probe](evidence/mvp-union/native-join.log): 100 lifecycles,
  zero public-finished events before native join.
- [GC-pressure real-GUI stress](evidence/mvp-union/gui-gc-stress.log): failed the
  enabled-Start admission scenario below. It is not reported as a pass.
- Independently reran the byte-peer mode matrix: 19 tests normal and 19 optimized,
  each 120 saved/reloaded windows across three modes/two edges. Manifests include
  per-window file hashes, Auto timeout/signal observations, exact source hashes
  and unchanged-source confirmation. [Summary](evidence/mvp-union/byte-impact-summary.json).
- Reconstructed old runtime hash `902e26df...` remains a meaningful negative
  control: exactly one expected Auto-timeout failure, no incidental error;
  [negative log](evidence/mvp-union/byte-modes-old-runtime-negative/tests.log).

## Personally executed final evidence

Every final test below was run after the corrected final tree was frozen. Both
complete aggregate modes use all tests; no failing scenario was excluded.

| Check | Final result | Evidence |
|---|---|---|
| Complete normal suite | 1,068 passed, 58.572 s | [log](evidence/mvp-union/full-final-normal.log) |
| Complete optimized suite | 1,068 passed, 56.485 s | [log](evidence/mvp-union/full-final-optimized.log) |
| Independent real-Qt integration | 13 normal + 13 optimized passed | [normal](evidence/mvp-union/critic_probe-final-r2-normal.log), [optimized](evidence/mvp-union/critic_probe-final-r2-optimized.log) |
| Gated idle-read Start and priority cancellation | 3 normal + 3 optimized passed | [normal](evidence/mvp-union/start_poll_repro-final-r2-normal.log), [optimized](evidence/mvp-union/start_poll_repro-final-r2-optimized.log) |
| Queued Disconnect/incomplete ownership ACK | 1 normal + 1 optimized passed | [normal](evidence/mvp-union/incomplete_disconnect_repro-final-r2-normal.log), [optimized](evidence/mvp-union/incomplete_disconnect_repro-final-r2-optimized.log) |
| Real-GUI GC pressure, thresholds (5, 1, 1) | 65 passed, five batches | [log](evidence/mvp-union/gui-gc-stress-final-r2.log) |
| Native shutdown ownership | 100 completions, zero before native join | [log](evidence/mvp-union/native-join-final.log) |
| Byte-peer mode matrix | 19 normal + 19 optimized passed; 120 saved/reloaded windows each | [normal manifest](evidence/mvp-union/byte-modes-final-normal/manifest.json), [optimized manifest](evidence/mvp-union/byte-modes-final-optimized/manifest.json) |
| Old-runtime Auto negative control | Exactly one expected failure; source unchanged | [manifest](evidence/mvp-union/byte-modes-final-negative/manifest.json), [log](evidence/mvp-union/byte-modes-final-negative/tests.log) |

The final mode matrices cover Auto/Normal/Single and both edges, Auto timeout
limits 100/60,000 ms, real-event versus timeout metadata, and exact capture/file
roundtrips. The old-runtime control remains a specific semantic failure rather
than an import, fixture or test-run error.

Final actual Qt renders cover main-window logical sizes 1240×800 and 1000×710,
acquisition-dialog sizes 560×650 and 450×450, and scale factors 1, 1.5 and 2.
Top/bottom scroll positions, device ratios and widget geometry are retained.
Reviewer inspected the actual small/large rendered dialogs: controls, warnings,
settings and buttons remain readable/reachable, with scrolling as documented.
These are running Qt grabs, not mockups, and do not establish Windows behavior.

One final stress attempt exposed a critic-oracle flaw: it disconnected immediately
after WINDOW_BEGIN yet required nonempty DATA fragments. A metadata-only partial
result legitimately has no sample fragments. The critic changed only its own
peer/probe to wait for an emitted complete DATA frame before that particular
sample-fragment assertion. The failed log is preserved as
`gui-gc-stress-final-oracle-negative.log`; all 65 corrected stress cases passed.
This did not remove a production failure or weaken the ownership/recovery checks.

## Newly reproduced P1 findings

### U-P1-01: enabled Start rejected during ordinary idle serial polling — corrected

The GUI reports READY, `busy=False`, and enables Start. During a background idle
serial read, `_Mailbox.executing=True` makes `_command` reject that user intent,
although the one-command mailbox slot is empty. The GUI tells the user to wait
for a previous action and retains no request. When the read ends, acquisition
remains stopped. This is user-visible operation loss, not a test timeout alone.

[Deterministic real-Qt reproducer](evidence/mvp-union/start_poll_repro.py) gates
only backend idle read. It checks one queued Start, rejection of 100 duplicates,
exactly one eventual CONFIG/ARM, and Stop/shutdown cancellation before releasing
the gate. Initial admission fails in all three scenarios. Preserve
[start negative](evidence/mvp-union/start-idle-poll-negative.log) and
[three-scenario negative](evidence/mvp-union/start-idle-poll-three-negative.log).

Expected correction: distinguish ordinary background polling from an executing
control action; admit at most one pending user command without bypassing current
command/Stop/disconnect/shutdown/ACK barriers. No GUI auto-retry or unbounded queue.

Verified correction: a separate `control_executing` flag distinguishes command/
priority/ACK work from ordinary polling. Final control ownership release and state
publication share one critical section; queued-state drain respects executing
controls. The deterministic independent probe now admits exactly one Start,
rejects 100 duplicates, produces exactly one CONFIG/ARM, and confirms Stop and
shutdown cancel the queued Start without subsequent ARM.

### U-P1-02: rejected incomplete-result ACK permanently blocks reconnect — corrected

The worker publishes an incomplete result and enters backend close. Before that
queued notification drains, the GUI requests Disconnect. MainWindow retains the
exact incomplete object, but `acknowledge_incomplete` rejects it because
Disconnect is pending. MainWindow ignores the false result and never retries.
After disconnect settles, runtime still has the incomplete result and blocks
reconnection indefinitely. The previous full capture is correctly preserved,
but there is no user-accessible ownership-release recovery path.

[Deterministic real-Qt reproducer](evidence/mvp-union/incomplete_disconnect_repro.py)
uses a backend-close gate and natural queued delivery; it does not inject a fake
incomplete object. [Negative log](evidence/mvp-union/incomplete-disconnect-negative.log)
shows exactly one rejected ACK and retained `has_incomplete=True` after close.

Expected correction: retain the exact result for a later bounded ownership ACK
when pending barriers clear. Do not synthesize a full graph, auto-reconnect, or
restart acquisition. Test that reconnect becomes possible after the ACK while
the prior full capture remains unchanged.

Verified correction: MainWindow keeps `_incomplete_waiting_for_safe_ack` for the
exact retained object and retries only after the READY/RECOVERY control barriers
clear, outside closing/busy state. The same natural queued-notification
reproducer now observes the first rejected ACK, a subsequent accepted ACK for
the identical result, unchanged previous full capture and explicit handshake-only
reconnection. No synthetic samples, automatic reconnect or restart were added.

Exact source hashes for these failures are recorded in
[initial-findings-source.json](evidence/mvp-union/initial-findings-source.json).

## Required gate and fixed rubric

| Criterion | Weight | Evidence/state |
|---|---:|---|
| C1 Correctness/requirements | 30% | Exact final composition proven; both new P1 defects corrected and independently retested |
| C2 Reliability/failure handling | 20% | Native lifecycle, bounded Start admission and incomplete ownership recovery verified with explicit negative/positive controls |
| C3 Evidence/verification | 25% | Full normal/optimized, real Qt and byte-mode checks; negative controls and failures retained |
| C4 Clarity/maintainability | 15% | Module boundaries preserved; five-path GUI correction is bounded and explicitly bound in the manifest |
| C5 Reproducibility/documentation | 10% | Exact local composition checks; required external/platform evidence remains absent |

C1–C5 and weighted total are **null** while required evidence is missing. Verdict remains **BLOCKED_EVIDENCE**, not a local acceptance
score or a transferred component PASS.

Any later successor requires a new exact source composition/manifest and
affected/full regression as applicable. Formal acceptance still requires the
remote tuple, permitted 14-wheel resolver evidence, applicable Windows checks,
and independent common-protocol judgments. The source-only component manifest
explicitly omits some historical component evidence trees; their relative links
must not be presented as a complete shipped evidence package. Real USB/firmware,
physical reset, electrical safety, calibrated timing, low-voltage and ignition
measurements are not established by this review.
