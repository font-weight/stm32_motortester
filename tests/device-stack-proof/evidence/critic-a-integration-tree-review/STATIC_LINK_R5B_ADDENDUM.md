# Critic-A integration addendum R5B: conditional static-link recipe

## Exact local conclusion

The bounded integration delta is locally cleared at tree **`4ecb8a310da3e2d93bd7cdf3d3ac2dfd85271d00`**, relative to reviewed R4 tree **`73864b10113c7cb53de5ae7a17d9734687aebe7e`**. The underlying parent remains PR72 **`adabc2044a3a376056d6756e67bede05c0726440`**. The earlier R5 tree `328f58ebbfe39fecfb69fb934bf7760c9268874f` is preserved as historical and is superseded by R5B's two README corrections.

This binds the previously reviewed conditional static-link implementation into the exact local integration tree. Formal project status remains **BLOCKED_EVIDENCE, score:null**. Physical board inputs, a justified production reserve/margin, hardware assumptions, publication and project acceptance remain open.

## Independent reconstruction and preservation

I reconstructed a fresh checkout from the immutable parent Git archive and the full patch with SHA256 **`368a3b2561bc1237204e0e2f6e6aab9d8231aa935116c48d2dbd9059ec65993e`**. A fresh index produced the exact R5B tree. All **1,075** files were verified by Git blob identity, mode and SHA256, and checked again after execution. The current root candidate was independently compared with those bytes and modes.

Exactly five paths differ from R4:

1. `firmware/build/build_image.py`: new, byte-exact to reviewed source **`0c4428388f0c261b1b96873072874b031cb6200ccbcf9fe51b942abac72c0d5d`**.
2. `firmware/build/BUILD_IMAGE_README.md`: new, with only review-status prose changed from the source-review snapshot. CLI, semantics, conditional assumptions and hardware/publication limitations are unchanged.
3. `firmware/build/IMAGE_EQUIVALENCE_README.md`: replaces the stale statement that the full recipe is not integrated with a link to the integrated recipe. The helper itself still reports `stack_bound_transferred=false`.
4. `firmware/build/BOARD_OBJECT_README.md`: changes “future recipe” to “full recipe”; its separate provenance and final-image obligations remain explicit.
5. `docs/progress.md`: prepends the 23:09 checkpoint after the title. Every byte of preceding history is preserved, including earlier statements of what had not yet been implemented at their recorded time.

The two README corrections close the concrete stale-prose finding reported during this review. All **1,070** other R4 paths and modes are unchanged. Across the full parent-to-R5B delta there are **48 additions, 14 modifications and zero deletions**; **1,013** original parent files/modes remain exact. Every `host/` path is preserved.

No firmware C/H, assembly, linker script, helper implementation, frozen reference, object builder or object manifests changed in this step. In particular, object builder `3acbaa9ae279d2483b37dd266a8826e26dc0cbdb507296ae286fd3aec861040b`, inputs `898deea0d20bbeefd2a7131b14ac21e8b3205bd9d7325f6c992f9d034c3c60da`, toolchain manifest `22f0c2204b8b381a26af3b910851b43b04f34549b6677cdb5b76f304c4e37b43` and reference `c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d` remain exact.

## Relocated integrated execution

From the freshly reconstructed checkout containing spaces, using isolated Python and deliberately polluted inherited Python/compiler variables, I ran the integrated recipe with the previously copied, byte-pinned 33 objects and toolchain and the synthetic minimum board fixture. No original firmware object was recompiled; the recipe compiled only the explicit generated board object and performed the real static link.

All **13 recorded stages** passed, including board-object validation, both original BOOT audits, exact final-image equivalence and linker-map binding. The result was `CONDITIONAL_STATIC_LINK_CHECKS_PASS`, with conditional bound 3016 B and `stack_bound_transferred=true` only under the original assumptions. All **323 pinned inputs** were independently rehashed unchanged; generated-output preservation passed. The map retained 363 nonempty ranges, 34 empty executable inputs and the expected 35 LOAD rows.

The synthetic test used reserve 3016 and explicit margin 0. Its ELF SHA256 **`8c589f9c9a7ab31335c21439a2a7f3170314940ee8114013f245886f2d166cde`** exactly matches the prior independently reviewed minimum link. Hardware verification and flash approval remained false. This test does not choose reserve or margin for a real board.

Two additional actual CLI checks passed their intended refusal criteria:

- reserve 3008: explicit conditional SRAM geometry rejection before output creation;
- invocation through a symlink to the integrated recipe: explicit symlink rejection before helper execution/output creation.

Exact argv, exit and stdout/stderr are preserved. The clean checkout retained no unstaged or untracked changes. New and amended relative README links resolve.

A preliminary R5 review-driver run stopped only because my expected diagnostic substring was `reserve` while the actual, correct rejection says `stack reservation`. That driver, the successful R5 link and the rejection receipt remain preserved. The corrected independent R5B driver checks the actual specific diagnostic; this reviewer assertion error is not counted as a product finding or an extra negative test.

## Carried-forward review scope

The full source review remains distinct: `CRITIC_A_STATIC_LINK_RECIPE_REVIEW.md`, SHA256 **`4059f1723179dc5a9c4b77433a70dfb7d6f312c73f5e32a6906c7f121c70cf30`**, and its manifest **`c84c39ede358d8ba646a8692fffde5514bd198916051cc58e8f3fa3531e47a4f`**. Its 943-case replay, separate 57-map corpus, two independent min/max links and retained failure probes were not redundantly repeated for this byte-identical integration delta. Their counts and attribution in the new progress entry are accurate.

The conditional transfer still depends on the original privileged reset/MSP/STKALIGN model, USB IRQ19/20 scheduling and priorities, terminal fault handlers, complete original callback/control-flow assumptions and the stated memory-integrity exclusions. Static equivalence does not establish physical clock, power, identity, interrupt timing or hardware behavior. This recipe produces no BIN/HEX and grants no flashing permission.

No production source or root candidate was edited by Critic-A. No remote publication, CI, merge, target execution or hardware activity occurred. This addendum is a local exact-tree clearance only.

Evidence: `verify_static_link_r5b.py`, `static-link-r5b-verification.json`, `static-link-r5b-final-check.json`, `static-link-r5b-docs.diff`, the three CLI records under `static-link-r5b-controls`, preserved full patch/metadata, and `REVIEW_MANIFEST_R5B.json`.
