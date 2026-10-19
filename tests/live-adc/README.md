# Независимая native-проверка ADC + TIME + live endpoint

Проверяемая область: программная композиция `mt_live_adc`, настоящего драйвера
ADC/DMA, TIME, `mt_live_clock`, endpoint, очереди и wire codec. Исходники
компилируются раздельными translation units. Подменённых `read_block`,
`timebase_snapshot`, stop-сертификатов или live snapshot в основном пути нет.

Результат для зависимостей commit
`24e597444f03b3826b3affd99f79653f0d74c9f5`: **22 группы, 550 сценариев PASS**
в строгом C11 и ASan+UBSan; **10/10** поведенческих mutants компилируются и
отклоняются конкретным assertion. Это результаты тестов, не независимый
приёмочный вердикт и не аппаратная квалификация.

## Воспроизведение

Нужны Python 3 и C11-компилятор с ASan/UBSan; зависимости Python и сеть не нужны.
Проверено на GCC 14.2.0, x86-64 Linux. Из корня репозитория:

```sh
work="$(mktemp -d)"
python3 tests/live-adc/run.py --source . --output "$work/native"
python3 tests/live-adc/mutations.py --source . --output "$work/mutations"
python3 tests/live-adc/check_runner.py --source . --output "$work/controls"
```

Вместо точки можно указать корень полного source-only экспорта. Отдельный
каталог source-24e5974 в репозиторий не включён. Из другого каталога укажите
полный путь к runner и корню исходников.
Любой output обязан быть новым каталогом вне исходников. Уже существующий
каталог, даже пустой, и dangling symlink отклоняются. Все parent-каталоги output должны существовать.
Результаты не перезаписываются. Папки с пробелами проверены отдельным запуском.
Одна группа, например `--case queue_full`, запускается тем же runner.

`source-manifest.json` фиксирует SHA256 всех 14 C/H входов. Runner проверяет
каждый вход до создания output, копирует проверенные байты в output/source и
компилирует эту копию. Поэтому одновременное редактирование рабочего checkout
не смешивает версии при компиляции. В provenance записываются manifest,
хеши C/runner, команды, return codes и настройки sanitizer. Тождественные
зависимости допускаются в source-only архиве и более позднем documentation-only
commit; manifest не утверждает, что весь такой checkout имеет старый HEAD.

Санитайзеры настроены fail-fast: `-fno-sanitize-recover=all`,
`halt_on_error=1`, ASan `abort_on_error=1`. Только LeakSanitizer отключён:
он завершился собственной ошибкой «does not work under ptrace» в этом executor.
Исходный неуспешный запуск сохранён. Ни код прошивки, ни C-модель не используют
динамическое выделение памяти. Исполнение ASan и UBSan подтверждено отдельными
canary с намеренными ошибками, ненулевыми exit codes и нужной диагностикой.

## Что проверяется

- Настоящие закодированные HELLO/CAPS/START/STOP; декодированные ACK, DATA, END,
  sequence/session/request, поля профиля 50 kS/s, поколения и границы prefix
- Все хвосты от 0 до 255 отсчётов на обоих положениях половин DMA-кольца:
  полный pending half, короткий хвост, оба сразу, пустой STOP
- Порядок нелинейного 12-bit шаблона каждого DATA, независимый CRC-32/ISO-HDLC
  oracle, последний DATA sequence, END перед STOP_ACK; TX по 3/7/64 байта
- Изменение уже остановленного DMA-кольца не меняет зафиксированные данные TX
- Queue FULL после прочтения 17-го реального блока: драйвер уже учитывает 2176
  отсчётов, сохранённый prefix остаётся 2048. Ещё 7 DMA transfers во время STOP
  попадают в сертификат, но не проходят через зафиксированный разрыв очереди
- TIM/ADC/DMA cleanup failures, повторный успешный stop после неудачного,
  DMA TE, первая причина перед ошибкой cleanup, пропуск service ≥2560 us
- TIME fault до service и после успешного реального read; в последнем случае
  прочитанные 128 отсчётов не предлагаются очереди
- DMA TE перед TIME fault в service и в прямом STOP, включая cleanup failure:
  первоначальная причина 2 сохраняется, DATA/END/ACK не придумываются
- DMA TE при transport loss, protocol recovery и свежем HELLO до старого
  START_ACK; повторный raw barrier с уже добавленным cached tail не создаёт
  ложную acquisition error из-за старого first_index
- Отказы раннего и обоих timed admission checks, смена original actor до/после
  CEN, восстановление прежней маски guard, зависшая calibration, неверный profile
- Stale generation в настоящем START callback при ещё остановленном драйвере;
  прямые stale/zero вызовы и stale STOP; CRC-invalid START не запускается
- Холодный active/unowned TIM/ADC/DMA не перехватывается и не объявляется off
- Неверные аргументы, обязательный timed guard, неизменность binding при
  неуспешном init, STOP до завершения START_ACK, повторный frozen stop snapshot
- Явно seeded white-box границы около UINT32_MAX и UINT64_MAX: последующие
  transfer/read/stop настоящие, индексы не сужаются; overflow даёт reason 4

Для успешного аппаратного stop wire `produced_end` остаётся UNKNOWN:
`produced_known=0`, `produced_end=0`, END/ACK flags=3 даже у пустого STOP.
Неуспешный hardware barrier даёт flags=7 и исключает STOP_ACK. Это намеренное
ограничение binding: число завершённых DMA transfers не доказывает полный drain
ADC/request. STOP_ACK с flags=3 не является свидетельством clean completion.

## Negative controls и история

`mutations.py` в отдельных копиях проверяет: выдуманный known end; потерю
последнего tail sample; сужение tail index до u32; удаление generation check;
допуск untimed guard; игнорирование TIME health; переписывание transfer reason;
выдуманный hardware_off; сдвиг offered index; скрытие queue rejection.
Каждый mutant обязан успешно компилироваться и падать по assertion нужной группы.
Ошибка сборки не засчитывается как пойманный mutant.

`check_runner.py` воспроизводит полную suite после переноса C/runner/manifest и
source-only зависимостей в пути с пробелами, проверяет отказ повторного output
без изменения старых файлов, отказ source mismatch до создания output и
fail-fast sanitizer canaries.

Сохранены отрицательные воспроизведения commit
`206aba0779ddef57e91ec2749f062afa1ec0aa98`:
- service TE + TIME fault менял endpoint reason с 2 на 3
- STOP TE + TIME fault оставлял endpoint reason 0 вместо 2

Root исправлял production самостоятельно. Эта suite не меняла production.
Фиксы проверены на точных финальных зависимостях; подробнее в PROVENANCE.md.

## Что не проверено

Это register model, не измерение STM32 или имитация аналогового преобразования.
Явно заданные transfer events пишут DMA-кольцо и меняют CNDTR/HT/TC, а реальные
production функции читают эти регистры. CNT продвигается в модели; длительность
C/IRQ/MMIO на целевом MCU здесь не измеряется. Нативный guard проверяет актор,
свежий TIME и restore mask, но не доказывает фактическую атомарность PRIMASK.

TX представляет синхронную упорядоченную копию; нет native USB, USB epoch owner,
платы, Windows host, durable file, legacy owner/boot integration, real-time
service qualification, поздних физических ADC requests или гарантии полного
acquisition/drain. C↔HOST в этой итерации не запускался. White-box u64 seeds
не означают фактический захват миллиардов отсчётов. ASan не доказывает отсутствие
всех ошибок памяти; LeakSanitizer здесь не работает.
