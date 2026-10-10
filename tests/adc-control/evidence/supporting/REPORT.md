# IO-001 independent supporting tests

This is supporting test evidence, not the Critic-A verdict or acceptance score. The tester did not author or modify implementation. No host files, GitHub writes, Work tasks, new chats, or paid resources were used. All test work is confined to this directory. Level: native program/register-model tests, not hardware measurement.

## Inputs and exact identity

Provided provenance:
- Controller PR35: `0ddea1e5992caa6231203838c3956e19675867a8`.
- ADC PR27: `5215177100453c6f77e5c5a637197fd626e57aec`.
- Work source: `/workspace/shared/adc-control-draft`; this copied draft has no local commit identity of its own.

Final adapter C SHA256: `f316523483171195d8f60bee38a3f716f3d887e506074f1038aafc14a99e64ce`.

Final adapter header SHA256: `85110d0698c322919ef2b8006a81e166b00cd11eea3a9b4cd972f550d244fbc7`.

ADC driver C SHA256: `91906d403ffc787d1b7e6541302226347bf299f58b2949be47742d9768240d43`.

`snapshot.sha256` records every copied input. `deliverable.sha256` records the test, runner, output, manifest, and key source hashes. Final `diff -ru snapshot/firmware /workspace/shared/adc-control-draft/firmware` produced no differences; see `final-source-diff.log`.

Read project AGENTS and quality gate, and relevant task, requirements, decisions, progress, MVP specification, coordination and module contracts. This report does not supersede the independent critic or the 9.5 quality gate.

## Commands and environment

Run from this directory:

```sh
./run.sh > run.log 2>&1
```

`run.sh` verifies source hashes, builds with `cc -std=c11 -Wall -Wextra -Werror -Wconversion -pedantic -O2`, executes the tests, then rebuilds with `-g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer` and executes with `ASAN_OPTIONS=detect_leaks=0`. The exact source and include lists and full command traces are in `run.sh` and `run.log`.

Compiler: Debian GCC 14.2.0-19. Executor: x86_64 GNU/Linux, kernel 6.18.44. Both final runs exit 0. LeakSanitizer was deliberately disabled; no leak-testing claim is made.

The original register-bus fixture from ADC PR27 is reused. The integration stimulus, independently computed uint64 timestamp oracle, capture content checks, command sequences and failure injections were written independently of the author's IO-001 tests. The original platform regression entry point is also executed. DMA data is synthetic; this is not an ADC electronics simulator.

## Verified results

Both strict and sanitizer builds report `ALL INDEPENDENT TESTS PASSED (313 exact complete windows)`.

- 180 complete windows: 20 for every Auto/Normal/Single and 0/512/1024-pretrigger combination. Auto alternates analog crossing and timeout-only capture. Validate all 2048 original codes in chronological order, complete COBS output, DATA offsets, metadata, one BEGIN and END, and CRC32.
- Normal/Auto RELEASE starts exactly one fresh hardware acquisition and resets DMA sample index/half selection. Duplicate RELEASE under a fresh request does not restart. Single RELEASE does not restart; new ARM does.
- All 128 possible last-sample positions in a DMA half: capture ends at the expected sample, hardware stops, retained window stays exact, and subsequent service returns ignored without MMIO writes. The unused driver snapshot is poisoned after stop to check that retained output does not depend on it.
- Timestamp split arithmetic agrees with an independent uint64 multiply/divide oracle at eight index boundaries, including values that overflow a 32-bit `index * 20` intermediate and the final permitted sequence region. High index boundaries are deliberate test-model injections, not an hours-long physical run.
- Starts themselves crossing microsecond wrap; windows crossing millisecond wrap; and additional fully consistent 64-bit source epochs around microsecond wrap at 71 minutes and millisecond/microsecond wrap near 49.7 days. The fixture's milliseconds are not obtained by dividing the already-wrapped microsecond value.
- Exact clock-correlation bracket widths 0 and 20 reported microsecond ticks accepted; 21 and 5000 rejected before hardware start or any MMIO writes. Test repeated across unsigned microsecond wrap. Nominal timestamp quantization remains explicit in the implementation documentation.
- Noncanonical 48 MHz plan rejected without MMIO writes on ARM and on RELEASE. An injected clock change between preflight and start is detected and stopped; runtime RCC mutation and DMA transfer errors enter recovery.
- Failed Stop on a command and at a partial final block retains driver ownership and required lifetime, suppresses output/success, and leaves service ignored. Repeated failed Stop followed by HELLO still preserves ownership; successful later HELLO retry releases it. No full frozen window is published after Stop failure.
- Null arguments and uninitialized driver rejected without controller mutation or MMIO. Rebinding a live driver is separately observed below.

## Findings and disposition

### Fixed: unbounded clock-reference gap

The pre-fix adapter SHA256 was `24bc1435f46845fd5da2d15a4478ccb24a3d69bfb13ec6f6bb85b0b1820e6013`. It read milliseconds first and then established its microsecond delay reference. A synthetic 5000 us gap after the epoch reading was silently omitted from sample timestamps: first sample actual_ms=6 versus assigned_ms=1, skew=5 ms. This affects acquisition timing/Auto timeout semantics; it is distinct from ADC aperture accuracy.

The author changed the implementation to bracket the millisecond epoch between microsecond readings and reject widths greater than 20 reported ticks before driver start. The independent boundary/wrap/no-write tests above pass on that revision.

Complete pre-fix source, test, runner, manifest and actual outputs are preserved under `evidence-v1/`. To reproduce the old observation, run `./run.sh` from that directory. Its output labels the skew as OBSERVATION and does not falsely report an assertion failure. The final tests require fail-closed rejection instead.

### Documented precondition: live reinitialization

Calling `mt_adc_control_init` on a running/owned driver returns OK and clears the controller's software running flag while the driver remains running/owned. This is a concrete observed consequence, but it violates the explicit quiescent-before-rebinding/initialize-once precondition, so it is not counted as an in-contract implementation defect. Integrators must obey the lifetime rule; init is not a recovery mechanism.

## Limits and next step

No remaining in-contract defect was demonstrated in this supporting set. This is not a proof of absence of defects and is not a release approval. No hardware, physical timing/aperture accuracy, ADC analog behavior, USB, Windows, final ARM link/map, WCET, ISR interaction, stack watermark or full-firmware execution was tested. Startup time sources and scheduling must still implement the documented contracts. Critic-A must bind its independent verdict to the final published candidate identity.

An initial independent test harness compilation failed on a size_t-to-unsigned assignment under `-Wconversion`. Only the independent test was corrected. The original diagnostic is retained in `run-initial-compile-error.log` and `evidence-v1/`; it was not an implementation defect.
