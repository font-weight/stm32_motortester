# Independent Critic-B: bounded axis navigation, PR139

**Scoped verdict: PASS, 10.00/10.** This accepts the three-file source/Linux Qt navigation change described below. It is not whole-application, Windows, packaging, hardware, or joint-contract acceptance. Those broader claims have no new score here. The inherited acquisition-panel Windows failure remains open on this exact branch, which does not contain PR140.

## Exact version and independence

- Remote draft PR139: `da469ceaa6421de40357376b859ad5175d6d4984`.
- Tree: `062ef56c1fa13e3600a30873e273156bcd3fe921`.
- Sole remote/local parent: PR132 `3bc7a251cd36714b1fec350c99e0e5b43ffc4950`.
- Executed local commit: `b57cbdd356e0b3c68e2758a33fe069c51e87c7bb`, same tree and parent.
- Reviewer: the continuing independent GUI Critic-B, not an implementation author. Reviewer writes were limited to separate probes, logs, images and this report. No source/test/doc fixes, publication, CI selection, firmware/protocol execution or merge were performed by this reviewer.

`source-proof.json` independently verifies all 1,388 tracked file bytes against Git blobs, clean worktree, exact three-path delta and 1,385 inherited files. Only `host/motortester/gui.py`, `host/docs/help.md`, and new `host/tests/test_axis_zoom.py` differ. Production AST changes are confined to `MainWindow.__init__`, `_build_ui`, and `reset_view`; all other production statements are identical. Acquisition panel, bridge, runtime, recording and firmware/CI remain inherited.

`remote-verification.json` binds the actual remote commit, parent, PR/ref, all 1,658 recursive path/mode/type/blob/size entries and all three fetched changed file bodies. SHA-256:

- gui.py: `9db091b7d6384d458c2402e1caa327437ed2a88e0671872cb7ad7e3d148007a9`
- help.md: `785a35c6d44d7e4a74e2a2b5d998b94981018ee2e432bc7a7cb6008db58b2d75`
- test_axis_zoom.py: `45fb3e24a26581332e92515c5cd6742cd023f3bcbc62e357162dbb4cf5966608`

Local commit identity differs from the connector-published commit; this is explicit, not hidden attribution. No latest-main or synthetic-merge qualification is claimed.

## Findings and behavior

No new blocking defect was found in the bounded navigation change. Native pyqtgraph axis routing already existed: the change makes it discoverable and constrains display ranges, without claiming new acquisition modes or hardware gain. Bottom-axis wheels/drags affect X only, left-axis events Y only, plot events both. Limits remain finite, time comes from the current capture's nominal Fs and trigger origin, X cannot zoom below one sample interval, Y below one code, and pan/zoom-out stay inside the documented overview. Rail codes and constant signals retain the full 12-bit Y scale. Empty and singleton views do not manufacture samples.

The raw immutable capture, encoded recording bytes, plotted sample arrays and existing cursor sample indices remain unchanged by navigation. A real scene cursor drag after zoom snaps to the original samples. Home restores overview. Successful file/live-window replacement installs fresh bounds; a failed file open preserves the retained recording and view. New-window reset behavior is preserved and now stated explicitly in both on-screen and offline help.

### Visible inherited constraints

1. **Windows acquisition-panel failure remains:** PR139 inherits exact PR132 panel bytes. Actual run11 (`38240077834`, source `3bc7a251…`) had 17 viewport failures, including 74/134 px horizontal overflow. PR140's separate three-label correction is absent here. Linux passes below do not supersede that result. Actual corrected Windows qualification remains `BLOCKED_EVIDENCE`, score null.
2. **Large-font main-header clipping is independently reproduced, not silently excluded:** at 1000×710 logical pixels, Linux offscreen DPR1, replacing the existing stylesheet's 13px font with 18px or 22px clips unchanged header text. At 22px, subtitle text advance is 501.67px versus width306; origin badge434.41 versus content284; acquisition button native hint316 versus width305. At18px, subtitle410.41 versus326 and badge355.41 versus304. Default13px fits. `header-{baseline,candidate}.json` are identical: the exact PR132 baseline reproduces the same geometry/hints and screenshots. This prevents a whole-header large-font readability claim and must stay visible in later composite/Windows review. The existing declared zoom-guide cases concern the wrapped guidance, plot minimum height, separation and scroll reachability; those predicates all passed and none were removed. This report does not accept every main-window control under the artificial enlarged-font condition.

## Fresh reviewer execution

Environment: Linux, CPython3.12.14, Qt/PySide6/shiboken6 6.12.0, pyqtgraph0.14.0, numpy2.5.3, pyserial3.5; offscreen. `environment.json` records loaded module paths and exact versions. Official pinned dependency recovery was performed separately by the author/coordinator; this reviewer installed nothing.

- Full frozen-source discovery: **1,227 normal PASS in146.175s**, **1,227 optimized PASS in145.737s**, no skips. Logs `full-normal.log`, `full-optimized.log` and exit files.
- Independently written `axis_probe.py`: **5 normal PASS in48.015s**, **5 optimized PASS in47.629s** (`probe-*-r4.log`). Real Qt wheel/held-button scene events, independently calculated bounds, fractional rates1.00000001/3.7/999999.99999 Hz, one/two/seven/65,536 samples, endpoint triggers and timeout origin, repeated saturation/pan, byte/cursor preservation, Home, valid/failed reopening, and two explicit byte-emulator Single windows.
- At scales1.25/1.5/2: three actual event/cursor/guide cases each pass. Guidance checked at1000×710 with stylesheet13/18/22px, no plot-card horizontal overflow, complete wrapped height, separated labels, minimum100px plot and reachable label corners. Actual Qt screenshots at default and enlarged font were personally inspected; the header limitation above is visible, not omitted.
- Fresh reuse of the reviewer's independent `lifecycle_probe.py`: **7 normal and7 optimized PASS**. This uses the existing test SerialPeer only as synthetic byte backend. Checks real MainWindow/bridge callbacks, no ARM on repeated Connect, retain-before-exact-ACK, Single one-shot, all three modes/two edges, file retention, Auto timeout metadata, waiting Normal/hide/Stop, reconnect/stale CAPS, interrupted handshake, close failure/retry and exactly one public completion after native join. These are new executions on PR139, not transferred PR140 outcomes.
- Negative controls, final script: exact prior gui.py loaded in memory fails the finite-empty-overview oracle; disabling new limits fails all five extreme fixtures; consuming bottom-axis wheel events fails independent-axis dispatch. All three exit1 as expected, with original logs retained. No production file was mutated.

### Preserved reviewer harness errors

Initial failures are not hidden or relabeled as product failures. `axis_probe-first.py` used the graphics item's expanded `boundingRect`, which includes adjacent view geometry; its event-point precondition correctly rejected that target. `axis_probe-second.py` fixed targeting to the axis's own `rect`, then exposed two probe mistakes: it tried to drag a cursor already outside the heavily zoomed viewport, and compared a disk-loaded value by identity to the pre-load object. The next revision reset to a view containing that cursor and compared identity to the actual retained loaded object. It also incorrectly expected Stop disabled while READY; unchanged panel code intentionally permits Stop on a connected non-recovery session. Final oracle checks actual READY and retained capture/no automatic acquisition, while the separate byte-backend lifecycle probe verifies no ARM. Every earlier failing log/script remains. No timeout or production assertion was weakened.

An initial remote-ref lookup used the author's local branch name and returned404; the actual branch `dot-b/host-axis-zoom` was read from PR139 and verified. That lookup error is retained separately.

## Reproduction

From the exact source worktree, set:

```sh
export PYTHONPATH=/workspace/shared/motortester-runtime-recovery-20261012/deps:host:host/tests
export QT_QPA_PLATFORM=offscreen
export XDG_CACHE_HOME=/workspace/shared/motortester-runtime-recovery-20261012/cache
export PYTHONDONTWRITEBYTECODE=1
python -X faulthandler -m unittest discover -s host/tests -v
python -O -X faulthandler -m unittest discover -s host/tests -v
python /path/to/evidence/axis_probe.py
python -O /path/to/evidence/axis_probe.py
python /path/to/evidence/lifecycle_probe.py
```

For negative controls set `MUTANT=baseline` or `no_limits` and run `IndependentZoom.test_02_limits_extreme_fractional_rates_and_replacement`; for `MUTANT=no_axis` run `IndependentZoom.test_01_actual_axis_and_plot_gestures`. `HEADER_BASELINE=1 python header_probe.py` reproduces the inherited header case. `verify_source.py` and `verify_remote.py` document the original local path; if relocating evidence, adjust that path explicitly rather than claiming the old path exists remotely. `SHA256SUMS` indexes every retained artifact. Images/archives are local evidence unless separately delivered and identified.

## Rubric and scope

Quality-gate v1.0 weights are unchanged. C1 correctness10: independently verified axis semantics, units, bounds and preservation. C2 reliability10: empty/singleton/extremes/replacement/error/lifecycle checks. C3 evidence10: exact-source dual-mode full suites, actual events, meaningful negatives, preserved failed harness attempts and explicit Linux limits. C4 maintainability10: native routing retained, small confined change, visible/help explanations, no new state/protocol machinery. C5 reproducibility10: source/remote inventories, commands, dependency provenance, raw logs and archive checksums. Weighted scoped result **10.00**.

This is an internal software/navigation verdict, not a probability of correctness, whole UI acceptance or product release. Windows10 full-package/manual behavior, physical native USB and electrical measurements remain unproven here. Historical joint-contract acceptance is neither revoked nor reissued. Any PR139+PR140 or later composition needs its own exact-tree applicability review; this score does not transfer automatically.
