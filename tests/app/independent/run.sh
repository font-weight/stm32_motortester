#!/bin/sh
set -eu
cd "$(dirname "$0")"
SOURCE=${SOURCE:-fixed-snapshot}
EXTRA="${CFLAGS:-}"
if grep -q dropped_windows "$SOURCE/firmware/app/mt_capture_link.h"; then EXTRA="$EXTRA -DTEST_HAS_DROPPED"; fi
cc $EXTRA -std=c11 -Wall -Wextra -Werror -Wconversion -Wshadow -Wpedantic -g -O1 -fPIC -shared -I"$SOURCE" adapter.c "$SOURCE/firmware/app/mt_capture_link.c" "$SOURCE/firmware/acquisition/acquisition.c" "$SOURCE/firmware/transport/mt_tx.c" "$SOURCE/firmware/protocol/mt_wire.c" -o test_link.so
python3 test_link.py "$@"
