#!/usr/bin/env bash
set -uo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SOURCE=$(realpath "${1:-$ROOT/snapshot-final}")
OUT=$(realpath -m "${2:-$ROOT/run}")
mkdir -p "$OUT"
cd "$OUT"
cp "$ROOT/test_control_independent.c" "$ROOT/verify_transcript.py" "$OUT/"
cp "$ROOT/run.sh" "$OUT/runner-used.sh"
SOURCES=("$OUT/test_control_independent.c" "$SOURCE/firmware/app/mt_control.c" "$SOURCE/firmware/app/mt_capture_link.c" "$SOURCE/firmware/acquisition/acquisition.c" "$SOURCE/firmware/protocol/mt_wire.c" "$SOURCE/firmware/protocol/mt_command.c" "$SOURCE/firmware/protocol/mt_session.c" "$SOURCE/firmware/transport/mt_tx.c")
COMMON=(-std=c11 -O1 -g -Wall -Wextra -Werror -Wpedantic -I"$SOURCE")
cc --version > toolchain.txt
sha256sum "${SOURCES[@]}" > compiled-source-sha256.txt
cc "${COMMON[@]}" "${SOURCES[@]}" -o native || exit 2
./native > native.log 2>&1
NATIVE=$?
cc "${COMMON[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "${SOURCES[@]}" -o sanitized || exit 2
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 ./sanitized > sanitizer.log 2>&1
SAN=$?
python3 "$OUT/verify_transcript.py" twenty-windows.cobs > independent-wire.json 2> independent-wire-errors.txt
WIRE=$?
printf 'native_exit=%s\nsanitizer_exit=%s\nindependent_wire_exit=%s\n' "$NATIVE" "$SAN" "$WIRE" | tee exit-status.txt
cat native.log
exit "$((NATIVE || SAN || WIRE))"
