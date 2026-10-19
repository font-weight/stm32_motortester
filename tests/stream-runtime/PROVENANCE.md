# Происхождение и привязка доказательств

## Production inputs

Основной источник: checkout `stm32_motortester`, commit
`cc2c938b122cc580d959740e16d11a2ac046b265`.
Полная байтовая привязка compile/include/patch/generator inputs —
`source-manifest.json` (177 файлов). Доступ к Git при replay не требуется:
истиной проверки служат закреплённые байты, а не совпадение имени каталога.

- `firmware/app/mt_live_runtime.c` SHA-256:
  `dff94c2def43397ab6be083f51e4cb0240241d0a63837702e18a57ea29243d01`
- `firmware/app/mt_live_runtime.h` SHA-256:
  `900f229fe02997e7d8c2e1dbe12c8386923fb46b7a3a4f8666e9044d9edd2562`

Runner отдельно компилирует production translation units. Production-код
не копируется в переносимый пакет, не встраивается в fixture и не меняется
ради результата. Копия исходников, созданная при запуске в OUTPUT, — отдельный
проверенный staged input; она не является содержимым переносимого пакета.

## Fixture ancestry

Фикстура разработана для этого runtime owner. Использованы проверенные
приёмы и интерфейсы существующих тестов:

- `tests/runtime-clock/run.py`: отдельные strict/sanitizer сборки,
  setup/reset/bridge patch chain, fake-DCD instrumentation, manifests/receipts.
- `tests/current-ab-reproducer/supporting_runtime.c`: register bus,
  save/restore PRIMASK, enumeration helpers и real TIME/ADC integration.
- `tests/current-ab-reproducer/test_integration.h`: interface fake-DCD hooks;
  неизменённая небольшая test-only декларация включена в этот пакет.
- `tests/usb-cdc/supporting/fake_dcd.c,h` и
  `tests/usb-bridge/supporting/instrument_fake.py`: берутся из pinned checkout
  во время запуска. Они не поставляются отдельной копией в пакете.
- Предшествующая native live-ADC composition fixture: pattern синтетических
  DMA codes, отдельный битовый CRC oracle и проверка полей END/STOP_ACK.
- Две отдельно проверенные дополнительные пробы включены в общую фикстуру:
  positive partial FIFO acceptance и сохранение short FIFO при soft pause.

Исходные parser/controller/ADC/TIME outputs не подменяются. Все `--wrap`
функции вызывают `__real_*`; кроме явно объявленных MMIO/DCD/IRQ/CTR моделей
нет fake production implementation.

## Историческая разница cleanup/TIME

Проверка 37 случаев на предыдущем
`161f36034cac547a22b9ac269983b80ee96ec2ac` прошла strict и ASan+UBSan.
`baseline-manifest-161f360.json` сохраняет байтовую привязку того снимка.
В injection CLOCK_STOP во время hard cleanup тот код сначала подтверждал
реальный для модели off и публиковал cleaned=processed_reset=2, затем
возвращал TIME_FAULT. Это наблюдение не скрыто и не переименовано в успех
нового требования.

`cc2c938` ужесточил порядок: fresh TIME проверяется после quiesce и до receipt
и RESET dispatch. Текущий regression требует cleaned=processed_reset=0,
TIME_FAULT в этом же step, pending hard cleanup и повторную попытку без receipt.
Текущую фикстуру нельзя выдавать за byte-identical исторический reproducer.
Baseline manifest — историческая привязка; runner использует только основной
`source-manifest.json`.

## Что сохраняет запуск

`receipt.json` содержит source hashes до/после, hashes staged inputs,
package-file hashes на входе, identity компилятора, Python, очищенное окружение,
каждую фактически запущенную команду/exit status и результаты каждого case.
Stdout/stderr лежат отдельно в том же OUTPUT. Неверный hash, compile failure,
case failure, sanitizer failure или input drift завершает запуск ошибкой.

Итоговые журналы и промежуточные неудачные запуски остаются вне пакета.
Исправления промежуточной фикстуры включали корректный <2560 us interval в
краю 255 transfers, реально queued RESUME вместо незаказанного SOF, C integer
promotion в CRC oracle и DTR CONTROL_STAGE_ACK/healthy setup opportunity.
Это не были изменения production-кода или физические измерения.

Полная acceptance оценка проекта, ARM full image, hardware/USB/timing
qualification этим пакетом не выполняются.
