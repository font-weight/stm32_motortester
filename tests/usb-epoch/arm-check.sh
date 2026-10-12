#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
: "${TC:?ARM toolchain usr directory required}"
OUT=${OUT:-/tmp/motortester-usb-epoch-arm}
mkdir -p "$OUT"
FLAGS='-std=c11 -Os -mcpu=cortex-m3 -mthumb -ffreestanding -fno-short-enums -fstack-usage -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
"$TC/bin/arm-none-eabi-gcc" --version | head -1
for UNIT in firmware/transport/mt_usb_epoch.c tests/usb-epoch/arm_probe.c; do
  NAME=$(basename "$UNIT" .c)
  "$TC/bin/arm-none-eabi-gcc" $FLAGS -isystem "$TC/include/newlib" \
    -c "$UNIT" -o "$OUT/$NAME.o"
done
"$TC/bin/arm-none-eabi-size" "$OUT/mt_usb_epoch.o"
"$TC/bin/arm-none-eabi-nm" -S --size-sort --radix=d "$OUT/arm_probe.o"
"$TC/bin/arm-none-eabi-nm" -u "$OUT/mt_usb_epoch.o"
cat "$OUT/mt_usb_epoch.su"
sha256sum firmware/transport/mt_usb_epoch.c firmware/transport/mt_usb_epoch.h \
  "$OUT/mt_usb_epoch.o" "$OUT/arm_probe.o"
