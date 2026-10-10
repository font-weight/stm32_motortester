# Critic-A TIME-001: recovery and documentation addendum

Date: 2026-10-10, after the observed environment replacement around 14:18 UTC. This addendum accompanies the preserved original review, SHA-256 `7f360ded40eb35943adbc8627fdc8909b3c4735ac48102b50b7774af71ead471`.

**Current verdict remains BLOCKED_EVIDENCE; score null.** No implementation change or new in-contract C defect is involved in this recovery. Immutable publication binding and the original integration/hardware gates remain pending.

## What was recovered, and what was rerun

The previous local workspace became unavailable before TIME-001 publication. Original raw logs and binaries were not recovered as retained files. I reconstructed all seven original source/document/test/script inputs, my supplemental harness/runner, source manifest and original report from their retained creation/read text. Every recovered item matched its SHA-256 recorded before the loss. The C/H remain exactly `b403cc8f5e60cc2568b59d9fc5de6cf2cf1f116c03291876e7a05a5dc19f2740` and `613135965695f6e1eb4e24edea9e2873a33b60ee4b44865a5db1ef1730b5b3d2`. This was byte restoration, not a rewritten implementation.

The supporting tester independently recovered its original harness, runner and report and verified their old hashes. I personally reran the following against the recovered exact C/H:

- Original author strict GCC and ASan/UBSan: successful; `author-recovery.log`.
- My original supplemental strict GCC and ASan/UBSan: **462,147 scenarios / 32,222,386 checks** in each execution; `independent-recovery.log`.
- Recovered supporting harness: all **16 groups**, strict GCC and ASan/UBSan; GCC `-fanalyzer` clean; `supporting-recovery.log`.
- Original ARM script with the restored ARM GCC 14.2.1: **1008 B text, 52 B context**, only unresolved `memset`; object SHA-256 again `7219ce425cb16a8ec91af9e6ba17d1a49cddb5d7040e935e5bb21cb3119f02c1`; `arm-recovery.log`.

These files are fresh post-replacement execution logs. The author and supplemental log contents happen to reproduce their former hashes because the commands/output are deterministic, but they are not represented as surviving original log files. Sanitizer leak detection remains disabled. No software-model result is promoted to physical timing or hardware acceptance.

## Narrow README evidence-attribution correction

The restored original Russian README, SHA-256 `9882be7c3de2a3f9c48ee3804f5d0919fda6977ad9dc35ef4afc5379a73b0786`, said the Stop gate/readback scenario had been found by tests. Available history instead establishes an inspection finding followed by tests of the corrected path. No saved execution of the prior faulty TIME-001 version was supplied.

I raised that documentation-attribution issue. Root personally changed only the relevant Stop paragraph to state those facts explicitly. I inspected the exact diff. The corrected README SHA-256 is **`ee9339a69044cfa609ded6f081ebec17e1705d24663a1fc2b4614d4d3492ee4d`**. This closes the local attribution issue; it does not create a historical pre-fix reproduction. The original README is preserved separately, and all six executable inputs are unchanged.

`source-manifest.json` remains the original recovered manifest; **`source-manifest-recovered.json` is the current seven-input manifest** with the corrected README. The original review's README hash and old log paths describe its historical checkpoint; use this addendum and the recovery filenames for the current candidate.

The same remaining gates apply: one shared application epoch, pre/post health checks and output withholding, invalidation/recovery of dependent state, service in every state, actual ADC deadline and callback-bracket timing, complete stack/ELF/resource evidence, startup/volatile MMIO and board validation. CA-CTRL-4 remains a separate open shared-policy question. No approval, merge or numeric acceptance score is supplied.
