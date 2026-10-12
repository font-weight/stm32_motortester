# Critic-B: fresh WAIT_RELEASE compatibility review, R2

Date: 2026-10-12. Reviewer: independent Critic-B, not an author of any reviewed production implementation, A oracle, or B parity tests. Review instructions and `docs/quality-gate.md` revision 1.0 were read before execution. Coordination is in English by explicit task instruction.

## Disposition

**Fresh bounded software checks PASS. No demonstrated in-scope production defect remains on the exact tuples below.** Native test delivery, completed-END timeout/late-release behavior, the new defensive pre-END parity, and the exercised actual host/runtime paths have sufficient local evidence. The bounded C1–C5 rubric is 10.00/10, as defined below.

**The common protocol acceptance gate remains `BLOCKED_EVIDENCE`, `score: null`.** This report is one current Critic-B conclusion, not a unilateral adoption of the shared draft. The exact new B tuple still needs the corresponding applicable Critic-A conclusion, authorized immutable publication/readback, and the owners' joint gate. The original scope is not reduced to turn that common gate green. No merge or release is authorized.

The old report `ee90f656…` and archive `189a1a92…` were lost with the prior environment. They have not been recovered or relabeled. Every execution counted here is fresh. Earlier comments are attributed sources, not this reviewer's executions.

## Exact subjects and bindings

| Subject | Exact identity | Binding used here |
|---|---|---|
| A proposal PR79 | `66fce97ba86de459e6d597deb32130e030f34594`, tree `c3479382770fe37f6d90ba2d66ef8eb7e3c5bdde`; parent `6851f4880143692a28b29867cdc14b0c2a5fa0bb` | Immutable local Git objects from the published clone; five-path delta and pinned wire/source inspection |
| A portable native delivery PR82 | `7382ae7f3490127d8a61733cbca948acd89047f9`, tree `a3d5722a6a93d463f06b8fa0428fdd35979c442e`; parent `dacc0c37cab54c69dc1852c6b357fe69b018ac94` | Exact 29-file export; all 18 production C/H bytes also compared directly with PR79 |
| Historical reproducer PR87 | `50ed6608df2fa8bae6799e6a96c09b7543cec596`, tree `475f0a4a0a60dd07e845f915d62f465e053a0f41`; parent `89675871402557c7999b477f4add23b4fc687385` | Complete immutable GitHub tree and all 33 package files retrieved; Git blob/SHA256 identities recomputed |
| Historical B negative control | `c44bae42b19ec8208c2ebee4a3e662ce1b7ff240` | PR87's exact five-module snapshot, with declared historical provenance |
| New B parity candidate | local `9a32271fcf40ac3b47fb0c9e81a66ec43ef8033a`, tree `10bdfb8906c22d33b917c869d86e938798f257eb`; sole parent `a60a05c0e0b0ce9db0c907843a5be97be73e64e1` | Six-file source freeze plus exact inherited production export; all 17 exported files checked against the local commit |
| Recovered Normal-only runtime | local `9988f2d093d3f44485c3df02972dac8a5cd4ed58`, tree `f07f815ddc4a6be43d174850c21d227435dd4844`; parent `45ef38792b8e824e2109e7bacc1c9dc605917d06` | Separate 14-module export from Git, matching its frozen worktree |
| Explicitly authorized all-mode pure-runtime extension | local `a25285dc98ca0d49d3c8eb1ba0e5c4f847cc0556`, tree `0e531eb2b57f4be31f656c2062aa82c1818968b6`; parent `da85d0aed68828214057797a5bd78f7b9f49ed35` | Isolated immutable export; seven actually imported files on native runs, eight with the emulator; no GUI imports |

Final local binding, independently read from Git after the runs: `16c40a8434c33b4e02bb71bf21294d58a90c5a18`, tree `88afe1d41f3c24ef032e1e17a6fd12655d6f5bfa`, sole parent `da85d0aed68828214057797a5bd78f7b9f49ed35`. All **eight actually consumed distinct B files** from the a252 runtime/emulator runs are byte-identical at this final object. `metadata/final-union-binding.json` records each full SHA256/Git blob. Execution remains labeled a252; this is an explicit unchanged-dependency binding, not a claimed fresh run on the final union. Native production/test fixtures remain bound to PR79/82, not substituted with union firmware.

The actual B import closure is `__init__.py`, `model.py`, `recording.py`, `runtime.py`, `session.py`, `window.py`, `wire.py`, and (emulator runs only) `device_emulator.py`. An exported ninth file, `acquisition.py`, is not falsely counted as imported. Review adapters and standard-library dependencies are separate. No GUI/Qt file or result is admitted through this binding.

The parity parent is the recovered Stop checkpoint (tree `7e0dda7ac222c8a5902cbb5ec9a216f6ed846b7d`, parent c44). This review does not repeat or replace the separate full Stop-barrier audit. The only production parity change is seven added lines in `device_emulator.py`; its SHA256 is `f733ab0fad14a443402a26f437a399fe69499ef1a68a8fa793a64d8d630daef5`. Six-file manifest SHA256: `98e7a6732a15ea4c75028bb2437b1dc22b5cf5ab9dfad1eb9a6d53573d735f94`.

The a252 runtime SHA256 is `de8553dba4db77e05e407758a01a935ba5f8aae06309b7a0cbb4a2bfaf997742`. Its seven shared model/recording/session/window/wire/emulator/package files equal the previously frozen candidate bytes. The newer runtime result is separate from the Normal-only result. No score applies to the whole a252 union or its GUI. Later working-tree changes are outside this immutable export.

B's `SOURCE_SHA=c54bd955aaf3cb863ef3df043ea237ea62e74ad4`, additive Stop pin `2017576dbe104f4c43d8d9feac28436589191fd1`, session pin `b6ae8025baf1a47e8173997029d2d8b858ada74c`, window pin `124b3f32e1e7f7282bce283baa4c9406d4d72950`, and PROVISIONAL hold label were not silently migrated. Local Git identities are not claims of remote publication.

## Criteria fixed for this review

Level: native software, synthetic samples and fake hardware callbacks. Applicable bounded MVP acquisition/control/window-retention requirements only; no whole-requirement or MVP release closure.

Required behavior: elapsed >=5000 ms from the final accepted encoded END byte; ERROR/reason3 with retained session/window/tombstone and no continuation/reuse; ordered observational STATUS before ordinary replies; late matching/repeated tombstone ACK without rearm; CONFIG/ARM BUSY; valid Stop/fresh HELLO supersession with partial-frame integrity; stale/malformed Stop cannot bypass expiry ordering. Acquisition read readiness after ERROR is not promised.

The authorized parity change additionally targets a deliberately invalid raw peer: matching current committed-but-partial END receives existing BUSY, after ID/CRC and existing admission checks. Supported host receipt gating, no duplicate terminal, all modes/edges, fragmented late RELEASE, physical storage checks, native package reproduction, failure controls, and actual runtime recovery were mandatory. The later explicit extension added the exact a252 all-mode runtime, genuine Auto timeout, Single rearm, file save/reopen before exact consumer acknowledgment, and pending-ACK Stop.

## Fresh execution results

All paths below are relative to the extracted evidence root `critic-b-wait-release-r2/`.

| Personally executed check | Result | Primary evidence |
|---|---|---|
| Unchanged PR82 portable runner from a 29-file spaced-path export and unrelated cwd | Strict **25 WAIT +103 Stop +3 mode/storage**, and the same under ASan/UBSan; 65 recorded subprocess commands | `results/native82 clean spaced/RESULTS.json`, `results/native82.log` |
| Independent decoding plus actual frozen B decoder | 78 snapshots, six intentional partial tails; actual B chunks 1/7/64/4096, 312 combinations; sample bytes and CRC independently checked | `results/independent-decode.json`, `probes/independent_wire.py` |
| PR82 fail-closed controls | 13 controls plus separate mutated-oracle control, all reject as expected | `results/refusals/RESULTS.json`, `results/refusal-oracle/RESULTS.json` |
| Unchanged PR87 qualified entrypoint | Historical A BUSY versus c44 ACK reproduced, fresh native compilation | `results/historical87/RESULTS.json` |
| Independent real-RX native partial-END matrix | **336/336 strict and 336/336 sanitized** | `results/native-prefix-{strict,sanitized}/RESULTS.json` |
| Independent real-RX native deadlines/order/supersession | **78/78 strict and 78/78 sanitized** | `results/native-deadlines-{v2,sanitized}/RESULTS.json` |
| Current B raw byte-peer matrix | **531/531 normal and 531/531 optimized**; all 1062 host/device stream files equal across the two runs | `results/emulator-v1/RESULTS.json`, `results/emulator-optimized-v2/RESULTS.json`, `metadata/final-input-binding.json` |
| Historical c44 against the new parity oracle | Six deliberate failures; separate six successful historical milestone traces explain ACK and rearm | `results/c44-negative-v2/`, `results/release-milestones-c44/` |
| Actual frozen all-mode session/window against native A | **42/42 strict, 42/42 sanitized with optimized Python** | `results/host-session-{v2,sanitized-optimized}/` |
| Same actual session/window against current emulator | **42/42** | `results/host-session-emulator-v4/` |
| Actual separate Normal runtime against native A | **10/10 strict, 10/10 sanitized with optimized Python** | `results/host-runtime-{v2,sanitized-optimized}/` |
| Same Normal runtime against current emulator | **10/10** | `results/host-runtime-emulator-v4/` |
| Valid-release ACK milestone and Stop crossing | Six paired A/B milestone traces; **48/48 raw native strict and sanitized**, plus **16/16 actual Normal-runtime crossing cases** | `results/release-milestones-current/`, `results/valid-release-crossing-{v1,sanitized}/`, `results/runtime-valid-release-crossing-v2/` |
| Authorized a252 actual all-mode runtime | **66/66 native strict, 66/66 native sanitized/optimized, 66/66 emulator normal, 66/66 emulator optimized** | `results/union-runtime-*/RESULTS.json` |
| Final source integrity | 131 exported input records unchanged; all actual union imports match the exact freeze | `metadata/final-source-check.json` |

The 336 native cases contain every partial END prefix 1–45 × three modes × two edges, ID/window/CRC controls at prefixes 1/23/45, and selected Stop-precedence crossings. The B matrix adds zero-END, exact/late deadlines, every one of 41 RELEASE fragmentation cuts across the deadline, ordinary command ordering, older mandatory STATUS, queued/partial timeout STATUS, valid/stale/malformed Stop, and fresh HELLO. No expected failure is admitted into a positive candidate aggregate.

The all-mode runtime cases independently exercise rising/falling signal capture in all modes and actual no-crossing Auto timeout in both edges. Native Auto receives constant below-threshold samples at199/200 ms; B Auto advances its public virtual clock to the same deadline. Auto metadata remains `auto_timeout` with no invented trigger index. Saved files reopen byte-equivalent; a newly loaded but merely equal Capture is refused as the runtime's exact-identity acknowledgment token. The original pending object can be acknowledged. Single does not acquire again until an explicit ARM; then a distinct second capture appears. ERROR keeps the verified capture, recovery pumps perform no I/O, local acknowledgment after recovery sends no RELEASE, and restart without explicit reset confirmation is refused.

## Three distinct compatibility conclusions

### 1. Completed-END hold and conservative host recovery

Both current A and B anchor the hold to final END transport acceptance. Delaying final END from100 to1099 shifts expiry to6099, rather than5100. They retain the exact raw sample array and logical identity through expiry. At4999 elapsed a valid ordinary release follows the normal mode continuation; at5000 and5001 elapsed, STATUS(ERROR,3) precedes matching RELEASE_ACK, the held logical window is freed, and ERROR persists. A's acquisition read API returns NOT_READY after expiry; this is the documented non-promise, not lost physical samples.

Stale Stop gives ERROR(STOP,4), malformed Stop gives ERROR(STOP,6), after timeout STATUS. Valid Stop may cancel queued observational STATUS and reach STOPPED; a started STATUS finishes first. New HELLO may replace unstarted old events while finishing already accepted frame prefixes. An old mandatory GET_STATUS response is retained ahead of the new event. No duplicate END or ABORT for an already committed completed window is introduced.

The actual host controller treats STATUS(ERROR,3) as `RECOVERY/DEVICE_ERROR`, with `reset_transport=True` and fragment-preservation intent. A following late ACK does not restore acquisition. The runtime retains its verified capture and stops transport I/O; it does not automatically retry RELEASE, ARM, or reconnect. This safe conservative policy is compatible with the device's optional serviceable late-RELEASE path. It need not expose that optional path to callers.

For partial ERROR followed by Stop, the device can finish STOP_ACK after host recovery. The probes explicitly mark their independent device drain: it is **not** evidence that the recovering host consumed that ACK. Host recovery and device STOPPED are compatible outcomes.

### 2. Deliberately premature RELEASE, now defensively aligned

Pinned [wire text](https://github.com/font-weight/stm32_motortester/blob/66fce97ba86de459e6d597deb32130e030f34594/protocol/wire-v0.1.md#L121-L155) requires validated complete-window receipt before the PC sends RELEASE. It distinguishes first-byte END commitment from final-byte hold anchoring. It does **not explicitly prescribe BUSY for a peer that violates that ordering**. BUSY is A's executable defensive behavior, preserved by the separately authorized B parity choice, not a new normative rule invented by this reviewer.

A's exact guard is [mt_capture_link.c:232–237](https://github.com/font-weight/stm32_motortester/blob/66fce97ba86de459e6d597deb32130e030f34594/firmware/app/mt_capture_link.c#L232-L237), after tombstone/ID/CRC validation. Its controller maps that result to existing error2. In nonce11/generation3/window1, matching early request4 receives ERROR payload `0702000003000000` (rejected RELEASE7, BUSY2, reserved0, generation3). Wrong ID/CRC remains BAD_WINDOW_RELEASE7; zero accepted END bytes without a tombstone remains code7. Existing Stop admission precedes the parity guard.

Historical PR87 replay: one of46 END bytes accepted at100, request at1099, remaining45 bytes then accepted. A waveform CRC is `01fac56e`; historical B's different synthetic waveform CRC is `b2cd8a0c`. Each request uses its own actual DATA-derived identity/CRC. A emits BUSY, starts hold at1099, and expires at6099. Historical c44 admits ACK and suppresses the hold timer. New B instead emits BUSY for every partial prefix tested, preserves hold/data, then starts the timer at final END.

The milestone traces are important: historical B frees its logical hold when the premature request is admitted, but cannot trigger or overwrite the samples while END remains partial or ACK remains partial. It becomes WAITING only after the full END and the final ACK byte; Single stops. **Premature physical sample reuse before those boundaries was not demonstrated.** The ordinary host has no completed result or validated receipt before full END; attempts to release an unvalidated receipt are refused without outbound traffic.

### 3. Valid post-END RELEASE: timing variance, not exact simulator timing

A and B intentionally do not provide an identical acquisition-scheduling reference. After a fully accepted END and a valid matching pre-deadline RELEASE:

- A admits ACK, calls `mt_acq_release`, and starts Auto/Normal acquisition immediately: [capture_link.c:238–253](https://github.com/font-weight/stm32_motortester/blob/66fce97ba86de459e6d597deb32130e030f34594/firmware/app/mt_capture_link.c#L238-L253), [control.c:237–241](https://github.com/font-weight/stm32_motortester/blob/66fce97ba86de459e6d597deb32130e030f34594/firmware/app/mt_control.c#L237-L241).
- B defers Auto/Normal rearm until the final accepted ACK byte. Single remains stopped in both.

This distinction was not used to change either implementation or the criteria.

The pinned buffer-ownership promise ends at a valid RELEASE; it does not explicitly impose a final-byte RELEASE_ACK acquisition barrier like the separately explicit Stop barrier. A new sample changing A's buffer after valid RELEASE therefore does not establish violation of the previous-window hold guarantee. B's later start is a **conservative simulator timing limitation**, not a firmware defect or timing calibration reference.

I nevertheless tested the observable safety boundary at ACK queued, zero accepted, one accepted, and37/38 accepted bytes. ACK precedes the next BEGIN/END, or the next-window early ABORT and STOP_ACK. Already completed window1 never receives ABORT. The actual runtime retains the caller's saved first capture and processes either a distinct second capture or an early ABORT without fabricating a zero/incomplete measurement. An early ABORT with no BEGIN/data yields the real cancellation event, not invented fragments. No unsupported ordinary-host-path failure was reproduced.

## Test delivery and failure evidence

PR82's exact package manifest SHA256 is `617fb2a77e81943d73f6953eaec1820bcce64d6e3d563cb9c9cf484004394c48`; entrypoint SHA256 is `596d3c9e109008683716d6fcf07b8e999728971f6dc78f6f82ffc6d4ef570921`. The minimal export has18 production C/H plus11 text package files, no `.git`, ARM/vendor tree or hidden old helper dependency. The new real-RX adapter additionally compiles all nine declared C units and feeds actual `mt_control_rx_feed` bytes; it never mutates production state fields. Fake callbacks and sample input remain explicitly synthetic.

PR82 controls reject -O, -OO, existing output (prior bytes unchanged), mutated production C, mutated C oracle, missing decoder, mutated manifest, output inside source, nonexistent parent, an actual invalid compiler option, an actual child exit7, injected TimeoutExpired with partial stdout, missing snapshot, and identically CRC-corrupted paired output. The latter passes pair equality but fails real independent CRC validation. Failed receipts and output remain saved. No helper was edited to accept bad package output.

PR87's trusted manifest SHA256 is `b092f7a1e6f0b71dcf4c966af72cab4b2d05e5c5858905f70d8ebd7e18f9b0da`. Only its qualified historical entrypoint was invoked; the larger historical/nonportable `run_audit` main was not. The manifest-`{}` bypass and caller obligations are explicitly disclosed by that delivery; no hostile-filesystem/tamper-resistant claim is made. The historical expectation is not a pass criterion for new B. Its new [Critic-A delivery review](https://github.com/font-weight/stm32_motortester/pull/87#issuecomment-6117366386) was read and remains attributed, not counted as my execution.

Preserved reviewer-harness failures:

- `native-deadlines-v1`:12 failures arose because the first expiry poll only queued STATUS, not yet accepting its first byte, and because the probe wrongly demanded physical retention across a new HELLO. V2 establishes an actual one-byte STATUS prerequisite and expects explicit new-session acquisition reset.
- `host-session-v1`:36 final observer failures assumed each nonce's outbound stream started at sequence1; HELLO uses session0/sequence1 and subsequent session11 requests start at2. The device-stream strict continuity check was not relaxed. Corrected host-stream parsing retains continuity after its explicit first request.
- `host-session-emulator-v3`:42 helper failures from a local expected-samples variable shadow and an adapter reading across several frames while the native adapter returned one frame. V4 preserves truthful accepted bytes but limits each adapter read to one frame; no emulator/private field was changed.
- `runtime-valid-release-crossing-v1`:8 failures incorrectly demanded an incomplete-data object for a canceled window before BEGIN. The actual event is WINDOW_ABORT; with no fragments, no fabricated measurement is required. V2 requires the real window2/reason1 cancellation and clean Stop.
- `final-bindings-v1`:wrong manifest key `production_h`; the failed provenance pass was preserved and repeated from its saved script, then corrected to the actual `production_files` map.

Successful runs are only the explicitly named corrected versions. All first-run source copies, errors, native command transcripts and raw byte streams remain in the archive. These failures are not hidden or recounted as production regressions.

## Environment and reproduction

Installed native GCC14.2.0 was used; driver path/hash are in the PR82 receipt. Main reviewer Python is3.12.14; the literal PR87 clean-environment command uses `/usr/bin/python3`3.13.5. No compiler or package installation occurred. Native flags are C11, strict warnings including Werror, and the recorded optimization/sanitizer flags. ASan/UBSan runs use `ASAN_OPTIONS=detect_leaks=0`, `UBSAN_OPTIONS=halt_on_error=1`; LeakSanitizer is not claimed. System compiler closure, libc and sanitizer/interpreter runtimes remain trusted, not hermetically pinned.

Exact top-level invocations, script-version hashes, exit codes and output locations are in `metadata/EXECUTIONS.json`; native bridge build argv are in `results/bridge-build-v2/RESULTS.json`. Each native/peer case preserves host/device bytes and per-call time/state transcripts. `metadata/final-source-check.json` contains actual imported-source identities and the final131-record preservation check.

Canonical package replay commands used fresh absolute external output directories:

```sh
cd /tmp
python3 -I -B '/tmp/critic-b-wait-release-r2/source82 spaced export/tests/wait-release/reproduce.py' \
  --out '/tmp/critic-b-wait-release-r2/results/native82 clean spaced'
env -i PATH=/usr/bin:/bin HOME=/tmp LANG=C LC_ALL=C TZ=UTC /usr/bin/python3 -I -B \
  /tmp/critic-b-wait-release-r2/source87/tests/native-host-reproducer/reproduce_early_release.py \
  --out /tmp/critic-b-wait-release-r2/results/historical87
```

These output directories now contain frozen evidence and must not be reused. For a new replay select new external outputs. Reviewer probes accept `--input`, `--binary`, `--out`; the archive README gives a clean build/replay recipe. Historical absolute paths in receipts describe real executions, not required hidden inputs.

## Fixed rubric and remaining gates

This rubric applies separately to the **bounded reproduced native-delivery/source behavior** and the **exact local B compatibility/parity/runtime paths exercised here**. It is not a substitute score for the common joint contract or a whole product.

| Criterion | Weight | Score | Evidence basis within this scope |
|---|---:|---:|---|
| C1 Correctness/requirements |30%|10|Exact final-END anchoring, retention, late/tombstone behavior, Stop/HELLO ordering, defensive parity and actual host/runtime transitions demonstrated. Valid-release scheduling variance is classified against the pinned ownership text, not mistaken for identical timing.|
| C2 Reliability/failure handling |20%|10|Strict/sanitized boundaries, fragmented RX, stale/malformed Stop, partial/queued output, ID/CRC controls, genuine negative baseline, fail-closed delivery controls and conservative recovery/no automatic retry.|
| C3 Evidence/verification |25%|10|Fresh independent COBS/CRC/raw-sample oracle, actual production host modules/native RX, explicit failed-harness preservation, real file round trip, exact input/import checks; author tests and historical claims not substituted.|
| C4 Clarity/maintainability |15%|10|Seven-line production parity guard preserves prior admission order. Native delivery has a qualified entrypoint and explicit historical-helper limits. Distinct simulator/API/physical and timing/ownership boundaries are documented.|
| C5 Reproducibility/documentation |10%|10|Exact immutable source manifests, clean minimal native export, spaced-path replay, trusted compiler identification, fresh commands/raw transcripts, complete allowlisted compressed evidence and honest publication/platform limits.|

Unrounded local bounded result: `0.30*10 +0.20*10 +0.25*10 +0.15*10 +0.10*10 = 10.00`, **PASS**. There is no demonstrated open P0/P1 within these bounded paths. Scores are not probabilities or physical acceptance.

**Common joint gate: BLOCKED_EVIDENCE / null**, pending the current counterpart conclusion and authorized exact publication binding. I read A's exact [PR79 review](https://github.com/font-weight/stm32_motortester/pull/79#issuecomment-6106388335) and [binding](https://github.com/font-weight/stm32_motortester/pull/79#issuecomment-6106404712), [PR82 delivery review](https://github.com/font-weight/stm32_motortester/pull/82#issuecomment-6106890690) and [binding](https://github.com/font-weight/stm32_motortester/pull/82#issuecomment-6106933392), and [historical narrow Critic-A confirmation](https://github.com/font-weight/stm32_motortester/pull/14#issuecomment-6107029063). Their scope/authorship and null shared-gate qualification remain intact. A's original unchanged Stop-oracle authorship is not recast as a fresh independent design review.

No physical USB delivery, ADC behavior, calibrated sampling frequency, Windows/serial backend, Qt/GUI admission, ARM build, complete firmware stack/image, LV capture, ignition or full-MVP acceptance was tested here. Root's separately evolving GUI work is outside this report. No production change, source adoption, external write, install, Git mutation or additional worker was performed by this reviewer.

## Artifact location

`host/reviews/evidence/wait-release-r2/` contains the compact final metadata, explicit archive file allowlist and `fresh-review-evidence.tar.xz`. The archive includes the report, exact source exports, all fresh successful/failed probes and raw traces; compiled executables are deliberately excluded and their identities are recorded. `FREEZE.json` binds the report/archive/index hashes without a circular hash claim. A compressed recovery copy is also persisted under the review's recovery-store keys, with the key inventory supplied to the coordinator.
