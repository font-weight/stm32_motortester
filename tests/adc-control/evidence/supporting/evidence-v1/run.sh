#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SOURCES=(snapshot/firmware/{acquisition/acquisition,app/mt_capture_link,app/mt_control,app/mt_adc_control,platform/adc_timer_dma,protocol/mt_command,protocol/mt_session,protocol/mt_wire,transport/mt_tx}.c)
INCLUDES=(-I snapshot/firmware/platform -I snapshot/firmware/acquisition -I snapshot/firmware/app -I snapshot/firmware/protocol -I snapshot/firmware/transport)
set -x
cc --version
sha256sum -c snapshot.sha256
cc -std=c11 -Wall -Wextra -Werror -Wconversion -pedantic -O2 "${INCLUDES[@]}" test_independent.c "${SOURCES[@]}" -o independent
./independent
cc -std=c11 -Wall -Wextra -Werror -Wconversion -pedantic -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer "${INCLUDES[@]}" test_independent.c "${SOURCES[@]}" -o independent_sanitized
ASAN_OPTIONS=detect_leaks=0 ./independent_sanitized
