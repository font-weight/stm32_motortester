#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
OUT=${OUT:-/tmp/motortester-timebase-inline-tests}
mkdir -p "$OUT"
FLAGS='-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
SOURCE=firmware/platform/mt_timebase.c
TEST=tests/timebase-inline/test_trace.c
cc --version | head -1
cc $FLAGS -O2 "$SOURCE" "$TEST" -o "$OUT/trace-strict"
"$OUT/trace-strict" "$OUT/current.trace"
cc $FLAGS -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$SOURCE" "$TEST" -o "$OUT/trace-sanitized"
ASAN_OPTIONS=detect_leaks=0 "$OUT/trace-sanitized" "$OUT/sanitized.trace"
cmp "$OUT/current.trace" "$OUT/sanitized.trace"
# Optional historical comparison: a source ZIP needs no Git metadata or old copy
# to run the canonical trace assertions above. Supply an existing baseline .c
# to test the same oracle and compare every recorded operation/state directly.
if [ -n "${TIMEBASE_BASELINE_SOURCE:-}" ]; then
    cc $FLAGS -O2 -I firmware/platform "$TIMEBASE_BASELINE_SOURCE" "$TEST" -o "$OUT/trace-baseline"
    "$OUT/trace-baseline" "$OUT/baseline.trace"
    cmp "$OUT/current.trace" "$OUT/baseline.trace"
    echo 'PASS baseline/current operation and state transcripts are byte-identical'
fi
