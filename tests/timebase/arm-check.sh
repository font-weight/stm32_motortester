#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
: "${TC:?Set TC to ARM toolchain usr directory}"
OUT=${OUT:-/tmp/motortester-timebase-arm}
mkdir -p "$OUT"
FLAGS='-std=c11 -Os -mcpu=cortex-m3 -mthumb -ffreestanding -fno-short-enums -fstack-usage -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
"$TC/bin/arm-none-eabi-gcc" --version | head -1
"$TC/bin/arm-none-eabi-gcc" $FLAGS -isystem "$TC/include/newlib" -c firmware/platform/mt_timebase.c -o "$OUT/mt_timebase.o"
"$TC/bin/arm-none-eabi-gcc" $FLAGS -isystem "$TC/include/newlib" -c tests/timebase/arm_probe.c -o "$OUT/probe.o"
"$TC/bin/arm-none-eabi-size" "$OUT/mt_timebase.o"
"$TC/bin/arm-none-eabi-nm" -S --size-sort --radix=d "$OUT/probe.o"
"$TC/bin/arm-none-eabi-nm" -u "$OUT/mt_timebase.o"
cat "$OUT/mt_timebase.su"
sha256sum firmware/platform/mt_timebase.c firmware/platform/mt_timebase.h "$OUT/mt_timebase.o" "$OUT/probe.o"
