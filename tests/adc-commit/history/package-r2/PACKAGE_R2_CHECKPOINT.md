# ADC003 package r2 independent review checkpoint

Reviewer: the unchanged independent Critic-A. No production edits.

Status: BLOCKED_EVIDENCE; score: null. Production review remains unchanged. The original wrapper defect is closed, but a new test-runner output-isolation defect requires correction before package clearance.

## Exact subject

Local-only snapshot `4de72de2800662c674e9cd3537890a2839e62c45`, tree `b2e4cb6a0f5eba9de09870f773ab13bcec796986`, intended sole parent PR72 `adabc2044a3a376056d6756e67bede05c0726440` / tree `f6750c8ccbd6dd8d296187fbc45cc3c72b03b0ca`.

Package manifest: `b7ded90a81fc05bc794353404efab1df23a3a6db84cdaa8938dc5fcbb2e274ac`. All-file manifest: `bc5b3dd03039a1bbe864426df894e5422536b45438d38d03ea10a4af65949497`.

## Personally verified

- Fresh independent Git clone, detached exact local snapshot. Full tree and every changed file's byte count/SHA-256/Git blob/mode checked against my previously live-fetched PR72 tree and the frozen package inventory.
- Exactly four already reviewed production replacements and 146 additions under tests/adc-commit; 1,023 inherited blobs/modes preserved. All four production files match the original source review and package r1. No host, shared wire or later-module delta.
- Complete packaged reproduction under deliberately polluted CPATH, GCC_EXEC_PREFIX, PYTHONOPTIMIZE and PYTHONPATH: strict/sanitized candidate and baseline, 65 admission scenarios plus original legacy tests, three assertion-detected mutation controls, 20 supplemental boundary scenarios in each mode, four ARM objects and ABI. All nine stages passed. Checkout hashes and Git status stayed unchanged.
- All four ARM object hashes equal the original source-review objects. No executable Cortex-M3, final ELF, real IRQ timing or board claim follows.
- The unchanged original valid-toolchain alias fixture now exits 2 before output creation, with no input changes. This closes the initial reproduce.py backing-parent defect. Current main wrapper SHA-256 is 3806ed4ca2cee854f99fee0145f2a232cb0645977a99330a3192a247d6f3b4c5.

Evidence: package-r2-integrity.json, reproduction-r2-check.json, reproduction-r2.log, /workspace/shared/adc003-critic-package-run-r2/receipt.json, and /workspace/shared/adc003-critic-fixed-alias-r2/receipt.json.

## P2: newly added companion repeats the output-boundary omission

`tests/adc-commit/test_reproduce_paths.py` protects only advertised checkout/toolchain roots and its resolved script directory before mkdir. It does not protect the actual backing parents of consumed aliased tools.

Using the valid official-tool-copy alias fixture left by the unchanged main-wrapper regression, I ran:

```
/usr/bin/python3 checkout-r2/tests/adc-commit/test_reproduce_paths.py \
  --checkout checkout-r2 \
  --toolchain /workspace/shared/adc003-critic-fixed-alias-r2/proxy \
  --out /workspace/shared/adc003-critic-fixed-alias-r2/real-tools/companion-must-not-be-created
```

The companion created its output and 352 files inside the real executable input parent. It subsequently exited 1 because `cc1.relative_to(tc)` assumes GCC's real cc1 path lies under the advertised proxy root. Original source and tool bytes remained unchanged. This is demonstrated unwanted output creation, not observed corruption or a false successful test result.

Evidence: companion-alias-r2.json and companion-alias-r2.log contain exact absolute argv, tool/script hashes and traceback.

Required correction: before any mkdir, validate every consumed input and protect its lexical/resolved directories and actual backing parents, including the companion's own script. Preserve this version and failing evidence. The alias fixture must reject before creation; disjoint supported layouts must continue passing. Avoid the unsupported assumption that GCC metadata lies inside the advertised toolchain root, or explicitly reject such unsupported layouts before writes. Production is unaffected.

## Remaining acceptance gate

Re-review the corrected companion/package and exact immutable publication/base. This checkpoint gives no remote publication authorization and no whole-firmware or physical-safety acceptance. Historical findings and failed evidence must remain separate from later fixes.
