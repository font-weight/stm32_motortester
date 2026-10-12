# Независимые синтетические проверки analog Auto / Normal / Single

Это область независимого автора тестов Dot B, не итогового Critic-B. Здесь нет
балла, приёмки, утверждения общего wire или аппаратной готовности. Изменяются
только новый `test_acquisition_modes_conformance.py` и эта новая папка fixtures.
Авторские helpers и прежние manifests не импортируются и не исправляются.

## Основания и происхождение

- Локальная исходная база: detached `ef2880c0655d155284c176424a12378b3b66adf4`
  плюс точный замороженный overlay HOST008 из 36 файлов. В исходном worktree
  проверено 165 файлов, запись 145 host-хешей — `dependency-before.json`.
- SHA256 исходного overlay manifest:
  `a4596d0f5eea1318841390600bcc763d9aa02ad66649ec4a06fece3de37a0042`.
  Overlay не объявляется самостоятельным immutable commit.
- Последующая публикация этих же байтов, подтверждённая координатором:
  PR69 `45ef38792b8e824e2109e7bacc1c9dc605917d06`. Полная запись, tree и
  квалификация источника сообщения — `publication-mapping.json`. Тестовый
  worktree не перебазирован; публикация не означает приёмки зависимости.
  Позднее координатор лично сравнил145/145 опубликованных host blobs
  с исходным baseline: все пути/хеши совпали (`published-binding.json`).
- Неизменные источники payload/state: session `b6ae8025baf1a47e8173997029d2d8b858ada74c`,
  emulator `c54bd955aaf3cb863ef3df043ea237ea62e74ad4`, receiver
  `124b3f32e1e7f7282bce283baa4c9406d4d72950`. Прочитаны через git show
  каждого SHA:protocol/wire-v0.1.md. Все три draft остаются непринятыми.
- PR52 `2017576dbe104f4c43d8d9feac28436589191fd1` с новой StopRX policy —
  только планирование. Никакое обновление source pin или молчаливое принятие
  этой более поздней нормы здесь не выполняется.

`reference.py` самостоятельно пакует ожидаемые поля, вычисляет побитовый
CRC-32/ISO-HDLC и COBS. Эталон CRC «123456789» проверяется отдельно. Formula
2048 синтетических кодов основана на опубликованном HOST007: при реальном
событии i=0 — level либо level−1; при Auto timeout — обычная формула без
искусственного crossing. Rails на i=511 и i=1023 действительно присутствуют.
Это синтетическая последовательность, не модель физического АЦП.

## Границы согласованного API и проверок

Единственное новое открытое constructor поле — строго bool keyword
`DeviceEmulator(*, all_analog_modes=False)`. Default остаётся Normal. Opt-in
объявляет caps0x1d/modes7/sources1/edges3/pre_count1/allpre0. Config, clocks,
SessionController и WindowReceipt.from_window сохраняют прежние сигнатуры.

Матрица тестов:

- Все шесть mode×edge настроек, level1/4094, Auto100/60000, exact ACK bytes;
  default capabilities и сохранение opt-in после нового HELLO/disconnect.
- Capability mismatch, неверные типы/огромные числа/пределы и неизменность
  состояния до проверки сроков; raw CONFIG не заменяет прежние настройки.
- Normal/Single без события ждут даже после1000/3000/60000; Auto before/exact/
  after и большой скачок дают максимум одно окно, без накопления backlog.
- Pre0 готова при исполнении ARM, даже до полного ARM_ACK; отдельные1000 ms
  command и3000 ms receiver-progress deadlines; все time entrypoints expiry.
- 20 новых окон через реальные байты и StreamDecoder → SessionController →
  WindowReceiver → WindowReceipt.from_window → RELEASE: 10 Auto под одним ARM
  с чередованием real/timeout, 10 Single с явным ARM и новым generation.
  Побайтные BEGIN/DATA/END, все2048 samples и CRC сверяются независимо.
- Последний байт RELEASE_ACK открывает следующий Auto/Normal wait; Single
  становится READY/STOPPED. READY с pending Stop разрешён, но ARM/CONFIG
  заблокированы до STOP_ACK. Ошибочное более строгое ожидание сохранено в
  `evidence/expanded-focused.log` и разобрано в `evidence/diagnoses.md`.
- Factory сама отвергает incomplete/CRC/rails/deferred, повреждённые поля,
  fragments, samples и несовпадающий context; assertRaises не скрывает
  permissive factory внутри вызова release. Session дополнительно связывает
  receipt с текущими session/generation/window и не разрешает повторный release.
- Raw RELEASE с неверными id/CRC, exact duplicate, повторным request, заменой
  tombstone, поздним release и сохранением нового held window; Single/Stop
  не вооружаются повторно от позднего ACK.
- Stop и новый HELLO на expiry, allocated, BEGIN first byte, DATA first byte,
  END до/после first byte, полном END и частичном RELEASE_ACK. Проверяются
  единственный terminal, целость начатого кадра и отсутствие data после ACK.

Точное равенство Auto deadline обрабатывается expiry-before-input — выбранная
локальная политика детерминированного эмулятора, а не дополнительное требование
wire. Срок5000 ms от последнего байта END остаётся прежним PROVISIONAL
допущением; он проверяется отдельно и не называется согласованным контрактом.

## Воспроизведение и результаты

Из корня checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen \
  python host/tests/fixtures/acquisition_modes/run_suite.py --require-gui
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen \
  python -O host/tests/fixtures/acquisition_modes/run_suite.py --require-gui
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen \
  python host/tests/fixtures/acquisition_modes/run_evidence.py \
  --output host/tests/fixtures/acquisition_modes/evidence/final-v1
```

Runner импортирует настоящие PySide6/numpy/pyqtgraph и делает любой skip
ошибкой. Evidence runner сохраняет before/after host-хеши вне изменяющейся
папки собственных evidence, команды/exit codes, stdout и реальные host/device
byte captures; существующие файлы не перезаписывает. `author-frozen-v1.json`
фиксирует предоставленные автором семь файлов. Исторические исходные логи
сохраняются. Фактические результаты окончательного запуска следует читать в
новом manifest; само наличие команды не означает её прохождения.

Не проверены Windows10, сборка Windows, USB, прошивка, ADC/DMA, физическая
частота, реальные пороги, низковольтный стенд и зажигание. Внешний source,
предыстория512/1024, GUI/runtime и disk recording не входят в эту итерацию.
