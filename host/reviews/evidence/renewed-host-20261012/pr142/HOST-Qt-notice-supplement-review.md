# Critic-B: pinned Qt GNU notice-text supplement

2026-10-12. **PASS 10.00/10 for the bounded source-level notice supplement on
PR142 head `24eadd15530a84384f58f9edcb50ddfea8785796`.** No blocking finding in
that scope. This is not a license choice, redistribution clearance, proof of
corresponding source, or Windows-package acceptance.

I am the independent packaging critic, not this implementation's author. I
made no production/test changes, Git mutations, installation, CI action or
external publication. Review criteria were fixed in the proposal before the
candidate tests; proposal SHA256 is
`462575dcd73418667cf1b62b63906a56166b57a243e82b5ddfc76327f442197f`.

## Exact source and remote binding

[Draft PR142](https://github.com/font-weight/stm32_motortester/pull/142) has:

- Remote head `24eadd15530a84384f58f9edcb50ddfea8785796`.
- Tree `03e334c06f3adabad704b5113b40a13f8b25ee36`.
- Sole parent PR132 `3bc7a251cd36714b1fec350c99e0e5b43ffc4950`.
- Head ref `dot-b/host-qt-notice-supplement`; base ref
  `dot-b/host-viewport-diagnostics`.

The tested local checkpoint is `699363047fa923c128fd41da67326bc008acbcdb`,
with that same tree and sole parent. The different publication commit identity
is explicit. I fetched the remote commit, complete tree, PR and both refs, plus
the actual bodies of all seven changed files. All **1,662 tree entries / 1,391
files** match the tested checkpoint. All 1,384 unaffected baseline files retain
their exact blobs and modes. There are no removals.

The delta is exactly the collector, three new literal license texts, new
`test_license_packaging.py`, the one permitted positive fixture in
`test_serial_packaging.py`, and `LICENSE-REVIEW.md`. Every pre-existing serial
test assertion and the `_pyserial_notice` function AST are unchanged.

Collector blob is `fed7af5c94a658324b6940021d169417dab05c1e`, SHA256
`908bca5cefd7598b77f9b9f643df88089c0b0fef55eeaf987cced077ae7caeb6`.
Recipe blob remains `503a9f4aec1cd7293770226caaaa5369c16b3cca`, SHA256
`df444d48f86529f50951b1734ae87cd2df3fc8b57c66b9af6dbfd602e92b77b0`.
The unchanged recipe transitively invokes the changed collector; recipe equality
alone was not treated as acceptance of that new dependency. Launcher, validators,
lock/spec, requirements, GUI, runtime, transport, firmware and protocol are exact
parent bytes. PR140's separate viewport correction is not part of this review.

## Personally verified upstream inputs

I independently read the immutable upstream text bodies and directory/tag
metadata, then compared them with both the author's research inputs and the
actual candidate/remote files. No text or newline differences exist.

PySide tag v6.12.0 resolves through annotated tag object
`6aefae25747a8d860d201f3ab2b2e199b9e8e006` to commit
`f50facb83fc3dc5f3240c854668c7a6dd93871b2`.
Qtbase v6.12.0 resolves through `fcef5b2c755492753eb8f7424dd2bc7337832068`
to `025bdad181de241e81bf853c8a2d7bf3d19261a9`. Both API tag records are
unsigned. The two immutable LICENSES listings identify the same three blobs:

| Text | Bytes | Git blob | SHA256 |
|---|---:|---|---|
| GPL-2.0-only.txt | 18,092 | `d159169d1050894d3ea3b98e1c965c4058208fe1` | `8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643` |
| GPL-3.0-only.txt | 35,147 | `94a9ed024d3859793618152ea559a168bbcbb5e2` | `8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903` |
| LGPL-3.0-only.txt | 7,651 | `65c5ca88a67c30becee01c5a8816d964b03862f9` | `da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768` |

Primary input: [the exact PySide LICENSES tree](https://github.com/pyside/pyside-setup/tree/f50facb83fc3dc5f3240c854668c7a6dd93871b2/LICENSES).
This identifies the source of the texts, not the full source corresponding to
a distributed binary. Inventory provenance says `upstream-license-text-only`,
records complete text identity and the appropriate Windows wheel reference for
each Qt-family distribution, and retains `manual_review_required: true`.
Wheel references agree with the unchanged lock and the recovered registry
snapshot; no Windows wheel download, install or new wheel-content audit was
performed by me. Historical Windows-wheel findings remain attributed history.

## Fresh independent checks

I exported the immutable local commit into an isolated directory and verified
every source byte/mode against Git and the author freeze. Environment: Linux,
CPython 3.12.14, recovered dependencies with Qt 6.12.0; Qt source tests use
offscreen. Ordinary and optimized executions are separate processes.

- **81 normal + 81 optimized focused tests, zero skips**: 14 new license tests,
  37 serial-packaging tests, 26 Windows-smoke tests and four archive tests.
- **34 independent outcomes per mode**, using my external probes rather than
  the author's probe script: three successful collections, a fresh baseline
  omission control and 30 fail-closed outcomes. All final probe groups pass.
- The baseline collector freshly completes with the same fixture distributions
  while omitting all three GNU texts. The candidate includes one shared exact
  set, referenced accurately by both Qt records, while retaining discovered
  commercial-reference fixture text, pyserial and the CPython fixture bytes.
- Missing Qt distributions, four wrong versions for each, normalized duplicates,
  each missing text, same-size corruption and changed length are rejected.
  Valid Qt pairs also exercise the existing missing/wrong/corrupt pyserial and
  missing CPython checks. Existing file/directory/symlink destinations preserve
  their sentinels before metadata access. A late copy fault leaves partial files
  but no completed inventory. Partial output is explicitly documented.
- Network/socket access, subprocess creation and Qt runtime import are forbidden
  during these collector probes. Paths include spaces and Cyrillic characters.
  A success case uses the six recovered Linux distribution-metadata records,
  with a fixture CPython notice; it is not a Windows installed-input audit.
- A real read-only Git archive on local699363 contains **230 exact host files /
  258 members**, including the three new texts, valid CRCs and the local commit
  comment. ZIP SHA256 is
  `2607e5a6c1e8ac6843c42d9caa219801d16e8fd2060f3505cf84fb7ff271fa0e`.
  This does not assert a remote-wrapper ZIP hash or a Windows binary archive.
- All **1,391 source hashes/modes remain unchanged** in both the isolated export
  and the clean author checkout after tests.

One initial reviewer probe incorrectly expected the restored Linux metadata to
list the Windows commercial-reference notice. It failed in both modes. Direct
inspection showed those Linux records list no matching discovered notice; the
fixture case remains the preservation check. Both initial logs/results and the
corrected full replay are retained. No product assertion was weakened. A local
evidence-writing attempt also exceeded the command argument-size limit; saving
the same fetched tree response in chunks resolved that tooling limit.

Author tests are separately recorded in its report SHA256
`2c482d30da52fb1da64213021a45a2222661d7c4dde3fad00804f723a73bdd60`;
they are not counted as my executions. Full host/composition testing remains
root-owned under the predeclared scope. No unrelated historical aggregate or
checker campaign was repeated, and no recovered old result was relabeled fresh.

## Rubric and remaining gates

| Criterion | Weight | Score | Basis within this bounded source task |
|---|---:|---:|---|
| C1 Correctness | 30% | 10 | Exact upstream texts and truthful dual-distribution provenance; exact scope and remote binding. |
| C2 Failure handling | 20% | 10 | Version/name/text failures, no overwrite, partial-copy failure, preserved pyserial/CPython and gate regressions. |
| C3 Evidence | 25% | 10 | Fresh isolated ordinary/optimized tests, independent controls and primary-source comparisons; failed reviewer probes preserved. |
| C4 Clarity | 15% | 10 | Small collector supplement, no framework or unrelated changes; explicit text-only meaning and partial-output behavior. |
| C5 Reproducibility | 10% | 10 | Exact tuple, inputs, hashes, local-only collection, repeatable probes and source archive. |

Weighted total: **10.00/10**. No P0/P1 finding in this declared scope.
This accepts only the exact source-level supplement, not the full package or
future composition. Current Windows execution must still confirm copied
text/inventory/hash correspondence. Complete Qt module/plugin and other
third-party attribution, precise corresponding source and recipient access,
working replacement, project licensing authority/terms and redistribution
approval remain open. Windows10 manual, COM/USB/LV and full-MVP gates are also
unchanged. Including alternative license texts chooses no license or entitlement.

Evidence entry points: `SOURCE-BINDING.json`, `UPSTREAM-VERIFIED.json`,
`FOCUSED-RESULTS.json`, `independent-replay-*/RESULTS.json`, `FINAL-BINDING.json`,
actual remote bodies, executable verifiers and `evidence/SHA256SUMS`.
