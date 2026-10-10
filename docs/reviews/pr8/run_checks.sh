#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
cc --version
python3 --version
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O2 firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c tests/protocol/test_mt_stream.c -o review-artifacts/test_mt_stream
./review-artifacts/test_mt_stream
printf '%s\n' 'Strict stream test: PASS'
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O2 firmware/protocol/mt_command.c tests/protocol/test_mt_command.c -o review-artifacts/test_mt_command
./review-artifacts/test_mt_command
printf '%s\n' 'Strict command test: PASS'
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c tests/protocol/test_mt_stream.c -o review-artifacts/test_mt_stream_san
ASAN_OPTIONS=detect_leaks=0 ./review-artifacts/test_mt_stream_san
printf '%s\n' 'ASan+UBSan stream test: PASS (LSan disabled due ptrace runtime limitation)'
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined firmware/protocol/mt_command.c tests/protocol/test_mt_command.c -o review-artifacts/test_mt_command_san
ASAN_OPTIONS=detect_leaks=0 ./review-artifacts/test_mt_command_san
printf '%s\n' 'ASan+UBSan command test: PASS (LSan disabled due ptrace runtime limitation)'
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -shared -fPIC -O2 firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c firmware/protocol/mt_command.c -o review-artifacts/libmt.so
python3 review-artifacts/independent_checks.py
