#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec env -i PATH=/usr/bin:/bin LC_ALL=C PYTHONDONTWRITEBYTECODE=1 python3 "$HERE/compare_arm.py" "$@"
