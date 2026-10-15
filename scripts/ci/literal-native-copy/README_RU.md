# Фиксированное сравнение 46 native-файлов — новый черновик

Эта реализация написана заново 15.10.2026 после недоступности прежних
неопубликованных исходников. Старые оценки не переносятся. Область задачи:
helper, синтетические переносимые проверки и документация. Реального
Windows-запуска и интеграции в workflow пока нет. Это не приёмка приложения,
не юридическое заключение и не выдача разрешения распространять DLL.

## Входы и происхождение

- Опубликованный observer PR173: `scripts/ci/observe_native_origins.py`,
  commit `9a7bc49c48daef9ea26c1b638ccea0f22e021158`, SHA-256
  `5b8ca95c9e9ea14f0a06d2edc8735c1bef6bc53d51985bfa0ae75adc37ee922c`.
  Он не изменяется. Из его **уже прочитанных и проверенных байтов** исполняются
  определения функций/классов и стандартные импорты; `main()` не вызывается.
  Используются его готовые JSON/COLLECT/path parsers, не новый общий parser.
- Baseline PR173: `scripts/ci/run17-native-baseline.json`, SHA-256
  `fb864aa52ccf4db813478a41ba256b9f6bc64c711403884d9ef0b8603926a8df`.
- `run18-reference.json`: точные восстановленные 69 692 байта наблюдения из
  [job 115879974929](https://github.com/font-weight/stm32_motortester/actions/runs/38572185383/job/115879974929),
  SHA-256 `2cd3be37cbc9737927f6832a5366fc29b835bd5fd9a4f6ea76b288a1d1c1dc61`.
  Единственный BASE64-блок заново декодирован и проверен. Сырые origin-байты
  этим восстановлением **не получены**: старые строки говорят
  `outside_observer_allowlist`, а supplying_package=null.
- Свежие `native-observation.json` и `COLLECT-00.toc` конкретной новой сборки,
  каталог её bundle и явные run_id/attempt/workflow_sha от вызывающей стороны.
  Приложение/inspector/recipe/collector/lock закреплены контекстом исходного
  observer. Текущие строки должны совпасть с reference и baseline по всем
  46 path/size/hash и literal origin; actual TOC должен иметь те же BINARY
  declarations и хеш из свежего observer receipt. В onedir COLLECT destination
  записан без `_internal/`: сопоставление выполняется как `_internal/` +
  destination = bundle path, ровно как в опубликованном observer.

Ровно 44 origin-пути имеют фиксированный префикс
`C:\hostedtoolcache\windows\Java_Temurin-Hotspot_jdk\8.0.504-1\x64\bin`,
два — `C:\Program Files\MySQL\MySQL Server 8.0\bin`.
Они берутся из hash-pinned reference, не из поиска по диску. Общий размер
46 bundle bodies в reference — 10 850 576 байт. Никакая строка пути или
PE-метаданные не удостоверяют поставщика/пакет/лицензию.

## Порядок и бюджет

До открытия какого-либо origin leaf проходят все metadata joins, проверка
полного плана и **чтение/size/hash каждого из всех 46 реальных bundle bodies**.
Плохая первая или последняя строка останавливает работу с нулём origin opens.
Только после этого читаются буквальные origin-пути и выполняется прямое
сравнение bytes с сохранёнными bundle bytes. Native-библиотеки не загружаются,
не импортируются и не исполняются; отдельные копии DLL не создаются.

Новый явный план состоит из **99 уникальных управляемых leaf-путей**:
6 metadata (сам helper, опубликованный observer helper, reference, baseline,
свежий receipt, свежий TOC) + 46 bundle + 46 origin + 1 output/readback.
Повторное открытие не создаёт новый уникальный путь, но все возвращённые
байты каждого чтения/повторного чтения учитываются заново. Отдельно учитываются
фактически записанные байты отчёта; лимит применяется к сумме read_bytes и
write_bytes, то есть строже прежнего read-only лимита.

Предел managed I/O — 123 MiB. Перед открытием проверяются размер и остаток
бюджета, чтение выполняется блоками до 64 KiB. Каждый native leaf ограничен
16 MiB; metadata ограничены явно в коде (64/128/256 KiB либо 2 MiB).
Output максимум 2 MiB. Первый readback, все final input rereads и второй
final output readback входят в бюджет. План не перечисляет каталоги диска.
5 MiB остаются зарезервированными для внешнего auxiliary/retainer кода;
этот helper не тратит их и не удостоверяет его реализацию. Это **не** общий
лимит памяти Python/ОС, не wall-clock timeout и не учёт чтений интерпретатора,
stdlib imports, Git extraction или будущего вызывающего retainer.

Безопасность путей: абсолютная каноническая запись, отсутствие ADS/UNC/device
alias, совпадающих casefold-путей, symlink/reparse и неправильных типов.
Проверяются предки, общие dev/ino/size, отдельные path-path и handle-handle
mtime/ctime (без несовместимого Windows cross-API ctime) и повторные
хеши. Output вне bundle, создаётся исключительно через O_EXCL; существующее
не перезаписывается. Предполагается стабильная кооперативная файловая система,
это не sandbox против одновременно враждебной подмены каталогов. Не заявляется
независимость одинаковых hardlinked bytes или аутентичность CI по строке run_id.

## Результат и ошибки

Origin с другим размером не открывается; отсутствующий/отличающийся/ошибочный
источник получает отдельный статус. Любой такой результат даёт `incomplete`
и ненулевой exit; никакого supplier/source/legal/replacement clearance нет
даже при совпадении всех bytes. Ошибка bundle/metadata/pin останавливает
процесс до отчёта и до origin phase. Поздняя ошибка оставляет созданный файл
для диагностики, не удаляет/не заменяет его и не выдаёт успешную квитанцию.

`output` всегда имеет статус **UNCOMMITTED_LITERAL_OBSERVATION**. После его
двух readback и final input rechecks CLI выводит малую JSON-квитанцию с точным
хешем/размером отчёта, счётчиками и результатом. Файл отчёта сам по себе никогда
не является подтверждением успеха: вызывающая сторона должна сохранить
exit/stdout/stderr и проверить связанный файл по хешу квитанции. Её I/O и
допуск должны быть отдельно ограничены при интеграции.

`successfully_observed_inputs_unchanged` относится к успешно прочитанным
или проверенным как отсутствующие входам. Размерные/доступные ошибки не
становятся «подтверждённо неизменными»; такие origin-строки сохраняют incomplete.
Reference устанавливает историческую baseline, свежий receipt — предыдущие
декларации. Helper сам заново проверяет все 46 bundle bodies и actual TOC,
но не перечитывает manifest/provenance/archive, чьи хеши находятся внутри
предыдущего receipt, не доказывает его внешнее происхождение и не проверяет
весь пакет приложения. Это отдельные обязанности workflow/PRELAUNCH.

## Вызов (только после отдельного допуска будущего Windows-run)

CLI требует Windows x64 CPython 3.13.16. Все пути канонические, абсолютные;
helper и baseline — ровно указанные immutable published bytes. Пример ниже
показывает параметры, не запускается автоматически и не даёт разрешения CI:

```text
python -I -B literal_copy.py --bundle ABS_BUNDLE --helper ABS_PINNED_OBSERVER
  --reference ABS_RUN18_REFERENCE --baseline ABS_RUN17_BASELINE
  --current ABS_CURRENT_OBSERVATION --toc ABS_CURRENT_COLLECT
  --output ABS_FRESH_REPORT --run-id ACTUAL_RUN --attempt ACTUAL_ATTEMPT
  --workflow-sha ACTUAL_EVALUATED_WORKFLOW
```

ImageVersion может измениться и записывается как свежая декларация; фиксированные
origin-пути не расширяются и не заменяются даже если файла на новой VM нет.
CLI не доверяет произвольной подмене mapping. Функции внутри модуля предназначены
для реализации/тестов; Python-код вызывающего не является враждебной границей.

## Проверки

Установите `LITERAL46_TEST_HELPER` в путь к точному опубликованному observer;
это только выбор источника для тестов, не переменная production CLI.
Рядом с выбранным observer должен находиться точный опубликованный
`run17-native-baseline.json` с указанным выше хешем: data-only regression
читает его оттуда. Эти два неизменённых файла берутся из PR173.

```sh
LITERAL46_TEST_HELPER=/absolute/observe_native_origins.py python3 -B test_literal_copy.py
LITERAL46_TEST_HELPER=/absolute/observe_native_origins.py python3 -O -B test_literal_copy.py
```

Положительные тесты создают 46 инертных маленьких файлов, временно заменяя
hash constants и resolver внутри тестового процесса; они **не** называются
реальными run18 DLL или положительным Windows CLI-run. При этом production
helper импортируется неизменённым, а публичный observer проверяется по своему
настоящему хешу. Отдельно проверяется отказ production CLI на не-Windows.
Реальное исполнение на Windows, wire/GUI/recording, PRELAUNCH и весь wrapper
остаются вне этого узкого статического/синтетического результата.

## Исправление первой итерации

Независимый критик нашёл P1: первая версия ожидала в COLLECT destination
лишний `_internal/`, а авторский fixture повторял ту же ошибку. Вторая
редакция использует фактическую onedir-грамматику опубликованного observer.
Fixture исправлен, добавлены отдельные проверки отказа ошибочной prefixed
формы и успешного data-only join настоящих pinned reference/baseline.
Прежняя версия и первоначальные журналы сохранены отдельно; её успешные
синтетические тесты не объявляются свидетельством совместимости.

В том же независимом цикле выявлены и исправлены отсутствие проверки
reparse-атрибутов у opened/after fstat handles (P1) и принятие boolean вместо
целочисленной schema_version (P2). Добавлены отдельные регрессии обоих handle
состояний и boolean schema. Эти смоделированные проверки не называются
наблюдением реальной Windows файловой системы.

Ещё два focused controls выявили Windows-разницу ctime между lstat/fstat
и отсутствие ранней проверки write handle. Исправлено: shared identity
содержит только dev/ino/size, timestamp сравниваются только внутри одного
метода; output проверяется по opened/finished handle и final path. Ошибочный
opened handle отклоняется до первого записанного байта, частичный результат
сохраняется. Смоделированы разные path/handle ctime и оба output reparse
состояния; это не заменяет последующую реальную Windows-проверку.

## Исправления R4 после опубликованного ревью

Сравнение path-stat и handle-stat теперь включает mtime, сохраняя исключение
для несовместимой Windows ctime между API. Зарезервированные имена COM/LPT
с надстрочными цифрами и имена консольных устройств отвергаются. Добавлены
регрессионные тесты; это модель путей, не наблюдение Windows-устройства.
Обязательные внешние joins manifest/provenance/archive и сохранение исходной
квитанции при раннем отказе остаются задачей проверяемой обвязки L1/L5.
Успех сравнения файлов не объявляет эти условия выполненными.
