#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SRC=${SOURCE_ROOT:-$PWD/snapshot}
OUT=${BUILD_DIR:-$PWD/build}
mkdir -p "$OUT"
FLAGS=(-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror -I"$SRC")
set -x
gcc --version
gcc "${FLAGS[@]}" -O2 test_api_boundaries.c "$SRC/firmware/transport/mt_usb_epoch.c" -o "$OUT/native"
"$OUT/native"
gcc "${FLAGS[@]}" -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer test_api_boundaries.c "$SRC/firmware/transport/mt_usb_epoch.c" -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
