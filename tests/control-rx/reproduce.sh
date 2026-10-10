#!/usr/bin/env bash
# Publication packaging runner: production and existing regression sources stay unchanged.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=$(realpath -m "${OUT:-/tmp/motortester-rx-reproduction}")
mkdir -p "$OUT"
cd "$ROOT"
cc --version > "$OUT/toolchain.txt"
OUT="$OUT/author" sh tests/control-rx/run.sh > "$OUT/author.log" 2>&1
bash tests/control-rx/independent/run.sh "$ROOT" "$OUT/independent" > "$OUT/independent.log" 2>&1
OUT="$OUT/control-author" sh tests/control/run.sh > "$OUT/control-author.log" 2>&1
bash tests/control/independent/run.sh "$ROOT" "$OUT/control-unchanged" > "$OUT/control-unchanged.log" 2>&1
python3 tests/control-rx/independent/prepare_old_rx.py "$ROOT" "$OUT/rx-adapted" > "$OUT/generator.log"
cmp tests/control-rx/evidence/supporting/old-controller-rx/adapter.diff "$OUT/rx-adapted/adapter.diff"
COMMON=(-std=c11 -O1 -g -Wall -Wextra -Werror -Wpedantic -I"$ROOT")
SOURCES=(firmware/app/mt_control_rx.c firmware/protocol/mt_stream.c firmware/app/mt_control.c firmware/app/mt_capture_link.c firmware/acquisition/acquisition.c firmware/protocol/mt_wire.c firmware/protocol/mt_command.c firmware/protocol/mt_session.c firmware/transport/mt_tx.c)
cc "${COMMON[@]}" "$OUT/rx-adapted/test_control_independent.c" "${SOURCES[@]}" -o "$OUT/rx-adapted/native"
(cd "$OUT/rx-adapted"; ./native > native.log 2>&1)
cc "${COMMON[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "$OUT/rx-adapted/test_control_independent.c" "${SOURCES[@]}" -o "$OUT/rx-adapted/sanitized"
(cd "$OUT/rx-adapted"; ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./sanitized > sanitizer.log 2>&1; python3 "$ROOT/tests/control/independent/verify_transcript.py" twenty-windows.cobs > independent-wire.json)
cmp "$OUT/control-unchanged/twenty-windows.cobs" "$OUT/rx-adapted/twenty-windows.cobs"
sha256sum "$OUT/control-unchanged/twenty-windows.cobs" "$OUT/rx-adapted/twenty-windows.cobs" > "$OUT/transcript-sha256.txt"
for TEST in tombstone_hardware_repro stale_stop_capacity_repro; do
 cc "${COMMON[@]}" -Itests/control/independent "tests/control/critic-a/$TEST.c" "${SOURCES[@]}" -o "$OUT/$TEST"
 (cd "$OUT"; "./$TEST") > "$OUT/$TEST.log" 2>&1
 cc "${COMMON[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer -Itests/control/independent "tests/control/critic-a/$TEST.c" "${SOURCES[@]}" -o "$OUT/$TEST-sanitized"
 (cd "$OUT"; ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "./$TEST-sanitized") > "$OUT/$TEST-sanitized.log" 2>&1
done
ORACLE=(tests/control-rx/evidence/critic-a/independent_rx.c firmware/app/mt_control_rx.c firmware/protocol/mt_stream.c firmware/protocol/mt_wire.c)
cc "${COMMON[@]}" -Wconversion -Wsign-conversion "${ORACLE[@]}" -o "$OUT/critic-strict"
"$OUT/critic-strict" > "$OUT/critic-strict.log" 2>&1
cc "${COMMON[@]}" -Wconversion -Wsign-conversion -fsanitize=address,undefined -fno-omit-frame-pointer "${ORACLE[@]}" -o "$OUT/critic-sanitized"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "$OUT/critic-sanitized" > "$OUT/critic-sanitized.log" 2>&1
sha256sum "${SOURCES[@]}" tests/control-rx/independent/test_rx_independent.c tests/control-rx/evidence/critic-a/independent_rx.c > "$OUT/source-sha256.txt"
tail -n 1 "$OUT/independent/native.log" "$OUT/control-unchanged/native.log" "$OUT/rx-adapted/native.log" "$OUT/critic-strict.log"
cat "$OUT/rx-adapted/independent-wire.json" "$OUT/transcript-sha256.txt"
printf 'PASS: author RX and RX/ADC, supporting RX, original and adapted controller, unchanged Stop reproducers, Critic-A wrapper oracle.\n' | tee "$OUT/summary.txt"
