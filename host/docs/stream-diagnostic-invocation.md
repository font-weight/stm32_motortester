# STR1: один явный диагностический вызов, неактивный по умолчанию

## Результат и текущая граница

Это source-only подготовка полного будущего operational route. Принятый
policy-only baseline PR213 сохранён в истории; новый путь использует тот же
collector, controller, Stop/close и owned export. Import/default/help ничего
не запускают. Фактический образ остаётся `NOT_FLASH_APPROVED`; реальные final
deployment/owner inputs отсутствуют. Все исполняемые source-тесты используют
обязательные injected fakes. Порты, плата, DTR устройства, flash, RAM и package
в рамках этих проверок не затрагиваются.

Различаются три результата:

1. `validate_selection` проверяет структуру и точные декларации выбора.
2. `evaluate_evidence` проверяет связанное runtime-свидетельство конкретной
   синтетической попытки. Положительный результат имеет scope
   `synthetic-rehearsal`, `physical_admitted=False`, `clean_completion=False`.
3. Для старого `Selection` `physical_admission` по-прежнему возвращает false.
   Отдельный `PhysicalSelection` допускает только явно согласованные selection,
   deployment и operator confirmation. `admitted` означает прохождение этих
   программных проверок, а не аутентификацию внешнего решения A/владельца.
   Missing/current-stage/mismatched inputs отказывают до backend/workers/outputs.

Синтетический PASS не допускает реальную плату, не разрешает RAM retrieval и не
доказывает физическое прекращение преобразований. Двухчтительная RAM-процедура
и decoder в эту итерацию не входят. Старый Windows source результат PR211 и
ранее переданный kit не квалифицируют новые байты; package review не затронут.

## Точные зависимости

Непосредственное основание successor: [PR213](https://github.com/font-weight/stm32_motortester/pull/213),
commit `5c0d8d64ef1a83fc5ffe2dad348356e48474aa58`, tree
`b0cd7d86831450df87b8e4458c72fb3be8ffa613`. Legacy rehearsal продолжает сохранять
своё основание [PR211](https://github.com/font-weight/stm32_motortester/pull/211),
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

Меняются семь существующих HOST-файлов: invocation module/test/guide,
diagnostic/provenance/controller/bridge. Endpoint, wire, session, serial,
transport, journal schema, firmware, CI и лицензии сохранены. Controller/bridge
получают узкий mutually exclusive context; все восемь CAPS и build проверяются
до START plan/writer/generation/R. Второго wire parser или serial loop нет.

## Входы и side-effect-free подготовка

Следующие `Selection`/`RehearsalInvocation` сохраняют прежний fake-only API.
Полный operational API описан отдельно ниже.

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
  выбранном типе evidence; synthetic scope не становится physical. В любом
  случае clean completion остаётся false, RAM action автоматически не следует.
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
Это read-only carrier schema, а не разрешение запуска. Operational report
использует отдельный format `motortester-str1-physical-callback-trace-1` и origin
`device`. Все physical schema fixtures явно fabricated, без owned physical proof.

Журнальный carrier `physical:str1:…` отличен от `rehearsal:str1:…`; fresh reader
отклоняет смешанные/частичные/неизвестные tuples. Старый reader не знает новый
physical marker и должен отказаться от интерпретации благодаря уже имеющимся
DMA markers. Чтение carrier не разрешает execution. Домен охватывает completed
DMA records, не ADC_DR overwrites, незавершённые conversion или missed triggers.
Индекс / nominal Fs не означает калиброванное физическое время.

## Operational selection, внешнее решение и явное намерение

Маршрут выбран A в [6275425310](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6275425310).
`PhysicalSelection` связывает invocation ID, тот же явный COM/serial/board/PC,
свежие пути, точную STR1 декларацию и policy, а также:

- `HostFreeze(commit, tree, module_sha256)`: immutable sorted pairs для
  фиксированного source map; JSON представляет их объектом. Перед I/O bounded
  чтение файлов сравнивает их hashes, при export проводится новое наблюдение.
  Commit/tree и эти hashes не аутентифицируют исполняемый Python.
- `package_sha256`, `exe_sha256`: точные идентичности будущего согласованного
  delivery, а не указание построить/проверить package этой командой.
- `ReadbackIdentity(sha256, coverage, artifact_identity)`: отдельная внешняя
  декларация readback. Выбранный BIN остаётся в declaration.artifact_sha256.
  HOST не наблюдал flash/readback и не сравнивает hashes разных/неизвестных
  диапазонов как доказательство физической идентичности. Интерпретация coverage
  и окончательная применимость принадлежат внешнему combined gate A.

Физические `board_instance` и `windows_pc` требуют непустых конкретных
деклараций: literal placeholders `unknown`, `TBD`, `TODO`, `pending`,
`placeholder`, `none` (без учёта регистра) отказывают до backend/workers/output.
Прежняя policy-only `Selection` сохраняет свои правила.

`DeploymentRecord` содержит полный точный selection, его canonical ASCII JSON
SHA256, version `str1-final-disposition-reference-1`, disposition и две
`DecisionReference(reference, content_sha256)`: A final disposition и owner
authorization. `APPROVED_FOR_ONE_STR1_DIAGNOSTIC_ATTEMPT` — внутренний consistency
enum, не токен. `NOT_FLASH_APPROVED`, отсутствующие/несовпадающие bindings,
неверные types, placeholders или лишние JSON keys отклоняются. Reference bytes
не скачиваются; запись не подтверждает issuer, физический образ или полномочия.

Отдельный `OperatorConfirmation(operator, invocation_id, selection_sha256,
action="collect-one-str1-attempt")` выражает намерение caller выполнить именно
этот выбор. Digest предотвращает случайное несовпадение, не является подписью.
Ответственный оператор/координатор обязан независимо проверить реальное решение
A и разрешение владельца. Нового сервиса authentication, expiry или persistent
attempt registry нет: объект/CLI выполняет одну операцию, внешняя координация
должна исключать повторную попытку через новый процесс.

Selection ограничен 4096 bytes; полный local deployment JSON — 12288 bytes;
bundle deployment+confirmation, сохраняемый collector, — 4096 bytes. Действует
прежний общий metadata limit 8192 bytes. Все строки и source-file reads конечны.
До Connect проверяется отсутствие обоих output paths, включая symlink; actual
writers также создают их exclusive. Между проверкой и create возможна гонка;
она даёт отказ writer, а не overwrite или успешное evidence.

Реальные owner references, output paths и board/PC labels могут быть приватны.
Они остаются в локальном/private deployment и диагностическом evidence. CLI
не печатает их и не публикует отчёты. Public examples/CI используют только явно
fabricated сведения. Не копировать реальные private references в issue/PR/CI.

## Два construction seams, один collector

`PhysicalInvocation(selection, deployment=..., confirmation=..., event_config=...)`
нужно сохранить в переменной до вызова `collect()` и удерживать после возврата
или исключения, пока cleanup не settled. Convenience `collect_physical(...)`
безусловно отказывает до создания invocation/backend/workers/output, даже при
полностью согласованных входах: один возвращённый `InvocationResult` не владеет
незавершённым cleanup. Использовать retained class API, а не временный
`PhysicalInvocation(...).collect()`. Source CLI уже сохраняет свой owner.

Конструктор `PhysicalInvocation(...)`
инертен. Единственное `collect()` выбирает обычную real serial factory без
public factory override, один заданный port и прежний receiver owner. Нет scan,
automatic reconnect, нового START или автоматической второй попытки.

`PhysicalInvocation.for_rehearsal(..., fake_backend_factory=..., fixture_name=...)`
требует явную fake factory. Она идёт через non-fake `backend_factory` seam
`SerialStreamPeer`, но construction context безусловно `injected-test`:
journal origin `synthetic` с `rehearsal:` carrier, report прежнего fake format с
явным operational-test context. Device-only `evaluate_physical_evidence` всегда
отказывает такому результату. API не является sandbox для hostile Python.

Future genuine construction задаёт journal origin `device` и `physical:` carrier.
Reader строго проверяет format/origin/context/selection/carrier и не разрешает
смешивать physical и fake. Actual HELLO/CAPS находятся в immutable bridge
observations; image/USB/board/deployment остаются отдельными декларациями.

После того как `SerialTransport` получил и владеет закрытым backend proxy,
proxy.open на receiver запрашивает `backend.dtr = True`, затем вызывает open.
Callback `dtr` сохраняет возврат/исключение. При ошибке setup реального open нет,
а прежний owner сохраняет cleanup и handle, включая close failure. Already-open
factory object отказывается/закрывается до DTR/open. Этот request не доказывает
электрический уровень, CDC signal или физический reset.

KeyboardInterrupt supervisor сохраняется как first run cause; Stop/drain/close
и доступный export выполняются в исходных budgets. Повторный interrupt или
невернувшийся OS callback не может быть принудительно устранён. Unresolved
состояние остаётся ineligible; late facts не переписывают result. API caller
сохраняет объект для cleanup-only snapshot/join. Его явный `retry_close()`
делегирует уже существующему owner только retry failed close. Повторный `close()`
сам retry не запускает. Нет нового backend/START/reconnect, сброса исходных
deadline или изменения уже возвращённого ineligible result. До failed close
или после settlement `retry_close()` возвращает false. В процессе source CLI последний
объект доступен через `retained_cli_invocation()`; unresolved owner запрещает
следующий вызов main. Это не отменяет внешние failure/power-off правила A.

## Source CLI и последующие gates

Source CLI в том же module вызывает этот же API. Default/help/validation не
создают backend/workers/outputs; validation читает только явно выбранный local
deployment JSON. Для source review из точного checkout:

```text
set PYTHONPATH=host
py -3.12 -m motortester.stream_diagnostic_invocation --help
py -3.12 -m motortester.stream_diagnostic_invocation --deployment C:\FABRICATED-EXAMPLE\deployment.json
```

Это явно вымышленные пути, не команда текущего запуска платы. Будущее действие
потребует дополнительно `--execute-one-attempt`, `--operator`, `--invocation-id`
и `--confirm-selection` с точным displayed digest. CLI не загружает dynamic
factory/import path. Его программный `fake_backend_factory` параметр предназначен
для явно синтетических source-тестов и принудительно сохраняет test provenance.
Вывод различает incomplete eligible, ineligible, export-failed/unresolved и всегда
пишет `clean_completion=false`; код 0 означает только заявленный scoped predicate,
1 — результат ineligible/unresolved, 2 — отказ input/preflight. RAM action нет.

Этот source launch — review interface, не альтернативный launcher/distribution
для обхода paused package review. До настоящего owner start нужны exact A/B
source reviews, один pinned Windows source-only run финального reviewed successor,
согласованный package/EXE/HOST/firmware bundle и применимые terms, завершение
отдельного package/amendment gate, final combined gate A и явный owner start.
Текущий образ/filename не переименовывается в approved. Эта итерация не выполняет
ни package review/build, ни COM/board/flash/RAM действия.

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
