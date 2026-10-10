#!/bin/sh
# Read-only source review. Build products and logs go to OUTPUT, never CHECKOUT.
set -eu
test "$#" -eq 3 || { echo "usage: sh run-review.sh CHECKOUT_826 CHECKOUT_06b OUTPUT" >&2; exit 2; }
src=$(CDPATH= cd -- "$1" && pwd)
old=$(CDPATH= cd -- "$2" && pwd)
mkdir -p "$3"
out=$(CDPATH= cd -- "$3" && pwd)
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cc=${CC:-cc}
exec > "$out/commands.log" 2>&1
set -x
uname -a
"$cc" --version
python3 --version
cd "$src"
sha256sum -c "$here/source-sha256.txt"
sh tests/transport/run.sh
SANITIZE=1 ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 sh tests/transport/run.sh
"$cc" -std=c11 -O2 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror -shared -fPIC firmware/protocol/mt_wire.c firmware/transport/mt_tx.c -o "$out/libtx.so"
python3 "$here/independent_tx.py" "$out/libtx.so" > "$out/independent.json" 2> "$out/independent.stderr"
"$cc" -std=c11 -O1 -g -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror -shared -fPIC -fsanitize=address,undefined -fno-omit-frame-pointer firmware/protocol/mt_wire.c firmware/transport/mt_tx.c -o "$out/libtx-san.so"
LD_PRELOAD="$("$cc" -print-file-name=libasan.so)" ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 python3 "$here/independent_tx.py" "$out/libtx-san.so" > "$out/independent-sanitized.json" 2> "$out/independent-sanitized.stderr"
cmp "$out/independent.json" "$out/independent-sanitized.json"
cat "$out/independent.json"
"$cc" -std=c11 -O2 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror -shared -fPIC "$old/firmware/protocol/mt_wire.c" "$old/firmware/transport/mt_tx.c" -o "$out/libtx-old.so"
set +e
python3 "$here/independent_tx.py" "$out/libtx-old.so" regression > "$out/old-regression.stdout" 2> "$out/old-regression.stderr"
old_status=$?
set -e
test "$old_status" -eq 1
grep -F 'RuntimeError: old Stop ACK released new session barrier' "$out/old-regression.stderr"
cd "$out"
"$cc" -std=c11 -Os -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror -fstack-usage -c "$src/firmware/transport/mt_tx.c" "$src/firmware/protocol/mt_wire.c"
{ size ./*.o; nm -u ./*.o; cat ./*.su; } > host-resource.log
cat host-resource.log
echo 'REVIEW COMMANDS COMPLETE: current candidate PASS; old candidate known regression reproduced'
