#!/bin/sh
set -eu
cd "$(dirname "$0")"
OUT=${OUT:-build}
mkdir -p "$OUT"
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
set -x
cc --version | head -n 1
cc $FLAGS -O2 -Isnapshot/firmware/platform snapshot/firmware/platform/mt_timebase.c independent_timebase.c -o "$OUT/strict"
"$OUT/strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Isnapshot/firmware/platform snapshot/firmware/platform/mt_timebase.c independent_timebase.c -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
