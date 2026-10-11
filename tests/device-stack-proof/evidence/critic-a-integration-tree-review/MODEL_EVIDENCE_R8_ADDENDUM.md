# Critic-A integration addendum R8: modeled firmware evidence checkpoint

## Exact local conclusion

The documentation-only delta is locally cleared at tree **`15c912a75bdb61546148f0abe483706d977955c1`**, relative to cleared R7 **`d133d4a207ea7ab5a284fcb6cca96b77494a6065`**. The underlying parent remains PR72 **`adabc2044a3a376056d6756e67bede05c0726440`**.

Formal project status remains **BLOCKED_EVIDENCE, score:null**. This checkpoint summarizes three independently reviewed local model scopes; it neither publishes their evidence nor expands their conclusions into whole-project, physical-board, Windows or target-stack acceptance.

## Independent identity and scope checks

I reconstructed a fresh checkout from the immutable parent archive and full R8 patch SHA-256 **`4b4aa7a0785440b8d69794afa8f14859fbccd5d74108437a0ef7de3a1d849bf1`**. Its newly generated index reproduces the exact R8 tree. All **1,076 files** were compared by Git blob, file mode and SHA-256 against the reconstructed and current root checkouts.

Only `docs/progress.md` changes relative to R7. The other **1,075 paths and modes** remain exact, and no file mode changes. The full parent-to-R8 delta remains **49 additions, 16 modifications, zero deletions**, with **1,011 inherited paths/modes** preserved exactly and every `host/` path unchanged. C/H, assembly, linker files, Python, vendor code, qualified recipes/manifests/reference files and all other documentation are byte-identical to R7.

The new checkpoint is inserted after the existing title. The entire earlier progress history is byte-identical both to R7 and to `progress-before-model-evidence-r8.md`; no historical result is rewritten. `git diff --check` passes. No executable tests or ARM builds were repeated for this prose-only delta. Previously reviewed executable evidence carries forward only by exact byte identity.

## Truthfulness of the checkpoint

I rechecked the identities and conclusions of all three independent reports:

- Runtime Auto/Single: `CRITIC_A_RUNTIME_MODES_REVIEW.md`, SHA-256 `8ed60499ecab7da4455fac7fd310042caff5b1736443a129d4f6cb6193550c21`.
- Modeled full FSDEV/runtime smoke: `CRITIC_A_FSDEV_RUNTIME_REVIEW.md`, SHA-256 `0ffda8551c18cb9326ec1132e1fb4ddf96ff5ba6c4c6e6598a7dc44ca5c5fc05`.
- Modeled Normal/pre1024 FSDEV capture: `CRITIC_A_FSDEV_CAPTURE_REVIEW.md`, SHA-256 `a954ec8945e518d17f5ce02b4c12ae131e349faefd69717da73de17c2ccbf2fb`.

The first bullet correctly identifies four Auto/Single combinations, real composed runtime/controller/core code with a modeled DCD, independent retained-sample/CRC checks, the Auto timeout, real code-path Stop, distinct release/rearm behavior and two selective mutations. It does not call this the complete FSDEV path.

The second bullet correctly distinguishes the complete patched FSDEV driver from its logical EP/ISTR/PMA hardware leaves. Actual core/CDC/descriptors/runtime and queue forwarding are present, with PMA observations, deferred address and reset/CTR/session invariants. The two negative controls refer to their intended semantic failures, not generic failed compilations.

The third bullet correctly records Normal/pretrigger1024, actual wire CONFIG/ARM, the 2,048 retained samples, eight DATA chunks of 250 samples plus one of 48, sample CRC `c7756652`, RELEASE restart, STOP and final reset. Its exact final 5,186-byte wire SHA-256 matches the independently reproduced capture. The repaired-frame-CRC omission control was rejected at the expected DATA offset assertion.

Crucially, the qualification paragraph preserves both applicable limits: the pending DATA IN was held **after acquisition freeze/Stop**, while draining the ready window; the subsequent 128-sample history evidence covers cleared state, count and last code rather than a second complete capture or an oracle over every internal history word. No continuing acquisition under stalled DATA claim is introduced.

The source model/hardware distinctions, disabled LeakSanitizer, local/non-autonomous nature of the supporting evidence and all physical IRQ/USB/ADC timing/Windows/stack/board limitations remain explicit. Board inputs, engineering reserve/margin, hardware evidence, applicable cross-team verdicts and publication permission stay open. The prose adds no electrical, power or flashing instructions or concrete board values.

I also read and hash-bound root's three separate strict replay receipts. All report PASS on the exact R7 tree with unchanged inputs, supporting the checkpoint's distinct statement that root repeated the strict builds. They are corroboration, not substitutes for the three independent replays.

No new local navigation links or missing evidence-file links are introduced. The entry deliberately summarizes local evidence without claiming that the unshipped test directories are a reproducible public archive. No finding remains in this bounded prose delta.

## Review artifacts

`verify_model_evidence_r8.py`, `model-evidence-r8-verification.json`, `model-evidence-r8.diff`, `model-evidence-r8-root-receipts.json` and `REVIEW_MANIFEST_R8.json` bind this addendum. Earlier integration reports and all candidate/evidence files remain untouched. No commit, remote ref, CI, publication or hardware action was performed.
