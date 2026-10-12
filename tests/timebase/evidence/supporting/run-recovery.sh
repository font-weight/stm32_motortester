#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
{
  printf '%s\n' 'RECOVERY RUN after environment reset, 2026-10-10: fresh TIME001 independent host model evidence; no hardware/critic approval.'
  printf '\n--- Host tools ---\n'
  uname -sm
  cc --version | head -n 1
  printf '\n--- Frozen source SHA256 ---\n'
  sha256sum subject/mt_timebase.c subject/mt_timebase.h test_timebase_independent.c
  flags=(-std=c11 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion -Wshadow -Wstrict-prototypes -Wmissing-prototypes)
  printf '\n--- Strict GCC C11 ---\n'
  set -x
  cc "${flags[@]}" -O2 -Isubject subject/mt_timebase.c test_timebase_independent.c -o test-strict
  ./test-strict
  set +x
  printf '\n--- GCC ASan + UBSan; leak detection disabled ---\n'
  set -x
  cc "${flags[@]}" -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -Isubject subject/mt_timebase.c test_timebase_independent.c -o test-sanitized
  ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ./test-sanitized
  set +x
  printf '\n--- GCC static analyzer ---\n'
  set -x
  cc "${flags[@]}" -O0 -fanalyzer -Isubject -c subject/mt_timebase.c -o timebase-analyzed.o
  set +x
  printf '\n--- Source still matches recovered frozen subject ---\n'
  cmp subject/mt_timebase.c /workspace/shared/critic-a-timebase-review-recovered/snapshot/firmware/platform/mt_timebase.c
  cmp subject/mt_timebase.h /workspace/shared/critic-a-timebase-review-recovered/snapshot/firmware/platform/mt_timebase.h
  printf '%s\n' 'Both C/H byte-identical to recovered frozen subject at verification.'
  printf '\n--- Binary SHA256 ---\n'
  sha256sum test-strict test-sanitized timebase-analyzed.o
} 2>&1 | tee logs/independent-host-recovery-run.txt
