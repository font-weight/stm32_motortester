# STR1: ограниченная HOST-репетиция prospective policy

## Результат и текущая граница

Это source-only подготовка: чистая проверка выбора, один явно синтетический
запуск принятого collector, отдельный bounded export и строгая проверка
quiescence-qualified incomplete evidence. Физический entry
`collect_physical(selection)` всегда отказывает с `NOT_FLASH_APPROVED` до
создания backend, worker, transport, handle или файла. Нет CLI, discovery,
COM scan, DTR операции, flash, RAM-чтения или упаковки.

Различаются три результата:

1. `validate_selection` проверяет структуру и точные декларации выбора.
2. `evaluate_evidence` проверяет связанное runtime-свидетельство конкретной
   синтетической попытки. Положительный результат имеет scope
   `synthetic-rehearsal`, `physical_admitted=False`, `clean_completion=False`.
3. `physical_admission` всегда возвращает `admitted=False`: в этой версии
   отсутствует отдельно проверенный operational route и фактическое решение
   A/владельца. Boolean, файл или caller attestation не открывают этот gate.

Синтетический PASS не допускает реальную плату, не разрешает RAM retrieval и не
доказывает физическое прекращение преобразований. Двухчтительная RAM-процедура
и decoder в эту итерацию не входят. Старый Windows source результат PR211 и
ранее переданный kit не квалифицируют новые байты; package review не затронут.

## Точные зависимости

Непосредственное основание: [PR211](https://github.com/font-weight/stm32_motortester/pull/211),
commit `4a7ddfc074ce4685f7a15536680a6777a06697ff`, tree
`3ce428067727165e4f5a98418d265443d548bd45`. Это идентичность reviewed API baseline,
а не хеш новой исполняемой версии. Конкретные новые файлы наблюдаются отдельно
при export: SHA-256 source map включает invocation module и его tests.
Source map является наблюдением файлов в момент export, не аутентификацией
Python-кода, backend или исполняемого firmware.

Политика A: [budgets 6274242926](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6274242926),
[коррекция 6274280027](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6274280027),
[окончательный predicate 6274335866](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6274335866).
Последний заменяет прежнее требование clean completion. Заявленные A hashes:
policy `1859e24afe0efd6f8e98c0d3b4ca598b3124d831c3b5a84b56184d2178f8da80`,
review `d3fb75fb6135f9dc7d0cd8e01bcf8a7e30dc01a8f2f084ad957600f593fa824e`.
Они сохраняются как атрибутированные привязки; отсутствующие policy bytes не
объявляются независимо аутентифицированными.

Образ: `str1`, `completed-dma-v1`, revision
`0.2-draft-dma-sequence-20261019`; compiled-local source
`31b93659164dec376bea5b973b597196c6a4a0ad`; BIN SHA-256
`90fee8a085d1e561cc6f5c28c26db26a43c1e57145ae41314bf70e9422c7b904`;
HELLO build `0x53545231`; CAPS `(1,1,0,12,50000,1,128,1)` в порядке
config, profile, channel, ADC bits, Fs numerator/denominator, max block, flags.
Public-equivalent `9ec9430fe83aa5e8ad9c504f9627e185f3285c36` не подменяет
compiled-local source. ELF-derived BIN digest не является восстановлением
исходного BIN. Совпадение HELLO/CAPS не удостоверяет прошитый образ.

Меняются только пять HOST-файлов: новый invocation module/test/guide и узкие
расширения diagnostic/provenance. Controller, bridge, endpoint, wire, session,
serial, journal schema, firmware, CI и лицензии сохранены. Используются прежние
pre-START build/CAPS guards и receiver ownership; второго wire parser нет.

## Входы и side-effect-free подготовка

`Selection` содержит обязательные invocation ID (32 lowercase hex), отдельные
абсолютные пути нового sample journal и report, `Association`, точные `Bindings`,
фиксированный stage и export bound. Canonical ASCII JSON выбора ограничен
2048 bytes, чтобы он помещался вместе с source map в прежний metadata limit. Нормализации в другой target нет. Пустые,
относительные, совпадающие output paths и traversal `..` отклоняются. Наличие
файла заранее не проверяется; создание остаётся exclusive и не перезаписывает
старые или частично записанные байты.

`Association` содержит буквальный native Windows COM (`COM7`, без scan/default),
USB serial `AH20261017001`, отдельный board-instance ID и Windows-PC ID,
VID/PID `1209:0001`, bcdDevice `0100`, require-DTR `1`. Поле COM имеет только
синтаксическую границу до пяти цифр; это не утверждение о Windows enumeration.
Объект целиком привязан к выбранным invocation/output/policy. Это caller
association, а не доказательство enumeration, USB uniqueness, DTR behavior,
состояния платы или полномочий A/владельца. Private-test VID/PID не является
идентификатором экземпляра. Expected данные не выдаются за observed.

`RehearsalInvocation` требует `fake_backend_factory`, `fixture_name` и
`EventConfig`. Настройки синтетического backend фиксированы:
`FAKE-STR1`, baudrate 230400, read_timeout 0, write_timeout 0.025 s. Это HOST
настройки тестового вызова, не аппаратная гарантия и не physical COM route.
Prospective COM из association не передаётся fake transport. Произвольная
injected Python-фабрика остаётся доверенным caller contract, не sandbox;
допустимые тесты используют прочитанный `FakeFactory/EncodedPeer`. Real factory
никогда не подставляется автоматически.

Import, validators и constructors не открывают порты/файлы и не запускают
workers. Старые `DiagnosticRun`, generic/syn1 и fake carrier сохраняют смысл.
Новый optional `invocation_selection` в `DiagnosticRun` допускается только
с точной STR1 fake declaration, фиксированными settings/limits и selected paths.
Он не открывает physical mode.

## Один вызов, budgets и cancellation

`collect()` выполняет один Connect, один Record, Stop/drain/close и один export.
Повторный collect запрещён. Нет reconnect, reset, retry START или новой попытки.

- Connect: 2000 ms от исходного connect intent.
- Run: 2000 ms от возврата публичного допущенного Record, включая negotiation
  и preparation. Это не две секунды отсчётов и не START_ACK deadline.
- Drain: 4500 ms от первого Stop intent.
- Close: 1000 ms от первого close intent.
- Export owner: отдельное наблюдение 1000 ms от export intent. Оно не продлевает
  исходные drain/close origins и не является новым разрешением питания платы.
  Исходный deadline повторно проверяется после write intent, join, получения
  owned proof, status и перед итоговым решением. Равенство deadline уже означает
  expiry. Поздние fsync/readback/settlement могут оставаться фактически true,
  но результат этой invocation фиксируется как ineligible/export-unresolved;
  позднее чтение receipt не превращает его в своевременный успех. Последующее
  чистое вычисление predicate не считается report I/O и не меняет время уже
  полученных owner/proof/status observations.

`request_stop()` и `close()` используют принятые reserved controller paths.
Повторные вызовы не перезапускают origins. Не допущенный Record остаётся с
`record_intent_return_ns=None`; отмена до Connect не создаёт backend или journal.
Новый invocation консервативно отказывает в timely observation на равенстве
connect/drain/close deadline. Старые generic diagnostic calls сохраняют прежние
правила. Принятые endpoint deadlines и rejection поздних callbacks не меняются.
Timeout означает конечное наблюдение; Python/OS callback не может быть насильно
прерван этим кодом.

Квоты сохранены: raw trace 4 MiB/16384 callbacks; 128 snapshots; report 32 MiB;
sample journal 2 MiB/16384 blocks и прежние session limits. Ошибка и partial files
сохраняются. После возврата исходный owner остаётся доступен через `snapshot()`
и конечный `join(timeout)`; поздний settlement не переписывает frozen report или
уже возвращённый `InvocationResult`. У wrapper нет автоматического cleanup retry.

## Runtime export proof

`DiagnosticRun.owned_export_proof()` возвращает `None`, пока export owner не
завершился или не создал проверенное свидетельство. `OwnedExportProof` связывает:

- ссылку на точный исходный frozen capture и SHA-256 его bounded содержимого;
- отдельную экспортированную копию с export-time source hashes;
- фактические byte count/SHA-256, полученные owner при записи и совпавшие с readback;
- фактически вернувшийся fsync, readback, complete receipt и owner settlement.

Чужой run, равная реконструированная копия capture, изменённое содержимое и
свободно переданные `ExportStatus` success booleans не заменяют это свидетельство.
Проверка не открывает файл и не ожидает I/O. Это in-process защита от случайной
подстановки, не криптографическая защита от произвольного Python. Fsync-факт нельзя
восстановить из footer/hash. Успех относится к наблюдённому readback; последующие
изменения файла вне owner не становятся частью frozen proof.

Исторический fsync-return остаётся отдельным фактом, даже если последующий
readback не прошёл. Sample durability, trace completeness, fsync/readback и
owner settlement не объединяются в одно произвольное слово «успех».

## Строгий predicate и outcomes

Все требуемые keys/type проверяются явно: bool не считается integer. Точная
версия bindings, image, policy и metadata обязательна. Требуются accepted END
reason 0/flags 3/produced_end None; started source; совпавшие nonce/generation,
terminal ID, Stop request/action и prefixes; ACK quiescence true. Generated end
и loss нормализованного terminal остаются None.

Единственное исключение: связанный endpoint `device_0`, normalized
`source_error`/source_code 0 с operation/request_id None; session first_failure
точно совпадает. Reason 2/device_2/source_code 2 не разрешён, даже при полном
экспорте. Recovery, late fault, pending START/STOP, status/run/trace error,
неполная finalization, clean_completion true, неопределённые workers/serial,
незакрытый storage/mailbox или ошибка serial исключают eligible result.
Callback trace не может иметь pending/unknown/invalid_rx/exception/dropped/gap;
known zero/short writes остаются точными наблюдениями.

Результаты `InvocationResult.outcome`:

- `quiescence-qualified-incomplete`: весь prospective predicate выполнен на
  синтетике; physical admission и clean completion остаются false.
- `ineligible`: недостающее/несовместимое evidence; краткая причина в decision.
- `export-unresolved`: owner не settled за отдельные 1000 ms. Frozen result не
  улучшается автоматически после позднего завершения.
- `export-failed`: export owner сохранил первую ошибку, включая exclusive create.

При unconfirmed Stop/quiescence, disconnect или unknown transport RAM не
предлагается. Прежнее timeout/raw-RAM исключение не действует. Software cleanup
не доказывает физическое quiescence. Дальнейшее физическое handling принадлежит A.

## Отдельная prospective physical schema

`validate_physical_carrier` принимает только format
`motortester-str1-device-declaration-1`, mode `physical-declaration`, origin
`device`, exact selected declaration и раздельные expected/observed negotiation.
Observed может отсутствовать; декларация не превращает отсутствие в observation.
Это read-only schema, а не operational physical report. Все соответствующие
тестовые dictionaries и identities явно fabricated.

Журнальный carrier `physical:str1:…` отличен от `rehearsal:str1:…`; fresh reader
отклоняет смешанные/частичные/неизвестные tuples. Старый reader не знает новый
physical marker и должен отказаться от интерпретации благодаря уже имеющимся
DMA markers. Чтение carrier не разрешает execution. Домен охватывает completed
DMA records, не ADC_DR overwrites, незавершённые conversion или missed triggers.
Индекс / nominal Fs не означает калиброванное физическое время.

## Воспроизведение source-only проверок

Python 3.12; стандартная библиотека, существующие HOST modules и test fixtures.
Focused invocation/diagnostic/controller/bridge/provenance/serial tests не требуют
Qt или real backend. Из корня исходников:

```text
PYTHONPATH=host:host/tests python -m unittest discover -s host/tests -p test_stream_diagnostic_invocation.py -v
PYTHONPATH=host:host/tests python -O -m unittest discover -s host/tests -p test_stream_diagnostic_invocation.py -v
PYTHONPATH=host:host/tests python -m unittest discover -s host/tests -p test_stream_diagnostic.py -v
```

Дополнительно сохраняются normal/optimized существующие stream controller,
bridge, serial, provenance, endpoint/session/recording/wire controls. Они проверяют
программу на синтетике, не native Windows deployment, EXE, устройство, USB/ADC/DMA
или измерение. Приёмка требует независимого Critic-B ≥9.5 для конкретного freeze.
