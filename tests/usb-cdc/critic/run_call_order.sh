#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
set -x
cc --version | head -n 1
cc $FLAGS -O2 -Istubs -Isnapshot/firmware/transport snapshot/firmware/transport/mt_usb_cdc.c test_call_order.c -o build/strict
build/strict
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Istubs -Isnapshot/firmware/transport snapshot/firmware/transport/mt_usb_cdc.c test_call_order.c -o build/sanitized
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 build/sanitized
