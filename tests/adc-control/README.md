# IO-001: воспроизводимая публикация черновика

Связано с issue #32. Владелец реализации — Dot A. Публикация сохраняет
все 126 файлов базы PR35 `0ddea1e5992caa6231203838c3956e19675867a8`
с исходными Git blob SHA и режимами. Контроллер и зависимости не переписаны.
`host/**`, workflow, startup, USB и общие документы базы не изменены.

Из PR27 `5215177100453c6f77e5c5a637197fd626e57aec` импортированы C/H
драйвера, его README и исходная регистровая модель теста. Модель сохранена
побайтно. Dot A исправил в README только ссылку на исходные тесты, закрепив
её на immutable PR27, и добавил ссылку на актуальный ADC_CONTROL_README.
Единственная разница C/H — функция и декларация
`mt_adc_dma_profile1_ready`, без MMIO-записей. Точная разница:
[driver-delta-from-pr27.diff](evidence/driver-delta-from-pr27.diff).
Новый адаптер, русское описание и авторский тест скопированы без правок
публикатора. Точные хеши текущих исходников — [source-provenance.json](source-provenance.json).

## Проверки из корня репозитория

```sh
OUT=/tmp/io001-author sh tests/adc-control/run.sh
OUT=/tmp/io001-control sh tests/control/run.sh
OUT=/tmp/io001-evidence python3 tests/adc-control/reproduce_evidence.py
```

Для последней команды использовать новую папку OUT. Требуются Python 3,
Bash и GCC с ASan/UBSan. Скрипт строит временные копии по точным SHA,
не изменяя исходники. LeakSanitizer отключён; проверка утечек не заявлена.
Все три команды лично повторены публикатором. Сохранённые логи:
[автор](evidence/author-reproduction.log),
[контроллер](evidence/controller-reproduction.log),
[независимые проверки](evidence/evidence-reproduction.log).

- Авторская регистровая модель, composed ADC/control/wire путь и неизменённые
  тесты контроллера: strict C11 и ASan/UBSan PASS.
- Поддерживающий независимый набор: 313 полных синтетических окон,
  641 024 точных исходных кода, все 128 позиций остановки внутри блока,
  режимы/предыстория, отказы и переполнения времени; strict/sanitizers PASS.
- Независимый Critic-A oracle: 10 139 сценариев, 1 280 000 меток отсчётов,
  strict/sanitizers PASS. Это отдельная модель соседних API, а не замена
  составной проверки с настоящим драйвером/контроллером.
- Сохранённый reproducer прежнего дефекта корреляции часов возвращает exit 1
  на старом адаптере и exit 0 на финальном. Предыдущие source bytes, тесты,
  журналы, начальная ошибка компиляции теста и наблюдение сдвига 5 ms сохранены.
  Исправлена причина, история не объявлена проходной проверкой новой версии.

Исходный [supporting report](evidence/supporting/REPORT.md), runner и manifest
сохранены без редактирования. Его snapshot включает старую версию авторского
теста; этот файл не компилируется независимым runner. Он восстановим через
`overrides/`, как и прежние C/H адаптера и исходная версия platform README
до исправления ссылки. Общие исходники и `tests/control`
повторно в PR не добавлены. Путь `snapshot/` в старом отчёте означает
восстанавливаемую временную копию, а исходные абсолютные пути — происхождение.

## Ресурсы и независимое заключение

[ARM report](evidence/arm/RESOURCE_REPORT.md) содержит команды, версии,
исходные хеши и ограничения. Два повторных relocatable-build дали одинаковый
SHA-256 `ae149ec3c091cf67d7ba3a94b5b3f16d2905b8b772f721ff106cf1957a9a9e2b`.
Сумма text/rodata — 11 281 B; модель владельца — 10 880 B; известная
Stop-цепочка стека — 2 944 B. Это не финальный ELF/linker map и не доказанный
остаток ресурсов. Объектные файлы и исполняемые инструменты не публикуются.

[Отчёт Critic-A](evidence/critic-a/CRITIC_A_ADC_CONTROL_REVIEW.md), SHA-256
`5f8edac107e67d1b1d6ccef4fa1a40631dccde722266e2e9cfc5df52cfa411de`:
**BLOCKED_EVIDENCE, score null**. Привязка к опубликованному commit оформляется
самим критиком отдельным комментарием после remote-readback, не самоприёмкой
публикатора. Не закрыты coherent startup clocks, WCET/service <2560 us,
реальные IRQ/DMA/USB, полный link/map/stack, Windows и низковольтные измерения.
Черновик не является software-ready, low-voltage-verified или ignition-verified.
Задача остаётся открытой; merge и приёмка не выполняются.

## Диагностика раннего отказа запуска (issue 188)

Черновой `mt_adc_first_start_failure` — отдельная volatile-запись из 13 слов
(52 байта) в SRAM, а не новое сообщение USB. Она сохраняет первый отказ
binding start после загрузки. Успешный запуск запись не создаёт; Stop,
recovery, повторный HELLO и повторная инициализация binding её не очищают.
Обнуление происходит при reset через BSS; это не энергонезависимый журнал.

Порядок слов: magic, version, stage, command_ms, epoch_ms, before_us,
after_us, driver_called, driver_result (signed int32), driver_fault,
driver_state, start_before_us, start_after_us. Magic = 0x4d544431, version=1.
Stage: 1 — ширина корреляции часов >20 мкс; 2 — разность epoch_ms-command_ms
>=0x80000000; 3 — driver вернул не OK; 4 — проверка nominal clock/задержки
после успешного driver start не прошла. Разности считать modulo 2^32.
При driver_called=0 поле result не означает успешный вызов: вызова не было.
Driver fault/state и CEN timestamps — сырой снимок возвращённого контекста:
внутренний cleanup драйвера уже мог заменить fault, а ранний BAD_ARGUMENT/BUSY
может оставить timestamps прошлой попытки. Это не универсальный first-fault
журнал всех подсистем и не физическое измерение времени.

Добавленных clock callbacks нет. Payload записывается только после отказа,
вне обеих измеряемых временных скобок; magic публикуется последним. При живом
чтении отладчиком сначала отдельно прочитать magic; только если он уже равен
ожидаемому, читать payload. До reset готовая запись больше не изменяется.
Не принимать payload, прочитанный до проверки magic: запись могла ещё идти.
Адрес символа получать из **точного нового ELF**, старые SRAM адреса не
переносить. Reset и отключение питания уничтожают запись. HotPlug документирует
отсутствие halt/reset, но не нулевое воздействие SWD на неизвестную плату.

Тесты покрывают границы 20/21 мкс, wraparound, command delta
0x7fffffff/0x80000000, возвраты BUSY/BAD_ARGUMENT, calibration и guarded
admission failure, postcheck clocks/delay и сохранение первой записи после
recovery/Stop/re-init/следующего старта. Старые synthetic capture tests остаются.
Это не разрешение прошивки. Исторические hash-pinned builders намеренно
отклоняют изменённые source/object bytes; их pins не ослабляются. Для нового
диагностического образа требуются отдельные ARM/link/RAM/stack доказательства
и независимое ревью. Низковольтная приёмка остаётся открытой.
