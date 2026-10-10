#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
BUILD=${BUILD_DIR:-"$(mktemp -d)"}
if [ -z "${BUILD_DIR:-}" ]; then trap 'rm -rf "$BUILD"' EXIT HUP INT TERM; fi
mkdir -p "$BUILD"
CC=${CC:-cc}
COMMON='-std=c11 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wshadow -fno-short-enums'
# shellcheck disable=SC2086
"$CC" $COMMON -O2 -I"$ROOT/firmware/platform" \
    "$ROOT/firmware/platform/adc_timer_dma.c" "$ROOT/tests/platform/test_adc_timer_dma.c" \
    -o "$BUILD/test_adc_timer_dma"
"$BUILD/test_adc_timer_dma"
if [ "${SANITIZE:-1}" = 1 ]; then
    # shellcheck disable=SC2086
    "$CC" $COMMON -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
        -I"$ROOT/firmware/platform" "$ROOT/firmware/platform/adc_timer_dma.c" \
        "$ROOT/tests/platform/test_adc_timer_dma.c" -o "$BUILD/test_adc_timer_dma_sanitized"
    "$BUILD/test_adc_timer_dma_sanitized"
fi
if [ -n "${ARM_CC:-}" ]; then
    : "${ARM_NEWLIB_INCLUDE:?Set path to verified newlib headers}"
    # shellcheck disable=SC2086
    "$ARM_CC" $COMMON -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -Os \
        -ffreestanding -ffunction-sections -fdata-sections -fstack-usage \
        -isystem "$ARM_NEWLIB_INCLUDE" -I"$ROOT/firmware/platform" \
        -c "$ROOT/firmware/platform/adc_timer_dma.c" -o "$BUILD/adc_timer_dma.o"
    # ABI probe uses object-symbol sizes, without executing Cortex-M3 code.
    # shellcheck disable=SC2086
    "$ARM_CC" $COMMON -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -Os \
        -ffreestanding -isystem "$ARM_NEWLIB_INCLUDE" -I"$ROOT/firmware/platform" \
        -c "$ROOT/tests/platform/abi_probe.c" -o "$BUILD/adc_timer_dma_abi.o"
    echo 'Cortex-M3 object compiled; no startup/linker/USB or hardware test implied'
fi
