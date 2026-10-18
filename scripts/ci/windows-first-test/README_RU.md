# Ограниченное локальное сохранение обычного Windows-кандидата

Черновик A, 17.10.2026. Область — только stdlib helper `retain.py`, его инертные
тесты и этот контракт. До независимого точного ревью это не принятый компонент.
Результат не является запуском Windows, приёмкой приложения или разрешением
выдать бинарный файл. Общий workflow/провайдер хранения добавляются отдельно.

Основание: [B, issue33#6232479247](https://github.com/font-weight/stm32_motortester/issues/33#issuecomment-6232479247).
Выбранное приложение: `dcafc5638bc6c3ddef6cba25a7a55016fe8e8933`, дерево
`61a9bd025081acec80997dff950d947eeceb3f4b`.
Builder, lock, collector и 23 runtime source тела сохранены от app244b.
Spec и новый selector PR179 задают отдельно рассмотренное исключение47
путей и сохранение7 зависимостей до замораживания. Полные исходные
serial/GUI/recording и archive/provenance проверки сохраняются; historical
fixed46 origin-observer неприменим к исключённым файлам и не выдаётся за PASS. PR174, AUX и PRELAUNCH
не являются зависимостями обычного retention. Их отдельные W1–W8 и
исторический source-ZIP pin не ослабляются этим helper.

## Назначение и граница доверия

После выполнения исходной сборки и всех применимых неизменённых проверок
доверенный caller готовит явный JSON-план фактически принятых файлов. Только
после отдельного точного ревью и разрешения применимого хранения caller может
передать реальные артефакты в helper. SHA-256 входного плана удостоверяет байты,
но не их происхождение, правомерность доступа/передачи или успешность тестов.
Сам helper проверяет ограниченную файловую границу, копирует, перечитывает и
сопоставляет байты. Он не исполняет EXE, Python из кандидата или firmware,
не импортирует приложение, не распаковывает ZIP и ничего не загружает в сеть.

Обязательные проверки caller:

- неизменённый builder в новой appdcafc563 и её актуальные source/unit checks;
- отдельная проверка actual47 отсутствующих/7 сохранённых путей в onedir и ZIP;
- исходные native serial/GUI/recording команды и строгие validators;
- исходная проверка всех членов candidate ZIP, manifest и source ZIP по
  фактическим Git blob/commit; новый обычный source.zip получает свой SHA,
  без навязывания исторической сериализации отдельного эксперимента;
- точный archive/provenance/native outcome и evaluated workflow/app pins;
- достаточность required source/notice accompaniment и применимый допуск
  к хранению/доступу/передаче;
- после helper: exit 0, terminal receipt и совпадающий OUTER_MANIFEST SHA,
  повторная проверка конкретного kit перед созданием внешнего ZIP;
- после разрешённой передачи: реальный upload, download и независимая
  проверка транспортного ZIP, kit ZIP, candidate ZIP и contained EXE.

`evidence_files` — явный allowlist caller. Helper проверяет наличие и байты
каждого указанного файла, но не умеет определять, полон ли смысловой набор.
В интеграции должны быть явно перечислены оригинальные archive/provenance/
native receipts, сведения о workflow/run, required source/notice support и
инструкция. Минимум одного evidence-файла не заменяет этот контракт caller.
Нельзя получить приёмку, передав один фиктивный JSON: инертные тесты специально
показывают лишь byte-retention, а не истинность его утверждений.

## Замороженный интерфейс v1

```text
python -I -S -B retain.py \
  --build-root ABS_BUILD_ROOT \
  --evidence-root ABS_EVIDENCE_ROOT \
  --output ABS_FRESH_OUTER_KIT \
  --expected-manifest ABS_EXPECTED_JSON \
  --expected-manifest-sha256 EXACT_SHA256 \
  --mode accepted
```

Все пути абсолютные локальные, без `.`/`..`, repeated separators, UNC/device
namespace, symlink/reparse и небезопасных Windows-компонентов. Корни build,
evidence и output взаимно не вложены, их предки уже существуют. Output обязан
отсутствовать, включая case-alias; существующее содержимое никогда не удаляется
и не перезаписывается. План не может находиться внутри output. Для минимального
межплатформенного контракта компоненты путей ASCII, относительный путь ≤180
символов/12 компонентов; абсолютный ≤512 символов/32 компонента.

Точные поля JSON (неизвестные поля и duplicate keys запрещены):

```json
{
  "schema_version": 1,
  "mode": "accepted",
  "application_commit": "dcafc5638bc6c3ddef6cba25a7a55016fe8e8933",
  "build_files": [
    {"path": "source.zip", "bytes": 123, "sha256": "64 lowercase hex characters"}
  ],
  "evidence_files": [
    {"path": "archive-verification.json", "bytes": 123, "sha256": "64 lowercase hex characters"}
  ],
  "executable": {"bytes": 123, "sha256": "64 lowercase hex characters"}
}
```

Это только иллюстрация структуры; она намеренно неполна и не проходит helper.
Полный рабочий инертный пример строится в `test_retain.py`.
`bytes` — положительный integer, bool запрещён; digest строго lowercase SHA-256.
Все заявленные размеры суммируются до чтения файлов. План ≤64 KiB.

В `accepted` build_files содержит ровно 11 строк:

1. `Motortester-windows-x64-candidate.zip`
2. `source.zip`
3. `frozen-window.png`
4. `frozen-window.png.smoke.json`
5. `frozen-serial-import.json`
6. `frozen-recording.json`
7. `frozen-recording.json.work/recording.mtrec`
8. `frozen-recording.json.work/recording-<32 lowercase hex>.mtspool`
9. `SHA256SUMS.txt`
10. `build.log`
11. `dist/Motortester/build-manifest.json`

Первые восемь — существующий неизменённый inner checksum contract. Helper
требует ровно эти восемь уникальных строк SHA256SUMS, сопоставляет все SHA с
планом и двумя именами/размерами/SHA в оригинальном recording receipt. Имена
из receipt не служат произвольными путями: допускаются только `recording.mtrec`
и строгий spool pattern; файл должен уже быть явно назван планом. Work directory
должна содержать ровно эти два листа. Подтверждаются status/phase/output/workdir,
несколько исходных candidate qualification полей и согласованность EXE SHA;
это дополнительный join, а не замена полным validators.

`executable` относится строго к `build-root/dist/Motortester/Motortester.exe`.
Его реальные байты проверяются отдельно; в kit он отдельно не копируется,
поскольку уже входит в candidate ZIP. Проверка соответствия ZIP member ↔ EXE
остаётся за исходным archive validator; helper не является ZIP parser.

## Результат и лимиты

Фиксированное размещение:

- каждый build file → `build/<тот же относительный путь>`;
- каждый evidence file → `evidence/<тот же относительный путь>`;
- исходные байты плана → `EXPECTED_MANIFEST.json`;
- новый независимый индекс/статусы → `OUTER_MANIFEST.json`.

Исходный SHA256SUMS.txt не изменяется и не расширяется. Внешний manifest
перечисляет скопированные строки с размерами/SHA и `bytes-match`; включает
отдельную EXE identity и digest первоначального плана. Он не хеширует сам себя:
собственный SHA/размер возвращается в terminal JSON stdout только при exit 0.
Не устанавливаются legal/physical/distribution-ready flags. Upload/download
остаётся `not-performed`, transport/outer ZIP identities — null. Отдельно
сохраняются отсутствие Windows 10/physical проверки и неустановленность
предыдущих validators, source completeness и независимых reviews.

Лимиты v1:

- максимум 64 скопированных build+evidence файлов, суммарно ≤512 MiB;
- candidate ZIP ≤200 MiB, source ZIP ≤20 MiB;
- PNG и каждый MTREC/spool ≤2 MiB, recording receipt ≤64 KiB;
- GUI/serial receipt и SHA256SUMS ≤1 MiB;
- build manifest ≤4 MiB, build.log ≤16 MiB;
- каждый evidence file ≤64 MiB;
- EXE check-only ≤64 MiB; он не добавляется к copy payload;
- expected/outer manifest каждый ≤64 KiB, поверх payload;
- чтение управлямых файлов ≤5×512 MiB + 3×64 MiB + 8×64 KiB,
  запись ≤512 MiB + 3×64 KiB, включая повторные проверки;
- перечисление любого затронутого каталога ≤4096 entries.

Размеры не расширяются автоматически. Если конкретный required support body
не укладывается, caller останавливается и получает пересмотренный контракт с
независимым review; нельзя молча исключить обязательные исходники/уведомления.
Распаковка, envelope ZIP и service transport ZIP требуют своих лимитов в caller.
Названия архива и provider здесь не выбираются; upload runner root по wildcard
не входит в этот контракт.

Перед output creation проверены весь план, все исходные файлы и необходимые
joins. Источники читаются повторно при copy, outputs перечитываются по SHA/size.
Lstat каждого предка, bounded casefold lookup, nlink=1, nofollow где доступен,
path/handle dev+ino+size+mtime/type guards и same-API ctime/mtime guards
проверяются до/после чтений и терминально. Windows cross-API ctime намеренно
не сравнивается. Финальный outer inventory запрещает дополнительные файлы.

Граница — доверенный локальный caller и замороженные во время копирования
каталоги. Это не race-free sandbox против произвольного параллельного
злоумышленника, который меняет pathname/данные между syscalls. Нельзя делить
корни с недоверенным concurrent writer. OS access-control и приостановка
producer принадлежат caller. Перед zip/upload caller снова проверяет identity.

## Отказы и отдельная диагностика

Любой отсутствующий, не соответствующий плану, redirected, hardlinked,
case-aliased, изменившийся или превышающий лимит вход приводит к exit 1.
До начала copy output не создаётся; поздний I/O failure может оставить
частичный каталог. Он сохраняется как непринятый и никогда не используется
для передачи. Повтор требует нового output; helper не удаляет старые результаты.
Даже корректный OUTER_MANIFEST в оставшемся каталоге не является terminal PASS:
нужен успешный завершённый вызов и соответствующий terminal manifest digest.

CLI stderr содержит bounded RETENTION_REJECTED code, без исходных путей/тел.
Автоматического копирования произвольных crash logs нет. Caller независимо
создаёт отдельный явный plan для доступных проверенных diagnostics и вызывает:

```text
python -I -S -B retain.py ... --mode diagnostics --output ABS_FRESH_DIAGNOSTIC_KIT
```

В плане mode=diagnostics, executable=null. Допускаются только явно названные
`.json`, `.txt`, `.log`, `.stdout`, `.stderr`; максимум 8 MiB на файл/16 MiB
суммарно, ≤64 строк. Требование 11 build files и recording joins не применяется.
Если важный log больше лимита, caller сохраняет отдельное явно помеченное
bounded excerpt, а не выдаёт усечённый файл за original. Если plan включает
отсутствующий diagnostic, вызов отвергается: caller должен явно записать
причину его отсутствия в своём outcome. Результат всегда
`INCOMPLETE_DIAGNOSTICS_ONLY`, не successful candidate. Расширение файла не
доказывает несекретность содержимого: caller контролирует сам список/данные и
отдельное разрешение передавать diagnostic kit.

## Локальные проверки

```text
python -I -S -B test_retain.py
python -I -S -B -O test_retain.py
```

Только инертные текстовые fixture bytes, включая файлы с .zip/.exe/.mtrec
расширениями. Они не являются пригодными пакетами/PE/записями. Проверяются
byte retention и ошибки, включая traversal/alias/hardlink/symlink/reparse,
искажения планов/checksum/receipts, поздние input/output mutations, extra output,
неперезапись, bounds, диагностическую отдельность и CLI. Reparse и различия
path/handle timestamps моделируются; это не Windows filesystem runtime test.
Отдельный критик обязан привязать вывод к exact hashes и повторить проверки.

Не выполнено здесь: настоящая сборка/запуск Windows, firmware execution,
проверка реального board, review лицензий, upload/download, передача бинарников.
Следующий шаг: точное независимое review helper и caller/transport integration,
после применимых разрешений — одна bounded сборка с заранее разрешённым storage.

## Внешний ZIP: отдельная фаза после успешного retention

`zip_kit.py` — отдельный A-owned упаковщик, не часть прежнего inner checksum.
Он связывает тело retain.py SHA-256 до импорта его path/inventory primitives.
Caller обязан получить exit0 исходного retention, сохранить его terminal и
передать именно оттуда SHA-256 OUTER_MANIFEST.json; найденный на диске manifest
сам по себе не доказывает успешный terminal. Параметр hash проверяет байты,
не аутентифицирует caller и не создаёт право на распространение.

```text
python -I -S -B zip_kit.py --kit ABS_KIT --output ABS_NEW_ZIP \
  --outer-manifest-sha256 EXACT_TERMINAL_HASH --mode accepted
```

Предполагаются стабильные кооперативные локальные каталоги под контролем
caller. Проверяются наблюдаемые изменения/подмены, ссылки, reparse и aliases;
это не sandbox против злонамеренного конкурентного владельца файлов.
Kit не меняется; output расположен вне kit, создаётся исключительно новым
файлом. Существующий ZIP не перезаписывается. Сбой оставляет частичный ZIP
как свидетельство; caller не должен выдавать его как успешный результат.

В ZIP входят только exact OUTER_MANIFEST inventory, EXPECTED_MANIFEST.json
и сам OUTER_MANIFEST.json, без wildcard. Все исходные размеры/hash проверяются
до записи, при записи и после чтения каждого ZIP member. Используются
ZIP_STORED, сортировка имён, фиксированная metadata date и regular0644;
это транспортная детерминированность, не дата создания исходных материалов.
Максимум66 членов, payload не более512 MiB (diagnostics16 MiB), плюс два
manifest по64 KiB, итоговый ZIP не более514 MiB. EXE не дублируется вне
исходного candidate ZIP. Дополнительное сжатие и ZIP64 не используются.

Эта фаза имеет свой бюджет, не расходует остаток retainer: не более трёх
проходов payload/manifest плюс один проход итогового ZIP; с metadata и
контрольными чтениями консервативная граница2052 MiB чтения и514 MiB записи.
Потоковые блоки64 KiB; весь payload в память не загружается. Bound следует
из фиксированных max files/bytes, конечных проходов без повторов и контроля
роста/размера в каждом цикле; это не общий счётчик всех системных обращений.
Чтение закреплённого helper и Python stdlib/runtime не выдаётся за учёт ОС.

Успех — только exit0 и terminal KIT_ZIP_BYTES_VERIFIED_ONLY с actual ZIP
размером/hash. Diagnostics mode сохраняет mode=diagnostics; даже успешная
упаковка не превращает INCOMPLETE_DIAGNOSTICS_ONLY в принятый build.
Upload/download, действительность прежних validator results, Windows10,
physical test и distribution permission упаковщик не подтверждает.
Caller проверяет terminal и exact ZIP перед отдельной разрешённой передачей.

## Привязка к PR179

Это явный successor исходного PR178, а не перенос его оценки. Изменены
только application pin, соответствующий hash/размер retainer в ZIP helper
и это описание consumer условий. Проверки восьми checksum payloads,
структур receipt, no-clobber и bounded IO не ослаблены. Caller и workflow
должны дополнительно закрепить spec/selector и новую source inventory;
сам helper их не проверяет. До нового независимого review — draft.
