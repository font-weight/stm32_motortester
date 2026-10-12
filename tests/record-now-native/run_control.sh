#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/record-now-native-control}
mkdir -p "$OUT"
SOURCES='firmware/app/mt_control.c firmware/app/mt_capture_link.c firmware/protocol/mt_wire.c firmware/protocol/mt_command.c firmware/protocol/mt_session.c firmware/acquisition/acquisition.c firmware/transport/mt_tx.c'
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
for MODE in strict sanitized; do
    EXTRA='-O2'
    if [ "$MODE" = sanitized ]; then
        EXTRA='-O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer -fno-pie -no-pie'
    fi
    cc $FLAGS $EXTRA $SOURCES tests/record-now-native/test_control.c -o "$OUT/control-$MODE"
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$OUT/control-$MODE"
    cc $FLAGS $EXTRA $SOURCES tests/record-now-native/test_link.c -o "$OUT/link-$MODE"
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$OUT/link-$MODE"
    cc $FLAGS $EXTRA -I. $SOURCES firmware/protocol/mt_stream.c firmware/app/mt_control_rx.c tests/record-now-native/test_stop_barrier.c -o "$OUT/stop-$MODE"
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$OUT/stop-$MODE"
    cc $FLAGS $EXTRA firmware/protocol/mt_command.c tests/protocol/test_mt_command.c -o "$OUT/command-$MODE"
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$OUT/command-$MODE"
done
