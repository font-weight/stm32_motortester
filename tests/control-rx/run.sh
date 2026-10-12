#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-control-rx-tests}
mkdir -p "$OUT"
SOURCES='firmware/app/mt_control_rx.c firmware/app/mt_control.c firmware/app/mt_capture_link.c firmware/protocol/mt_stream.c firmware/protocol/mt_wire.c firmware/protocol/mt_command.c firmware/protocol/mt_session.c firmware/acquisition/acquisition.c firmware/transport/mt_tx.c tests/control-rx/test_control_rx.c'
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
cc --version | head -1
cc $FLAGS -O2 $SOURCES -o "$OUT/strict"
"$OUT/strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $SOURCES -o "$OUT/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"

IO_SOURCES='firmware/app/mt_adc_control.c firmware/platform/adc_timer_dma.c'
RX_BASE=${SOURCES% tests/control-rx/test_control_rx.c}
cc $FLAGS -Ifirmware/platform -O2 $RX_BASE $IO_SOURCES tests/control-rx/test_rx_io.c -o "$OUT/io-strict"
"$OUT/io-strict"
cc $FLAGS -Ifirmware/platform -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $RX_BASE $IO_SOURCES tests/control-rx/test_rx_io.c -o "$OUT/io-sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/io-sanitized"
