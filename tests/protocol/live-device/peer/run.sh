#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
: "${SOURCE_ROOT:?Set SOURCE_ROOT to the C device repository or source export root}"
: "${HOST_ROOT:?Set HOST_ROOT to the exact PR195 host directory}"
if [ -n "${OUT:-}" ]; then
    mkdir "$OUT"
else
    OUT=$(mktemp -d "${TMPDIR:-/tmp}/mt-live-peer.XXXXXX")
fi
SOURCE_ROOT=$(CDPATH= cd -- "$SOURCE_ROOT" && pwd)
HOST_ROOT=$(CDPATH= cd -- "$HOST_ROOT" && pwd)
CC=${CC:-cc}
PYTHON=${PYTHON:-python3}
OUT=$(CDPATH= cd -- "$OUT" && pwd)
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$HOST_ROOT"
export HOST_ROOT
build() {
    "$CC" -std=c11 -Wall -Wextra -Werror -Wconversion -pedantic -fPIC -shared \
        -I "$SOURCE_ROOT" "$HERE/bridge.c" \
        "$SOURCE_ROOT/firmware/protocol/mt_live_device.c" \
        "$SOURCE_ROOT/firmware/protocol/mt_wire.c" \
        "$SOURCE_ROOT/firmware/acquisition/mt_sample_queue.c" "$@"
}
build -O2 -o "$OUT/bridge.so"
BRIDGE_LIBRARY="$OUT/bridge.so" TRACE_DIR="$OUT/traces-normal" \
    "$PYTHON" "$HERE/test_coupled.py" > "$OUT/normal.log" 2>&1
BRIDGE_LIBRARY="$OUT/bridge.so" TRACE_DIR="$OUT/traces-optimized" \
    "$PYTHON" -O "$HERE/test_coupled.py" > "$OUT/optimized.log" 2>&1
build -O1 -g -fsanitize=undefined -fno-sanitize-recover=all -o "$OUT/bridge-ubsan.so"
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 BRIDGE_LIBRARY="$OUT/bridge-ubsan.so" \
    TRACE_DIR="$OUT/traces-ubsan" "$PYTHON" "$HERE/test_coupled.py" > "$OUT/ubsan.log" 2>&1
build -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -o "$OUT/bridge-asan-ubsan.so"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
    LD_PRELOAD="$("$CC" -print-file-name=libasan.so)" BRIDGE_LIBRARY="$OUT/bridge-asan-ubsan.so" \
    TRACE_DIR="$OUT/traces-asan-ubsan" "$PYTHON" "$HERE/test_coupled.py" > "$OUT/asan-ubsan.log" 2>&1
for name in normal optimized ubsan asan-ubsan; do
    printf '\n=== %s ===\n' "$name"
    tail -5 "$OUT/$name.log"
done
SOURCE_ROOT="$SOURCE_ROOT" OUT="$OUT" "$PYTHON" - <<'PY'
import hashlib,json,os,platform,subprocess,sys
from pathlib import Path
root=Path(os.environ['SOURCE_ROOT']).resolve();out=Path(os.environ['OUT'])
paths=['firmware/protocol/mt_live_device.c','firmware/protocol/mt_live_device.h',
       'firmware/protocol/mt_wire.c','firmware/protocol/mt_wire.h',
       'firmware/acquisition/mt_sample_queue.c','firmware/acquisition/mt_sample_queue.h',
       'protocol/wire-v0.2-stream.md']
record={'source_root':str(root),'source_revision':(root/'SOURCE_REVISION').read_text().strip() if (root/'SOURCE_REVISION').exists() else None,
        'python':sys.version,'platform':platform.platform(),'sources':[],
        'host_root':os.environ['HOST_ROOT'],'host_commit':'0e96e574c2ef5c7234a6cfc96665cb88ddca9911','host_files':[]}
if record['source_revision'] is None:
    try:
        completed=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],text=True,capture_output=True)
    except FileNotFoundError:
        completed=None  # Source exports do not require Git; file hashes remain authoritative.
    if completed is not None and completed.returncode==0:
        record['source_revision']=completed.stdout.strip()
for rel in paths:
    b=(root/rel).read_bytes()
    record['sources'].append({'path':rel,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),
                             'git_blob_sha1':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()})
for rel in ['motortester/__init__.py','motortester/stream_endpoint.py','motortester/stream_wire.py']:
    b=(Path(os.environ['HOST_ROOT'])/rel).read_bytes()
    record['host_files'].append({'path':rel,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
(out/'source-manifest.json').write_text(json.dumps(record,indent=2)+'\n')
PY
