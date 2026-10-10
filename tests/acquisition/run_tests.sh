#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/mt-acquisition.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT HUP INT TERM
CC=${CC:-cc}
"$CC" --version | head -n 1
for variant in strict ubsan; do
    if [ "$variant" = ubsan ]; then
        SANITIZERS='-fsanitize=undefined -fno-sanitize-recover=all'
    else
        SANITIZERS=''
    fi
    # Intentional word splitting of this fixed, script-owned flag list.
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Wpedantic -Wconversion \
        -Wsign-conversion -Werror $SANITIZERS \
        -I"$ROOT/firmware/acquisition" \
        "$ROOT/firmware/acquisition/acquisition.c" \
        "$ROOT/tests/acquisition/test_acquisition.c" \
        -o "$BUILD/test-$variant"
    printf '%s: ' "$variant"
    "$BUILD/test-$variant"
done
