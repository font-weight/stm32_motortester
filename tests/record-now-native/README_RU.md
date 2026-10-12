# Черновая интеграция «Запись сейчас», wire 0.2

Это отдельный программный кандидат по предложению PR #141. Он не является
принятым общим контрактом, готовой прошивкой или подтверждением захвата всей
физической искры. Исходная база: 3d32073fee40aec270b242e86689d78b4804a2ad
(PR #135, ядро и ранее проверенные relocatable objects).

## Изменение

- Строгая версия wire 0.2, без смешивания с 0.1 и без автоматического отката.
- Команда 8 содержит config_id и запускает Single/source0/pre0/timeout0.
  Первый новый допустимый отсчёт сохраняется как sample[0].
- HELLO_ACK: capability_bits=113; CAPS: mode_bits=4, source_bits=1, edge_bits=3.
- ARM сохраняет аналоговый Single-запуск. Общие внутренние Auto/Normal API
  и их модульные регрессии не удалены.
- ACK допускается в очередь до аппаратного запуска; это не означает, что
  компьютер уже получил его. Далее используются прежние реальные guarded
  ADC, sample, Stop, fault, END и Release пути.
- Приложение и формат файлов принадлежат Dot B; host/** здесь не изменяется.

## Воспроизводимые проверки

Запускать из корня checkout, с доступными Python 3 и GCC:

```sh
sh tests/control/run.sh
sh tests/control-rx/run.sh
sh tests/adc-control/run.sh
sh tests/record-now-native/run_control.sh
python3 -B tests/record-now-native/check_vectors.py
python3 -B tests/record-now-native/run_runtime.py --out /tmp/record-now-runtime-fresh
```

Последняя директория должна быть новой и находиться вне checkout.
Во время runtime-проверки не менять её входы: runner записывает SHA-256
до/после, команды, полные журналы и результаты. Исторические доказательства
не заменяются. Санитайзеры: ASan+UBSan; обнаружение утечек выключено из-за
ограничения executor/ptrace. Проверка векторов отдельно запускает UBSan.

| Проверка | Что действительно проверяется |
|---|---|
| Controller | Некорректная длина, stale/busy, pre512/1024 без изменения CONFIG; profile/ACK queue/start/stop failures; Stop до первого sample; полные 2048 кода/CRC при writer1/7/64; Release без автоперезапуска; следующий ARM без залипания software-marker |
| Link | Сохранённые generic Auto/Normal/Single регрессии; отдельно type8 END до первого принятого байта и после него; 4999/5000ms, поздний и повторный Release |
| Stop barrier | 103 сценария, 6926 утверждений на новом коде; изменён только новый смысл type8/length0 и допустимый Single CONFIG |
| ADC-control | Реальный register-model драйвер; analog17 и immediate16 половин DMA; точные массивы, timestamps/wrap, stale Stop, transfer/deadline fault до/после allocation |
| Векторы | 35 фиксированных frame bytes из PR141, полное decode/encode равенство для0.2; реальные0.1 отказы, CRC и неизменность output при ошибке; parser type8 |
| Guarded runtime | 29 сценариев для ARM и RECORD_NOW: 58 запусков strict и58 ASan/UBSan. Пять сценариев не зависят от операции; это не116 разных свойств |

Runtime собирает 16 production-модулей, четыре upstream TinyUSB unit и
явно сгенерированный fake DCD. Используются настоящие runtime/ADC guards,
USB core/CDC/FIFO и controller; MMIO, часы, IRQ masking и DCD моделируются.
Применённые USB patches и effective configuration проверяются явно.

## Происхождение fixtures

- test_runtime.c явно произведён от
  tests/current-ab-reproducer/supporting_runtime.c,
  SHA-256 836eff699265d37f6c50cd9936065bc7585afae2211cc826b2a79617adcd114d.
- Неизменный test_integration.h читается из той же исторической директории,
  SHA-256 9d233d779f7dda40c938627c4169f852f5aaba8a6aa1f8dcf936f289b6f67053.
- test_stop_barrier.c произведён от tests/wait-release/stop/test_stop_barrier.c,
  SHA-256 979cbede3d78c2647e376f9a1dc674daf33b59312917f7845b6d2c48dcd5ec51.
- vectors.json побайтно скопирован из PR141
  2e6faa55e889ed909098d0b80c94233cbf87bd63,
  SHA-256 f5bc85ef9756f9177d1713c104938454457ea701e71feff6d38fab3d282630ea.
  Его legacy_* поля описывают старый0.1 oracle, а не результат нового0.2.

Исторические файлы и их manifest оставлены неизменными. В новом runtime:
явно выбран ARM/type8, CONFIG Single, нет autorearm, разные16/17 DMA halves,
проверяются первый код, BEGIN kind/index/quality и Stop активного окна.
Старый deadline oracle ожидал полный recovery. В текущей базе этот fault
уже serviceable: STATUS state5/reason2, session остаётся, hardware остановлено.
Новый тест проверяет это поведение и отсутствие ложного окна/повторного запуска.
Первый запуск выявил именно устаревший oracle; второй выявил ошибку смещения
поля STATUS в новом тесте. Оба исправления относятся к тесту, не к production.

## Ограничения и следующие ворота

Авторские исполнения: controller/link/barrier/parser strict+ASan/UBSan,
35+35 fixed vectors и58+58 guarded-runtime запусков прошли 12.10.2026.
Независимый вердикт должен ссылаться на итоговый SHA, а не на эту запись.

Source/object/image pins намеренно не обновлены и остаются fail-closed.
Ранее полученные ARM objects, ELF/RAM/Flash/stack/IRQ proofs не переносятся
на этот кандидат. Нужны новая target-сборка и применимые доказательства,
независимые Critic-A/Critic-B и действительный paired host/file replay.
Не запускались плата, физический USB/ADC, Windows-приложение с0.2 или
зажигание. Полнота2048 отсчётов и CRC не доказывает начало, горение и
затухание физического разряда.
