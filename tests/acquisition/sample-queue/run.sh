#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "${1:?Usage: run.sh source-root [new-output-directory]}" && pwd)
if [ "$#" -ge 2 ]; then
    mkdir "$2"
    BUILD=$(CDPATH= cd -- "$2" && pwd)
else
    BUILD=$(mktemp -d "${TMPDIR:-/tmp}/mt-sample-queue.XXXXXX")
fi
CC=${CC:-cc}
{
    printf 'UTC: '; date -u '+%Y-%m-%dT%H:%M:%SZ'
    printf 'Source root: %s\nTest source: %s\n' "$ROOT" "$HERE"
    if command -v git >/dev/null 2>&1 &&
       git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        printf 'HEAD: '; git -C "$ROOT" rev-parse HEAD
        printf 'Tracked status:\n'; git -C "$ROOT" status --short
    else
        printf 'Git provenance: unavailable (source-only export or Git not installed).\n'
        printf 'Actual source and test SHA-256 values are recorded below.\n'
    fi
    "$CC" --version | head -n 1
    uname -a
    sha256sum "$ROOT/firmware/acquisition/mt_sample_queue.c" \
        "$ROOT/firmware/acquisition/mt_sample_queue.h" \
        "$HERE/test_sample_queue.c" "$HERE/run.sh"
} > "$BUILD/provenance.txt"
cat "$BUILD/provenance.txt"
for variant in strict asan-ubsan; do
    if [ "$variant" = asan-ubsan ]; then
        SANITIZERS='-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer'
    else
        SANITIZERS=''
    fi
    # Intentional splitting of this fixed script-owned flags list.
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Wpedantic -Wconversion \
        -Wsign-conversion -Wshadow -Werror $SANITIZERS \
        -I"$ROOT/firmware/acquisition" \
        "$ROOT/firmware/acquisition/mt_sample_queue.c" \
        "$HERE/test_sample_queue.c" -o "$BUILD/test-$variant" \
        > "$BUILD/compile-$variant.log" 2>&1
    # LeakSanitizer is disabled because ptrace makes it unsupported here.
    # This does not claim that any leak check was performed.
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
        UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
        "$BUILD/test-$variant" > "$BUILD/run-$variant.log" 2>&1
    printf '%s: ' "$variant"
    cat "$BUILD/run-$variant.log"
done
printf 'Artifacts: %s\n' "$BUILD"
