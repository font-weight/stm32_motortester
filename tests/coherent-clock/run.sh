#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-coherent-clock-tests}
mkdir -p "$OUT"
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
SOURCES='firmware/platform/mt_timebase.c tests/coherent-clock/test_snapshot.c'
cc --version | head -1
cc $FLAGS -O2 $SOURCES -o "$OUT/snapshot-strict"
"$OUT/snapshot-strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $SOURCES -o "$OUT/snapshot-sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/snapshot-sanitized"
