# Transparent review evidence: PR139, PR140 and PR142

This is an evidence-only delivery. It does not modify application, firmware,
protocol, build recipes or CI, and it is not a tested combined application.
The exact independent reports keep their original component scopes and limits.

## Source identities and complete reports

- PR139 axis navigation: remote `da469ceaa6421de40357376b859ad5175d6d4984`,
  local `b57cbdd356e0b3c68e2758a33fe069c51e87c7bb`, tree
  `062ef56c1fa13e3600a30873e273156bcd3fe921`.
  [Published complete report](https://github.com/font-weight/stm32_motortester/pull/139#issuecomment-6125658985).
- PR140 multiline acquisition controls: remote
  `7e317224ea802d64b4686274cd76c3603e760321`, local
  `3a718eda6587b623b6ea3d06fd293f2068a351b5`, tree
  `131f11ec4ee21d5c281cae220baebee03906724b`.
  [Published complete report](https://github.com/font-weight/stm32_motortester/pull/140#issuecomment-6125393470).
- PR142 GNU text supplement: remote `24eadd15530a84384f58f9edcb50ddfea8785796`,
  local `699363047fa923c128fd41da67326bc008acbcdb`, tree
  `03e334c06f3adabad704b5113b40a13f8b25ee36`.
  [Published complete report](https://github.com/font-weight/stm32_motortester/pull/142#issuecomment-6125431797).

All three component source commits have sole parent PR132
`3bc7a251cd36714b1fec350c99e0e5b43ffc4950` and were reviewed separately.
Local and published commit identities differ, but their source trees match as
proved in each report. Literal local commit-object text is included so that
identity can be checked without a binary Git bundle. A combined source needs
its own exact-version review; these reports do not supply it.

## What is delivered

`DELIVERY-MANIFEST.json` lists the original UTF-8 artifact names, sizes,
SHA-256 values and ordered representation files. Reports, reviewer-owned probe
scripts, complete representative normal/optimized logs, source/remote identity
manifests, explicit negative controls and earlier reviewer-harness failures are
included unchanged. The explanations of those earlier failures remain in the
full reports. No production assertion or original evidence file was edited.

Long text files are delivered as literal UTF-8 parts, each under 88,000 bytes.
Concatenate listed parts in order **without separators** and check the original
size and SHA-256 from the manifest. This is plain text, not base64, a compressed
archive or a binary payload. For example:

    cat pr140/full-normal.log.parts/part-*.txt > /tmp/pr140-full-normal.log
    sha256sum /tmp/pr140-full-normal.log

`DELIVERY-SHA256SUMS` hashes every delivered representation file except itself.
Each reviewer's original `SHA256SUMS` remains unchanged and can name additional
retained artifacts. Its existence does not mean all those artifacts were uploaded.

The manifest explicitly inventories retained local-only PNG images, binary
archives and selected redundant large traces. Their hashes do not deliver their
bytes. No PNG, font, wheel, ZIP, tar archive, executable or other binary is
included in this evidence PR. Earlier temporary workspaces were lost in an
environment replacement: older archives are not asserted currently available
unless separately recovered and hash-matched. Historical published reports keep
their original attribution and test dates.

## Replaying the behavioral probes

Use a fresh isolated checkout of the applicable **source** commit above, not the
old application files inherited by this evidence-only branch. Install/use the
exact dependencies listed by the report under normal local permission rules;
this delivery contains no installer and performs no automatic network access.
The original Linux environment was CPython3.12.14, Qt/PySide6/shiboken6 6.12.0,
pyqtgraph0.14.0, numpy2.5.3, colorama0.4.6 and pyserial3.5. Offscreen execution
is not Windows qualification.

For the GUI probes, set `PYTHONPATH` to that checkout's `host` and `host/tests`
plus the matching dependency directory, set `QT_QPA_PLATFORM=offscreen` and
`PYTHONDONTWRITEBYTECODE=1`, then run the copied `layout_probe.py`,
`axis_probe.py` or `lifecycle_probe.py` with ordinary and optimized Python.
Use the corresponding component report for exact test selectors, scale values
and negative-control environment variables. Probe-created screenshots belong
in a writable evidence directory. The source/version verifiers record their
original absolute paths; inspect and recreate that isolated layout, or explicitly
record any local path adaptation. Do not represent such an adaptation as an
unchanged original execution. Some original remote-verifier inputs are retained
locally rather than copied; immutable remote source trees remain accessible by
the exact public commits. The delivered manifests are sufficient to inspect
source equality without treating a current branch tip as historical evidence.

The package behavioral probe `pr142/independent_probes.py` expects an isolated
`/tmp/notice-supplement-critic/source` export, its evidence directory containing
`UPSTREAM-VERIFIED.json`, and a Git checkout at `/tmp/motortester-host-qt-notices`
containing the baseline PR132 commit. Its two output directories must not already
exist. It uses synthetic distribution/CPython fixtures and also the recovered
Linux metadata; it does not inspect a Windows installation. The original fixed
paths and probe bytes are preserved. Never overwrite an existing user's path
merely to reproduce this layout. The unchanged source tests themselves can be
run from the source checkout without that historical directory arrangement.

## Retained limits

Actual corrected Windows behavior, clean Windows10/manual use, physical COM/USB,
low-voltage/ignition qualification and product release remain separate gates.
PR139 records inherited large-font main-header clipping. PR140's Linux measured-
width model does not claim a native Windows result. PR142 closes only the GNU-
text copying omission: complete notices, corresponding source/recipient access,
working replacement, licensing authority and redistribution clearance stay open.
The paused historical PR95 inquiry is not executed or reclassified here. No new
record-now protocol or capture semantics are adopted by this evidence delivery.
