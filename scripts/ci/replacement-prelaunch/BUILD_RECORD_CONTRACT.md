# PRELAUNCH build record v1 — predeclared data-only contract

Declared before implementation on 2026-10-15. R1 refinement declared before prefix implementation. UTF-8 JSON, maximum 65536 bytes;
no duplicate keys/nonfinite scalars, exact types and exact keys at every level.
No field grants permission to open a path, launch a process or assert actual
runtime/build ownership. The wrapper is the source of every declaration.

The complete object has exactly these keys:

- schema_version: integer 1 (not boolean)
- scope: string `wrapper-declared-prelaunch-inputs`
- trial_id: exactly the 32-lowercase-hex ID from deterministic preparation
- application: {commit, tree}, equal to APP/TREE in pins.json
- source_zip: {bytes, sha256}, equal to the actual admitted 1411889-byte/e263… ZIP
- tools: {inspector, companion, comparator}; each {bytes, sha256} equals
  the actual read body; inspector and companion additionally match immutable pins
- app_inputs: exactly the six path→{bytes,sha256} entries in pins.json, for
  spec, lock, build recipe and serial/GUI/recording original validators
- wheels: exactly the ordered14 {name,version,filename,sha256} records in pins.json,
  derived from the exact published app244b lock. These are declarations, not
  proof that those wheel bytes were downloaded, installed or used in the build
- runtime: exactly {platform:"win32", python:"3.13.16", machine:"AMD64",
  free_threaded:false, python_magic:"f30d0d0a"}; not a detected live runtime
- source_date_epoch: integer1..4294967294, not boolean/string; a declared
  reproducibility input used in both COFF headers, not an observed execution time
- prefix_policy: exactly `pe32plus-checksum-only-r1-proposal`; only validated,
  independently recomputed4-byte CheckSum may differ; all timestamps/debug and
  other prefix bytes remain identical. Shared Windows adoption is not yet approved
- builds: exactly {control,modified}; each has exactly:
  - initializer: {bytes,sha256}, matches the actual deterministic prepared source
  - source_inputs: {bytes,sha256}, actual read source-inputs.json body
  - build_log: {bytes,sha256}, actual nonempty build.log body
  - executable: {bytes,sha256}, actual read bundle/Motortester.exe body
  - work_pyz: {bytes,sha256}, actual read PYZ-00.pyz body
  - bundle_inventory_sha256: SHA256 of companion.encoded(name→{bytes,sha256})
    for every regular bundled leaf, including the EXE
  - source_tree_unchanged: true, a wrapper assertion only
  - changed_inputs: [] for control, ["PySide6/__init__.py"] for modified
  - build_succeeded: true, a wrapper assertion only
- verified_by_comparator: exactly {wheel_bodies:false, runtime:false,
  build_execution:false, source_to_bytecode:false}

The comparator constructs this closed expected object from immutable pins,
preparation and inspected bytes, then uses the exact inspector same_json type
comparison. It never adds execution.json, PID, smoke receipts or marker claims.
The exact source-ZIP identity is verified; app source statements in the build
record are not proof of actual source→compiled-byte correspondence.

The whole wrapper must separately establish actual wheel/runtime/source/build
admission before any child launch. A passing comparator result alone never
authorizes execution or removes false verification/clearance flags.
