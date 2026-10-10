# Независимое доказательство RX guard

Проверенный commit: `b6ae8025baf1a47e8173997029d2d8b858ada74c`, PR #14.
Источник получить через `git archive` этого SHA в отдельную папку; рабочую ветку не менять.

```sh
python host/reviews/evidence/session-b6ae/check_session.py /path/to/exact/archive
```

Скрипт самостоятельно собирает shared library из `mt_command.c` и `mt_session.c`
и сравнивает C API с независимой Python-моделью правил. GCC 14.2.0,
Python 3.12.14, Linux x86-64. Результат: 108814 сравнений, PASS.
`sizeof` относится только к этому ABI. Скрипт пишет `session.so` рядом с собой;
для повторения можно скопировать его в `/tmp`.

Авторские session-тесты также независимо пересобраны:

```sh
cc -std=c11 -O2 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror \
  firmware/protocol/mt_command.c firmware/protocol/mt_session.c \
  tests/protocol/test_mt_session.c -o /tmp/test_session
/tmp/test_session
cc -std=c11 -g -O1 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  firmware/protocol/mt_command.c firmware/protocol/mt_session.c \
  tests/protocol/test_mt_session.c -o /tmp/test_session_san
ASAN_OPTIONS=detect_leaks=0 /tmp/test_session_san
```

`native.log`/`sanitizer.log` — их stdout; оба exit 0. Утечки не проверялись.
Это не исполнение TX/Stop-диспетчера, USB или аппаратуры.
