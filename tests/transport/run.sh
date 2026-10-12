#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
build=$(mktemp -d "${TMPDIR:-/tmp}/mt-tx-tests.XXXXXX")
trap 'rm -rf "$build"' EXIT HUP INT TERM
cc=${CC:-cc}
set -- -std=c11 -Wall -Wextra -Werror -pedantic -Wconversion -Wshadow
if [ "${SANITIZE:-0}" = 1 ]; then
    set -- "$@" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
else
    set -- "$@" -O2
fi
"$cc" "$@" "$root/firmware/protocol/mt_wire.c" \
    "$root/firmware/transport/mt_tx.c" "$root/tests/transport/test_mt_tx.c" \
    -o "$build/test_mt_tx"
"$build/test_mt_tx"
