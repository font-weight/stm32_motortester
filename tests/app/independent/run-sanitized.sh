#!/bin/sh
set -eu
cd "$(dirname "$0")"
SOURCE=${SOURCE:-fixed-snapshot}
EXTRA=""
if grep -q dropped_windows "$SOURCE/firmware/app/mt_capture_link.h"; then EXTRA=-DTEST_HAS_DROPPED; fi
cc -std=c11 -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic -g -O1 -fno-omit-frame-pointer -fsanitize=address,undefined $EXTRA -fPIC -shared -I"$SOURCE" adapter.c "$SOURCE/firmware/app/mt_capture_link.c" "$SOURCE/firmware/acquisition/acquisition.c" "$SOURCE/firmware/transport/mt_tx.c" "$SOURCE/firmware/protocol/mt_wire.c" -o test_link_sanitized.so
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 LD_PRELOAD="$(cc -print-file-name=libasan.so)" TEST_LIBRARY=./test_link_sanitized.so python3 test_link.py "$@"
