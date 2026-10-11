#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SRC=${SOURCE_ROOT:-$PWD/snapshot}
OUT=${BUILD_DIR:-$PWD/build}
mkdir -p "$OUT"
SOURCES=(test_stop_barrier.c "$SRC/firmware/protocol/mt_wire.c" "$SRC/firmware/protocol/mt_command.c" "$SRC/firmware/protocol/mt_session.c" "$SRC/firmware/protocol/mt_stream.c" "$SRC/firmware/transport/mt_tx.c" "$SRC/firmware/acquisition/acquisition.c" "$SRC/firmware/app/mt_capture_link.c" "$SRC/firmware/app/mt_control.c" "$SRC/firmware/app/mt_control_rx.c")
FLAGS=(-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror -I"$SRC" -g)
set -x
gcc --version
gcc "${FLAGS[@]}" "${SOURCES[@]}" -o "$OUT/native"
"$OUT/native"
gcc "${FLAGS[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "${SOURCES[@]}" -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
