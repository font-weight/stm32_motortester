# Independent Critic-B: PR140 native multiline acquisition labels

**PASS, 10.00/10 for the exact bounded source correction, Linux Qt regression and remote binding. Windows/product qualification remains BLOCKED_EVIDENCE, score:null, pending an actual corrected-source Windows run and rendering evidence.** No prior score is transferred and no full-MVP, hardware, release or merge acceptance is issued.

## Exact version and scope

Same independent GUI Critic-B, not the implementation author. Quality gate: docs/quality-gate.md v1.0. This review addresses the measured Connect/checkbox width constraints in WIN131-VIEWPORT-01 while preserving the existing MVP-U02 controls and acquisition behavior.

- Local tested commit: `3a718eda6587b623b6ea3d06fd293f2068a351b5`.
- [PR140](https://github.com/font-weight/stm32_motortester/pull/140) remote head: `7e317224ea802d64b4686274cd76c3603e760321`.
- Shared exact tree: `131f11ec4ee21d5c281cae220baebee03906724b`.
- Exact local/remote sole parent: PR132 `3bc7a251cd36714b1fec350c99e0e5b43ffc4950`.
- Exactly three changed paths: host/motortester/gui_acquisition.py, host/tests/test_gui_acquisition.py and host/docs/acquisition-integration.md.

All1,387 working files match Git. All1,384 other files are unchanged, including main GUI, runtime, acquisition bridge, protocol, firmware, recipe and CI. AST comparison proves production differs only by replacing three word-boundary spaces with newlines; the comment is non-executable. Native QPushButton/QCheckBox types, all words, font/style, size policies, handlers and state logic are retained. All25 prior tests and14 existing geometry predicates remain; three glyph-bound assertions and three tests are added. Historical doc wording is explicitly made past-tense, with the run11 measurements added; no historical evidence report is edited. Diff-check and final clean-source checks pass.

Independently fetched remote commit, parent, branch ref, PR metadata, full recursive tree and all three actual changed bodies. All1,657 tree entries and1,387 file path/mode/blob/size entries bind exactly. Remote production SHA256: `99173d60c071f0bbd45733a0ab887f961b4ec8fc1bea17c7295de52a95fa17f8`. Verifiers, full inventories, body readbacks and patch are retained beside this report.

## Measured defect and correction

The primary Windows run11 log was independently retrieved after the environment reset and hash-matched:355,741bytes, SHA256 `15ddda4384153cae8f9edb456064f2498f39891df760f79594dbce0d98ddc398`. Source was PR1323bc7a251; evaluated workflow was `e21e92a42dc03d235123a5d250e174caa149c11f`. Run11 was a narrow25-test observation with17 failures, not a full-suite or packaging result.

All17 complete geometry objects were parsed. Connect minimum442 plus9+9px margins gives content460. Actual viewports386/326 give exactly74/134px overflow. Checkboxes independently require390/377px in Fusion and391/378px in Windows style; the form minimum is290. QFontInfo reports an empty actual family and invalid pixel/point sizes, so a resolved Windows font or readable physical glyph rendering is not established. The [separate Critic-A report](https://github.com/font-weight/stm32_motortester/pull/34#issuecomment-6123064273) was read, but these numeric checks were personally repeated from the primary log.

The candidate inserts semantic line breaks into the Connect sentence and both checkbox sentences, preserving every word. Qt now sizes and paints the full native multiline text. It does not hide a scrollbar, force a minimum-size override, reduce the font or discard the no-capture/reset/emulator information. This addresses all three measured constraints rather than only the largest button.

## Fresh reviewer execution

Environment: Linux, CPython3.12.14, PySide6/Qt6.12.0, numpy2.5.3, pyqtgraph0.14.0, pyserial3.5. Dependencies were restored once by the separate author from verified pinned official wheels; this reviewer used that installation and recorded actual module paths. No install was performed by the reviewer. These are local Linux executions on the exact local commit; remote execution is not implied.

- **Full1,219 normal tests PASS,133.880s; full1,219 optimized tests PASS,133.783s**, no skips. Complete logs/exit sidecars are retained. Mock packaging output inside source tests does not prove a real executable or archive.
- **288 independent layout cases PASS**, plus72 optimized cases: both Qt styles, native application-font extremes9/22pt, ordinary and measured-width-model rendering, IDLE/READY/RECOVERY, sizes560×650/420×360/360×300, scales1/1.25/1.5/2. Existing author tests in the full suite additionally cover14/18pt. All horizontal ranges are0. Checks cover full glyph width/height, all control/label corners, simultaneous whole-control visibility when it fits vertically, retained words, minimum hints, combo text bounds and no unsolicited bridge operation.
- Native checkbox second-line mouse clicks, clicked(bool) emission, Space toggling, focus and accessible names pass. Native types and full semantic text remain. The source suite additionally checks both text lines and the Connect second-line click. Personally viewed ordinary and synthetic-pressure renders at360×300 and scale2, plus a scale1 Windows-style disabled-control render. Disabled etching is a style decoration; the independent text oracle measures full glyph layout, with no relaxed width/height tolerance. Qt documents the distinction through [SH_EtchDisabledText](https://doc.qt.io/qt-6/qstyle.html#StyleHint-enum).
- **Seven fresh independent real MainWindow/bridge lifecycle cases pass normally and optimized.** Synthetic serial peer only: connect/repeated click without ARM; retained exact capture before ACK; Single one-shot; all three modes/both edges and bitwise sample file round-trips; Auto timeout metadata; Normal waiting/hide/Stop without restart; reconnect/no stale accepted CAPS; close failure retry; interrupted handshake with responsive GUI. Every case verifies exactly one public completion after native thread join. Final logs: `final-lifecycle-joined-*.log`.

The horizontal-metric model uses a test-only monospace font and real Qt letter spacing to produce13px character advance on the three controls, without replacing size hints. It reproduces the old442/390/377 widths and74px overflow at420×360. Heights and raster appearance differ from Windows, so this is explicitly a width model, not an emulated Windows screenshot.

## Meaningful negative controls and retained failures

Using the same real-Qt width model and zero-horizontal oracle, independent in-memory reversions reject each component:

- Restore all three original one-line labels:74px overflow.
- Restore only Connect:74px overflow.
- Restore only reset confirmation:22px overflow.
- Restore only emulator-event checkbox:9px overflow.

Each expected-negative process exits1 and retains its geometry/traceback; production files are never changed. This demonstrates that each of the three corrections is necessary under the observed width pressure.

Fresh probe development failures are also retained, not counted as product defects or hidden: the first lifecycle harness used the wrong Snapshot attribute (`session_id`, corrected to `session`); the first layout launcher accidentally discovered imported author test classes and used the no-argument clicked overload rather than clicked(bool). Initial reviewer scripts, negative logs and corrected checks remain. The accidentally discovered draft author glyph oracle also exposed disabled-style decoration bounds; final tests use full glyph bounds and preserve all pre-existing predicates. Final aggregate and independent runs occur on the frozen version after these harness corrections.

The environment reset removed former unuploaded /tmp reviewer archives. They are not claimed recovered. Published PR131/PR132 reports were read back and retained as historical snapshots; the complete run11 primary log and all new evidence live in this persistent review directory and its lossless recovery archive. Earlier run9/run10 failures remain explicit history and are not averaged away by these Linux results.

## Rubric and disposition

| Criterion | Weight | Score | Basis within bounded source-review scope |
|---|---:|---:|---|
| C1 correctness |30%|10|All original words/semantics retained; exact three-line-break AST delta; measured width constraints addressed without shrinking/clipping.|
| C2 robustness |20%|10|All three component negatives, native input/accessibility, DPI/state/size matrix and fresh asynchronous lifecycle/join checks.|
| C3 evidence |25%|10|Fresh full normal/optimized suites, independent real Qt model/oracles/renders, primary Windows measurements and exact remote bytes.|
| C4 maintainability |15%|10|Native controls and three text substitutions; no new production widget implementation or state machinery.|
| C5 reproducibility |10%|10|Honest measured-versus-modeled documentation, source/dependency identity, complete logs/scripts/negative controls and verified archive.|

Weighted result: **10.00**, scoped PASS. No open P0/P1 is found in the bounded source correction under the personally tested conditions. **WIN131-VIEWPORT-01 is not closed as a Windows/product gate:** require the new authorized exact Windows run and actual rendering evidence. Manual Windows10, full package/retention/licensing, USB/LV, physical safety and release requirements remain separate. No new common-protocol decision is made. Separate PR139 axis-zoom work and any future composition or newer-main merge are outside this verdict.

## Reproduction

From `/workspace/shared/motortester-host-wrap-controls-20261012`:

```sh
export PYTHONPATH=/workspace/shared/motortester-runtime-recovery-20261012/deps:host:host/tests
export QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/workspace/shared/motortester-runtime-recovery-20261012/cache PYTHONDONTWRITEBYTECODE=1
python -X faulthandler -m unittest discover -s host/tests -v
python -O -X faulthandler -m unittest discover -s host/tests -v
# Set QT_SCALE_FACTOR to1,1.25,1.5,2 in separate processes.
python /workspace/scratch/d2a4dc03255e/critic-wrap-controls-review/layout_probe.py
python /workspace/scratch/d2a4dc03255e/critic-wrap-controls-review/lifecycle_probe.py
python -O /workspace/scratch/d2a4dc03255e/critic-wrap-controls-review/lifecycle_probe.py
python /workspace/scratch/d2a4dc03255e/critic-wrap-controls-review/verify_source.py
python /workspace/scratch/d2a4dc03255e/critic-wrap-controls-review/verify_remote.py
```

Same critic; no candidate edits, external publication, CI action, installation, firmware/protocol change, merge or paused PR95 execution. Author results were read for coordination and are not counted as reviewer executions.
