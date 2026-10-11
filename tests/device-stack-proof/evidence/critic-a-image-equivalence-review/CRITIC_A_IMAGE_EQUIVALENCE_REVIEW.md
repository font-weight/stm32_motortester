# Critic-A: narrow final-ELF byte/layout gate

## Conclusion and exact subject

Locally cleared for its documented narrow scope. No remaining demonstrated implementation defect was found. The gate correctly leaves `stack_bound_transferred=false`: this result does not complete the stack-rebinding procedure. Formal project status remains `BLOCKED_EVIDENCE`, score `null`, and the external-disclosure hold remains in force.

- Implementation `image_equivalence.py`: `b8322ee42b850ce38c025b213643a89ab3a71c25182b257aa2a6bf9e6b64a86d`.
- README: `21bdbd41b8bd24be8b94b112e1c9cca11c9e7623d812b9942837d352a8f80c5f`.
- Frozen reference: `c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d`.
- Pinned generator/parser: `0b5d6ba56603e908d7f0b079e3319863f1f7733c97324644377a3e46244f9f27`.

The reference's independent derivation and transfer argument were reviewed separately in `CRITIC_A_STACK_REBINDING_DESIGN_REVIEW.md`, SHA `abd2126c03be3011215537285e7572578481c53b2237f30b6dca732e45f7b6f6`. This implementation review does not silently replace that report or the separate board-object review.

## Source inspection

The checker parses bounded raw ELF bytes directly, requiring the fixed ELF32 little-endian ARM soft-float executable profile, entry, ten sections and five program-header roles. Section/table bounds, alignments, payload nonoverlap and zero uncovered bytes are checked before success. Exact program headers bind file offsets, VMAs, physical/data-load addresses, sizes and flags; a broad segment containing the expected sections cannot hide additional load bytes. The empty executable stub remains empty. BSS retains its fixed NOBITS geometry and zero-file-size mapping.

All four independently derived region digests are enforced. The single reset literal is separately checked against the actual constrained `.data` LMA, including the exact PHDR mapping. No blanket relocation, literal-pool or Reset_Handler exception exists. The complete board tail is compared to the validated JSON: exact scalar encoding, pointers to exact string starts, ordered complete NUL-terminated strings and only zero final alignment padding. Flash end, fixed SRAM limits and explicit aligned reserve are checked.

Symbols are retained as records/multisets rather than name-keyed dictionaries. The full 410 function records and 1,053 immutable records are checked against the pinned reference. Three mutable linker symbols must each have exactly one matching global absolute record. Each new local board OBJECT and `$d` record must occur exactly once with the expected address/size/role. Exclusion of FILE metadata and the empty-stub SECTION metadata follows the reviewed design; their nonruntime roles are validated, and their names do not prove object provenance. New runtime aliases cannot be hidden in those exclusions.

The pinned sibling and JSON checks and success record preserve the intended scope. Inputs are canonical regular files and re-read after analysis; this is a cooperative stable-filesystem contract, not protection against an adversarial concurrent writer. The implementation does not run a compiler, linker or target code, regenerate its reference, emit a flash image, or choose a reserve/margin.

## Independent replay and probes

I copied the final supporting runner unchanged and personally replayed all **76 API and 76 CLI outcomes**: **8 positives and 68 rejections**, with no unexpected exception or mismatch. The exact case manifest is `41f951b39e4a0b81481b9d9d1977a16cf375f12901c53572d58db20cf6b507c2`. Its corpus includes actual separately linked synthetic boundary/padding variants, a permitted local-symbol permutation and FILE rename, instruction/direct-BL/switch-table/callback mutations, coordinated false LMA/literal changes, duplicate exception symbols, segment tails and altered board pointers. Of 68 negative cases, 53 retain all four immutable digests, so the broader structural checks are exercised independently of the content hashes. All fixture and checker hashes remained unchanged.

I also authored **1,576 additional API rejection probes** against the independently reproduced synthetic ELF:

- 212 single-byte changes across the complete ELF and five-PHDR headers;
- all 100 32-bit fields of the ten section headers;
- value changes in all 1,097 symbol records, retaining duplicate local records separately;
- 70 boundary/seeded changes across the four immutable loaded regions;
- all 32 single-bit changes of the one permitted reset literal;
- one changed byte at every offset of the 48-byte board structure;
- 11 invalid reserve/type values, five truncation boundaries, and the actual earlier R=3008 ELF that passed the old raw BOOT audit.

Every intended probe rejected with `ValueError`; no arbitrary crash was counted as a rejection. The underbound object illustrates why the old BOOT placement audit alone is insufficient.

Three previously independently linked synthetic images passed this actual implementation: the unchanged author fixture, minimum strings with test-only R=3016, and maximum strings/scalars with test-only R=8104. This review reused those exact already-built files and did not claim another compiler/linker run. One in-memory, explicitly test-only caller-mode classification probe preserved all three false approval/transfer flags; it was not a real board attestation.

A fresh relocated checker/sibling set passed under isolated Python and hostile inherited Python variables. Separate stale-reference and wrong-generator copies rejected by their specific hash checks. These three CLI receipts preserve argv and output. Source, reference, parser, README and original fixture hashes rechecked unchanged.

Evidence is in `supporting-replay/RESULTS.json`, `independent_probes.py`, `independent-results.json`, `snapshot/` and `REVIEW_MANIFEST.json`. The bounded tests strengthen the source review but are not exhaustive malformed-ELF fuzzing.

## Remaining obligations before any bound transfer

The full procedure still must establish the exact ordered original 33-object provenance, generated and compiled board source/object provenance, the separately reviewed board-object gate, pinned linker/flags/inputs, both original BOOT audits, the map's exact 363-input-range bijection, stable-input/failure receipts and explicit reserve/margin rationale. None is inferred from this layer's success. Runtime interrupt, memory-integrity, reset/MSP and physical-board assumptions remain conditional exactly as before.

A future combined success may bind the conditional bound only to an exact final ELF and complete evidence record. No whole-device acceptance, hardware verification, execution, flash approval, engineering reserve selection or remote publication occurred here. Production/design files were not edited.
