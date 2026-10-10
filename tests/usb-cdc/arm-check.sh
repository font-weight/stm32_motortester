#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
: "${TC:?ARM toolchain usr directory required}"
: "${USB_DEPS:?Verified USB dependency root required}"
OUT=${OUT:-/tmp/motortester-usb-cdc-arm}
mkdir -p "$OUT"
FLAGS='-std=c11 -Os -mcpu=cortex-m3 -mthumb -ffreestanding -fno-short-enums -fstack-usage -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror'
"$TC/bin/arm-none-eabi-gcc" --version | head -1
for UNIT in firmware/transport/mt_usb_cdc.c tests/usb-cdc/arm_probe.c; do
  NAME=$(basename "$UNIT" .c)
  "$TC/bin/arm-none-eabi-gcc" $FLAGS -DSTM32F103xB \
    -isystem "$TC/include/newlib" -isystem "$USB_DEPS/vendor/tinyusb/src" \
    -I "$USB_DEPS/evidence/config" -c "$UNIT" -o "$OUT/$NAME.o"
done
"$TC/bin/arm-none-eabi-size" "$OUT/mt_usb_cdc.o"
"$TC/bin/arm-none-eabi-nm" -S --size-sort --radix=d "$OUT/arm_probe.o"
"$TC/bin/arm-none-eabi-nm" -u "$OUT/mt_usb_cdc.o"
cat "$OUT/mt_usb_cdc.su"
sha256sum firmware/transport/mt_usb_cdc.c firmware/transport/mt_usb_cdc.h \
  "$USB_DEPS/evidence/config/tusb_config.h" "$OUT/mt_usb_cdc.o" "$OUT/arm_probe.o"
