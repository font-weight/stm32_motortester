# Critic-A addendum: intermediate file-symlink parent admission

2026-10-10 UTC. This new bounded observation qualifies the earlier ADC003 r3 and TX002 r2 local package clearances. Preserve the original reports and successful functional evidence unchanged; do not use their earlier publication clearance until this P2 is addressed. Formal verdict remains BLOCKED_EVIDENCE, score null. No production defect or source damage is claimed.

## Exact affected subjects

- ADC003 r3 tree `1a4900ceed7f29b83f2c2b09693183f7be3bad42`, reproduce.py SHA-256 `5fb967b0d94bad1e522dd0f0951736d839921375e97040aa1a80e2d32392b362`.
- TX002 r2 tree `2a697a3eaf7558a46c82f413eed664f3b5564a1e`, reproduce.py SHA-256 `035956d07c9231420919ae39d7ab61347742a4aea1ca95caac4a219d4b5e826d`.

## P2 finding

The three-parent protection covers the invoked file's lexical parent, that directory's resolved parent, and the final file target's parent. It does not cover a distinct intermediate FILE symlink's parent when the chain is:

`invocation/reproduce.py -> intermediate/reproduce.py -> exact-checkout/tests/.../reproduce.py`.

Both first links are actual file symlinks; their directories are ordinary separate directories. This differs from the previously closed directory-symlink followed by file-symlink geometry. In both otherwise valid invocations, an output under `intermediate/` was accepted even though the documented backing-parent isolation promises exclude it. These packages support valid symlink interfaces and do not currently exclude this stable chain.

I reproduced the exact unchanged scripts through disposable aliases. TX002's read-only `--preflight-only` also exited zero. Actual writer invocation created ADC003's output directory and TX002's output directory plus 80 files before I intentionally terminated each process group as soon as admission was observed. Both exits are -15 from that deliberate stop, not passing full-suite executions. Original script hashes remain unchanged. No original-file mutation, recursion, malicious race, hardware impact or security-sandbox escape is claimed.

`probe.py` and `results.json` preserve the full argv, exact graph, hashes, observed counts and termination attribution. This is a bounded admission check only. The older successful suites and previous alias fixes remain valid within their tested geometry; they did not cover this one.

## Required bounded resolution

Either protect every intermediate symlink substitution used to resolve each consumed file, including script/tool/header/source backing paths, or explicitly reject unsupported chained-file aliases before mkdir. Apply the same rule to the companion entrypoint and preserve the original bytes/evidence. Add this exact stable geometry as a refusal regression and retain a disjoint supported positive run. A documented narrower canonical-path-only profile is also possible if the implementation actually rejects noncanonical inputs, rather than only weakening the claim after acceptance.

The BOOT001 profile rejects all symlink components up front, so this finding does not qualify its separate canonical-path clearance. No external publication or remote mutation was performed.
