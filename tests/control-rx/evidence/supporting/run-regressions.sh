#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SOURCE="$ROOT/snapshot"
OUT="$ROOT/regression-run"
mkdir -p "$OUT"
OUT="$OUT/author" bash "$SOURCE/tests/control/run.sh" > "$OUT/author.log" 2>&1
bash "$SOURCE/tests/control/independent/run.sh" "$SOURCE" "$OUT/independent-unchanged" > "$OUT/independent-unchanged.log" 2>&1
COMMON=(-std=c11 -O1 -g -Wall -Wextra -Werror -Wpedantic -I"$SOURCE")
SOURCES=("$SOURCE/firmware/app/mt_control_rx.c" "$SOURCE/firmware/protocol/mt_stream.c" "$SOURCE/firmware/app/mt_control.c" "$SOURCE/firmware/app/mt_capture_link.c" "$SOURCE/firmware/acquisition/acquisition.c" "$SOURCE/firmware/protocol/mt_wire.c" "$SOURCE/firmware/protocol/mt_command.c" "$SOURCE/firmware/protocol/mt_session.c" "$SOURCE/firmware/transport/mt_tx.c")
mkdir -p "$OUT/rx-adapted"
cc "${COMMON[@]}" "$ROOT/old-controller-rx/test_control_independent.c" "${SOURCES[@]}" -o "$OUT/rx-adapted/native"
(cd "$OUT/rx-adapted"; ./native > native.log 2>&1)
cc "${COMMON[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "$ROOT/old-controller-rx/test_control_independent.c" "${SOURCES[@]}" -o "$OUT/rx-adapted/sanitized"
(cd "$OUT/rx-adapted"; ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./sanitized > sanitizer.log 2>&1; python3 "$SOURCE/tests/control/independent/verify_transcript.py" twenty-windows.cobs > independent-wire.json)
for TEST in tombstone_hardware_repro stale_stop_capacity_repro; do
  cc "${COMMON[@]}" -I"$SOURCE/tests/control/independent" "$SOURCE/tests/control/critic-a/$TEST.c" "${SOURCES[@]}" -o "$OUT/$TEST"
  (cd "$OUT"; "./$TEST") > "$OUT/$TEST.log" 2>&1
  cc "${COMMON[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer -I"$SOURCE/tests/control/independent" "$SOURCE/tests/control/critic-a/$TEST.c" "${SOURCES[@]}" -o "$OUT/$TEST-sanitized"
  (cd "$OUT"; ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "./$TEST-sanitized") > "$OUT/$TEST-sanitized.log" 2>&1
done
sha256sum "${SOURCES[@]}" "$ROOT/old-controller-rx/test_control_independent.c" > "$OUT/source-sha256.txt"
printf 'All author, unchanged 39-scenario, RX-adapted 39-scenario, wire oracle and two critic reproducer tests passed.\n' | tee "$OUT/summary.txt"
tail -1 "$OUT/independent-unchanged/native.log"
tail -1 "$OUT/rx-adapted/native.log"
cat "$OUT/rx-adapted/independent-wire.json"
