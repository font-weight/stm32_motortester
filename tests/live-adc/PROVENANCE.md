# Происхождение и доказательства

Автор этих тестов не редактировал production. Root самостоятельно создал
binding и исправления endpoint. Независимый Critic отдельно проверял код,
воспроизводил first-cause дефекты и запускал свои регрессии; эта папка содержит
тестовый отчёт, не выдаёт вердикт Critic и не повышает уровень hardware evidence.

## Зафиксированные production входы

Финальный manifest: commit `24e597444f03b3826b3affd99f79653f0d74c9f5`.
14 входных файлов (7 C + 7 H) перечислены с SHA256 в source-manifest.json:
- firmware/app/mt_live_adc
- firmware/platform/adc_timer_dma, mt_timebase, mt_live_clock
- firmware/protocol/mt_live_device, mt_wire
- firmware/acquisition/mt_sample_queue

В runtime linked ровно эти C, без подмены driver/time/certificate/queue/codec.
Основной combined fixture создан для этой проверки; модель использует
существующие проверяемые идеи с явным происхождением:

1. `tests/adc-stop-snapshot/test_stop_certificate.c` из проверяемого репозитория,
   SHA256 `b8761aa9fc22891adedbf3026d15c5a21fdae1a65eb702078da4c2fe670e350a`.
   Взяты register map, forward circular transfer/HT/TC модель, 12-bit pattern,
   calibration/self-clear и ignored-stop fault injection. В этой suite now_us
   заменён реальным mt_timebase_now_us, добавлен общий TIME register bus и
   настоящий live consumer. Сам исторический test не включается в compilation.
2. `tests/timebase/test_timebase.c`, SHA256
   `264ccbb8aa85ddd2cd1d798e9c8e33f7e1d5e4317ebce363f4d908307f382f52`, и
   `tests/coherent-clock/test_snapshot.c`, SHA256
   `44d1c9993305d5e4b3900740b0ae1c2400a276144ee32c3d8234acc3e98ad23b`.
   Заимствованы номинальная RCC-конфигурация, TIM2 CNT modulo-65536 модель и
   подход к регистровой инъекции fault. TIME snapshot не подменяется.
3. Существующая независимая live endpoint suite `test_live_device.c`, SHA256
   `f1aa80446bc38e8158dc5e9ed49fb2a3830c2260b4b3173a050dd6e30484e381`.
   Использованы wire IDs, helpers кодирования/декодирования и ACK/END offsets.
   Её synthetic acquisition snapshot и clock callback не перенесены.
   Endpoint получает реальный mt_live_adc и mt_live_clock.

CRC oracle здесь отдельный: нерефлектированный полином 0x04C11DB7 с отражением
входных байтов/выхода; production считает reflected 0xEDB88320. Каждый sample
проверяется отдельно, поэтому один совпавший CRC не является единственным oracle.

## Фактические записи

Перечисленные ниже исторические логи находятся вне репозитория в исходной
папке независимой тестовой работы и в этот каталог не включены. DELIVERY.json
фиксирует хеши переносимых входов после адаптации только документации.
Новый запуск runner создаёт собственные полные логи и provenance в выбранном
output; точный независимый вердикт и его хеш приводятся в сопровождающем PR.

- `frozen-native-24e5974/`: последний полный запуск с исходниками из actual
  root checkout, 550 scenarios strict и ASan+UBSan; входы проверены manifest
- `frozen-mutations-24e5974/results.json`: 10 успешно собранных assertion-killed
  mutants; команды, assertions и exit codes, отдельные compile/run logs
- `frozen-runner-controls/results.json`: relocation, source mismatch и два варианта защиты output,
  ASan/UBSan abort canaries; вложенная полная relocated suite тоже 550 PASS
- `baseline-206aba0-firstcause/`: assertion failure, реальный driver first_fault
  TRANSFER(4), endpoint first_reason 3 вместо 2
- `baseline-206aba0-stopcause/`: assertion failure, driver TRANSFER(4), endpoint
  first_reason 0 вместо 2
- `final-b055129-asanfix/`: промежуточные 539 scenarios после service/STOP fix;
  не заменяют расширенные финальные 550 и raw_barrier проверки

Runner хранит точные copies теста, runner и source manifest для каждого запуска.
Изменения теста после раннего запуска не переписывают старое evidence.

## Сохранённые неуспешные технические попытки

`final-b055129/` прошёл strict, но ASan+UBSan процесс завершился из-за
неподдерживаемого LeakSanitizer under ptrace. Следующий новый output отключал
только LSAN; ASan/UBSan оставались fail-fast. Этот сбой не назван PASS.

Два первых sanitizer-control output сохранены: первый ASan canary был
оптимизирован GCC в константу; второй ошибочно попал по соседнему живому stack
объекту, поэтому ASan не выдавал ожидаемую диагностику. В окончательном canary
volatile доступ ровно на один элемент за массивом попадает в redzone. Эти
контроли не маскировались и не засчитывались до финального нового запуска.

При разработке fixture исправлены неверный test-only DATA offset (24 вместо
контрактных20) и test-only момент service127 (нужно оставить запас <2560us до
STOP255). Это ошибки первоначального oracle/model, не дефекты production.
Финальный тест сервисит sample127 через15 моделируемых us после transfer;
это синтетический график, а не измеренный MCU бюджет.
