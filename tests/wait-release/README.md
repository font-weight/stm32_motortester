# Native-проверки WAIT_RELEASE и Stop

Пакет воспроизводит неизменённые WAIT_RELEASE/Stop-проверки предложения PR79
на исходниках ADC-ветки PR95, commit
`b607392cb4f1cc19918e2795359df4680b645643`. Отдельные ADC fault-сценарии
этот пакет не проверяет; прежний PR82 остаётся историческим повтором. Это синтетические
данные/fake hardware, не принятие общего A/B-контракта и не тест физической платы.
ARM toolchain, TinyUSB, host codec, Git history и соседние внешние каталоги не нужны.

Из любого рабочего каталога, указав существующий checkout и новый абсолютный
output вне него:

```sh
python3 -I -B /absolute/checkout/tests/wait-release/reproduce.py \
  --out /absolute/existing-parent/new-run
```

Нужны POSIX/Linux, Python 3.9+ standard library и native GCC с C11, libc,
ASan и UBSan. Root проверил GCC 14.2.0/Python 3.12.14. Windows/macOS-переносимость
не заявлена. Sanitizer-режим обязателен: при отсутствии compiler/runtime запуск
завершается отказом, strict-only fallback отсутствует. Утечки не проверяются
(`detect_leaks=0`); `UBSAN_OPTIONS=halt_on_error=1` усиливает остановку на UB.

## Что должно пройти

- 25 фиксированных WAIT_RELEASE-сценариев в native и sanitizer-сборках;
- прежние 103 Stop-сценария / 6926 C-проверок в каждой сборке;
- Auto/Normal/Single: сохранность 2048 raw samples, late ACK без повторного старта;
- ровно 78 wire snapshots, попарно одинаковые между режимами;
- независимый Python decode COBS/header/CRC/sequence/sample CRC;
- шесть известных частичных хвостов отмечены как неполные, не как полные frames;
- неизменность закреплённых исходных/тестовых входов до и после запуска.

Итого 131 сценарий × 2; это не количество независимых физических измерений.
Оба C oracle используют прежнюю characterization с переименованным main.
Её старый fatal-recovery сценарий не является положительным тестом нового
протокола. Исторические отрицательные R8/R1 прогоны сохранены отдельно и не
подменяют default positive acceptance этого пакета.

## Защита от ложного PASS

Entry point закрепляет SHA manifest и всех 18 production C/H, C oracle,
decoder и aggregate helper. Собственные bytes orchestration также проверяются
на изменение во время run, но не включены в manifest во избежание циклического
hash. Полная identity пакета связывается с Git tree/commit при публикации.
Manifest не разрешает тестировать произвольную изменённую прошивку как ту же версию.

Запуск с Python `-O`/`-OO` отвергается. Helpers вызываются изолированно без
optimization; их assertions сохраняются. Имена всех 25 случаев и каждого
snapshot проверяются явно: одинаково пустые каталоги не дают PASS. Любая ошибка
сборки/процесса/сравнения/decoder делает общий exit ненулевым. Старый collector
не используется как самостоятельный критерий успеха.

Output не должен существовать; parent должен быть каноническим существующим
каталогом. Пути с пробелами передаются списками argv, shell не используется.
В output сохраняются команды, cwd/environment, logs и RESULTS.json; при отказе
после создания output остаётся FAILED receipt. Preflight-отказ не создаёт output.
Неудачные каталоги не очищаются и не переиспользуются.

Это не полностью hermetic build: системные headers/libc/sanitizer runtime и
host Python не поставляются. Compiler hash/version записываются. Проверки
предполагают стабильную совместно используемую файловую систему и доверенные
исполняемые инструменты; это не sandbox против злонамеренной подмены.

`validate_run.py`, `decode_wire.py` и исторический `stop/run.sh` не являются
публичным aggregate entry point. Прямой исторический Bash-маршрут требует
абсолютных SOURCE_ROOT/BUILD_DIR; его default snapshot сюда не поставляется.
Используйте команду выше. Происхождение и точные адаптации — в PROVENANCE.md
и package-manifest.json. Generated executables/logs/wire bytes в Git не включены.
