# USB-003 supplemental test relocation

This addendum preserves the local review report unchanged (SHA-256
8d30e446ac77b675bd24e2725ab7d70743030e796103567da3fc9e9035ab6225).
It adds a reproducible staging wrapper for that report's independent tooling tests.

Place run_supplemental.py beside the unchanged test_patch_tool.py and
test_output_separation.py. Run:

```sh
python3 run_supplemental.py --checkout /absolute/path/to/checkout --out /new/external/output
```

The checkout must contain the reviewed USB-001 and USB-003 paths. The wrapper
verifies both original supplemental hashes and five exact checkout input hashes
before staging a separate output tree. The apply-tool test is copied unchanged.
An isolated copy of the separation test substitutes exactly four historical
absolute path literals: dependencies, supporting harness, firmware root and an
unused toolchain argument. All assertions and test logic remain unchanged. The
original test sources and production inputs are rechecked after execution.
No ARM compiler is invoked by these two supplemental tests.

I personally ran this wrapper against the relocated package assembly. Result:
11 patch-tool invocations and 12 equal/nested/symlink-alias isolation cases passed.
The fresh receipt, commands and both stdout logs are preserved separately from
historical outputs; no old execution log was overwritten or reconstructed.

Wrapper SHA-256:
64f5ae9efd4f7585317fd501ece5ff7e76e272154dd957861d42128b3bb9fbac.
Original apply test:
55f379b38bf3490c98eebbe1dd27c3afee95e96ce5ca6c6a51f190af19aa3704.
Original separation test:
cfe46a9aa7797cbc0d6bcabcefbe777bd28f8f3eb2487959796f88a838947f08.

This closes supplemental-tool portability for the stated checkout layout.
Final publication/base binding remains pending; the earlier BLOCKED_EVIDENCE/null
verdict is unchanged. This is no new firmware, concurrency or hardware evidence.
