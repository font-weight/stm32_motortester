# CTRL-001 draft reproduction and evidence index

This draft is stacked on PR #30, commit `a451e5a491439fcfed31f298a589643cf7050ec9`. All existing base blobs are preserved. Only the controller, its caller documentation, tests and selected textual evidence are added. The controller remains a software draft; publication is not acceptance.

## Reproduce from the checkout

Requirements: Bash, a C11 compiler supporting AddressSanitizer/UndefinedBehaviorSanitizer, Python 3, GNU coreutils. The recorded native compiler is GCC 14.2.0.

Set SOURCE to the root of this checkout. Keep OUT outside the checkout and distinct from the evidence directory:

```sh
SOURCE="$(pwd)"
OUT="$(mktemp -d /tmp/motortester-ctrl001.XXXXXX)"
OUT="$OUT/author" bash "$SOURCE/tests/control/run.sh"
bash "$SOURCE/tests/control/independent/run.sh" "$SOURCE" "$OUT/independent"
```

The independent runner is copied unchanged. Its first positional argument is the source root; the command above explicitly supplies it. Do not rely on its historical default `snapshot-final`: duplicate production-source snapshots are deliberately not committed here. The runner creates binaries and a binary wire transcript only in OUT. Expected final results are 39/39 scenarios, 576,994 runtime checks, native and ASan/UBSan exits 0, and a separate Python oracle reporting 344 frames, 20 full windows and 40,960 exact sample values.

ASan/UBSan runs disable leak detection. No LeakSanitizer pass is claimed. The earlier environment failure is preserved in `independent/run-corrected/sanitizer.log`.

## Critic-A targeted regression reproducers

These two sources are Critic-A's unchanged reproducers. They reuse the independently authored supporting harness through an include path; another harness copy is not needed.

```sh
SOURCE="$(pwd)"
OUT="$(mktemp -d /tmp/motortester-ctrl001-critic.XXXXXX)"
INDEPENDENT="$SOURCE/tests/control/independent"
CRITIC="$SOURCE/tests/control/critic-a"
for TEST in tombstone_hardware_repro stale_stop_capacity_repro; do
  cc -std=c11 -O1 -g -Wall -Wextra -Werror -Wpedantic \
    -I"$SOURCE" -I"$INDEPENDENT" \
    "$CRITIC/$TEST.c" \
    "$SOURCE/firmware/app/mt_control.c" \
    "$SOURCE/firmware/app/mt_capture_link.c" \
    "$SOURCE/firmware/acquisition/acquisition.c" \
    "$SOURCE/firmware/protocol/mt_wire.c" \
    "$SOURCE/firmware/protocol/mt_command.c" \
    "$SOURCE/firmware/protocol/mt_session.c" \
    "$SOURCE/firmware/transport/mt_tx.c" \
    -o "$OUT/$TEST"
  (cd "$OUT" && "./$TEST")
done
```

Expected: the old-tombstone case leaves the start callback count at 2 and hardware stopped; stale Stop leaves the stop count at 1, ready true and hardware running.

## Preserved evidence

All imported reports, sources and logs are unmodified. Historical absolute paths and snapshot names in those reports record the original run provenance; they are not required checkout paths.

- `evidence/native.log`: author's strict and sanitizer results.
- `independent/README.md`: independent supporting report with the original defects, correction regression, final results and limits.
- `independent/run-final/`: selected final text logs, compiler version, exact compiled-source hashes, exit statuses and Python oracle JSON.
- `independent/run-baseline-preserved/`: selected baseline evidence; 34/39 scenarios passed. Four failures represented two production defects; the fifth was the later chosen reply-capacity policy test.
- `independent/run-corrected-stale-stop/`: selected intermediate correction evidence; 38/39 scenarios passed and the stale-Stop backlog regression remained.
- `independent/source-sha256.txt`, `source-corrected-sha256.txt`, `source-final-sha256.txt`: original source identity records.
- `critic-a/CRITIC_A_CONTROL_REVIEW.md`: independent local verdict, BLOCKED_EVIDENCE / score null, pending immutable publication binding and integration evidence.
- `critic-a/source-manifest.json`: final SHA-256 and Git blob identities, including dependency equality.
- `critic-a/dependency-equality.txt`, `author-final.log`: Critic-A's dependency comparison and personally rerun author evidence.
- `critic-a/*repro.c` and targeted `*.log`: original and final behavior of Critic-A's tombstone and stale-Stop reproducers.
- `critic-a/arm-final/`: selected textual ARM object-size/ABI/stack-use evidence and ABI probe source. No ARM binary or complete firmware is included.

The supporting report's historical contract reference is [wire v0.1 at c54bd955](https://github.com/font-weight/stm32_motortester/blob/c54bd955aaf3cb863ef3df043ea237ea62e74ad4/protocol/wire-v0.1.md). Original snapshots, duplicated harness copies, binary COBS transcripts and executable/object files are intentionally excluded from this publication; full final source is the checkout itself.

## Limits

The known static Stop call chain is 2,944 bytes before library functions, caller, IRQ, runtime and platform callbacks. The prior 2 KiB stack assumption is insufficient. ARM object size and native sanitizer passes are not a final linker-map fit or MCU stack proof.

Actual stream/timeout/disconnect adapter, USB, canonical-profile MMIO callback integration, DMA timing/WCET, full firmware fit, Windows interoperability and physical low-voltage windows remain open. A synthetic 20-window transcript is not a physical measurement. No acceptance, merge, software-ready or hardware-ready status is claimed.
