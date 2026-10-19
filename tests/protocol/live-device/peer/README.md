# Coupled C endpoint / exact external HOST tests

Integration-ready test files: `bridge.c`, `test_coupled.py`, `run.sh`.
These files contain no copied HOST implementation. Supply an external exact
PR195 materialization or checkout. Neither side is a replacement model:
the C bridge calls actual endpoint/codec/queue code, and Python imports the
actual HOST Endpoint after validating all three runtime bodies by SHA-256.

## Run

```sh
SOURCE_ROOT=/path/to/device-repository-or-source-export \
HOST_ROOT=/path/to/exact-pr195-checkout/host \
OUT=/path/to/new-results ./run.sh
```

Both roots are required; no particular workspace layout or absolute local path
is embedded. `CC` and `PYTHON` are optional. `OUT` must name a new, nonexistent directory; when omitted, a new temporary
directory is created outside the source checkout. A system C11 compiler with GCC-compatible sanitizers and
Python 3.10+ are required. No Python package installation is required.

HOST is pinned to commit `0e96e574c2ef5c7234a6cfc96665cb88ddca9911`, tree
`aa4f0ef50c901dc3faf10578ce5d66615d58c3a5`. Package initializer, codec and endpoint
hashes are checked before import; actual import paths are then checked too.
These checks are explicit exceptions and stay enabled under Python `-O`.

The original frozen worker run targets C source `6fef3f9786813f97d334bc1e21a848d63d2f99de`.
The integrated four-mode replay also passes corrected C source
`dd40a62e39db556c268baca8b229c7c77b3b7738`; actual input hashes bind each run.
The runner records actual C/protocol blob and SHA-256 identities, HOST hashes,
source revision, platform and Python version in `source-manifest.json`.
It accepts another C source root for independent regression/mutation review;
do not transfer a verdict across those recorded source identities.

## Verification

Unchanged 22 coupled methods produce 148 completed scenarios and two frame-size
probes in each of four runs: normal Python, optimized Python, UBSan, and
ASan+UBSan. C is compiled with strict C11, `-Wall -Wextra -Werror -Wconversion
-pedantic`. ASan runtime is preloaded before Python; leak checking is disabled.
Address/UB checks cover instrumented C and bridge, not a general Python leak
claim. Failures stop the runner and retain the failing log.

Each run saves exact accepted HOST/device bytes, observed immutable HOST events
and selected read-only C state. Actual device writes are limited to 64 bytes;
HOST receives exact bytes chunks up to 544. HOST records each single-use
WriteOffer's accepted prefix before any C response can be returned to it.
Sample and CRC expectations derive only from synthetic acquisition inputs.

Coverage includes clean full/final-partial/empty streams, two shutdown tails,
sample queue and tail-capacity overrun, known/unknown produced endpoints,
invalid snapshot, failed quiescence, exact failed-START causal identity,
first fault preservation, 12 zero/partial-write/fragmentation combinations,
every incomplete START_ACK boundary, every END boundary including complete,
STOP during DATA, pending START samples, fresh-nonce recovery and independent
HOST timeout. Detailed device timing faults are covered by the separate native
failure suite.

Synthetic callback results do not establish real hardware quiescence, DMA/ADC
ownership, qualified callback WCET/preemption, USB/CDC, Windows/COM, throughput,
storage/durability or common-gate acceptance. This package assigns no score.
