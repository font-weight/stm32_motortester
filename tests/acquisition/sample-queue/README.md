# Independent bounded sample queue tests

This native test suite verifies the standalone queue in
`firmware/acquisition/mt_sample_queue.c` against a structurally independent,
linear FIFO. The reference stores whole blocks in arrival order and shifts them
on pop; it contains no ring-head/tail/modulo implementation.

The queue implementation was written by Dot A. This test suite was written by
a separate worker and does not change the queue implementation. This test result
is evidence for the separate Critic-A review, not a substitute for that review.

## Reproduce

From the repository root, after placing these files in
`tests/acquisition/sample-queue/`:

```sh
sh tests/acquisition/sample-queue/run.sh .
```

The first argument names the source repository. An optional second argument
names a **new**, nonexistent output directory. The default is a new temporary
directory. The runner compiles the actual current queue source directly, without
copying a production implementation into the test suite. It retains provenance,
compiler logs, test logs, and binaries outside the source checkout.

Requirements: POSIX shell, `sha256sum`, GCC-compatible C11 compiler with
AddressSanitizer and UndefinedBehaviorSanitizer. `CC` selects the compiler.
Git metadata is optional: source-only exports record an explicit marker and
still hash the actual source and test files. Both variants use warnings-as-errors,
including conversion, sign-conversion,
shadow, and pedantic warnings. Sanitizers are fail-fast.

LeakSanitizer is explicitly disabled with `detect_leaks=0`, because it is
unsupported under ptrace in the execution environment. **No leak check is
claimed.**

## Coverage

The suite uses literal capacity expectations of 16 blocks × 128 codes rather
than deriving its reference capacity from the production macros. After every
operation it checks model occupancy, accepted end, head index/data, closed state,
and first fault. It also verifies that peek is observational and preserves its
output on failure.

Nine scenario groups cover:

1. Empty/open, empty/sealed, repeated seal, null queue/input/output, and documented
   null init/seal no-ops.
2. Sixteen accepted blocks, rejected seventeenth, repeated ring reuse with
   interleaved push/pop, stale and wrong-head rejection, and complete FIFO drain.
3. Every final length 1–128; a full-size final in the sixteenth slot; rejected final
   on a full queue; source-buffer mutation after copying.
4. Every invalid short nonfinal count 0–127, every oversized uint16_t count
   129–65535, zero final count, and final flags 2, 3, and UINT_MAX.
5. Invalid codes 4096 and 65535 at **all 128 positions**, for every occupancy
   0–16, including the last input code. Valid data patterns include 0 and 4095.
6. Duplicate/backward/gapped first indices, plus literal exclusive-end boundaries:
   - start 18446744073709551487, count 128: accepted; end UINT64_MAX
   - start 18446744073709551488, count 128: rejected overflow
   - start 18446744073709551614, count 1 final: accepted; end UINT64_MAX
   - start UINT64_MAX, count 1 final: rejected overflow
   - pop/refill at end UINT64_MAX−1, followed by a one-code final
7. The documented validation precedence: null queue, closed, null samples,
   count/final flag, first index, exclusive-end overflow, codes, capacity.
   Includes CODE before FULL and OVERFLOW before CODE/FULL. These are queue API
   rules, not a claim about wire-protocol validation.
8. 512 deterministic healthy traces of 1024 interleaved operations. These avoid
   deliberate faults to exercise long-lived ring reuse and continuous FIFO order.
9. 2048 deterministic traces of 128 operations mixing successful input, faults,
   final blocks, seal, correct/stale/empty pop, and post-close input.

Each rejected push compares the complete preexisting queue object byte-for-byte,
except for the two permitted fault fields. It therefore checks preservation of
**every queued block**, not just the visible head. Rejected pop and peek compare
the whole queue unchanged. Successful and rejected pushes check the previously
borrowed head pointer/bytes. Seal may alter only the sealed field. Each fault
path is followed by repeated CLOSED pushes, repeated sealing, and complete model
comparison during draining. Caller buffers are mutated after accepted pushes to
check queue ownership; valid source objects are sized to satisfy the API's input
precondition, including the oversized-count rejection cases.

## Recorded independent run

- Source commit: `a4048ab522f88932792aca25fe10a9d0f4651092`
- Base supplied for this iteration: `032028be52dc9f21fc13848f9caa31ecf52482af`
- Platform: x86_64 Linux; GCC 14.2.0
- Strict C11: PASS
- ASan + UBSan: PASS
- 9 groups, 2560 deterministic traces, 1,582,641 operations and 211,087,220
  checks per variant
- Fixed initial PRNG seed: `0x162128c011f00d59`
- Native `sizeof(mt_sample_queue)`: 4168 bytes; raw sample payload: 4096 bytes

Source/test SHA-256:

```text
3ec95dc93d61db0f33fa227deea4606ee272d43632b6cdc19a0b65837830886c  mt_sample_queue.c
4c856b9b312b93bdd0fbf4d616316b2d101cad0a807734c4e7488fd03968edc4  mt_sample_queue.h
4f70d7a466ee454ddca50537754c13102c769bd1a8b3c3a369df33af2f9f4be1  test_sample_queue.c
ceb6e607aaa5fb3208f6e6b443d50f4595c723a0647f2f0eff87f3c2ea4baf74  run.sh
```

The initial run also passed; the second run adds null-init/seal and final/full
boundary coverage. A third run verifies the final runner, and a fourth verifies
the same inputs from a source-only export without Git metadata. Raw logs for all
runs are retained by the authoring worker.
The test counts describe the exercised coverage, not a quality score or proof
that no defects exist. Re-run the suite after any relevant source/test change;
recorded results do not automatically cover a different commit.

## Limits

This is a single-owner native software test. It does not validate undefined
caller behavior (uninitialized objects, direct state corruption, invalid
pointers, overlapping input/output, concurrent or interrupt calls). It does not
certify DMA ownership, hardware quiescence, serialization completion, USB, host
receipt, CRC, durable files, timer/ADC performance, an ARM executable, target
memory/stack budgets, or physical continuity. No hardware was accessed.

Mutation sensitivity is performed separately by Critic-A; it is not claimed by
this test-author run. Broader firmware/device acceptance remains separate.
