# Critic-B: HOST-001 layout, scoped Linux preflight

Дата: 10.10.2026. [PR #43](https://github.com/font-weight/stm32_motortester/pull/43).
Независимый Critic-B, gpt-6-astra xhigh; не автор реализации/тестов.

**Linux/Qt preflight кандидата: PASS, 10,0/10.** Новых дефектов в объявленной
области plot/annotation separation не найдено. Кандидат пригоден для следующей
экспериментальной Windows CI проверки.

**Закрытие исходного Windows failure: BLOCKED_EVIDENCE, score: null.**
Новый SHA на Windows в этом ревью не запускался. PASS ниже не разрешает считать
Windows-дефект окончательно закрытым, пакет собранным, PR готовым к merge или
продукт software-ready. Эти границы заданы до проверки, не изменены ради оценки.

## Точная версия и ограниченность изменения

- Head **`3b315ac4e1184c4c7e9216ce5594cff0f9beff33`**, проверен через GitHub API.
- Tree **`6fca4af819e48e624f83066cce72347c03f7d5ab`**, проверен локальным Git.
- Parent/base `2376d325d573b1aa3da6feaf8fddf05d2e1cb601`.
- Два файла: `host/motortester/gui.py` и `host/tests/test_gui.py`,
  45 добавлений/2 удаления. Весь diff прочитан. Production/test source не менялся
  критиком; выполнен собственный clean `git archive` точного head.
- Исходный `test_minimum_window_with_cursors_has_no_overlapping_plot_labels`
  **не ослаблен и не изменён**. Новый pressure regression добавлен отдельно.
- `build_windows.py` неизменён, blob
  `4a1b5d7184253a11d342ba1db909879a2515a184`; весь packaging diff пуст.
  Общий wire/драйвер/recipe/CI не редактируются этой правкой.

## Основание исправления проверено лично

Прочитан [старый Windows job](https://github.com/font-weight/stm32_motortester/actions/runs/38055733142/job/114223753438)
через GitHub connector. Лог подтверждает source 2376, runner image
`win22/20261004.326.1`, 77 тестов и один failure:
`plot.bottom=158`, `trigger.top=111`. Packaging не получает доказательства
успеха после падения этого шага. Это Windows CI runner, не испытание Windows 10
на пользовательском компьютере и не новое выполнение кандидата.

На Linux отдельно воспроизведена та же категория прежнего геометрического отказа
в source 2376: font 18 даёт 163>=112, font 22 — 147>=80; normal 13 — 188<198.
Это исполняемое сравнение, а не доверие только авторскому описанию.

Новое решение ограничивает preferred/minimum PlotWidget размеры, сохраняет
его minimum height 100 и переносит всю plot-card в resizable QScrollArea,
где layout minimum не сжимается ниже реальных child minima. При нехватке места
появляется вертикальная прокрутка вместо размещения label под фактической
областью plot. Минимальный размер MainWindow остаётся 1000×710.

## Лично выполненные проверки нового exact SHA

Среда: Linux x86-64, Python 3.12.14, pinned зависимости HOST-001,
Qt/offscreen. Архив: `/tmp/critic-b-layout-3b315/source`.

1. **78 полных тестов, 12,251 с, OK**.
2. **20 GUI tests при QT_SCALE_FACTOR=2, 21,545 с, OK**.
3. Собственный probe в двух отдельных процессах (scale 1 и 2): **30** сочетаний
   font 13/18/22 и последовательности размеров 1000×710 →1240×800 →1240×900
   →1600×1000 →1000×710. Во всех случаях:
   - plot.height≥100;
   - plot.bottom<trigger.top и trigger.bottom<cursor.top;
   - cursor label достижим прокруткой и его нижняя граница внутри viewport;
   - raw `encode(Capture)`, identity Capture и current_path неизменны;
   - после возврата к normal font/1240×900 scrollbar maximum=0.
4. Курсоры после циклов resize/scroll попадают на исходные индексы 512/562
   для 0/1 мс; zoom/reset не меняют raw. Checkbox остаётся рабочим.
5. Настоящие normal screenshots при scale 1/2 и pressure 22 осмотрены лично.
   Normal minimum-size GUI читаем, подписи не пересекаются. На pressure 18/22
   plot и labels разделены; scrollbar позволяет открыть нижние пояснения.
6. Smoke PNG полного Qt-окна успешно создаётся в обоих GUI suite после
   file save/reopen. **Пользовательский PNG export HOST-003 в этом дереве отсутствует**;
   его совместимость с будущей app-composition здесь не объявляется проверенной.

Измеренные minimum-size значения нового source (logical pixels, одинаковы при
scale 1/2 в этой среде):

| Font | Plot bottom | Trigger top | Scroll max | Cursor bottom / viewport height |
|---:|---:|---:|---:|---:|
| 13 | 188 | 198 | 0 | 279 / 293 |
| 18 | 166 | 176 | 91 | 228 / 242 |
| 22 | 171 | 181 | 213 | 167 / 181 |

Наблюдение за границами stress: искусственный font 22 всё ещё обрезает некоторые
sidebar/верхние надписи вне изменённой plot-card. Это прежняя ограниченность
фиксированной sidebar/нет общей функции смены шрифта; не новый дефект этой
bounded правки. Pressure screenshot **не** выдаётся за подтверждение полной
large-font доступности приложения. На реальной Windows нужно осмотреть также
нормальную компоновку, а не довольствоваться одними координатами теста.

## Доказательства и воспроизведение

[Логи, собственные scripts, JSON и настоящие изображения](evidence/layout-3b315/README.md).

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  XDG_CACHE_HOME=/tmp/critic-b-layout-3b315/cache \
  PYTHONPATH=/tmp/motortester-deps:$PWD/host \
  python -m unittest discover -s host/tests -v
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 \
  XDG_CACHE_HOME=/tmp/critic-b-layout-3b315/cache \
  PYTHONPATH=/tmp/motortester-deps:$PWD/host \
  python -m unittest discover -s host/tests -p test_gui.py -v
```

## Фиксированная рубрика v1.0: только объявленный Linux preflight

| Критерий | Вес | Балл | Основание |
|---|---:|---:|---|
| C1 | 30% | 10 | Исправлена воспроизводимая категория наложения, geometry/курсор/минимальные размеры сохранены |
| C2 | 20% | 10 | Pressure/resize/scroll/recovery проходят; raw и файловые свойства не меняются |
| C3 | 25% | 10 | Полный exact-SHA suite, DPI2, собственный red/green geometry probe и действительные screenshots |
| C4 | 15% | 10 | Малый двухфайловый diff, понятная причина scroll boundary, старый regression не ослаблен |
| C5 | 10% | 10 | Точные SHA/tree/recipe blob, воспроизводимые команды и честная граница Windows evidence |

Итог: `0,30×10 + 0,20×10 + 0,25×10 + 0,15×10 + 0,10×10 = 10,0`.
Это не автоматический перенос старого HOST-001 PASS: изменённый layout проверен
заново на указанном source. Linux preflight не замещает отсутствующее обязательное
Windows-свидетельство для исходной Windows-задачи.

## Следующий обязательный шаг вне этого verdict

A должен закрепить новый immutable source в своём CI, подтвердить исходный
min 1000×710/cursor regression и остальные проверки на реальной Windows-среде,
затем получить результаты неизменённого recipe/packaging/smoke. До нового
свидетельства прежний Windows failure не считается закрытым. Новый source,
сборочная композиция или recipe требуют актуального exact-version заключения.
