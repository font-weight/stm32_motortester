#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-capture-link-tests}
mkdir -p "$OUT"
SOURCES='firmware/app/mt_capture_link.c firmware/transport/mt_tx.c firmware/acquisition/acquisition.c firmware/protocol/mt_wire.c tests/app/test_capture_link.c'
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
cc --version | head -1
# Intentional word splitting: the variables contain fixed flag/path lists.
cc $FLAGS -O2 $SOURCES -o "$OUT/strict"
"$OUT/strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $SOURCES -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
