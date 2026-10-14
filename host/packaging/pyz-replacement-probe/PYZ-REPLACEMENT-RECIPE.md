# Fixed PySide6 PYZ recombination preparation

This companion prepares one append-only library modification and checks bounded
original/derived bytes and externally supplied execution receipts. It never
installs software, launches a subprocess, imports a candidate module, loads a
DLL, deserializes candidate code, patches an EXE, runs a build or contacts a
network. Windows execution remains a separately authorized, reviewed A task.

Only three companion files are owned: `pyz_replacement_probe.py`,
`test_pyz_replacement_probe.py`, and this recipe. Application 244b, its source,
spec, recipe, lock, collector, separate inspector and A's consumer/CI stay frozen.
The exact companion commit/tree/blob hashes accompany the author/reviewer freeze;
there is no self-referential commit string in these files.

## Current baseline and qualifications

Run 38572185383 successfully built actual application
`244b0786a18fed19a9e708bb815150475ddd193f`, tree
`0c0e61fb8aadd4a4380e2d7e0b8818e4c9da0d5e`. Independent terminal report SHA256:
`1f0508f6805f9fc84cdc555028528b35089ed25954073dc2665df8b81688129b`.
The exact primary log has SHA256
`80b517c85dba0f505ff87e570db7f7b550c5f1744419b411db51a79828ca7997`.
Its EXE is reported as 6,342,472 bytes/SHA256
`8e4298ec481b401376206762a450dffee10354404ec77b17b6c1dac4b67512e5`;
embedded PYZ is reported as 5,937,680 bytes/SHA256
`694d0735acb90dfd61a209b1e3db163f0a9beb0a377146b9b2ebbf3c72cdf60b`.
Those original EXE/PYZ byte streams are still unavailable locally, and the run
has zero retained Actions artifacts. A successful log is not an input executable.
Do not fabricate a baseline from its reported hash.

The local source ZIP admitted here is a specific existing serialization:
1,411,889 bytes/SHA256
`e263fbb3b1734d8ca6d57d1db1493f2c6ce219979e2ffd581af56b4646c48d7f`.
It contains the reviewed 304 host files. The actual Windows ZIP has the same
length but reported SHA256
`6d112f2159fbb28c2f2d5b0bd7c9c512ccb6e2bf3f08400e535be6ae77f48e13`;
its bytes are absent. This companion does not silently accept or relabel that
second serialization. A later workflow must make the admitted exact local ZIP
available through an authorized transfer, or obtain explicit review for a
separately identified source-input contract. No transfer is performed here.

All generated Linux fixture results are receipt correspondence only. The
preparation manifest records `actual_windows_execution=false`,
`native_replacement_verified=false`, `corresponding_source_verified=false` and
`legal_clearance=false`; it does not contain the verifier's additional fields.
In every verifier result, `actual_windows_execution_verified`, `pyz_recombination_observed`,
`native_replacement_verified`, `native_embedded_support_verified`,
`corresponding_source_verified`, `legal_clearance` and `distribution_clearance`
remain false. A data-only verifier cannot authenticate a process just from a
supplied PID/loader string or successful JSON fields. Independent review of real
A execution establishes any later narrow observation; do not edit these flags
to turn a fixture into a pass.

## Fixed trusted inputs

- Official PySide6 Essentials 6.12.0 wheel:
  `pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl`, 82,153,795 bytes,
  SHA256 `c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42`.
- Its only selected member: `PySide6/__init__.py`, 6,418 bytes,
  SHA256 `3b662ee91f12feb02507dfaff907b7306bf5c851f1374563f70fae19e13711fc`.
- Official PyInstaller 6.22.3 wheel SHA256
  `500bd58c7bf7e584a8435adccbd763a0b918d5c12b08d74ff50fd79b2915458b`.
  Its retained `PyInstaller/loader/pyimod02_importers.py` is 36,530 bytes,
  SHA256 `33e786adc20d247cc5acf02eaa021f8629f30b8d3d5bf8a1e651088cca132eef`.
  It defines the expected `pyimod02_importers.PyiFrozenLoader` class.
- Regular GIL CPython 3.13.16 x64. The retained official embed archive's .pyc
  header independently confirms magic `f30d0d0a`. No .pyc was deserialized.
- Unmodified PR159 inspector
  `54d63be3fdd8eba858bafafbf6cec33c4ed989b8`, 53,390 bytes,
  Git blob `c04ffcccdd24d280806e746d348cad5ee788096f`, SHA256
  `fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53`.
  Its exact bytes alone may be compiled/imported as an explicitly trusted tool.
  They are loaded from the already hash-verified buffer; no second unchecked
  import read occurs. No candidate or extracted library code is imported.
- Application's original 14-wheel lock blob
  `256a8005b4f7de19651c9b9bdd70419b5b52af3d`; build recipe blob
  `be9c0f648681f60a20ee14ce19c65cab5c8a465d`, SHA256
  `40aecb99f408b292b486c9d9aea7e723173e904116833a924a522582581a7f8a`.

The exact source, tool and wheel bytes are checked before preparation. No version
label, filename, upstream index or external receipt substitutes for those reads.

## B: data-only preparation

Use an already available Python interpreter. Production entry requires `-I -S -B`;
the preparation and verifier themselves use only the standard library.

```text
python -I -S -B pyz_replacement_probe.py prepare --inspector <exact-inspector.py> --wheel <exact-pyside-wheel> --source-zip <exact-local-source.zip> --trial-id <32-lowercase-hex> --output <new-preparation-directory>
```

The new directory must not exist. Output consists of exactly:

- `original-init.py`: the original 6,418 bytes, unchanged;
- `modified-init.py`: those bytes as an exact prefix, followed by the reviewed
  local-modification declaration and marker branch;
- `change.patch`: the fixed-target append-only diff;
- `preparation.json`: exact body identities, source/tool/wheel pins and false
  execution/clearance flags, written last.

The original license/copyright text is preserved. The appended function returns
before nonessential imports or I/O on an ordinary nonfrozen import, and removes
its temporary helper afterward. The trial attribute is introduced only in its
frozen branch. The full upstream initializer is never executed by B tests.

In a derived frozen Windows `Motortester.exe`, the branch requires the frozen
loader and writes exactly one exclusive marker for that PID into the precreated
`replacement-probe` sibling directory. The basename is
`marker-<trial-id>-<pid>.json`. It checks every output ancestor for ordinary
directories/reparse redirects, bounds encoded output to 1,024 bytes, refuses
overwrite, checks output identity and rechecks ancestor identities. A missing,
unsafe, preexisting or unwritable destination fails the experiment with a fixed
error. No caller-selected arbitrary output path is accepted.

Marker schema has exactly these keys:

```json
{"schema_version":1,"module":"PySide6","trial_id":"<32 lowercase hex>","pid":1234,"frozen":true,"loader":"pyimod02_importers.PyiFrozenLoader"}
```

The raw marker JSON serialization is retained and hash-bound; fields/types must
match exactly. Private absolute paths are never placed in the marker.

## A: separately reviewed Windows experiment

These are future controlled operations, not commands this companion runs. A's
process/receipt wrapper needs its own exact review before execution.

1. Preserve the original actual baseline and receipts. Obtain actual original
   and derived bytes in an isolated trial. If the run18 EXE cannot be recovered,
   a separately identified pristine control must be built from the exact same
   application/tool inputs; do not call that new EXE the old run18 artifact.
2. Use a separate trial environment, regular CPython 3.13.16 AMD64 and all 14 exact
   wheels. If installation is separately authorized, use the existing lock and
   already retained wheelhouse with `pip --isolated install --no-index
   --find-links <wheelhouse> --require-hashes --only-binary=:all: -r <lock>`.
   No network fallback, new versions or host-global environment changes.
3. Verify app244b/tree0c0e, all relevant source/spec/lock/smoke hashes and source
   cleanliness. Record installed original initializer and actual runtime source
   file hashes. Build the pristine control before changing that input.
4. In the separate trial environment only, replace the one installed initializer
   with the reviewed `modified-init.py`; verify exact old/new hashes and preserve
   the original RECORD as upstream metadata. Record the local change explicitly.
   Do not claim the altered file still equals its wheel RECORD entry.
5. Invoke the same fixed freeze step from the original recipe, with entirely new
   work/dist directories and a separate `PYINSTALLER_CONFIG_DIR` cache:

```text
<trial-venv-python> -m PyInstaller --clean --noconfirm --distpath <new-dist> --workpath <new-work> <unchanged-app-host>/packaging/motortester.spec
```

   The exact environment must clear `PYTHONPATH`, `PYTHONHOME`, `QT_PLUGIN_PATH`,
   `QML2_IMPORT_PATH`, `QT_QPA_PLATFORM`, and retain original
   `PYTHONNOUSERSITE=1`, `PYTHONUTF8=1`, `PYQTGRAPH_QT_LIB=PySide6`,
   `PYTHONHASHSEED=0`. Preflight every destination as new so `--noconfirm` cannot
   overwrite existing evidence. Capture actual exit status and complete log.
   **Do not run the whole original build recipe after patching:** its install
   step would restore the original wheel and erase the intended modification.
6. Place each completed test bundle in its separate layout below; retain the
   actual corresponding work PYZ and bounded build log. No original EXE is
   patched or overwritten. Create empty `replacement-probe` directories before
   the smokes. Run these exact relative commands from each variant directory:

```text
bundle/Motortester.exe --serial-import-smoke receipts/frozen-serial-import.json
bundle/Motortester.exe --screenshot receipts/frozen-window.png
bundle/Motortester.exe --recording-smoke receipts/frozen-recording.json
```

   The actual process wrapper must capture PID, normalized command/cwd binding,
   UTC start/end, monotonic elapsed duration, timeout/exit status and exact EXE
   hashes before/after. Preserve the original 30-second serial-import child
   deadline and 60-second screenshot/recording child deadlines, with at most
   5-second reap on failure. Do not infer deadlines from UTC metadata. Require the exact
   original source validators to accept serial, decoded screenshot and recording
   outputs. Record validator identity/result and retain raw receipts/artifacts.
   The source-bound full recording invocation must complete its mandatory live
   prehistory/received-STATUS cases as well as the persisted pretrigger-zero
   archive path. Receipt v1 alone describes only that persisted archive; no new
   field silently upgrades it to live-case proof.
7. The pristine control must have no marker. Modified GUI and recording children
   must each have their own marker. The serial-only child normally does not import
   PySide6 and must not produce or be required to produce one. Preserve a real
   pristine-control rejection and wrong-trial-marker rejection. A marker by itself
   is insufficient: the actual source/build/execution and validated smoke evidence
   are necessary.

This companion's normalized command assertions must be reconciled with the actual
A wrapper/log; supplied JSON alone does not prove that an indicated command ran.
The source hash table likewise contains attributed A source-file observations,
not file-read authority or source-to-bytecode attestation.

## Exact comparison input layout

```text
trial/
  preparation/{original-init.py,modified-init.py,change.patch,preparation.json}
  execution.json
  control/
    bundle/Motortester.exe
    bundle/_internal/... native/runtime bundle inputs ...
    bundle/replacement-probe/                  (empty)
    PYZ-00.pyz
    build.log
    source-inputs.json
    receipts/frozen-serial-import.json
    receipts/frozen-window.png
    receipts/frozen-window.png.smoke.json
    receipts/frozen-recording.json
    receipts/frozen-recording.json.work/{recording.mtrec,recording-<receipt-bound UUID>.mtspool}
  modified/                                   (same layout)
    bundle/replacement-probe/marker-<trial>-<GUI PID>.json
    bundle/replacement-probe/marker-<trial>-<recording PID>.json
```

Additional names in the fixed top-level/preparation/variant/receipt/marker/work
directories are rejected. Within each explicit bundle, the unchanged pinned
reader performs its bounded traversal; no other filesystem roots are scanned.
All native `.dll`/`.pyd` leaves must be under `_internal`, have safe unique paths,
and match in both bundles. No additional EXE is allowed.

`source-inputs.json` is a finite object keyed by exactly the actual embedded PYZ
module names. Each non-namespace value is `{"bytes":<int>,"sha256":"<digest>"}`;
namespace values are null. The target must match the actual prepared initializer
identity; all other source observations must match between control and derived.
A obtains this table from its actual controlled build input observations without
turning untrusted declared paths into arbitrary read authority. The companion
reads the saved table only and explicitly attributes its source observations.

`execution.json` has exactly the following top-level keys:
`schema_version` (int 1), `scope` (external-a-windows-replacement-execution),
`trial_id`, `application_commit`, `application_tree`, `inspector_sha256`,
`pyinstaller_wheel_sha256`, `runtime`, `builds`, `runs`.
`runtime` is exactly platform win32, Python 3.13.16, machine AMD64,
free_threaded false. `builds` has control and modified records, each with
`initializer_sha256`, `source_tree_unchanged` true, `changed_inputs` (empty for
control, exactly `["PySide6/__init__.py"]` for modified), `build_succeeded` true,
`log_sha256`, `source_inputs_sha256`.

There are exactly six ordered run records: control then modified; within each,
serial, screenshot, recording. Each has exactly `variant`, `kind`, `pid`,
`exit_code` (int 0), `elapsed_ms` (int 0..30000 for serial, int 0..60000 for
screenshot/recording), `timed_out` false,
`command` (the normalized three-string command above), `started_utc`, `ended_utc`
(bounded UTC timestamps, nondecreasing), `validator_passed` true,
`executable_sha256_before`, `executable_sha256_after`, `receipt_sha256`,
`marker_sha256`. PIDs are positive unique integers. Marker SHA is null except
for modified screenshot/recording. Bool never substitutes for integer.

The actual wrapper must retain its separately reviewed exact identity, raw log
and validator identities with the experiment report. This schema is an input
correspondence check, not authentication of that wrapper or its execution.

```text
python -I -S -B pyz_replacement_probe.py verify --inspector <exact-inspector.py> --trial-root <trial> --source-zip <exact-local-source.zip> --output <new-result.json-outside-trial>
```

The actual embedded CArchive `PYZ.pyz` and unique **package entry `PySide6`** are
authoritative. `PYZ-00.pyz` is a separately parsed work artifact: its own hash,
module count and exact-byte relationship to embedded PYZ are reported explicitly.
They may differ; no work-file equality or cause is assumed. Do not substitute
extracted embedded bytes for the original work file to manufacture a match.

The target stored-member hash must change; all other module names/kinds/stored
hashes and all non-PYZ CArchive payload hashes/options must remain identical.
Offsets may shift and are not compared as module content. Any collateral change
under this narrow rule stops the experiment for review. EXE prefix hashes are
retained separately; no PE/signature/bootloader-build authenticity is asserted.

## Bounds, failures and preserved limits

- Exact 82,153,795-byte wheel admitted only after whole-file hash; at most 4,096
  ZIP entries; exactly one case-exact 6,418-byte source member; bounded member read.
- Source ZIP admitted by its fixed 1,411,889-byte hash; it is not broadly extracted.
- Trusted tool exactly 53,390 bytes; own companion source at most 128 KiB.
- Execution/receipt JSON at most 64 KiB; marker at most 1 KiB; source table/build log
  at most 2 MiB each; actual EXE/PYZ and each native file at most 64 MiB; at most 256
  native files; PNG at most 32 MiB; each recording artifact at most 2 MiB.
- Total read work at most 1 GiB, including the final full second hash pass; all
  delegated reads are capped by the remaining aggregate allowance before the
  pinned reader's own initial stat, with its actual charge checked afterward;
  existing per-file cap-plus-one mutation rejection stays in place. All
  inherited archive/table/depth/node/range/alias checks stay unchanged. No cap is
  silently widened for a new candidate. No candidate code decompression or marshal
  loading occurs in inventory comparison.
- Original/derived source and tool identity, regular-file type, every ancestor,
  reparse/link/path/case/device checks and same-API mutation fingerprints are
  retained. The separate path/handle ctime semantics of Windows are respected.
- Outputs are exclusive, finite and outside original input roots. An interrupted
  preparation may leave a partial new directory; it is not accepted unless the
  exact four members and exact generated bodies/manifest validate. Never reuse or
  overwrite it. A partial output/failed exit is not successful evidence.
- Error output uses fixed codes, with no raw exception/path string or candidate
  content. The result emits sanitized numeric/identity summaries, not raw receipts.

The marker and original smoke receipt hashes join actual supplied bytes. Screenshot
pixel decoding and recording-member/sample validation remain producer-validator
assertions; the verifier does not replay Qt or sample processing. Source/build,
clock and process declarations remain external assertions. A legitimate-looking
generated fixture can satisfy this schema, so actual observation flags remain
false even after local correspondence succeeds.

## Local tests and actual stop conditions

Run the three-file tests normally and with `-O`, both under `-I -S -B`. Test inputs
are existing trusted inspector, exact local source ZIP and retained official wheel;
environment variables `PYZ_PROBE_INSPECTOR`, `PYZ_PROBE_SOURCE_ZIP`, `PYZ_PROBE_WHEEL`
and `PYZ_PROBE_ORIGINAL` may point to those exact bodies. Their contents remain
hash-bound. Defaults identify the existing shared workspace inputs.

Tests include actual fixed-wheel preparation and generated opaque EXE/PYZ
fixtures. Any generated-tail-only execution is labeled modeled: it executes only
our newly emitted standard-library marker tail, never the retained upstream
initializer, native library or candidate application. It cannot prove Windows.

Stop on missing original/trial bytes, wrong source/tool/wheel identity, absent or
wrong-kind target, unrecognized format, parsing/resource/path/mutation failure,
unexplained other source/module/native changes, absent/wrong marker or failed
smokes. Do not replace the original baseline, alter frozen application/CI, change
the dependency pins, relax the reader, add network fallback or issue an upload.

Passing this narrow future experiment can support a PySide initializer
recombination route. It does not establish native Qt/Shiboken replacement,
native-embedded helper regeneration, complete vendor corresponding source,
OGL3/linked notice coverage, recipient-source access, legal redistribution,
Windows 10 acceptance, physical Stop/USB or whole-product readiness.
