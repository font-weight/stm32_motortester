# Critic-B: HOST-002, ограниченный host byte/stream codec

Дата: 10.10.2026. [Issue #10](https://github.com/font-weight/stm32_motortester/issues/10),
[PR #15](https://github.com/font-weight/stm32_motortester/pull/15).

**PASS, 10,0/10 для bounded HOST-002.** Все заранее заявленные в #10
software-component критерии выполнены. P0/P1/P2-дефектов новых файлов не найдено.
Этот PASS не принимает общий wire-контракт и не подтверждает session/USB/Windows.

## Точная версия и область

Head **`c89c12df984390b34785eba81665abd6418658e2`** подтверждён GitHub API.
Родитель реализации/merge-base: `7d74cdf1cb04d8d3cbd9edcd06d6b23a27007392`.
Текущая PR base-ветка указывает на `2376d325d573b1aa3da6feaf8fddf05d2e1cb601`;
это не родитель проверенного head. Diff задачи — 13 новых host-only файлов,
1994 строки, без изменений GUI/recording/model/A-owned source.

Экспериментальный формат закреплён на
`124b3f32e1e7f7282bce283baa4c9406d4d72950`; потоковая ссылка —
`8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7`. Общий контракт остаётся draft.

Независимый Critic-B, gpt-6-astra xhigh; не автор wire.py, product-тестов или
conformance suite. Все 13 файлов прочитаны; проверки выполнены из собственного
чистого `git archive` head. Новые независимые harness/логи — только review artifacts.
Критерии issue #10 и quality-gate v1.0 не менялись.

## Выполнение критериев #10

| Критерий | Проверено |
|---|---|
| Framing/endian/COBS/CRC | Фиксированные внешние векторы, все 513 длины payload, предельный 544-byte кадр, отдельный CRC/COBS oracle и C |
| Ограниченные вход/память | Exact bytes/int API, payload≤512, encoded≤543, chunk≤4096, фиксированный buffer, ≤137 frames/call и агрегированные bounded diagnostics |
| Fragmentation/coalescing | Все двухчастные split девяти векторов, побайтный максимум, случайная нарезка и объединённые сообщения |
| Отрицательные данные | CRC/length/framing/reserved/version, усечения, переполнение, garbage storms и восстановление до следующего delimiter |
| Время | Точные 999/1000/1001 и 2999/3000/3001, idle tick не продлевает кадр, rollback/uint64 bounds, reset timebase |
| Immutable output/diagnostics | bytes/frozen tuples, прежние результаты сохраняются после buffer reuse/reset; фиксированные русские тексты без отражения входа |
| Независимость от семантики | Frame не считается командой/измерением, type/payload opaque, нет session/window/USB API |
| Воспроизводимая C-сверка | Положительные/отрицательные векторы и дополнительное прямое сравнение C/Python stream выполнены лично |

## Лично выполненное доказательство

Среда: Linux x86-64, CPython 3.12.14, GCC 14.2.0; baseline GUI тесты с настоящим
Qt/offscreen и закреплёнными dependencies в `/tmp/motortester-deps`.
Исходный архив: `/tmp/critic-b-host-wire-c89/source`.

1. **141 тест, 17,223 с, OK** из exact archive. Это 75 прежних baseline,
   16 author wire и 50 отдельно написанных conformance tests; последнее само
   по себе не заменяло данное независимое заключение.
2. Воспроизведён опубликованный `compare_c.py`: **522 положительных** encode/decode,
   **49 отрицательных** decode, 2 отказа encode и 2 CRC check; pinned source hashes
   совпали. В C-ветки ничего не копировалось/не изменялось.
3. Лично проверен запрет `python -O`: ожидаемый exit 1 до тестирования/компиляции.
4. Все **12** source/evidence SHA-256 из опубликованного run-manifest совпали
   с exact archive. Его оговорка о precommit tree корректна: реальные байты
   фиксируются manifest, текущий review дополнительно привязывает их к commit.
5. Дополнительный независимый harness:
   - **2052** трёхсторонних encode/decode: C, host Python, отдельная bitwise CRC и
     COBS-реализация с иной структурой цикла; четыре шаблона для каждого размера 0…512;
   - **50736** отрицательных decode: 736 одно-битовых повреждений и 50000 случайных
     кадров. Success/failure и применимые коды C/Python совпали;
   - **6242** вызова потока, **6461517** байтов: C per-byte stream и Python chunk
     stream выдали одинаковые frames, агрегированные diagnostics, used bytes и
     discard state. Включены valid/garbage/coalescing, exact timeouts, overflow,
     truncation, reset. Seed 20261010; host часы здесь не пересекают uint32 wrap.
6. C byte-файлы из stream pin совпадают с 124b3f32 по SHA-256; это тот же codec,
   а не скрытая сверка с изменённым протоколом. C/ARM ABI не приравниваются.

Команды основных повторов из exact archive:

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  XDG_CACHE_HOME=/tmp/critic-b-host-wire-c89/cache \
  PYTHONPATH=/tmp/motortester-deps:$PWD/host \
  python -m unittest discover -s host/tests -v
GIT_DIR=/workspace/scratch/d2a4dc03255e/motortester/.git \
  PYTHONDONTWRITEBYTECODE=1 python host/tests/fixtures/wire/compare_c.py
```

GIT_DIR нужен только потому, что clean archive не содержит `.git`;
compare script выполняет только чтение pinned объектов. Постоянный
[независимый harness и логи](evidence/host-wire-c89/README.md) содержат
точные исходники и результаты дополнительной проверки.

## Интеграционные условия, не скрытые дефекты scope

- В StreamResult теряется чередование ошибок и успешных кадров; это явно описанный
  bounded API. Будущий потребитель обязан сначала обработать diagnostics,
  особенно VERSION, и только затем допускать результаты к session/window policy.
- `now_ms` host — монотонный uint64 без wrap, C — uint32 с modular elapsed.
  Это различие локального clock API документировано и проверено; транспорт должен
  вызывать feed сразу после чтения и tick регулярно. Физические задержки не измерены.
- Head основан на **старом HOST-001 7d74**. Его известные file-encoding/overwrite
  замечания не исправляются этим new-file diff и не получают нового PASS.
  Перед общей сборкой включить уже проверенные исправления 2376 и проверить точный
  интегрированный SHA. Данный PASS относится к **новому HOST-002 компоненту**, а не
  ко всему состоянию приложения на c89.
- W-02 Auto/external, shared TX/Stop evidence и прочие общие gates по предыдущим
  wire reports остаются открытыми. Модуль их не интерпретирует и не маскирует.
- Ни serial I/O, ни firmware/session/window-команды, ни Windows build, ни устройство
  не исполнялись и не входят в заранее заявленные критерии #10.

## Фиксированная рубрика

| Критерий | Вес | Балл | Основание |
|---|---:|---:|---|
| C1 | 30% | 10 | Побайтовая совместимость с pinned draft/независимыми ожиданиями и корректные bounded stream результаты |
| C2 | 20% | 10 | Чёткие пределы входа/выхода, строгая валидация до мутаций, noise/timeout/reset и неизменность результатов |
| C3 | 25% | 10 | Чистый SHA, все обязательные сценарии, воспроизведение C и собственная независимая сверка |
| C4 | 15% | 10 | Opaque API, фиксированные диагностические тексты и явные границы интерпретации/несовместимости |
| C5 | 10% | 10 | Закреплённые источники, проверенный manifest, команды, артефакты и честно перечисленные ограничения |

Итог: `0,30×10 + 0,20×10 + 0,25×10 + 0,15×10 + 0,10×10 = 10,0`.
Все обязательные свидетельства этой bounded задачи имеются. Отсутствие общих
wire/MVP свидетельств не переименовано в PASS: эти объекты принимаются отдельно
обоими критиками. Изменение базы или реализации требует актуального exact-SHA
заключения; старый результат автоматически не переносится.
