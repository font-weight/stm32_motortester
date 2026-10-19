#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
CC=${CC:-cc}
# Retain evidence outside the source tree; do not remove existing user files.
OUT=${1:-$(mktemp -d "${TMPDIR:-/tmp}/mt-revision-codec.XXXXXX")}
mkdir -p "$OUT"
OUT=$(CDPATH= cd -- "$OUT" && pwd)
python3 "$HERE/make_corpus.py" --vectors-dir "$ROOT/protocol" --output-dir "$OUT"
"$CC" --version | head -1
FLAGS="-std=c11 -pedantic -Wall -Wextra -Werror -O1 -g"
RENAMES="-Dmt_wire_encode=baseline_mt_wire_encode -Dmt_wire_decode=baseline_mt_wire_decode -Dmt_wire_crc32=baseline_mt_wire_crc32"
set -x
$CC $FLAGS $RENAMES -I"$ROOT/firmware/protocol" -include "$HERE/baseline_mt_wire.h" -c "$HERE/baseline_mt_wire.c" -o "$OUT/baseline.o"
$CC $FLAGS -I"$ROOT/firmware/protocol" "$ROOT/firmware/protocol/mt_wire.c" "$HERE/test_codec.c" "$OUT/baseline.o" -o "$OUT/test_codec"
"$OUT/test_codec" "$OUT/corpus.bin"
$CC $FLAGS -fsanitize=address,undefined -fno-omit-frame-pointer $RENAMES -I"$ROOT/firmware/protocol" -include "$HERE/baseline_mt_wire.h" -c "$HERE/baseline_mt_wire.c" -o "$OUT/baseline_san.o"
$CC $FLAGS -fsanitize=address,undefined -fno-omit-frame-pointer -I"$ROOT/firmware/protocol" "$ROOT/firmware/protocol/mt_wire.c" "$HERE/test_codec.c" "$OUT/baseline_san.o" -o "$OUT/test_codec_san"
# LeakSanitizer cannot operate under executor ptrace. This is not a leak check.
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 "$OUT/test_codec_san" "$OUT/corpus.bin"
printf 'Evidence directory: %s\n' "$OUT"
