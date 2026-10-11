# Critic-A integration addendum R4: copied board/object and ELF gates

## Exact local conclusion

The bounded integration delta is locally cleared at tree **`73864b10113c7cb53de5ae7a17d9734687aebe7e`**, relative to previously reviewed R3 tree **`c599cff95ba18ac354e6187b7bce6c7d6816b371`**. The underlying parent remains PR72 **`adabc2044a3a376056d6756e67bede05c0726440`**. This is an exact local tree binding; no new remote commit or publication is inferred.

Formal project status remains **BLOCKED_EVIDENCE, score:null**. No full stack transfer, reserve/margin selection, board verification or hardware acceptance follows from this delta.

## Scope and byte preservation

I independently reconstructed the entire candidate from the parent Git archive and the supplied full patch, SHA256 **`0d8e05e4d3586d3eafd3e8c60e9f60ce0cd48ed685a4e32d20d348f1ebf7914c`**. A fresh local index yielded the exact R4 tree. Every one of the **1,073** resulting files was checked against Git blob identity, mode, root candidate bytes and a SHA256, and rechecked after the helper executions.

Exactly six paths differ from R3:

1. `docs/progress.md`: a new checkpoint prepended after the title; all preceding historical contents remain byte-exact.
2. `firmware/build/board_object.py`: new, byte-identical to reviewed source `c8cce02a89bead37f59bff5c49572a08c55637d85a8fdf4f8f234ec89924dc9e`.
3. `firmware/build/image_equivalence.py`: new, byte-identical to reviewed source `b8322ee42b850ce38c025b213643a89ab3a71c25182b257aa2a6bf9e6b64a86d`.
4. `firmware/build/stack_reference.json`: new, byte-identical to independently reviewed reference `c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d`.
5. `firmware/build/BOARD_OBJECT_README.md`: reviewed documentation with only its opening status corrected to completed narrow review and continuing project block.
6. `firmware/build/IMAGE_EQUIVALENCE_README.md`: the same bounded opening-status correction, retaining the separate design review and incomplete full recipe.

All other **1,067** R3 paths and modes remain unchanged. Across the full parent-to-R4 delta there are 46 additions, 14 modifications and no deletions; 1,013 original parent paths/modes remain exact. All `host/` paths remain exact. No C/H, assembly, linker, object builder or object manifests changed in this step.

The unchanged builder is `3acbaa9ae279d2483b37dd266a8826e26dc0cbdb507296ae286fd3aec861040b`; `inputs.json` is `898deea0d20bbeefd2a7131b14ac21e8b3205bd9d7325f6c992f9d034c3c60da`; `toolchain.json` is `22f0c2204b8b381a26af3b910851b43b04f34549b6677cdb5b76f304c4e37b43`. The prior qualified 33-object build evidence carries forward through byte identity. No redundant firmware rebuild was performed.

## Relocated helper checks

From the freshly reconstructed checkout whose path contains spaces, using isolated Python with hostile inherited Python variables, I ran six actual CLI checks:

- board-object checker on both independently compiled minimum and maximum synthetic board objects: structural PASS;
- ELF checker on both independently linked minimum and maximum synthetic images, with their original test-only 3016/8104 arguments: byte/layout PASS, `stack_bound_transferred=false`;
- board-object checker on a copied object with an altered relocation type: specific relocation-mismatch REJECTED;
- ELF checker on the preserved R=3008 underbound image: specific explicit-reservation REJECTED.

Exact argv, exit codes and stdout/stderr are retained. All successful records keep hardware/flash approval false. These are relocated integration checks using existing synthetic fixtures, not fresh compiler/linker runs or actual board inputs. The reconstructed tracked and untracked file sets remained unchanged after execution.

## Documentation and carried-forward limits

The new progress statement accurately distinguishes the separately reviewed design, data-only object gate and final-ELF byte/layout gate. Its counts agree with the completed scoped reports; it explicitly keeps ordered provenance, board compilation, original BOOT audits and linker-map binding open. It does not claim stack transfer or a newly chosen reserve. Relative links to both new READMEs resolve in this exact checkout. The old progress file matches the preserved root copy and the R3 Git blob.

The source-layer reports remain separate:

- board-object review: `15c2465e82e4b0a2c282c7da3295e6043b13e89a9abc5465d3d1b000bf65e375`;
- ELF byte/layout review: `ca080d8fb4d973cdb10c1f2f4e74ff385e81829dbc49eb9bb094db53c8a4141d`;
- design/reference review: `abd2126c03be3011215537285e7572578481c53b2237f30b6dca732e45f7b6f6`.

No source/design was edited by Critic-A. No remote publication, CI, merge, target execution or hardware activity occurred. A future combined recipe and its complete failure behavior still require independent review before any conditional bound-transfer claim.

Evidence: `verify_object_image_r4.py`, `object-image-r4-verification.json`, `object-image-r4-docs.diff`, `basis/LOCAL_TREE_R4.json`, preserved full patch and `REVIEW_MANIFEST_R4.json`.
