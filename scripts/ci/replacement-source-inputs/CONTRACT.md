# SOURCE-C1: portable source observation consumer

Declared before code, 2026-10-15, following WR-01..03 and the frozen wrapper plan.
This component consumes an externally admitted source-pool manifest; it does not
perform or authenticate installation/runtime/build admission. Wrapper must freeze
the expected manifest SHA in its real admission record before invoking this tool.

Only explicit CLI app/site/stdlib roots create source read scope; manifest roots
must equal them. Roots must be disjoint. Pool schema exactly: schema_version1,
scope wrapper-frozen-source-pools, trial_id, application {commit,tree}, roots
{app,site,stdlib}, files {app,site,stdlib}. Each files dictionary maps canonical
relative .py leaf to {bytes,sha256,owner}. App owner is exact APP commit; site owner
is one of the14 pinned distribution names; stdlib owner is
trusted-runtime-observed. This consumer does not claim those owners authenticated.
App .py map must equal the actual e263 sourceZIP's .py members after removing host/.
The installed PySide6/__init__.py baseline must equal the official original; the
modified variant admits only the exact prepared modified source at that one path.

Inputs: exact PRELAUNCH r3, exact inspector/companion/pins, actualsourceZIP,
exact preparation, poolmanifest+expectedSHA, explicit roots, actual EXE/workPYZ/
pureTOC, variant control/modified, fresh source-table and external sidecar outputs.
Read-only inputs/roots and both outputs must not overlap; outputs cannot be in a
source pool or replace inputs. Source table may be in its closed variant directory.
WorkTOC header is the actual original work-PYZ canonical path; never rewritten.

Use existing restricted parsers. Embedded names define required sources; declared
only names are metadata and never opened. Namespace iff None/'-', package iff
__init__.py. Plan every source before opening any source leaf; require admitted
canonical membership, safe ancestry/handles, bounded sizes, unique path spelling
and no distinct-path hardlink aliases. Read each source, compare actual bytes,
then fully reread all inputs and recheck ancestors before exclusive outputs.

Additional strict implementation bound:8300 total managed paths (narrower than
proposal16500), at most8192 unique source leaves. Source pool manifest2MiB,
sourceleaf2MiB, EXE/work64MiB, each output2MiB, receipt64KiB. Aggregate actual
managed read+write256MiB includes bootstrap, rereads and outputreadbacks. Reserve
full final rehash plus maximum two outputs/readbacks before source opens. These
are not memory/OS/wholemachine bounds. Outputpair may remain partial on failure;
only final stdout receipt with both hashes and successful exit commits observation.

Sidecar status UNCOMMITTED_SOURCE_OBSERVATION, exact trial/variant, actualtool/
metadata/inputbindings, root-relative associations, work/embedded qualification,
declaredonly names, source_pool_admission_verified/runtime_verified/
wheel_bodies_verified/build_execution_verified/source_to_bytecode_attestation/
launch_authorized allfalse. Final receipt status SOURCE_BYTES_OBSERVED, both output
hashes/sizes, inputdigest, actualread/write counts and identical falseflags.

Tests under normal/-O isolated/no-site/no-bytecode Python use opaque data only.
No candidate/module/native/bytecode execution; zero source opens for invalid last
plan entry; unchanged/changed sources, namespace, unknownowners, wrongroots/hash,
TOC expressions, aliases/links/ancestors, hardlinks, budget/reread/partialoutput
and original/modified target controls. Actual Windows implementation and historical
W3/W4 applicability remain separate mandatory evidence, not fixture claims.

SC-08 layout clarification before r3 review: the supported retained EXE path is
trial/<variant>/bundle/Motortester.exe and preparation is trial/preparation.
A source-table output inside trial must be exactly trial/<variant>/source-inputs.json;
an external source-table output is also supported. Sidecar is strictly outside
trial. This enforces the already required closed companion layout, not new source
read authority or a relaxation of an earlier rejection.
