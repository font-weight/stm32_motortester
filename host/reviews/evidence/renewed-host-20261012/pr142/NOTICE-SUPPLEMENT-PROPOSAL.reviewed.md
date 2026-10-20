# Proposed bounded task: pin Qt-family GNU notice texts

2026-10-12 13:16 UTC. Proposal only; no package source was edited. Root assignment is required before implementation.

## Verified basis

The published audit was restored from PR130 comment6122558122 and its body SHA256 exactly matches `63adb3890d6844b7eeae4505e059b9f3ce398fa62442078e1359ae50e35f53a1`. Its original wheel and historical-manifest bytes were lost with the earlier environment; the report is recovered evidence, not a fresh reproduction of that entire audit.

The recovered PR132 checkout is clean at `3bc7a251cd36714b1fec350c99e0e5b43ffc4950`, tree `d80d0d730a193480004382857375f5a0f6c65515`. Collector, recipe and dependency-lock blobs remain `53d501546755d37dafbb1767352f8dffbb9b574d`, `503a9f4aec1cd7293770226caaaa5369c16b3cca`, and `256a8005b4f7de19651c9b9bdd70419b5b52af3d` respectively.

Current A responses on PR132 and PR34 confirm run11 was a diagnostic-only failing source test, without production packaging. No later licensing decision or successful current package was found. The known collector gap remains: the recorded pinned Windows Qt-family wheels supplied conditional commercial-reference text, not the missing GNU texts. This does not mean the wheels are commercial-only.

## One useful fix

Vendor verbatim GPL-2.0-only, GPL-3.0-only and LGPL-3.0-only texts from the immutable PySide6.12.0 upstream source and require the collector to copy and record them for the pinned PySide6-Essentials/shiboken6 pair. Including the advertised alternatives avoids silently choosing a project license or substituting a commercial entitlement. The result closes only the reproducible GNU-text omission, not the complete Qt third-party/copyright notice, corresponding-source, replacement or redistribution audit.

Record exact notice-text provenance in the existing `vendored_notices` inventory entries: immutable upstream URL/commit, source Git blob, byte length, SHA256, relevant installed version and reference Windows wheel hash. The field naming/documentation must say that the upstream source is the source of the **license text**; it is not proof that the full source corresponding to the shipped binary was acquired, hosted or offered. Keep `manual_review_required: true` and all release/source/replacement gates open.

No network lookup during collection. Fail closed if either required Qt-family distribution is missing, has a wrong pinned version or appears twice under normalized naming; or if any required vendored text is absent/modified. Preserve existing pyserial validation, CPython copying, non-overwrite behavior and discovered-notice collection. Do not expand this fix into a collector framework or unrelated cleanup of code files matched by the old keyword heuristic.

## Exact upstream input evidence acquired now

GitHub's Qt mirror resolves PySide tag `v6.12.0` through annotated tag object `6aefae25747a8d860d201f3ab2b2e199b9e8e006` to commit `f50facb83fc3dc5f3240c854668c7a6dd93871b2`. Qtbase tag `v6.12.0` resolves through `fcef5b2c755492753eb8f7424dd2bc7337832068` to `025bdad181de241e81bf853c8a2d7bf3d19261a9`. The APIs report these tags unsigned; no signature-authentication claim is made. All three license blobs are identical in the two exact-version LICENSES directory listings.

The PySide files were fetched by immutable commit, their bytes saved only as research artifacts, and both Git blob identities and SHA256 recomputed:

| File | Bytes | Git blob | SHA256 |
|---|---:|---|---|
| GPL-2.0-only.txt | 18,092 | d159169d1050894d3ea3b98e1c965c4058208fe1 | 8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643 |
| GPL-3.0-only.txt | 35,147 | 94a9ed024d3859793618152ea559a168bbcbb5e2 | 8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903 |
| LGPL-3.0-only.txt | 7,651 | 65c5ca88a67c30becee01c5a8816d964b03862f9 | da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768 |

Source example: https://api.github.com/repos/pyside/pyside-setup/contents/LICENSES/LGPL-3.0-only.txt?ref=f50facb83fc3dc5f3240c854668c7a6dd93871b2 . The other two exact paths use the filenames above. Saved input/provenance records are under `upstream/`. The code.qt.io read route was inaccessible through the web tool; the permitted GitHub mirror read succeeded. No bulk source or wheel download/install occurred.

## Proposed file ownership, exclusively B packaging

Use a separate implementation checkout from the agreed exact base; do not write into the GUI author's working tree.

1. `host/packaging/collect_licenses.py`
2. `host/packaging/licenses/qt-6.12.0/GPL-2.0-only.txt` (new, exact upstream bytes)
3. `host/packaging/licenses/qt-6.12.0/GPL-3.0-only.txt` (new, exact upstream bytes)
4. `host/packaging/licenses/qt-6.12.0/LGPL-3.0-only.txt` (new, exact upstream bytes)
5. `host/tests/test_license_packaging.py` (new focused tests)
6. `host/tests/test_serial_packaging.py` (only adjust the existing collector-positive fixture to contain the now-required Qt pair; preserve all pre-existing assertions)
7. `host/packaging/LICENSE-REVIEW.md` (narrow Russian-language provenance/status update)

No `build_windows.py`, spec, dependency lock, GUI, plotting, acquisition, firmware, protocol, CI, source-offer destination, root project license or release change. The recipe blob remains unchanged; its transitively invoked collector deliberately changes and must receive a new source-specific packaging review. Root owns final composition/checkpoint documentation and any publication/A handoff.

## Acceptance before a scoped source PASS

- Independent verifier compares all three new files byte-for-byte to the saved immutable upstream responses and recomputes SHA256/Git blob IDs. No newline, translation, copyright or license-text edits.
- Successful temporary-directory collection includes the full texts, correct shared-path references and complete truthful notice provenance for both normalized distribution names. Existing discovered commercial-reference notices are retained; no commercial-only inference is introduced.
- Negative tests reject each missing/changed license, each wrong/missing Qt version, duplicate normalized distribution records and existing destination; no completed success inventory is produced on failure. Exercise ordinary and optimized Python, spaces/Cyrillic paths and the no-network property.
- Existing pyserial/CPython tests remain meaningful; only required fixture inputs change. No assertion weakening and no dependency upgrades.
- Run focused packaging suites in both normal and optimized modes with existing recovered dependencies only. Full host/composition coverage is root-owned when combined with independently reviewed GUI work. All tests are honestly classified as source/fake-backed, not actual Windows binary evidence.
- Independent packaging Critic-B reviews the exact diff and evidence under the existing threshold; the implementation author cannot approve it. No source score carries over to a later untested composition.
- Final current Windows output must eventually confirm the copied texts/inventory/hash correspondence. That future run is A-owned and is not requested or authorized by this proposal.

## Still open after this task

Actual Qt module/plugin third-party attribution and copyrights; CPython/OpenSSL/Mesa/Microsoft-runtime provenance; colormap attribution; exact library corresponding-source and durable recipient access; functioning replacement; project-owned licensing authority/terms; redistribution approval; current Windows and manual/hardware gates. These stay visible. Neither owner choice nor a license audit is replaced by the proposed automated copy check.
