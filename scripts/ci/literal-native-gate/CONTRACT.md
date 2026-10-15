# AUX-L1-L5-R6: data-only metadata gate and original-receipt retention

Draft implementation scope fixed before coding on 2026-10-15. This module provides
retain_current(), check_joins() and finalize() for the auxiliary metadata stage only.
It does not start the native observer, a process, Windows executable or DLL.
It does not establish that prerequisite package validators actually executed.
The native helper remains unchanged at its 99-path / 123 MiB profile.

Original L1/L5 criteria: https://github.com/font-weight/stm32_motortester/pull/174#issuecomment-6188840871 .
The pre-code design was fixed before implementation; this is its bounded implementation scope.
This implementation freezes these clarifications before tests:

1. The app244b authoritative 23-source map is mandatory in the pinned auxiliary
   input configuration. Matching assertions in receipts alone are insufficient.
2. Retention uses this trusted module's self-contained bounded filesystem code.
   It reads and retains current.original.bin and current.identity.json BEFORE
   parsing current JSON or admitting observer/literal/workflow/pins inputs.
   Those later failures preserve files and separately report whether their
   retained bytes can currently be verified; completed status is never inferred
   from a historical in-memory identity after corruption or budget exhaustion.
   The caller must admit this module's own Git source; self-hashing is not trust.
3. package-gate.json is UNCOMMITTED_AUXILIARY_METADATA. Only a successful final
   CLI receipt, after readback and unchanged-input checks, binds its exact bytes
   with status data_only_joins_validated. All execution/launch/source/legal flags
   stay false. Missing original bytes have an explicit unavailable hash, never
   a partial or stat-derived hash presented as the original.
4. One actual-byte ledger spans retention, metadata reads, copies, rereads,
   report/failure writes and CLI stdout/stderr. Ceiling is 5 MiB; 16 KiB remains
   reserved for failure output. No base64 or child-pipe capture is implemented.
   Optional future native finalization requires a separately reviewed caller
   using this same ledger; its reserved capacities are not spent twice.

The researched fixed profile has18 inputs and8 possible outputs,26 leaves;
7 overlap the native helper, giving118 across the future composition. This
implementation uses only current.original.bin, current.identity.json,
package-gate.json and failure.json; other reserved outputs remain unused.
Input caps in KiB: self64,pins16,workflow64,literal32,observer64,baseline16,
reference128,current128,toc256,manifest128,provenance512,archive16,checksums4,
serial4,gui64,recording64,notice_source64,notice_copy64. These do not silently
support the generic observer's8 MiB provenance limit. All filesystem reads,
writes and terminal output are charged; interpreter/import/internal memory
operations are not claimed as managed-file I/O.

Current schema16 keys; manifest14; archive15. Mandatory joins are exact current
input hashes, historical46 rows/declarations, manifest/provenance/TOC baseline,
strict8-entry checksums, recording/source23 map, original smoke receipt bytes
and notice sidecars. Reuse hash-pinned observer.bind_inputs and literal.bind,
and the pinned original workflow's pure recording postcondition. No new PE,
PYZ or archive-content parser is introduced. No declared origin path is opened.

Missing actual historical COLLECT TOC prevents a historical all-input positive.
Synthetic TOC fixtures are labeled synthetic; they do not recover its old hash.
Public metadata snapshot b65b3ca4773bd909b676fa5f2bd0543a319748e5 provides exact
hash-matching log reconstructions, not retained binary artifacts. Original
package source/archive/smoke validation remains an external mandatory stage.
Full L1/L5 workflow composition stays blocked until that integration/evidence.

Minimum tests: malformed/extra/missing/aliased hashes/rows/schemas, one-byte
changes, wrong context/source-map/checksum/recording joins, zero native opens,
retention before later bad input or pin, late partial output/readback/budget
failures preserving original identity, no-clobber and unsafe paths, exact cap
boundaries and reread accounting, terminal mutation, false assurance flags,
normal and optimized execution. Independent review is required before acceptance.

## R2 corrections after frozen R1 independent rejection

R1 was REWORK 8.125, not accepted. R2 retains the same caps, input paths and
assurance limits. Pure inspector 54d63be3 recording_structure and dependencies
are vendored verbatim with per-segment line/hash provenance in
VENDORED_VALIDATORS.json. Added data-only strict nested schemas, typed bounds,
manifest/native/EXE/count/hash projections, exact serial receipt, GUI dimensions
and phases, and a recomputed complete recording summary. No executable/PNG/PYZ
bytes are opened or inferred to have been observed from these assertions.

On failure, fresh original/identity rereads cannot spend the final 16 KiB reserve.
If current retention cannot be verified, retained_original is null and
retention_completed is false; observed_original still records a previously
completed full input read. failure.json is UNCOMMITTED_FAILURE_METADATA. Only
the terminal stderr message carries the post-write retention observation; a
partial/uncommitted file alone is not authoritative. Fallback output never
asserts completed retention without verification. --help is rejected through
the same bounded terminal path. Optimized author runs also start optimized CLI
children. R1 evidence remains separate and unchanged.

R3 adds source-derived manifest ancestor/case-collision checks and the original
1 GiB expanded-archive projection including raw manifest bytes. R2 is preserved
as a separate snapshot. No predecessor verdict transfers to R3.

R4 corrects a second late-readback retention defect found independently in R2
(and present in R3): after both retained files are read, their own final
path/time/ancestor observations are rechecked, excluding unrelated failed inputs.
All managed ordinary files additionally require exactly one hard link.
A linked input or newly linked retained file cannot be claimed retained.
The vendored MAX_PATH_PARTS line preserves its original inline comment; recorded
segment hashes now explicitly refer to complete original source lines.

R5 reserves mandatory build-manifest.json in ancestor checks as well, rejecting
its use as a directory even though it is outside the declared payload file map.
The single-hardlink restriction is conservative profile hardening, not a new
retroactive acceptance criterion or proof of distinct physical origin.

R6 adds loaded-assertion consistency: payload_bytes includes the already-read
CPython notice as well as native bytes; every repeated known origin_kind/path
has one coherent size/hash/owner identity; constructed Motortester.exe origins
join the declared executable size/hash. These checks open no new origin paths.
R5 and prior failed evidence remain preserved, without transferred scores.
