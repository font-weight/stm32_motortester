#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-adc-control-tests}
mkdir -p "$OUT"
SOURCES='firmware/app/mt_adc_control.c firmware/app/mt_control.c firmware/app/mt_capture_link.c firmware/platform/adc_timer_dma.c firmware/protocol/mt_wire.c firmware/protocol/mt_command.c firmware/protocol/mt_session.c firmware/acquisition/acquisition.c firmware/transport/mt_tx.c tests/adc-control/test_adc_control.c'
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror -Ifirmware/platform'
cc --version | head -1
cc $FLAGS -O2 $SOURCES -o "$OUT/strict"
"$OUT/strict"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer $SOURCES -o "$OUT/sanitized"
# LeakSanitizer cannot operate under this executor's ptrace. ASan and UBSan run.
ASAN_OPTIONS=detect_leaks=0 "$OUT/sanitized"
