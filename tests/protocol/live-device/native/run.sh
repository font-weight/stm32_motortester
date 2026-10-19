#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
src=$(CDPATH= cd -- "${1:?Usage: run.sh source-root [output-directory]}" && pwd)
if [ "$#" -ge 2 ]; then
    mkdir "$2"
    build=$(CDPATH= cd -- "$2" && pwd)
else
    build=$(mktemp -d "${TMPDIR:-/tmp}/mt-live-device-native.XXXXXX")
fi
cc=${CC:-cc}
{
    printf 'UTC: '; date -u '+%Y-%m-%dT%H:%M:%SZ'
    printf 'Source: %s\nTests: %s\n' "$src" "$here"
    if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        printf 'HEAD: '; git -C "$src" rev-parse HEAD
        git -C "$src" status --short
    else
        printf 'Source-only export; source SHA-256 manifest below is authoritative.\n'
    fi
    "$cc" --version | head -n 1
    uname -a
    sha256sum "$src/firmware/protocol/mt_live_device.c" "$src/firmware/protocol/mt_live_device.h" \
        "$src/firmware/protocol/mt_wire.c" "$src/firmware/protocol/mt_wire.h" \
        "$src/firmware/acquisition/mt_sample_queue.c" "$src/firmware/acquisition/mt_sample_queue.h" \
        "$src/protocol/wire-v0.2-stream.md" "$here/test_live_device.c" "$here/run.sh"
} > "$build/provenance.txt"
cat "$build/provenance.txt"
result=0
for variant in strict asan-ubsan; do
    if [ "$variant" = asan-ubsan ]; then
        san='-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer'
    else
        san=''
    fi
    # Intentional word splitting of a fixed compiler flag list only.
    "$cc" -std=c11 -O2 -g -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion -Wshadow $san \
        -I"$src/firmware/protocol" "$here/test_live_device.c" \
        "$src/firmware/protocol/mt_live_device.c" "$src/firmware/protocol/mt_wire.c" \
        "$src/firmware/acquisition/mt_sample_queue.c" -o "$build/test-$variant" \
        > "$build/compile-$variant.log" 2>&1
    # ptrace makes LeakSanitizer unavailable here; no leak-check claim is made.
    if ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
        "$build/test-$variant" > "$build/run-$variant.log" 2>&1; then
        printf '%s: PASS\n' "$variant"
    else
        printf '%s: FAIL\n' "$variant"
        result=1
    fi
    cat "$build/run-$variant.log"
done
printf 'Artifacts: %s\n' "$build"
exit "$result"
