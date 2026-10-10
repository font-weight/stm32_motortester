# Critic-B: wire 124b3f32 и stream 8d5f68c7

Дата: 10.10.2026. Независимый проверяющий: gpt-6-astra/xhigh, не автор рассматриваемого кода. Production/test-файлы не исправлялись; записаны только review-артефакты.

**Общий wire-контракт: BLOCKED_EVIDENCE, score:null.** Прежние указанные неоднозначности исправлены; найдены два конкретных противоречия нового текста. Byte-codec и накопитель потока прошли фактические независимые host-проверки. Это свидетельство работы ограниченных модулей, а не утверждение сеанса, USB/аппаратуры или всего MVP.

## Точные версии

- [PR #4](https://github.com/font-weight/stm32_motortester/pull/4): head `124b3f32e1e7f7282bce283baa4c9406d4d72950`, base `3fe1a2d8c53f77e0510a3da9ddd125f68cb872fa`.
- [PR #7](https://github.com/font-weight/stm32_motortester/pull/7): head `8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7`, base `124b3f32e1e7f7282bce283baa4c9406d4d72950`.
- Оба актуальных head получены через GitHub перед проверкой. Источники извлечены по SHA отдельно; ни ветка автора, ни main не переключались.
- [Предыдущий отчёт](WIRE-draft-review.md) остаётся свидетельством только `18f154f3`. Повторное чтение diff и запуски ниже относятся к новым точным версиям; автоматического переноса оценки нет.

Уровень текущих личных запусков: Linux x86_64, GCC 14.2.0, Python 3.12.14. Границы общего результата и рубрика — [quality-gate v1.0](../../docs/quality-gate.md), [ТЗ MVP](../../docs/mvp-spec.md), issue #3. Новые независимые payload/session-сценарии, предусмотренные wire:150, пока не предъявлены как выполненный общий комплект.

## Ответ на прежние замечания

В `124b3f32` проверено текстом:

- Выделен WAIT_RELEASE; CONFIG/ARM при удерживаемом буфере/TX drain получают BUSY. Single больше не выглядит готовым к перезаписи удерживаемого окна.
- Tombstone последнего завершённого окна и поздний RELEASE после Stop явно не повторно вооружают режим.
- Header-reserved отбрасывается byte-codec, payload-reserved получает INVALID_PAYLOAD: противоречие устранено.
- Маски mode/source/edge и STATUS.reason определены; задано соответствие HELLO_ACK/CAPS и допустимость полного декартова произведения capabilities.
- Ответ прежнему запросу должен предшествовать STOP_ACK, определён STOP до первого ARM и при ожидаемом ARM_ACK; нулевой generation не wildcard после первого ARM.
- Сеанс/HELLO, старый nonce, направление сообщений и отсутствие ответа теперь описаны отдельно; timeout сам по себе не объявляется доказательством несовместимости.
- Требование попарного непересечения message/output/written в публичном encode API уточнено.

Это закрытие конкретных прежних документальных замечаний, а не подтверждение реализации состояния/dispatcher.

## Два оставшихся противоречия для общего контракта

### W-01, P1: разные точки назначения sequence и необратимости WINDOW_END

Место: [wire:68](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md#L68), [wire:131](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md#L131). Требования: отсутствие ложных sequence gaps, идемпотентный Stop, MVP-F03/F06/F07.

Первая строка назначает TX sequence при начале сериализации. Вторая допускает отмену END до принятия первого байта USB TX FIFO и утверждает, что sequence ещё не назначен. Между этими моментами может существовать полностью сериализованный кадр, ожидающий освобождения FIFO.

Контрольный сценарий: ожидаемый получателем sequence=10; END сериализован с 10, но USB не принял ни одного байта; приходит STOP. Если END удалён и ABORT использует следующий sequence=11, host видит gap и начинает восстановление вместо нормального Stop. Если номер 10 сохраняется для ABORT, нужен явный контракт резервирования/commit, поскольку нынешняя фраза уже считает его назначенным.

Нужно выбрать один инвариант: например, номер до первого принятого байта только зарезервирован и не расходуется при отмене; либо запрещено начинать сериализацию без гарантированной атомарной передачи первого байта, и это объяснено как обязанность транспорта. Добавить trace с backpressure и STOP между кодированием и первым enqueue. Это правило подготовки очереди, не требование изменить корректный byte-codec.

### W-02, P1: Auto с настоящим внешним запуском получает два возможных external_edge_id

Место: [wire:106](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md#L106), [wire:117](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md#L117), [wire:141](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md#L141). Требования: различать реальный внешний запуск и auto_timeout, сохранять привязку к независимой команде, FR-04/11 и семантика MVP Auto.

Строка 141 требует external_edge_id, равного новому счётчику принятого внешнего запуска, но затем говорит «analog/Auto имеют 0». Mode Auto + source external разрешён capabilities и может получить настоящее событие раньше тайм-аута.

Контрольный сценарий: mode=Auto, source=external, пришёл 17-й внешний фронт до тайм-аута, trigger_kind=external. По первому правилу edge_id=17, по второму edge_id=0. Это мешает двум реализациям одинаково сохранять связь окна с внешним событием.

Уточнить правило по **trigger_kind**, а не mode: external → принятый ненулевой edge counter независимо от Auto/Normal/Single; analog и auto_timeout → 0. Добавить два отдельных вектора Auto+external: реальное событие и истечение тайм-аута. Если автор имел в виду auto_timeout, это нужно написать точно.

## Что можно делать независимо

Текущих размеров и правил byte framing достаточно для **draft экспериментальных codec/stream conformance**. Эти два замечания относятся к будущему session/TX/window слою и не требуют останавливать HOST-001 или непретендующий на принятие HOST-002 с opaque frames. До фиксации нельзя молча выбрать один из двух вариантов как общий интерфейс. Новый accepted-контракт потребует обоих критиков и остающихся сценариев, а не только успешного накопителя.

## Лично выполненные проверки PR #4

Точные `.c/.h` и единственный vector извлечены из `124b3f32`, перекомпилированы с:

```sh
gcc -std=c11 -O2 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -fPIC -shared mt_wire.c -o libmt_wire.so
python check_wire.py
```

[Скрипт](evidence/wire-124b3/check_wire.py), [фактический результат](evidence/wire-124b3/results.json). Независимые `struct`, `zlib` и COBS-разбиение; не только round-trip двух функций автора.

Результат: опубликованный golden совпал; 2052 oracle-сравнения по всем payload lengths 0..512 и разным данным; 544 недостаточные ёмкости, oversized payload, 8 структурных отказов, 736 однобитных повреждений, 93 усечения, 7 framing/length случаев и 100000 случайных некорректных кадров. Выходы при отказе и guard-области сохранились. Новых дефектов byte-codec не обнаружено.

## Лично выполненные проверки PR #7

1. Из `git archive 8d5f68c7...` скомпилированы и запущены авторские `tests/protocol/test_mt_stream.c`, с C11 и `-Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion`: exit 0.
2. Тот же suite повторён с AddressSanitizer/UndefinedBehaviorSanitizer, exit 0. `ASAN_OPTIONS=detect_leaks=0` из-за ранее проверенной несовместимости LeakSanitizer с ptrace этой среды; утечки не объявлены проверенными.
3. Написана отдельно и запущена независимая Python-модель накопителя и decode: [скрипт](evidence/stream-8d5f/check_stream.py), [фактический результат](evidence/stream-8d5f/independent-stream-results.json). Ни encoder, ни decoder C не использованы для построения ожидаемых кадров/событий.

Независимый результат: 6 630 701 сравнение feed/poll с reference-моделью, включая каждую двухчастную границу большого кадра, склейку кадров, пустые разделители, overflow, discard/resync, точные 999/1000/1001 и 2999/3000/3001 мс, rollover, правильный кадр после ошибки, 2000 смешанных случайных потоков и CRC-valid неподдерживаемые версии/reserved. Получено 13661 MESSAGE, 9956 REJECTED, 3371 OVERFLOW и 755 TIMEOUT, совпадающих с моделью; остальные события NONE. Output не менялся при неуспехе; поля/полезная нагрузка успешного сообщения и guard-байты совпали. Seed `20261010`.

`sizeof(mt_stream)=568` в проверенном x86_64 ABI. Это **не** размер ARM-объекта и не подтверждение бюджета STM32. Прочитаны инструкция и public API; NULL/reset, single-owner/non-overlap, clock contract и отсутствие влияния parser.init на acquisition честно разделены. Найденных алгоритмических дефектов накопителя нет.

Воспроизведение из чистого архива PR #7:

```sh
gcc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c tests/protocol/test_mt_stream.c -o /tmp/test_mt_stream
/tmp/test_mt_stream
gcc -std=c11 -O1 -g -Wall -Wextra -Wpedantic -Werror -fsanitize=address,undefined -fno-omit-frame-pointer firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c tests/protocol/test_mt_stream.c -o /tmp/test_mt_stream_san
ASAN_OPTIONS=detect_leaks=0 /tmp/test_mt_stream_san
# Скопировать независимый check_stream.py в отдельную временную папку $T.
gcc -std=c11 -O2 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wsign-conversion -shared -fPIC firmware/protocol/mt_wire.c firmware/protocol/mt_stream.c -o "$T/libmt_stream.so"
python "$T/check_stream.py"
```

Не применять `-DNDEBUG` к assert-based авторским тестам. Python-скрипт требует только стандартную библиотеку.

## Рубрика и граница заключения

Для общего результата C1–C5 сохраняют веса 30/20/25/15/10%. Численные оценки не выдумываются при отсутствующих обязательных свидетельствах:

- C1: byte layouts и прежние уточнения проверены; W-01/W-02 требуют исправления общего документа.
- C2: bounds/ошибки/timeout/resync ограниченных функций подтверждены; сеанс, TX/Stop и окно ещё не подтверждены реализацией/trace.
- C3: реальные независимые host-проверки codec/stream выполнены; весь набор payload, handshake/reconnect/Stop/rearm и 20 **wire**-окон по §150 пока не закрыт. 20 внутренних HOST-001 окон — другая проверка.
- C4: малые API и границы понятны; семантика двух конфликтующих правил нуждается в точности.
- C5: воспроизводимые reviewer-скрипты/результаты сохранены; target ARM/полный стек/USB/Windows не проверены. Их нельзя выводить из host GCC.

Итог общего контракта: `score:null`, `BLOCKED_EVIDENCE`, с дополнительно указанными W-01/W-02. Этот отчёт не выдаёт отдельное разрешение на слияние firmware PR #7: его применимая приёмка, Critic-A и зависимость от непринятого #4 сохраняются. Функциональные результаты script PASS не являются формальным gate PASS.

Никакие аппаратные характеристики, безопасность подключения, Windows или software-ready этим отчётом не подтверждены. Следующее действие: точные правки двух правил, независимые payload/session traces и ревью окончательного SHA; доступная draft conformance-работа может продолжаться.
