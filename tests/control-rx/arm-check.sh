#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
: "${TC:?Set TC to ARM toolchain usr directory}"
OUT=${OUT:-/tmp/motortester-control-rx-arm}
mkdir -p "$OUT"
FLAGS='-std=c11 -Os -mcpu=cortex-m3 -mthumb -ffreestanding -fno-short-enums -fstack-usage -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
"$TC/bin/arm-none-eabi-gcc" --version | head -1
"$TC/bin/arm-none-eabi-gcc" $FLAGS -isystem "$TC/include/newlib" -c firmware/app/mt_control_rx.c -o "$OUT/mt_control_rx.o"
"$TC/bin/arm-none-eabi-gcc" $FLAGS -isystem "$TC/include/newlib" -c tests/control-rx/arm_probe.c -o "$OUT/probe.o"
"$TC/bin/arm-none-eabi-size" "$OUT/mt_control_rx.o"
"$TC/bin/arm-none-eabi-nm" -S --size-sort --radix=d "$OUT/probe.o"
"$TC/bin/arm-none-eabi-nm" -u "$OUT/mt_control_rx.o"
cat "$OUT/mt_control_rx.su"
sha256sum firmware/app/mt_control_rx.c firmware/app/mt_control_rx.h "$OUT/mt_control_rx.o" "$OUT/probe.o"
