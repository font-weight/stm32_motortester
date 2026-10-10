#!/usr/bin/env bash
set -uo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SOURCE=$(realpath "${1:-$ROOT/snapshot}")
OUT=$(realpath -m "${2:-$ROOT/run}")
mkdir -p "$OUT"
SOURCES=("$ROOT/test_rx_independent.c" "$SOURCE/firmware/app/mt_control_rx.c" "$SOURCE/firmware/app/mt_control.c" "$SOURCE/firmware/app/mt_capture_link.c" "$SOURCE/firmware/acquisition/acquisition.c" "$SOURCE/firmware/protocol/mt_wire.c" "$SOURCE/firmware/protocol/mt_stream.c" "$SOURCE/firmware/protocol/mt_command.c" "$SOURCE/firmware/protocol/mt_session.c" "$SOURCE/firmware/transport/mt_tx.c")
FLAGS=(-std=c11 -O1 -g -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror -I"$SOURCE")
cc --version > "$OUT/toolchain.txt"
sha256sum "${SOURCES[@]}" > "$OUT/compiled-sha256.txt"
cc "${FLAGS[@]}" "${SOURCES[@]}" -o "$OUT/native" > "$OUT/native-build.log" 2>&1 || { cat "$OUT/native-build.log"; exit 2; }
"$OUT/native" > "$OUT/native.log" 2>&1; NATIVE=$?
cc "${FLAGS[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "${SOURCES[@]}" -o "$OUT/sanitized" > "$OUT/sanitized-build.log" 2>&1 || { cat "$OUT/sanitized-build.log"; exit 2; }
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 "$OUT/sanitized" > "$OUT/sanitizer.log" 2>&1; SAN=$?
printf 'native_exit=%s\nsanitizer_exit=%s\n' "$NATIVE" "$SAN" | tee "$OUT/exit-status.txt"
cat "$OUT/native.log"
exit "$((NATIVE||SAN))"
