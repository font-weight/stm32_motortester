# Независимая проверка HOST-002

Host commit `c89c12df984390b34785eba81665abd6418658e2`.
Native reference `8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7`:
byte codec идентичен закреплённому 124b3f32 по хешам, плюс mt_stream.

Для повторения скопировать `check_host_wire.py` в отдельный writable каталог:

```sh
PYTHONDONTWRITEBYTECODE=1 python check_host_wire.py /path/to/exact/host/archive /path/to/git/repository
```

Скрипт только читает pinned C-файлы через `git show`, собирает native shared
library рядом с собой и сравнивает три независимо полученных кодирования
(Python production, C production, отдельный bitwise CRC/COBS reference).
Также сопоставляет Python chunk stream с C byte stream после явной агрегации
по правилам host API. Сеанс/команды/окно остаются вне этой проверки.

Результаты: 2052 положительных кодирования, 50736 отказов decode;
6242 stream вызова / 6461517 байтов, выходы/диагностика/состояние совпадают.
GCC 14.2.0, Python 3.12.14, Linux x86-64, seed 20261010.

`full-tests.log`: 141 product/conformance/baseline тест, exit 0.
`c-crosscheck.json`: повтор опубликованного отдельного compare_c.py,
522 положительных, 49 отрицательных, 2 негативных encode, 2 CRC check.
`optimized-rejected.log`: ожидаемый exit 1 при `python -O`, assert нельзя отключить.
Команды и границы подробнее в HOST-002-review.md.
