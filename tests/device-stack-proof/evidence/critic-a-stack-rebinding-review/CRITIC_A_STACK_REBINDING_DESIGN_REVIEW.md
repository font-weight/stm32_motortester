# Critic-A: conditional stack-rebinding design and reference

## Conclusion

**The proposed narrow design is sound enough to proceed to implementation.** I independently verified the reference derivation and found no remaining design blocker. If a future checker establishes every stated byte, layout, provenance, board-data and exception-condition obligation, the existing **conditional 3,016-byte bound transfers** to that exact new ELF. Matching object hashes or passing the original BOOT audits alone does not establish this result.

This is **design/reference clearance, not acceptance of an implemented gate**. The extraction and one-fixture comparison scripts expressly are not such an implementation. No production reserve, engineering margin, board configuration or flash permission is selected. Formal project status remains **BLOCKED_EVIDENCE, score null**; disclosure hold remains.

Exact reviewed subject:

- `DESIGN_RU.md`: SHA-256 `f4b2b72907820dcc1f3467444f781f298a35232cf0c3d8ea5eda091324d4f5ce`.
- `DRAFT_REFERENCE_MANIFEST.json`: SHA-256 **`c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d`**.
- Original analyzed ELF: `0450e7ee104e19d6a1c018c2f6d1bdcb6f4b74544063044e14c41c01b586c2ae`.
- Prior independent stack review: `035d31817eeccf077280fd36ae9c06e0240dbe61977e89bd80703d7c7f68afa4`.
- Date: 2026-10-10 UTC.

## Reference derivation

I wrote a separate extractor/parser in my own review directory rather than accepting the author's generated manifest by identity alone. Its results agree with the reviewed reference:

- All **33 original objects**, in the original successful ld command's order, with exact names and SHA-256. Their basenames are unique, so the canonical range record's basename identifies its position in this pinned list unambiguously.
- **363 text-input ranges**, independently parsed from the original linker map and checked against their original objects' input-section identities and sizes.
- The full sorted multiset of **410 STT_FUNC records**, preserving aliases, duplicate local names, types, bindings, visibility and section identities; **360 distinct function addresses**.
- The **1,053 immutable symbol records** under the stated narrow exclusions.
- All four loaded-region hashes: 59 vector words, both immutable text/rodata intervals and 128 initial data bytes.
- Fixed entry, vector/text/board/data/bss geometry, static end and stack top.
- The original proof's **344 input hashes and 29 artifact hashes**, plus the declared BOOT files and the independent review identity.

The baseline ELF also matches the ELF from my earlier independent complete BOOT004 rebuild. All nonempty original text inputs are included, including the three retained overridden weak fallback bodies; there is no reduction to public symbols or a dictionary keyed by function name.

Evidence: `derive_reference.py`, `reference-derivation.json`, `derived-ranges.json`, `derived-symbols.json` and the exact copied design/reference.

## Why the single reset exception is valid

The actual Reset input section starts at `0x08007d90`. Its literal at input offset `0x58` is the linked word at **`0x08007de8`**. The original reset object has the stated **R_ARM_ABS32 relocation to `__data_load`** at that exact input offset.

I decoded the actual Thumb instruction at Reset+0x1a: `0x480f`, LDR r0 from aligned PC+4 plus 60 bytes, which addresses precisely this word. It supplies the source address for the bounded `.data` copy loop. It is not a branch target, stack value or callback. The final instruction at +0x48 is an unconditional self-loop; +0x4a is zero padding and the following literal pool is nonexecuted under the original verified CFG. The original BOOT checker must independently revalidate the complete reset sequence for each candidate.

Therefore allowing this one word to change is justified **only when its entire uint32 value equals the independently validated new data LMA and actual segment/file mapping**. It is not an arbitrary ignored window. Both surrounding hashes retain all other instructions, branch encodings, literal values, callback tables, switch data and padding before mt_board. Broad ABS32 or whole-reset/rodata masking would not be justified.

The copy destination, size, initial data contents and bss-zero range remain fixed. Moving the source LMA while preserving these properties does not change the initialized runtime state or the reset stack use.

## Why conditional stack equivalence transfers

The proposed relation does not assert that all program data or feasible traces are identical. Valid board data can allow execution beyond the old invalid-zero-board startup rejection. The prior stack analysis deliberately did **not** prune those paths by the zero fixture, so its bound already includes the working startup, admission, foreground and USB IRQ paths.

Under the proposed full gate:

1. Instruction bytes and addresses are unchanged. Every direct branch/call target and SP-affecting operation is consequently unchanged.
2. The nine guarded switch tables, their guards and addresses remain byte-identical; the prior target/dominance proof carries forward.
3. Existing code-pointer constants and initial writable data remain identical. The original object/address-taking relationships and absence of a new producer preserve the 53 indirect-site resolutions, including the explicitly infeasible/NULL cases.
4. The only new data are the validated board scalars and three bounded ASCII arrays. Their exact layout and relocations forbid converting board data into an extra callback table or code pointer. No new executable input, strong override, alias, helper or veneer is admitted.
5. Vectors, CPU admission/masking code, strong USB wrappers, equal-priority setup and all original exception assumptions remain unchanged. The board priority value changes both USB priorities together within the same supported profile; it does not authorize additional interrupt sources.
6. The earlier machine-derived local frames and acyclic call graph therefore remain applicable. Bounded loop iterations may differ with board values, but the earlier CFG/SP proof contains no accumulating stack loop or input-dependent dynamic frame to amplify them.

This preserves foreground 2,640 bytes and USB IRQ software 264 bytes. Adding the same conservative 36-byte exception allowance yields 2,940; the same terminal HardFault/NMI assumptions yield 3,012, rounded to **3,016**. Startup remains bounded separately at 288. Integrity of memory/MSP, genuine reset-only execution, the original interrupt exclusions and absence of external code/debugger interference remain conditions, not newly verified board facts.

## Board, symbol and load-image obligations

The restrictive board-object contract is materially necessary: one 48-byte GLOBAL/DEFAULT OBJECT, three bounded LOCAL const arrays, no code or other allocating payload, and only three zero-addend ABS32 relocations to the exact array section starts. The first 36 bytes must be checked against parsed JSON and the fixed ABI. A familiar filename or “no functions” symbol listing is insufficient.

The original linker merges board rodata into the executable-flagged output `.text`. Requiring non-X board **input** sections is correct; requiring a separate non-X final section would change this linker profile. The transferred control-target proof, explicit data boundaries and absence of fallthrough establish that those appended bytes do not introduce instruction paths. This is not MPU or physical nonexecutable-stack enforcement.

The five program-header roles and exact section/file/physical-load mappings prevent an equal-looking section table from hiding extra runtime bytes in a load-segment tail. All candidates must preserve the old data/bss VMAs and sizes, and the final board/string region must exhaust the text tail with only the stated zero alignment padding. Header/symbol metadata cannot substitute for checking actual bytes.

Implementation should preserve the design's narrow exclusions literally: validate the three excluded linker symbols' expected roles/values, the exact three new local board objects and their three mapping records before removing them from multisets. Do not drop every similarly named symbol or infer trust from a map alone. The reference must be independently pinned to the hash above; extracting a new trusted reference from an arbitrary candidate would invalidate this review.

## Fresh bounded experiments

I independently linked the original 33 objects with the author's existing synthetic board object and explicit **test-only R=3016**, using the original strict ld command profile and both unchanged BOOT audits. The new ELF reproduced byte-for-byte:

`48ff7846006813b13141d6a2d65fb05c0615ac51b9243bd2e5e57ba7831f982b`.

I then generated two additional **all-false-attestation synthetic** inputs through the reviewed generator and compiled only their data objects using the recorded canonical board flags. No actual board values were assigned and none of these ELFs was executed.

- Minimum strings: one character each, S=6 bytes including NULs; test R=3016. New text size 32,180, data LMA `0x08007ea0`; ELF `fe29e4b6b89de919b8cc6232e9ad333f437bfb11c29ffd44cf9d9c04dbf3943c`.
- Maximum strings and scalar bounds: 31 characters each, S=96; test R=8104. New text size 32,268, data LMA `0x08007ef8`; ELF `a0d948d98a7941c2f5d3e5f150621c6c7806c00b9e28dc21c16a51512bfb7b66`.

For all three positive links I checked the four region hashes, all 363 ranges, both symbol multisets, the precise reset literal, board scalar/pointer/string bytes, text-tail formula/padding, and all five segment roles and their actual no-tail mappings. Both original BOOT audits and strict links passed. Inputs rehashed unchanged.

A fourth counterexample used **R=3008**. The raw strict link and original BOOT ELF audit both passed, but the proposed explicit bound inequality rejects it. This demonstrates the need for the new gate rather than attributing stack sufficiency to existing placement checks. Conversely, R=8104 merely reaches the fixed geometric limit (`__StackLimit == __static_end`); neither endpoint is a production-reserve recommendation. Margin remains an explicit separate decision.

Evidence: `test_links.py`, `link-commands.json`, `design-experiments.json`, all command stdout/stderr and four prominently test-only experiment outputs. There were 16 recorded commands. My initial isolated-Python probe failed before any link because of a sibling-import assumption; its script/log remain preserved. Explicit sibling loading corrected only this local test harness. No failure was counted as an intended gate rejection unless the actual intended check executed.

## Implementation and acceptance still required

The design's full negative suite remains necessary: tampered/reordered objects, board code/aliases/sections/relocations/addends, wrong literals, shifted functions, changed SP/callback/switch/vector/data bytes, fake maps, load tails, stale reference, failed audits and input drift. Positive scalar/string/reserve boundaries must be checked without weakening any equality after failures. My bounded probes are not that implementation suite and my parser is not a production ELF gate.

A future successful record may state only that the **conditional** bound is rebound to its exact ELF SHA under the unchanged enumerated assumptions. It must bind reference/review identities, all ordered inputs, board kind and provenance, tool/flags, audits, actual layout, equivalence results and explicit R. It must not mark hardware_verified, flash_approved, whole-device PASS or a new quality score.

No final-image recipe, executable checker, code change, remote action, board experiment or production reserve was created or accepted in this review.
