# Read-only candidate provenance companion

This inspector produces evidence about an existing candidate. It does not build,
run, modify, license or approve that candidate. It must receive its own exact
independent package review (at least 9.5/10) before A uses it on Windows.

## Separate source identities

The application base is PR146 `db86d2247f02dbc6f770a674654f5c5bf9d92f99`,
tree `b9d7ac81a2f36da0573d208f1d61cdb94a37cdbc`. The companion has a separate
reviewed commit and script SHA256. Adding the companion does **not** change the
selected application source. A owns any later workflow integration and optional
PE metadata/import-name layer; neither is implemented here.

The application commit and inspector commit arguments are externally asserted
identities. The inspector verifies actual script/manifest/EXE bytes but does not
run Git or prove commit ancestry. A must separately bind the reviewed inspector
and the actual clean application source. A rebuild of the same application source
is a new candidate identity, not a retrospective inspection of run14.

The fixed paths were checked against that exact application's recipe/spec:

- `<build-root>/venv/Scripts/python.exe`
- `<build-root>/venv/Lib/site-packages`
- `<build-root>/dist/Motortester`
- `<build-root>/pyinstaller/motortester/COLLECT-00.toc`
- `<build-root>/pyinstaller/motortester/PYZ-00.toc`
- `<build-root>/pyinstaller/motortester/PYZ-00.pyz`
- `<build-root>/pyinstaller/motortester/Motortester.exe`

Use an existing completed build and its original work directory. The inspector
requires regular Windows AMD64 CPython **3.13.16** and the exact 14 installed
distribution versions already pinned by the application. It does not install or
download anything. Run with **`-I -S -B`**: no site/package startup, no `.pth`
execution, no bytecode writing. For Python 3.13 with `-S`, venv `sys.prefix` is not
the environment-root authority; the exact executable/fixed layout is checked.

Example invocation shape, to be filled with independently verified identities by A:

```text
<build-root>\venv\Scripts\python.exe -I -S -B <reviewed-inspector-path> \
  --build-root <build-root> \
  --expected-source-sha db86d2247f02dbc6f770a674654f5c5bf9d92f99 \
  --expected-manifest-sha256 <actual-manifest-SHA256> \
  --inspector-commit <separate-reviewed-inspector-commit> \
  --expected-inspector-sha256 <reviewed-script-SHA256> \
  --output <new-report-outside-input-roots.json>
```

The backslashes above show line continuation conceptually, not a PowerShell
script. A should construct an argument array. This document does not authorize a
CI run, upload, artifact-retention change or redistribution.

## What is verified

1. Actual inspector hash, expected manifest hash, manifest-recorded application
   source, fixed runtime/dependency versions and the candidate's complete ordinary
   file set. Every payload hash is read; the EXE hash/size is recorded.
2. The original COLLECT table declares an origin for a native destination. Within
   the fixed environment/interpreter/work roots, source/output hash equality is
   required. Installed RECORD supplies only a unique distribution association.
   It is not a wheel authenticity or upstream build attestation.
3. The actual EXE's CArchive and embedded PYZ tables identify stored entries. The
   embedded PYZ must match the complete fixed work PYZ bytes. Actual module names,
   kinds, offsets, compressed lengths and stored-byte hashes are recorded.
4. The fixed PYZ build table supplies declared source associations. Declared-only
   entries are separately listed, since the pinned writer can omit a module after
   a compilation error. They are never counted as embedded modules.
5. CArchive bootstrap/script/runtime-hook entries are separate from PYZ modules.
   A PYZ table is not claimed to contain the entire executable or every possible
   dynamically imported module.

No archive module body is decompressed or unmarshaled. No extracted code object
is created, imported or executed. No candidate, subprocess, network request or
third-party package is invoked. The inspector uses only the standard library.
Stored hashes are not a semantic bytecode audit or source-to-bytecode proof.

### Native origin statuses

- `hash_correspondence`: the table-declared readable origin equals the payload.
  `origin_kind=environment` also has a unique installed RECORD owner;
  `interpreter` identifies installed runtime bytes; `constructed` identifies the
   intermediate EXE, not an unchanged original wheel member. The main EXE must
   bind to the exact fixed work EXE; a bundle file cannot be its own origin.
- `outside_allowed_origins`: the table points outside the allowed roots/locations.
  No outside file is stat'ed, opened or probed; its absolute path is not emitted.
- `no_collect_entry`: a native manifest file has no declared table entry.
- Changed/transformed in-root bytes fail with `origin_hash_mismatch`; missing or
  unreadable required inputs fail. They are not promoted into a successful origin.
- Pure-source associations use `declared_input` or `namespace_or_no_file`; neither
  establishes that source compiled to the recorded stored bytes.

Windows system/SDK, external OpenSSL and other origins may remain unknown. There
is no arbitrary root option, PATH search, home scan or drive scan to find them.
Only fixed interpreter root/DLL locations are eligible for native reads; pure
interpreter modules are confined to `Lib`. Venv inputs need a unique safe RECORD
member. Legitimate absolute or parent-relative out-of-site RECORD rows are treated
as bounded metadata and ignored for ownership, never followed.

`inventory_status=complete_with_unknown_origins` may return exit 0 after every
mandatory structural/identity check succeeds. Every unknown native row remains
visible. `complete` means this particular inventory has no unknown native origin,
not that provenance, licensing or source obligations are complete.

All reports retain these literal false values:

```text
legal_clearance=false
corresponding_source_verified=false
replacement_verified=false
```

## Pinned parser and resource boundary

The design inspected official PyInstaller 6.22.3 Windows wheel SHA256
`500bd58c7bf7e584a8435adccbd763a0b918d5c12b08d74ff50fd79b2915458b`.
Installed reader, writer and bootstrap-reader source hashes are checked against
that wheel without importing them. Their generic APIs are intentionally unused:
CArchive/PYZ readers collapse duplicate names; generic PYZ loading unmarshals
metadata; extractors decompress without these bounds; the build-table helper
uses `eval`.

The companion instead parses only this pinned format: COLLECT `(toc,)`; PYZ guts
`(name, toc)`; unsigned appended CArchive; exactly one uncompressed PYZ; the
17-byte PYZ header; marshal TOC lists/tuples/strings/integers and references to
completed permitted data. It rejects unsupported variants, code-object tags,
forward/cyclic references, duplicates, inconsistent ranges and overlaps. Reference
reuse is charged against expanded work/depth limits. The only allowed CArchive
option is `pyi-contents-directory _internal`; unchecked option values cannot leak.

Build-table lexemes/token counts/depth are bounded before AST construction.
Multiline/triple-quoted literals are unsupported. JSON structure is bounded before
decoding. There is no `eval`, `exec`, generic marshal loader or executable
compilation. Unsupported input needs review; limits are never increased silently.

| Limit | Inclusive boundary / first rejection |
|---|---|
| Candidate ordinary files | 4,096 including manifest; 4,097 rejected. Therefore at most 4,095 manifest payload records |
| Candidate directory entries | 8,192 total files/directories; next entry rejected |
| Relative path depth | 16 components including a filename when present; component 17 rejected. Actual run14 notice directories reach depth 9 |
| Absolute input/output ancestry | 64 components and 1,024 UTF-8 bytes; next component/byte rejected before ancestor inspection |
| Ordinary payload/origin file | 256 MiB; next byte rejected |
| Total candidate payload | 1 GiB; next byte rejected |
| All streamed reads, including critical rehashes | 2 GiB; next byte rejected |
| EXE/PYZ | 64 MiB; next byte rejected |
| Manifest/build table/PYZ TOC/RECORD/METADATA | 2 MiB each; next byte rejected |
| Archive/module or total RECORD rows | 16,384; next record rejected |
| Environment top-level entries | 4,096; next entry rejected |
| Parser work/nodes/tokens | 100,000, including expanded reference work; next unit rejected |
| Metadata data depth | Root at depth 0, maximum 8; depth 9 rejected. JSON nesting maximum 8 |
| Name | 1,024 UTF-8 bytes; next byte rejected; bounded escaped spelling is checked before generic parsers |
| JSON scalar spelling | 64 characters; next character rejected; nonfinite constants rejected |
| Report | 8 MiB including final LF; next byte rejected during encoding |

Payload/body hashing is streamed. EXE/PYZ and bounded metadata are held in memory;
the byte caps are not a promise that process RSS equals the input size. Multiple
bounded objects and interpreter overhead exist. Fixed entry/node/depth limits
prevent unbounded expansion. Stored-range hashes are computed only after all
ranges pass overlap validation, so overlapping aliases cannot amplify hashing
work. Current actual EXE/PYZ sizes still require A's
Windows validation; no lost run14 byte size is invented.

Every relevant path ancestor is checked for symlinks/reparse points, including
output ancestors. Unsafe drive-relative/UNC/device/ADS/traversal and reserved
names and invalid filename characters are rejected before filesystem access.
Device checks include the documented superscript port digits and conservatively
reject legacy console/clock/zero aliases and reserved stems before extensions.
COLLECT uniqueness is checked both before and after `_internal/` destination
mapping, case-insensitively, so a mapped row cannot overwrite another origin.
These spelling rules follow [Microsoft's file naming guidance](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)
with the additional conservative alias exclusions above.
Only root-relative labels appear in the report. Public errors
are fixed codes with no raw arguments/exception paths. Output is exclusive and
outside input roots; a failed write removes only the new incomplete file if its
identity still matches. No preexisting report is overwritten.

File identity/size/timestamps and critical byte rehashes detect concurrent
mutation. They do not provide an atomic filesystem snapshot or defend against
all privileged races. A must run against a quiescent completed build.

## Acceptance and remaining evidence

Focused tests generate the pinned formats and exercise positive/negative cases
normally and under `-O`. Guard behavior does not depend on Python assertions.
Generated POSIX fixtures, mocked Windows guards/reparse attributes and an offline
metadata-only fixture from verified wheels are distinct from actual Windows use.
No fixture EXE is executable, and no actual Windows inventory is claimed here.

The exact three-file commit/tree/script hash and author evidence must be frozen
for the same independent package critic. Require its explicit score at least 9.5
and exact-version binding before A uses the inspector. No historical score
transfers automatically. Source/doc tests are not real-candidate acceptance.

Later A-owned validation must retain the reviewed inspector SHA, actual regular
Windows interpreter identity, source/manifest/EXE/PYZ/table hashes, complete
native and embedded-module counts, unknowns and privacy checks. Run14's unavailable
ZIP/EXE bytes cannot be reconstructed merely from its manifest.

Corresponding source/build mapping, recipient source access, effective library or
embedded-module replacement/recombination, owner project terms, Microsoft rights,
artifact delivery, Windows 10/manual acceptance and hardware remain separate
gates. The companion does not revalidate the application's prior smoke receipts
or choose a license. See [LICENSE-REVIEW.md](LICENSE-REVIEW.md).
