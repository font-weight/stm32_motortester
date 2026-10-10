# Независимое уточнение Windows failure: проверка достижимости

Дата: 10.10.2026. Critic-B, gpt-6-astra xhigh; production/product-tests не правились.
Объект: неизменённый `3b315ac4e1184c4c7e9216ce5594cff0f9beff33`.
Это диагностика условия теста перед новым candidate, не новый PASS или Windows-прогон.
Предыдущий exact-SHA Linux preflight-отчёт сохранён как историческое свидетельство.

## Вывод для автора

**Подтверждён дефект переносимости test oracle.** `ensureWidgetVisible(label)`
не гарантирует, что нижняя граница целого label после одного вызова попадёт
в viewport любого размера. Исполняемая проверка точной продукции показывает,
что несовпадение этого условия не означает недостижимость части label.
Производственный дефект scroll/overlap по данному свидетельству не найден;
исправлять production лишь ради этого assert оснований нет.

Исходные два строгих условия separation plot→trigger→cursor оставлять.
Проверять достижимость верхней и нижней границы **отдельно**, с проверкой
`0 <= y < viewport.height()` после соответствующего движения прокрутки.
Добавить явный случай label больше viewport, чтобы проверить переносимое условие,
а не просто заменить `<` на `<=` или удалить проверку.

## Действительный Windows результат

Самостоятельно прочитан [job 114239338404](https://github.com/font-weight/stm32_motortester/actions/runs/38061091297/job/114239338404):
source 3b315, Windows Server 2022, 78 tests, 1 failure.
Старый тест no-overlap проходит; новый pressure 22 падает на line 295:
`cursor_bottom.y()==100`, `viewport.height()==100`.
Исключение относится к добавленному oracle, а не к прежнему overlap assertion.
Размер самого label в этом логе **не записан**. Не утверждается, что Windows
его измерил как 101 px: это независимо воспроизведённый механизм, согласующийся
с наблюдаемым failure. Windows suite всё ещё failed, а не переобозначен как PASS.

## Собственное исполнение

Linux/offscreen, реальный Qt 6.12.0 и исходный MainWindow из clean archive 3b315.
Менялись только runtime widget constraints в probe, не source-файлы.
9 сочетаний viewport/label × 7 вариантов прокрутки = **63 состояния**;
при каждом сохранены separation и raw Capture. `result.json` содержит все координаты.

- Естественный label 78 px при viewport 78: default ensureWidgetVisible даёт
  top 1/bottom 78. Значит даже равенство размеров не гарантирует исходный assert
  при стандартных margins/целочисленной геометрии.
- Label 101 px при viewport 100: default helper даёт **top 0/bottom 100**, точно
  воспроизводя числовую форму Windows failure. Оба края одновременно физически
  не помещаются в 100 px, но доступны по отдельности.
- В этом же случае bottom-point с margin 1 даёт bottom 99; scrollbar maximum —
  bottom 86. Top-point с margin 1 даёт top 1; установка scrollbar на content-coordinate
  верхней границы даёт top 0. Потери доступа к содержимому нет.
- Естественный label 78 проверен также при viewport 181/100/79/77/76/50;
  искусственный label 150 при viewport 100. Адресные края доступны во всех случаях.
- Zero-margin `ensureVisible(bottom, 0)` тоже способен поставить endpoint в
  y==height. Начальный самостоятельный probe это обнаружил; сохранён отдельный
  log. Для строгого pixel-bound check использовать положительный inset (здесь 1 px)
  либо непосредственно scrollbar position, а не нулевой margin.

[Официальный Qt API](https://doc.qt.io/qt-6/qscrollarea.html#ensureWidgetVisible)
задаёт стандартные margins 50 px и оговаривает ограничение допустимыми позициями
прокрутки. Это не обещание полного одновременного показа widget, превышающего
viewport. Конкретные координаты выше получены исполнением, а не выведены только
из текста документации.

## Воспроизведение

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  XDG_CACHE_HOME=/tmp/reachability-cache \
  PYTHONPATH=/tmp/motortester-deps:/path/to/exact-3b315/host \
  python probe.py
```

`probe.py` не пишет production/source/tests. Он сравнивает default helper,
endpoint margin 0/1, scrollbar maximum и точную top-coordinate; assert проверяет
реальную достижимость, не одновременный fit. `windows-excerpt.txt` — выдержка
прочитанного job. Новый тест/commit и новый Windows прогон автор предоставляет
отдельно; без них кандидату не выдаётся Windows approval.
