# Critic-A: complete bounded static-link recipe

## Conclusion and binding

The final recipe is **locally cleared for conditional static stack-bound rebinding** under the previously reviewed assumptions. No remaining demonstrated defect was found in this bounded implementation review. A successful run may identify the exact ELF to which the conditional 3016-byte bound transfers. This is not whole-project acceptance, an independently selected production reserve/margin, physical board verification or permission to flash.

Exact subject:

- `build_image.py`: `0c4428388f0c261b1b96873072874b031cb6200ccbcf9fe51b942abac72c0d5d`.
- `BUILD_IMAGE_README.md`: `dfbe614b37c3e4d953da02c08ff2d32169bd4a8879af2a963c15aaede4830409`.
- Frozen reference: `c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d`.

The project remains **BLOCKED_EVIDENCE, score:null**. This source is a new local draft, not an implicit modification or remote binding of the separately reviewed R4 integration tree. Publication/disclosure approval remains open.

## Reviewed composition

The recipe requires explicit JSON, object directory, toolchain, reserve and margin. It pins helper implementations, source/tool manifests, the actual 33 original object bytes and their order. Additional files in that directory do not enter the link. The generated board object is exactly the 34th input; no combined object, archive, CRT, library or old board fixture is substituted.

Actual board compilation uses the previously reviewed Cortex-M3/Thumb/soft-float/full-enum `-Os` profile, no-common/data sections/noexecstack and strict warning flags. The generated C comes directly from the pinned parser/generator and is retained with its JSON and SHA. Compiler dependency records must identify only pinned source/tool inputs or that generated C. The actual GCC support paths are executable and hash-bound; the environment excludes inherited compiler/include overrides. Host Python/shared libraries are not a fully hermetic installation, as documented.

The gate order is material and correct: data-only board-object validation; original input/vector BOOT audit on all 34 objects; direct pinned ld with fatal warnings, no GC and orphan errors; original post-link BOOT audit with explicit reserve; exact ELF byte/layout/symbol validation; map/input-range validation; final preservation checks. Both BOOT commands require exit zero and the expected JSON verdict. The pinned narrow helpers do not themselves claim stack transfer; only the completed orchestration does.

The map gate uses exact full input paths before deriving reference basenames. It checks ordered LOAD records, all executable input sections including 34 empty `.text` entries, exact input names/sizes and the 363-range canonical multiset. The range addresses map consistently through real final sections and file-backed PT_LOAD offsets, and nonempty intervals do not overlap. Actual relocated bytes are covered by the preceding immutable-region checks rather than incorrectly compared to unrelocated object bytes. The board's four nonempty readonly rows and their exact final layout are checked separately. The three GNU-generated empty pseudo rows and four empty stub rows have constrained identities, destinations, sources, addresses, sizes and order. This is a pinned-profile map gate, not a general GNU-map parser or an independent proof from map text alone.

The trusted reference was independently derived before implementation. Its exact original instruction/data/vector/function/alias identities, the one constrained reset data literal, closed callback targets and board-only changes satisfy the reviewed transfer argument. The former invalid-zero-board fixture was not used to prune working paths. `R = align8(3016 + M)`, R in 3016..8104 and M in 0..5088, is checked and recorded with alignment slack; these arithmetic checks do not select an appropriate physical reserve.

## Findings and their disposition

### Own-script alias admission: fixed

The provisional recipe `334a9c2494968bc549a6de3bf1d5c5a243092c1dfddbc7a161742f8122cb5635` resolved `__file__` before the canonical/no-symlink check. My actual isolated CLI invocation through a script-file symlink completed exit 0 and created 34 output paths. The result was still correctly provisional and did not claim a stack transfer. Exact argv, source snapshot and outputs remain preserved.

The correction validates the lexical script path before helper import or resolution. I replayed the exact alias geometry: explicit symlink refusal, nonzero exit and no output directory. Final supporting tests also repeat both script-file and source-root alias refusals. No input corruption was demonstrated or claimed.

### Map-consistency gaps: fixed, separate from changed-code safety

The map worker's initial 22-case corpus found six malformed-map acceptances in `7df3e4dd…`: unknown metadata source, incorrect board row size/address, invalid empty-text VMA, incorrect output-text size and an extra malformed indented text row. I independently injected the extra row immediately after the real linker into a complete run; it produced the earlier conditional success despite the map inconsistency. Existing object pins and final bytes were still unchanged, so this was not a demonstrated changed-code stack-bound bypass.

The final source closes those gaps. I personally replayed all **57** final malformed-map fixtures, including 35 GNU pseudo/stub controls, with intended `ValueError` refusals. Both independent positive space-path/same-basename relinks pass. My same full-recipe extra-row injection now yields FAILED with transfer false. Root's intervening overly broad input-row rule failed on genuine empty GNU pseudo rows; its failed run and historical source are retained by root, not represented here as my successful execution.

## Independent executed evidence

1. **Full supporting suite personally replayed:** all **943** scenarios passed on the final source in a fresh critic-owned copy: 103 CLI/integration cases, 823 map cases, 15 fault/postcheck cases and two actual GCC/ld diagnostic failures. I copied the supporting test logic unchanged; only the outer replay wrapper's input-base and output-ownership paths were adapted. Its original bytes and the adapted wrapper are preserved. This is not an assertion that the supporting writer itself is a qualified arbitrary-path sandbox.

2. **Separate map corpus:** all 57 final malformed fixtures reject, with both positive independent relinks accepted. These overlap conceptually with parts of the 943 suite and are reported separately, not added into an inflated unique-case total.

3. **Own full clean relocation:** source, all selected toolchain files and the 33 objects were copied into distinct paths containing spaces. Runs succeeded with hostile inherited Python/compiler variables. Two final actual compiler/linker executions used previously defined all-false synthetic boundary JSON:
   - minimum strings, test-only R=3016/M=0: ELF `8c589f9c9a7ab31335c21439a2a7f3170314940ee8114013f245886f2d166cde`;
   - maximum permitted strings/scalars, test-only R=8104/M=5088: ELF `ba010ae20b51b6ed23060e91183d3bcfcb3c224369e61f08ad659ddd448dbd2c`.
   Each has 363 nonempty/34 empty text inputs, canonical range SHA `7e3551c52217911dded147b2fced46bdd72cd18440ffaf5636035292c5cd2be1`, both BOOT audits, both narrow gates and the final map gate. No target code was executed.

4. **Own seven retained-failure probes:** timeout with partial byte streams, OSError, nonzero link, successful-exit/wrong BOOT verdict, post-success generated ELF drift, timeout plus a disappeared pinned input, and the malformed-map injection. All now finish FAILED with transfer false. Partial streams/argv/exceptions survive. The combined timeout/missing-input case preserves its primary error and separately records the missing input's null after-hash/postcheck error.

5. **Post-run preservation:** independently rehashed all 323 pinned inputs for each of the two final boundary runs and the supporting baseline. Every hash still matches. The baseline ELF from the replay is `1098ebc6f3002732b2ba156396e40649cbff744424cde95ea698955637f0c90a`. Final root source and README equal the reviewed copies byte-for-byte.

Exact scripts, source snapshots, commands, outputs, prior failed/accepted probes and manifests remain in this review directory. The conditional successes use explicitly synthetic data and test-only reserves. No actual board values or physical margin were assigned.

## Acceptance boundary

Success transfers the conditional static result only for the exact recorded ELF under the original privileged reset-only/MSP/STKALIGN, interrupt/terminal-exception, memory-integrity, fixed-code/callback and no-RTOS/deferred-producer assumptions. Source/tool pinning and receipt rereads assume stable cooperative filesystems; they do not provide an adversarial TOCTOU sandbox. The read-only metadata marker is not physical non-executable-stack protection.

No hardware enumeration, ADC/USB timing, physical HSE/power/identity confirmation, target execution, final device release, new score, BIN/HEX, flash approval, CI, merge or publication was performed or accepted. Any later change to the recipe, reference, contributing objects, helpers, linker/flags or execution assumptions needs a new impact review and exact binding.
