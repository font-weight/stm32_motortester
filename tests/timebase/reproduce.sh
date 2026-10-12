#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${OUT:-/tmp/motortester-timebase-reproduction}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "$ROOT"
printf '%s\n' 'TIME001 publication reproduction; software models only.'
uname -sm | tee "$OUT/toolchain.txt"
cc --version | head -n 1 | tee -a "$OUT/toolchain.txt"
python3 tests/timebase/verify.py | tee "$OUT/verified-manifest.txt"
OUT="$OUT/author" sh tests/timebase/run.sh > "$OUT/author.log" 2>&1
cat "$OUT/author.log"
FLAGS=(-std=c11 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion)
SOURCE=firmware/platform/mt_timebase.c
INCLUDE=firmware/platform
SUPPORT=tests/timebase/evidence/supporting/test_timebase_independent.c
CRITIC=tests/timebase/evidence/critic-a/independent_timebase.c
run_suite() {
  local name=$1 harness=$2; shift 2
  cc "${FLAGS[@]}" "$@" -O2 -I"$INCLUDE" "$SOURCE" "$harness" -o "$OUT/$name-strict"
  "$OUT/$name-strict" > "$OUT/$name-strict.log" 2>&1
  cat "$OUT/$name-strict.log"
  cc "${FLAGS[@]}" "$@" -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -I"$INCLUDE" "$SOURCE" "$harness" -o "$OUT/$name-sanitized"
  ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 "$OUT/$name-sanitized" > "$OUT/$name-sanitized.log" 2>&1
  cat "$OUT/$name-sanitized.log"
}
set -x
run_suite supporting "$SUPPORT" -Wshadow -Wstrict-prototypes -Wmissing-prototypes
cc "${FLAGS[@]}" -Wshadow -Wstrict-prototypes -Wmissing-prototypes -O0 -fanalyzer -I"$INCLUDE" -c "$SOURCE" -o "$OUT/timebase-analyzed.o"
run_suite critic "$CRITIC"
set +x
sha256sum "$SOURCE" firmware/platform/mt_timebase.h "$SUPPORT" "$CRITIC" | tee "$OUT/source-sha256.txt"
printf '%s\n' 'Author, supporting, supplemental Critic-A strict/ASan/UBSan and GCC analyzer passed.' 'Leak detection disabled; no physical timing, integrated firmware or acceptance claim.' | tee "$OUT/summary.txt"
