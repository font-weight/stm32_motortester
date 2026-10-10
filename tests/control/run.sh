#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-control-tests}
mkdir -p "$OUT"
SOURCES='firmware/app/mt_control.c firmware/app/mt_capture_link.c firmware/protocol/mt_wire.c firmware/protocol/mt_command.c firmware/protocol/mt_session.c firmware/acquisition/acquisition.c firmware/transport/mt_tx.c tests/control/test_control.c'
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
cc --version | head -1
cc $FLAGS -O2 $SOURCES -o "$OUT/strict"
"$OUT/strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $SOURCES -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
