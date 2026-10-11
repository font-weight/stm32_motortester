# Path-chain correction: local draft, independent review required

This package replaces only the two supported packaging entrypoints, their
packaging documentation and manifest. Production bytes and inherited parent
paths are unchanged. The sole proposed parent remains
`adabc2044a3a376056d6756e67bede05c0726440` (PR72).

The preceding exact package tree `2a697a3eaf7558a46c82f413eed664f3b5564a1e` and its prior clearances are
historical. They do not accept this new tooling revision. Frozen preceding
entrypoint bytes, manifest, plan, documentation and Critic-A's exact counterexample
are preserved under `history/path-chain-before/`; their original copies remain
unchanged outside this candidate. Critic-A's two entrypoint observations ended
with reviewer SIGTERM (exit -15) after output admission. Those observations are
not complete reproduction runs; their exact counts, hashes and scope are retained.

The bounded P2 was: input FILE symlink -> intermediate FILE symlink -> final
file, with output admitted beneath the intermediate file's parent. The preceding
three-root check missed that parent. This is stable-input output isolation,
not a production defect, security boundary, sandbox or TOCTOU claim.

Both `reproduce.py` and `check_preflight.py` now use the same component-by-component
`path_forms` function used by the parallel memory/clock packaging work. It retains
lexical input/output forms, each symlink target substitution with the remaining
suffix, and the physical final form. Relative targets and `..` are processed in
physical traversal order. More than 80 substitutions fails closed before mkdir.
For consumed files, every form's parent and resolved parent is protected; for
declared input directories, every directory form and its resolved form is
protected. Every output form is checked for either ancestry direction. Existing
source-alias rejection and input/hash preflights remain in place. This is a stable,
cooperative-filesystem contract only, with no concurrent replacement guarantee.

Normal inputs and documented external binary/header proxies remain supported,
including chained script aliases with genuinely disjoint output. Missing inputs,
loops, and protected-parent output are refused before the first output mkdir.
No hardware, network, installation, remote tree, commit, ref or PR is part of this
correction. Public evidence disclosure remains independently denied pending owner
approval; the correction does not override that gate.

New consolidated writer-admission/refusal/proxy receipts and exact-tree clean
full-reproduction receipts are kept outside the candidate to avoid self-hash
cycles. The clean full run is separate from deliberately terminated admission
probes. A new independent Critic-A verdict is required for this exact candidate.

## Portable current regression source

The existing supported companion command now includes current FILE -> FILE
script and compiler-tool parent refusals, its own script/tool-chain refusals,
and a disjoint chained-tool positive control. These are executable source in
the candidate, not just external receipts or archival absolute-path probes.
TX companion now runs 23 controls, including five current file-chain cases and a valid chained-tool read-only preflight.
