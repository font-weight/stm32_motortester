# Independent native endpoint traces

## Result and scope

On 2026-10-19, all 79 test functions passed strict C11 and ASan/UBSan against
root-authored commit `dd40a62e39db556c268baca8b229c7c77b3b7738`.

An initial 60-function subset passed predecessor `c2609a9`, but Critic-A then
found a missing failed-recovery case. Two added regressions reproduced that
defect on `c2609a9`; both now pass with the root author's correction. The
historical passing subset never overrode the later established defect.

This is independent synthetic native testing of the actual `mt_live_device`,
`mt_wire`, and `mt_sample_queue` C implementations. The harness supplies a bounded
clock, start, quiesce, synchronous FIFO-write and isolation adapter. It does not
reimplement the endpoint or a HOST state machine. All frames pass through the
actual revision-aware C encoder and decoder; terminal fields and ordering have
literal independent assertions.

The worker wrote only this test/evidence directory, never the implementation.
This test report is not Critic-A's review, a quality-gate score or acceptance.
No physical ADC, DMA, USB, target timing, real transport, durable host storage,
ARM memory/stack or hardware-off behavior was tested.

## Reproduce

From any directory:

    sh run.sh /path/to/source-root /path/to/new-evidence-directory

The source root must contain the six actual C/header inputs and the protocol
file. The optional output directory must not already exist. If omitted, a fresh
temporary directory is used. `CC` may select a compiler. The script records UTC,
HEAD/status when available, compiler/platform, and SHA-256 values of the actual
source, protocol and tests before building. It runs both variants, records their
exit status in its own exit code, and retains compile/run logs.

Flags: C11, `-Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion
-Wshadow`, with `-fsanitize=address,undefined -fno-sanitize-recover=all
-fno-omit-frame-pointer` for the sanitized variant. LeakSanitizer is disabled
because ptrace makes it unavailable here. No leak-check claim is made.

For a targeted reproduction, the compiled binary accepts a substring filter,
e.g. `test-strict inherited_hello` or `test-strict late_known_acceptance`.

## Tested behavior

- CRC-valid 0.1 and different-major frames fail explicitly as incompatible at
  idle/active phases, without inventing wire replies or trying legacy arming.
  Corruption of the actual encoded version bytes without updating CRC is instead
  discarded as CRC failure. Request/sequence/duplicate history is unchanged.
  Incompatibility remains observable with an earlier first cause and clears only
  after timely successful 0.2 HELLO commitment.
- Exact HELLO/CAPS/profile fields; no acquisition before START; successful START
  ordering; Stop before and during START_ACK; empty, full and final-short streams.
- Terminal prefix, raw little-endian sample CRC, last DATA sequence, single END,
  matching ACK and no DATA after END.
- Sixteen-slot queue exhaustion, known and unknown produced ends, incomplete
  known tails, full-queue snapshot overflow, invalid offered code/index/count,
  and rejection of blocks after shutdown.
- Whole-snapshot validation before any append, including a valid first block and
  an invalid second block. Boolean/count/reason/index/endpoint fault matrix. Snapshot overlap/u64 overflow,
  two valid full snapshot blocks, and a fault-sealed queue supplied with a valid
  post-gap tail (which must never be appended).
- First acquisition cause retained across later faults and failed quiescence;
  failed hardware-off forces unknown/incomplete tail and suppresses STOP_ACK.
- Late Stop before END freezing, after an unstarted frozen END, during END and
  after complete unsolicited END; one Stop only; exact duplicate handling.
- Correct next transport sequence with nonincreasing request ids is rejected;
  STARTING, STREAMING and TERMINAL reject a new START without another ERROR.
- Exact encoded-byte duplicate semantics, including two valid COBS encodings
  with equal decoded fields that must not count as identical duplicates.
  Old-nonce history, reorder/conflicting duplicates, wrong-direction and unknown
  active-phase commands, malformed envelopes and fragmented receive/discard.
- Zero, partial, unknown and oversized write results; sequence commitment at the
  first accepted byte; detach; resynchronization delimiters and ordered recovery.
- Shutdown/HELLO/late-Stop deadline boundaries, barrier time included before
  shutdown, finite trickle progress, whole-transaction admission guard with a
  bound larger than one millisecond, inherited old HELLO/END/START_FAILED budgets,
  and replacement HELLO finishing only started old frames.
- Failed-start cleanup/error at 498/499/500/501 ms, sole reserved error and fresh
  HELLO requirement, no successful START_ACK for failed start.
- Reserved TX/request ranges, maximum Stop request and sequence, no TX wrap,
  refusal to start without reserve, sample-exclusive-end overflow, clock
  regression and deadline-arithmetic ceiling.
- Deliberately violated adapter timing/clock assumptions: first accepted bytes
  are retained as evidence and force resynchronization; a post-completion time
  failure leaves FAULT rather than a locally usable session.

Several grouped functions contain independently initialized boundary/matrix
cases. The report counts test functions, not a fabricated number of assertions.

## Important limitations of the synthetic seam

`transaction_bound_ms` is supplied by the fixture; these tests do not establish
that any real implementation satisfies it. A callback intentionally violating
that assumption can already have emitted bytes before the endpoint detects it.
The negative tests check local fault handling and preserved framing evidence;
they cannot retract a transmitted ACK or prove real deadline safety.

Most traces use only public operations. Counter-boundary tests deliberately seed
publicly visible implementation counters (and one sample queue origin) to reach
u32/u64 limits without billions of operations. The u64 queue-origin trace checks
arithmetic only; it does not claim that a nonzero-origin stream is valid wire
output. Tests that inspect state do not grant applications permission to mutate
implementation-owned fields.

Only the callbacks used here are modeled. Reentrancy, aliasing caller buffers,
non-returning callbacks and concurrency are outside the API contract. Native
success is not evidence that a target adapter obeys that contract.

## Evidence and independently observed regressions

The initial passing subset is retained in `evidence-c2609a9/` and
`frozen-60-c2609a9/`:

- `provenance.txt`: exact source/test hashes and tested commit
- `compile-strict.log`, `run-strict.log`: strict native build and 60 passes
- `compile-asan-ubsan.log`, `run-asan-ubsan.log`: sanitized build and 60 passes

During the first exploratory run, the harness independently observed that a
first partial accepted write followed by deadline expiry or clock regression
failed to require a recovery delimiter. The root author's concurrent fix
`dfcda2c4b8c70bd856f580f32c9db611344f0f76` addresses these cases, and the final
regression tests pass. Early exploratory logs are retained separately; they also
contain two corrected harness expectations and are not exact-version evidence.

The preserved predecessor failing evidence is in
`evidence-c2609a9-two-recovery-regressions/`: 62 functions, two failures in both
strict and sanitized runs. On a failed replacement HELLO final observation,
`completed()` clears the old acquisition cause, first local cause, verified
prefix/CRC, retained queue and nonce before its final time check. Both deadline
equality and regressing time reproduce it. The tests require preservation of
those old-session diagnostics and queue through failed recovery; a successful
recovery twin checks that timely commitment still clears them correctly.
Critic-A supplied the original reproduction; this worker independently extended
and reran it with a nonempty verified prefix and retained queue. The root author fixed the implementation in `6fef3f9`. Both regressions and the
successful-recovery twin pass in the full corrected run.

The 69-case corrected run is preserved in `evidence-6fef3f9/`. Critic-A
sensitivity checks then identified four non-discriminating coverage areas.
Added literal cases isolate future sequence with increasing request, a later
fault that must not renew the drain deadline, overflow snapshot invalidating a
known produced end, and a nonfull fault-sealed queue that must not reopen even
for an otherwise valid contiguous tail. An additional consistency regression
checks that a rejected 127-count offer seals the queue before any contiguous
snapshot tail is considered, preserving the prior prefix as incomplete. These were evidence gaps, not established
implementation failures.

The current authoritative evidence is in `evidence-dd40a62/`: provenance
and compile/run logs show 79 functions passing in both strict and sanitized
builds, including the root author's explicit incompatible-revision diagnostic.
Copy-ready sources are frozen separately under `frozen-79-dd40a62/`. The latter
contains only this README, `test_live_device.c` and `run.sh`; historical logs and
predecessor sources are kept outside the intended published test directory.
The correction still needs the separately owned exact-version Critic-A review.
No independent acceptance score is asserted by this test report.
