# Индексированная запись: ограниченная host-only основа

Статус: локальный кандидат для независимого точного ревью, issue191. База:
`e38a36004befe453923f3e86165b7fd6d83076d9`, tree
`ffab972586afff8d0a4e13a1e73d56af49190292`. Только новые `stream_model.py`,
`stream_recording.py`, их тесты и этот документ. Это **не wire-контракт**, не
интеграция приложения, не Windows-сборка и не аппаратное свидетельство.
Приёмка точной версии требует отдельного Critic-B ≥9,5; сам документ PASS не даёт.

Одна запись хранит один непрерывный **по проверенным входным индексам** префикс
`[first_sample_index, end_sample_index)`. Непрерывность физических преобразований
по нему не доказана. Первый индекс может быть ненулевым: слой хранения не выбирает
момент Record в preview. Нет ретроактивного добавления preview, автоматического
возобновления, rolling overwrite, внешнего запуска или номера цилиндра.

Старые `recording.py`/MTCAP и `session_archive.py`/MTREC не изменены.
Сегментированные MTREC остаются сегментированными с неизвестными межоконными
паузами; их нельзя передать сюда как доказанный непрерывный поток.

## Нормализованный API и происхождение

Все модели — frozen/slots dataclass с точными типами. `bool`, float, строка и
преобразование `int(...)` не заменяют целый индекс. Все индексы/ID/счётчики ниже,
если не указан другой предел, — `int` от0 до2^64−1. Конец исключающий и тоже
укладывается в u64: последний допустимый сохранённый индекс —2^64−2. Пустой
интервал с first=end=2^64−1 допустим. Это преднамеренный предел host-модели,
а не принятый wire-диапазон.

- `StreamIdentity(session_id, generation, config_id, nominal_fs_hz, origin,
  protocol_revision=None, firmware_version=None, application_version=None)`.
  session_id — непустой непрозрачный текст≤128 символов; это идентификатор
  источника, не локального файла. generation/config_id — u64, в том числе0;
  допустимость конкретных wire-значений проверит будущий адаптер.
  Fs **обязательна**, exact int1…1_000_000 Гц, nominal/unverified.
  origin — `synthetic` или `device`, заявленный вызывающим кодом источник.
  Каждая версия — None либо текст1…64. Все восемь полей сравниваются при append.
- `StreamStart(identity, first_sample_index, recording_id,
  boundary_evidence='unspecified')`. recording_id — отдельный локальный
  идентификатор1…128, его создаёт вызывающий код. Он не подменяет session_id.
  boundary_evidence1…128 — явное заявление вызывающего кода о границе,
  а не проверенный самим файлом факт.
- `StreamBlock(identity, first_sample_index, raw_u16le)` принимает только
  принадлежащие входу неизменяемые `bytes`, чётная длина2…16384. bytearray,
  memoryview, list и пустой блок отвергаются. sample_count=len/2;
  end_sample_index=first+count. Один канал, little-endian, **все16бит сохраняются**,
  включая слова4096…65535. Это raw-code words0…65535, не вольты и не утверждение,
  что все эти значения допустимы для12-битного АЦП прибора. Формат задаёт это
  фиксированно; config_id сам по себе не сообщает разрешение АЦП.
- `FailureEvidence(code, detail, operation=None, request_id=None,
  source_code=None)` сохраняет первый отказ и известный контекст. code из
  фиксированного списка: source_overrun, ambiguous_dma, transport_error,
  source_error, host_io, input_identity, input_order, invalid_input,
  invalid_terminal, capacity_raw, capacity_blocks, unknown. detail1…256;
  operation None/текст1…64; request_id и исходный числовой source_code None/u64.
  Детали не исполняются и не являются HTML. Автоматическая диагностика writer
  использует фиксированный текст, не копирует traceback, путь, порт или секреты.
  Неизвестные значения остаются None. Весь текст запрещает управляющие U+0000…001F,
  surrogate и U+FFFE/U+FFFF; будущий UI обязан выводить его как обычный текст.

Будущий согласованный wire-адаптер отдельно проверяет negotiation, session,
CONFIG/разрешение АЦП, CRC/sequence, физическую допустимость кодов и terminal/ACK.
Эта библиотека принимает **заявленные нормализованные входы**, повторно проверяет
их типы, identity и непрерывный порядок. Она не хранит исходные wire-кадры,
не перепроверяет их CRC и не подтверждает, что вызывающий код выполнил валидацию.
Fs50000 в тестах — синтетический номинал, не измеренная частота.

## Завершение и разные границы

`StreamTerminal(identity, end_sample_index, reason, terminal_observed,
 device_generated_end_sample_index=None, device_transmitted_end_sample_index=None,
 loss_samples=None, boundary_evidence='unspecified', failure=None,
 stop_requested=False, stop_request_id=None)`.

- end_sample_index — **точный принятый writer endpoint при вызове finish**,
  включая принадлежащий writer неподтверждённый RAM/файловый хвост. Это не прежний
  checkpointed endpoint. finish проверяет identity и точное равенство принятому
  концу. Терминал при восстановлении вводит весь этот диапазон только после
  проверки всех предшествующих raw-кадров и самого терминала.
- generated и transmitted — отдельные заявленные устройством исключающие границы.
  Каждая известная граница≥принятого end, а при двух известных generated≥transmitted.
  Отсутствие границы — None, не0. Разность границ не присваивается loss_samples.
- loss_samples — отдельно сообщённый счётчик потерянных отсчётов None/u64, а не
  вычисление по испорченным байтам файла. Его состав остаётся обязанностью
  согласованного адаптера; значение0 само по себе не доказывает полноту.
- reason — stopped/source_failure/host_failure/capacity. Для трёх отказных причин
  failure обязателен, для stopped запрещён. terminal_observed — exact bool:
  вызывающий код сообщает, что он проверил соответствующий source terminal.
  Локальный finish или fsync **не вправе** установить этот флаг вместо адаптера.
- stop_requested — отдельно сообщённый запрос пользователя/контроллера, не
  подтверждение устройства. stop_request_id разрешён только при stop_requested.
- `clean_stop_reported` — только согласованные заявления: reason=stopped,
  stop_requested, terminal_observed, loss_samples=0 и обе device-границы точно
  равны принятому end. У reader дополнительно нет recovery_issue и есть целый
  терминал. Это не receipt успешного fsync, не доказательство происхождения,
  физической остановки или полного разряда.

При неизвестной длине потери сохраняется None. Префикс не дополняется нулями,
не перескакивает через повреждение и не склеивается с последующим generation.
Пустой известный интервал допустим; само его создание не доказывает начала
сбора или отсутствия физического разряда. Для нового Record до установления R
используется двухфазный API ниже.

## Writer, контрольные точки и ресурсная политика

`StreamWriter.create(path, start, limits=StreamLimits())` создаёт **новый** файл,
пишет заголовок и вызывает flush/fsync. Существующее имя не заменяется. Ошибка
create оставляет возможный частичный файл для диагностики; повторного открытия
на запись/resume нет. Успешная подготовка writer ещё не разрешает START прибора.

`StreamLimits(max_raw_bytes=67108864, max_blocks=65536)` — явные лимиты записи,
сохранённые в заголовке. Разрешено только уменьшение этих hard bounds; raw quota
чётная, обе могут быть0. max_blocks считает **архивные raw-кадры**, не входные
DATA, не отсчёты и не все кадры файла. Формат и лимиты — foundation proposal,
а не утверждённая максимальная длительность продукта.

`append(block)` возвращает None, не receipt/ACK. При успехе writer владеет всем
переданным блоком; `accepted_end_sample_index` показывает принятый конец.
Совпадение identity и exact next index проверяются **до** копирования/записи.
Пропуск, overlap, duplicate, reorder и смена identity навсегда закрывают interval:
предыдущий принятый префикс по возможности завершается host_failure с первой
причиной, входной спорный блок целиком отвергается. При quota тот же путь с
capacity. Нет частичного допуска блока для заполнения последнего места.

Уже проверенные соседние inputs объединяются в буфер≤16384B и архивные блоки
≤8192samples. Входные границы DATA не приравниваются к архивным границам. Поэтому
128-sample inputs не расходуют65536 index entries за167,77s: при автоматических
контрольных точках v1 64 MiB дают 4096 raw-кадров. У v2 первый короткий B
сохраняется отдельно, поэтому при таком режиме возможно 4097 raw-кадров. Семантика raw/identity/terminal одинакова
при разных корректных разбиениях, байты всего журнала могут различаться.

Контрольная точка выполняется до превышения1MiB принятого неподтверждённого raw
либо при достижении128 raw-кадров. Счётчик учитывает RAM и записанный хвост вместе,
overshoot исходным блоком не допускается. `checkpoint()` явно сбрасывает RAM,
пишет C, затем делает **один** flush/fsync. Пустой/redundant checkpoint — no-op,
поэтому контрольных точек не больше raw-кадров. Автоматическая обычная пачка при
максимальных блоках —64 raw-кадра, так как раньше срабатывает1MiB.

`checkpointed_end_sample_index` меняется только после успешного fsync этого
writer. До/после flush/fsync проверяются исходная path/descriptor identity,
ancestor identities, ожидаемые размер/позиция и неизменность metadata относительно
последней собственной записи. После каждой своей записи writer допускает только
её ожидаемое изменение размера и обновляет отдельные path/handle snapshots.
Observed replacement/truncation/metadata drift закрывают writer без продвижения
checkpointed endpoint. Это наблюдение ОС без readback, не legacy verified-receipt. Библиотека
не выдаёт receipt вообще; нечего связывать с firmware credit/RELEASE/ACK.
Поблочного fsync/readback нет. Последний RAM-буфер≤16KiB; весь volatile suffix≤1MiB.
Последнее не включает внешнюю очередь приёмника, которой здесь нет.

Нет фонового timer/tick. **Нет гарантии “каждую1s” или предела потерь по времени**:
при малом потоке хвост может оставаться неподтверждённым сколь угодно долго.
Контроллер может вызывать checkpoint по своему конечному расписанию; факт его
исполнения требует отдельной интеграционной проверки.1MiB при номинальном100kB/s
соответствует примерно10,49s raw, но это арифметика, не измерение потери при аварии.

`raw_bytes_remaining` и `archive_frames_remaining` дают остатки для активного
writer; последний учитывает резерв одного raw-кадра для текущего RAM-хвоста.
Достижение лимита не разрешает следующий блок. Более частые явные checkpoint
порождают короткие raw-кадры, так что **раньше срабатывает любой из двух лимитов**.
64MiB при50000×2B/s — максимум671,08864s, около11m11s; не безлимитная запись.
Внешний контроллер обязан заранее показывать quota и запрашивать Stop по своему
приёмочному правилу. Эта библиотека команд устройству не отправляет.

`finish(terminal)` сбрасывает последний RAM-блок, пишет T, flush/fsync, закрывает
writer. `close()` только закрывает дескриптор: не checkpoint, не Stop и не clean
terminal; повторный close допустим. Любая I/O-ошибка закрывает writer без retry.
`first_failure` сохраняет первую причину в процессе; поздний нормальный finish
закрытого writer её не заменит. Если ошибочный диск не позволил записать причину,
исключение остаётся у вызывающего кода; восстановление не выдумывает эту причину.
Если ошибка сохранения отказа последовала за отказом admission, первичное
исключение сохраняется, а вторичное доступно через exception cause.

Все операции синхронные, для одного владельца. Интеграция обязана выполнять их
в отдельном файловом исполнителе, **не** в GUI/COM callbacks. Здесь нет очереди,
сохранения через GUI, deadline/отмены блокирующего OS-вызова или потокобезопасности.

## Файл и точная схема

Предлагаемое расширение `.mtstream`; reader проверяет magic, не расширение.
Отдельного экспорта/перепаковки/атомарной публикации Save здесь нет.

1. Заголовок `<8sI>`: magic `MTSTRM\x01\x00`, длина JSON1…4096.
   JSON UTF-8: ровно `{"start": StreamStart, "limits": StreamLimits}`;
   вложенная identity содержит ровно8 полей модели, start ровно4, limits ровно2.
   Следующие32B — SHA256 всех12B заголовка и JSON.
2. Каждый кадр: `<cI>` type+payload_length, payload,32B SHA256 от
   `previous_digest + frame_header + payload`. Первый previous_digest — hash
   заголовка. Хеш цепочки связывает тип, длину, порядок, metadata и raw.
3. `B`: payload `<QI>` first_index+sample_count, затем ровно count×2 rawB;
   count1…8192. first равен предыдущему end; переполнение недопустимо.
4. `C`: payload ровно `<QQ>` end_index+общее число raw-кадров. Оба значения
   обязаны совпасть с проверенным префиксом. C без новых B запрещён.
5. `T`: JSON1…4096, ровно11 полей StreamTerminal, identity — та же модель;
   failure null либо ровно5 полей FailureEvidence. T единственный и последний.

Неизвестные/повторные/недостающие JSON-поля запрещены. NaN/Infinity, неверный
UTF-8 и неверные типы запрещены. До общего JSON-разбора читается≤4096B; даже
патологические вложения/токены ограничены этим размером, RecursionError отклоняется.
Reader v1 не принимает версию 2; отдельный recover_attempt ниже принимает только v2. SHA256 — контроль случайного повреждения, не
цифровая подпись: сознательный автор может переписать файл и пересчитать хеши.

## Физические и оперативные пределы v1

- raw≤67_108_864B, B≤65_536, C≤числаB, T≤1, всех кадров≤131_073.
- Заголовок≤4140B; overhead одногоB49B, C53B; резервT4133B.
- **Общий hard limit73_801_809B** =4140+67_108_864+65_536×(49+53)+4133.
  РезервT проверяется до записи каждогоB/C, raw quota его не расходует.
  Это резерв формата, не обещание свободного места на физическом диске.
- Один input≤16384B; RAM coalescer≤16384B; frame payload≤16396B;
  сборка/хеш кадра даёт несколько ограниченных копий такого размера.
- Recovery держит≤65536 небольших immutable index entries и pending suffix≤128
  entries; raw всего файла в памяти не держит. Entry содержит first/count/offset
  и два32B digest, соседние digest могут разделяться. Дополнительные массивы
  ссылок на entries/first тоже≤65536. Проверка tracemalloc в CPython тестовой
  среды требует peak<24MiB для максимального индекса; это не обещание RSS любой ОС.
- read_raw ограничен65536samples/131072B. read_samples создаёт tuple≤65536
  Python integers. При чтении держится одна ограниченная raw-frame payload,
  накопитель и итоговая копия; входной журнал целиком не загружается.

## Восстановление и lazy reads

`recover(path) -> StreamRecording` — read-only линейная проверка≤73_801_809B и
≤131073frames. Сначала тип/размер файла, затем заголовок/схема; после этого каждый
frame length до чтения payload, chain, диапазоны, quota и точные C/T границы.
Начальные ошибки заголовка/версии/размера вызывают StreamError; I/O-ошибка чтения
остаётся I/O-ошибкой. Writer и reader не пропускают непроверенный участок.

Возвращаемые start/identity, first/end, limits, terminal, recovery_issue,
discarded_tail_bytes и индекс неизменяемы. При первом неверном кадре возвращается
последний целый checkpoint prefix с `invalid_tail`; все байты после этой границы
учтены в discarded_tail_bytes, но **не** в loss_samples. Полные B после последнегоC
безT дают `uncheckpointed_tail`; чистый EOF послеC/заголовка — `missing_terminal`.
RAM-хвост погибшего процесса не виден в файле: его размер и причина неизвестны.
T с лишними байтами после него не принимается; возвращается прежнийC. ЦелыйT
вводит предшествующий префикс, `recovery_issue=None`; `terminal` сохраняет именно
заявленные поля. Нет дописывания, усечения, repair или resume исходного файла.

**Читаемый C/T не доказывает, что прежний fsync вернулся успешно.** При исключении
fsync байты могут остаться видимыми в OS cache; тесты сохраняют это различие.
Recovery подтверждает сейчас прочитанные байты, не выдаёт ретроактивный receipt
старому процессу. fsync не является универсальным доказательством выживания
потери питания/контроллера/носителя; directory entry отдельно не fsync-ится.
Создание/сохранение pathname после потери питания не гарантируется.

Reader: `read_raw(start_sample_index,count)->bytes`, `read_samples(...)->tuple`,
`iter_raw(start_sample_index=None,end_sample_index=None,chunk_samples=8192)`.
Диапазоны должны целиком лежать в проверенном префиксе. Нулевое count допустимо
включая конец; iterator chunk1…8192 возвращает StreamBlock с абсолютными индексами.
Поиск первой frame — binary search, затем только затронутые bounded frames,
каждая повторно хешируется. Время seek — O(log B+числа затронутых frames),
без повторного полного scan. На каждом чтении новый fd, закрываемый до возврата;
reader не имеет close/lifetime и не держит общий seek-cursor.

Перед открытием проверяются обычные родительские каталоги и отсутствие
symlink/reparse ancestors; чтение требует обычного leaf, создание O_EXCL.
Где доступны, используются O_NOFOLLOW/O_NONBLOCK. После открытия сравниваются
path/handle dev,ino,type,size,mtime и предки. ctime между lstat/fstat **не**
сравнивается: сохраняются **два** снимка, lstat→lstat и fstat→fstat.
Reader сохраняет также bounded ancestor identities. До возврата recovery/lazy-read
повторно проверяются оба снимка и все ancestors, включая отсутствие symlink/reparse.
Writer сравнивает такие же снимки перед собственными записями и до/после sync;
при собственной записи ожидаемые размер/позиция и исходные identity контролируются
отдельно от разрешённого изменения mtime/ctime. Observed замена, truncation,
ancestor substitution или same-API metadata drift отклоняются. Неподходящий созданный файл не удаляется автоматически.
Решение рассчитано на кооперативный локальный каталог, не на враждебную гонку ОС;
hard links/права/блокировки против злонамеренного процесса не обеспечены.
Windows fallback flags и stat-семантика требуют отдельного нативного прогона.

## Двухфазная подготовка без выдуманного R: версия 2

Добавление после сохранённого R2 `1292711b4c86847e096defdf0e6e49831e01d623`.
Основание политики: обсуждаемый PR192 draft
`72d400660a0af896ebb3810a8c9a22166d7fa78a`, §1
`protocol/wire-v0.2-stream.md`. Это не приёмка draft или wire-схемы.
Общий controller/session выбирает очередность нормализованных событий; хранение
только последовательно сохраняет выбранный исход. Новый API не имеет собственной
очереди, receiver, блокировки DATA/END, START/Stop команд или нового wire endpoint.

В `stream_model.py` добавлены три immutable модели:

- `StreamPreparation(identity, recording_id, start_policy)`: та же identity,
  отдельный локальный recording_id длиной 1…128 символов, start_policy ровно
  `next_block` либо `source_start`. Выбранного first/end здесь нет.
- `PreparedReadyToken(token_id, preparation)`: token_id — 32 символа lowercase hex,
  выдаваемые через UUID4; preparation — точная модель выше. Writer принимает
  только свой первоначально выданный объект token, не копию равных полей.
  Это локальная защита согласованности состояния, не сетевой секрет, не подпись
  и не средство защиты от злонамеренного Python-кода в том же процессе.
- `UnboundOutcome(reason, failure=None, terminal_observed=False,
  device_generated_end_sample_index=None, device_transmitted_end_sample_index=None,
  loss_samples=None, boundary_evidence="unspecified", stop_requested=False,
  stop_request_id=None)`. reason: `ended_unbound`, `cancelled_unbound` или
  `failed_unbound`; только последний требует FailureEvidence. Булевы поля строго
  bool; известные индексы/потери/request ID — u64; generated ≥ transmitted,
  если оба известны. stop_request_id требует stop_requested. Граница evidence
  длиной 1…128, с теми же ограничениями plain text. Нет принятого диапазона,
  поэтому source-end не сравнивается с несуществующим host end. Unknown loss
  остаётся None. Это не clean indexed recording и не свидетельство физического Stop.

`PreparedStreamWriter.prepare(path, preparation, limits=StreamLimits())`
создаёт новый файл через тот же O_EXCL и те же проверки path/handle/ancestors,
записывает заголовок без R, делает flush/fsync и повторные наблюдения файла.
Только после успеха выдаёт объект с read-only `preparation` и `readiness_token`.
Ошибка до возврата не выдаёт readiness. Читаемый заголовок после failed fsync
не доказывает, что prepare вернулся. Подготовка не резервирует будущие bytes на
носителе, не исключает будущий disk full и сама не разрешает START источника.

Допустим ровно один переход из prepared, вызываемый одним сериализованным
controller/adapter. Тот хранит настоящий token и, если нужно, использует token_id
только для корреляции своего задания. Внешняя очередь обязана быть ограниченной
и учитывать целиком owned raw блока до возврата операции; очередь здесь не создаётся.

1. Preview: `bind_first_block(token, block) -> StreamWriter`, только `next_block`.
   До выбора R повторно проверяются точный StreamBlock, immutable bytes, вся
   identity/Fs, индекс/длина/overflow и quota целого блока. Отказ правильного token
   по возможности пишет failed_unbound с первой причиной, закрывает попытку без R.
   При успехе R равен фактическому first этого блока; boundary_evidence фиксировано
   как `caller_next_whole_validated_block`. Первый B записывается сразу целиком,
   на **том же дескрипторе**, без отдельного selection marker или fsync. Следующие
   входы объединяются обычным bounded coalescer. Весь первый блок принят один раз.
   Только controller может утверждать, что это именно следующий whole validated
   DATA после readiness; storage не проверяет исходные CRC/sequence или chronology.
2. Direct Record: `bind_start(token, start: StreamStart) -> StreamWriter`, только
   `source_start`. Вызывается после того, как controller проверил успешный START_ACK
   и допустил точный source start. identity и recording_id должны совпасть с
   preparation. Будущий wire-адаптер проверяет требуемый протоколом нулевой R;
   wire-neutral API допускает любой допустимый u64. Пишется S declaration без
   fsync; она ещё не является committed empty recording. Неудачный START остаётся
   unbound и заканчивается третьим переходом. Никакой token/UUID/hash/fsync не
   удостоверяет фактический START_ACK или источник boundary_evidence.
3. `finish_unbound(token, outcome) -> None` пишет U и делает проверенный flush/fsync,
   закрывает попытку, не создавая first/end. Применяется, когда controller уже
   выбрал END/fault/cancel раньше first DATA/успешного START. Исход — переданное
   утверждение controller, а не результат атомарной гонки внутри файловой системы.

После bind prepared-объект считается closed (его capability consumed), а descriptor
принадлежит только возвращённому StreamWriter. `prepared.close()` после передачи
не закрывает active writer. До выбора close лишь прекращает попытку, не пишет U
и не доказывает отмену. Wrong-writer/скопированный/чужой token не потребляет
правильный token владельца; повторное, позднее, отменённое или закрытое использование
отклоняется. Новый prepare требует новое незанятое имя; recover не выдаёт token и
не открывает resume. I/O failure после начала bind закрывает writer без повторного
выбора; частичные bytes остаются для восстановления.

Для active writer `accepted_end_sample_index` включает принятый первый блок,
а `checkpointed_end_sample_index` до первого C/T равен исходному R как нейтральная
граница **нулевого committed raw**. Это поле не доказывает durable selection R.
После успешного checkpoint/finish оно имеет прежний flush-only смысл. Возврат bind
не является durability/readback receipt. Source END, Stop acknowledgement,
quiescence и operation correlation проверяются внешним controller; библиотека
не может проверить эти факты и не добавляет их в clean_stop_reported автоматически.

### Версия 2: строгая грамматика и бюджет

- Заголовок имеет magic `MTSTRM\x02\x00`, прежний `<8sI>`, UTF-8 JSON длиной
  1…4096 и SHA256. Ровно `{"preparation": StreamPreparation, "limits": StreamLimits}`.
  preparation ровно 3 поля, identity прежние 8. v1 schema остаётся прежней;
  null/отсутствующий R не становится допустимым в v1.
- `next_block`: первый B устанавливает R. Затем разрешены обычные B/C/T с теми же
  quota, счётчиками, непрерывностью и цепочкой digest. C/T до B запрещены.
- `source_start`: первый S имеет JSON StreamStart длиной 1…4096, ровно прежние
  4 поля, совпадающие identity/recording_id. Он допустим один раз и первым кадром.
  После него B/C/T подчиняются прежним правилам. C без новых B запрещён.
  Нулевой T после S допустим и представляет established-but-empty interval;
  качество исхода всё равно определяется terminal, не одним наличием S.
- U — JSON UnboundOutcome длиной 1…4096, ровно 9 полей; failure null или прежние
  5 полей. U допустим только первым и последним кадром, пока нет S/B, для обеих
  политик. После U любые bytes отвергают U; ранее committed recording не возникает.
- Frame header/hash прежние. Подготовленный заголовок ≤4140 B; S ≤4133 B,
  U ≤4133 B. U заменяет selected-журнал и его T, не добавляется к нему.
  next_block: прежний worst-case 73_801_809 B и ≤131_073 frames.
  source_start: плюс один S, **73_805_942 B и ≤131_074 frames**. Reader v2 имеет
  этот отдельный общий hard bound; политика/счётчики дополнительно ограничивают
  доступную грамматику. v1 hard bound не расширен. Резерв последнего T/U — 4133 B.
  Все raw/index/input/read/pending limits прежние; один S не расходует raw quota.
  Пустой/repeated checkpoint остаётся no-op, duplicate selection/U закрыт token.

### Восстановление попытки

`recover_attempt(path) -> AttemptReport` принимает только v2. Объект содержит
path, preparation, limits, selected_start, recording, unbound_outcome,
recovery_issue, discarded_tail_bytes. У него **нет** waveform read API и first/end;
view получает только непустое поле `recording` после явной проверки состояния.
Reader не создаёт capability, START/Stop ACK или ретроактивный I/O receipt.

- Torn/invalid header отклоняется, без R.
- Целый header: selected_start=None, recording=None, unbound_outcome=None,
  issue `missing_selection_or_terminal`. Неизвестно, успел ли controller выбрать
  блок в RAM до crash; это не доказательство победы END или отмены.
- Целый first B без C/T: selected_start хранит заявленный R, recording=None,
  issue `uncheckpointed_tail`. Нельзя читать неподтверждённые samples как recording.
- Целый S без committed raw/valid T: selected_start сохраняет только declaration,
  recording=None, issue `selection_without_commit`. Даже checkpoint() без B
  остаётся no-op и не превращает S в успешную пустую запись.
- B-префикс с проверенным C/T даёт обычный StreamRecording, с точным raw/end и
  прежними lazy validation guards. Только S + подходящий целый T может дать
  legitimate empty recording. Ошибочный terminal всё равно не clean.
- Целый U даёт unbound_outcome, selected_start=None, recording=None и issue=None.
  Это завершённое описание попытки без выбранного R, а не успешная пустая запись.
- При первом повреждении issue `invalid_tail`; только прежний committed raw
  доступен в recording. Metadata о полностью прочитанном S/B может остаться
  selected_start. Хвост не превращается в известное число потерянных samples.

Каждый байтовый cut и failed-fsync варианты проверяются синтетически. Сейчас
читаемый header/S/B/C/T/U не может доказать, какая операция вернулась прежнему
процессу. Допустимое завершение интервала и успех сохранения — разные сведения.

## Воспроизводимые проверки и следующие ворота

Из корня exact source snapshot:

```text
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_recording.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_recording.py -v
PYTHONPATH=host python -m unittest discover -s host/tests -p test_recording.py -v
PYTHONPATH=host python -m unittest discover -s host/tests -p test_session_archive.py -v
```

Тесты включают независимый little-endian/hash/schema fixture, свежий процесс,
все16bits raw, индексы>2^53/околоu64, пустой interval, first failure/context,
разные input partitions, coalescing, реальные raw64MiB и индекс65536, ограниченное
lazy чтение и hash corruption, every-byte truncation малого fixture,
payload/checkpoint/terminal cuts, заголовок/JSON/oversize/unknown frames,
short/zero writes, synthetic disk-full/flush/fsync faults, no-clobber/aliases,
path/descriptor mismatch и same-API mutation. Узкая редакция после
23888d23f818ab047a3c6912de71fd9e5c157707 закрывает три воспроизведённые критиком
filesystem-семьи: writer publication checks, финальные ancestor checks и отдельный
path-ctime snapshot. Прежний candidate/negative controls сохраняются; schema,
публичный API и лимиты R2 той редакцией не менялись. Новая двухфазная редакция
добавляет только явно версионированные attempt-модели и одну ограниченную S/U
ветвь. Проверки включают обе сериализованные DATA/END очередности, direct START
с нулевым T, failed START без R, чужие/повторные token, pre-readiness faults,
все байтовые срезы header/B/C/S/T/U и сохранение старого v1 fixture. Негативные результаты сохраняются
отдельно от исходников; source freeze и авторские/независимые логи привязаны к SHA.

Отдельно остаются общий wire-контракт, device CRC/profile/ACK/Stop ordering,
backpressure/queues/threads, фактическая скорость и длительные stalls,
Record/preview/Save/UI и event composition, упаковка/нативная Windows и физический
низковольтный sustained-stream/loss опыт. Эта библиотека не закрывает эти ворота.
