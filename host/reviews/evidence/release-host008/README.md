# Reproduce independent HOST-008 review

Reviewed source: 45ef38792b8e824e2109e7bacc1c9dc605917d06, tree e39dfe24663d089e8ff9888101305a0e2af8a626, parent ef2880c0655d155284c176424a12378b3b66adf4.
The frozen checkout used here is `/tmp/motortester-host-session-release`. All 165 files matched the commit. Source caches are excluded from the byte manifest.

These scripts belong to the critic, not the reviewed implementation. Run from the main repository containing this review directory. They write only this evidence directory. To retain this run, copy the critic directory to a new writable evidence destination before rerunning.

```sh
PYTHONDONTWRITEBYTECODE=1 python host/reviews/evidence/release-host008/verify_sources.py . /tmp/motortester-host-session-release
PYTHONDONTWRITEBYTECODE=1 python host/reviews/evidence/release-host008/reproduce_suites.py /tmp/motortester-host-session-release
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/motortester-host-session-release/host python host/reviews/evidence/release-host008/run_suites.py host/reviews/evidence/release-host008 critic_probes.py > host/reviews/evidence/release-host008/critic-normal.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/motortester-host-session-release/host python -O host/reviews/evidence/release-host008/run_suites.py host/reviews/evidence/release-host008 critic_probes.py > host/reviews/evidence/release-host008/critic-optimized.log 2>&1
```

The complete host suites require the recorded GUI dependencies in `/tmp/motortester-deps`. The pure critic probes need only Python's standard library and the reviewed modules. No install or network action is included in these scripts. `verify_sources.py` requires the exact local Git objects for the reviewed commit and its stated source pins.

`run_suites.py` rejects zero tests, skips, expected failures, unexpected successes, failures and errors. `suite-runs.json` records all before/after source hashes and commands. `probe-runs.json` records the extra probes' source digest, command, result and closed-log digest. `source-verification.json` records comparison with Git and all supplied evidence digests. `SHA256SUMS` binds the final review and evidence, excluding itself to avoid recursion.

The seven old emulator hold-timeout regressions remain provisional. No Windows, hardware, transport, shared-contract, new PR #52 Stop-spec, or MVP release acceptance is implied by these tests.
