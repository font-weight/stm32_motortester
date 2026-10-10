# HOST scroll reachability: независимое локальное ревью

**Вердикт: PASS, 10.00/10 для локальной коррекции test oracle.** Исправление
обосновано и пригодно для следующего Windows-прогона. **Привязка к remote commit
ожидается; Windows-проверка исправленного кандидата не выполнена.** Закрытие
Windows failure остаётся `BLOCKED_EVIDENCE`, `score: null` до нового exact-SHA
прогона. Этот PASS не является разрешением слияния, релизом или software-ready.

Дата: 10.10.2026. Critic-B, gpt-6-astra, xhigh. Я не автор проверяемого изменения;
не редактировал production/product-tests, не публиковал ветку или PR.

## Точная область

Репозиторий `font-weight/stm32_motortester`, локальная ветка
`dot-b/host-scroll-reachability`, worktree `/tmp/motortester-host-scroll`.

| Поле | Проверенное значение |
|---|---|
| Parent commit | `bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed` |
| Parent tree | `c7829a53e7151ed1ec27fe0150966344e8fa7b33` |
| Candidate tree | `89ee762ea583e587924ceb15bf7507b9d9afe29b` |
| Candidate commit / remote SHA | **Ещё нет; pending** |
| Единственный изменённый файл | `host/tests/test_gui.py`, +25/−4 |
| Git blob файла | `95d65a75752cae84b2625de238ca66a2258db308` |
| SHA-256 файла | `d44e46031d56bbd1be1edae28b063a97c0628aba9a198fa581af0b39e0ed3b03` |

Независимо создан чистый archive parent; поверх него скопирован единственный
изменённый файл. В отдельном временном Git repo получен tree. Проверены полный
набор **105 tracked files**, modes и bytes: **104 файла не изменились**. Таким
образом, production, формат данных, packaging/recipe и другие tests неизменны.
[Source binding](evidence/scroll-local/source-binding.json),
[точная дельта](evidence/scroll-local/candidate.patch),
[скрипт проверки](evidence/scroll-local/check_binding.py).

До проверки задана ограниченная цель: исправить ложное условие полной
одновременной видимости высокого cursor label, сохранить строгую геометрию
plot→trigger→cursor и доказать достижимость каждой границы прокруткой. Требования:
CON-09/12, программная часть FR-05/06 и MVP-U04/U05; рубрика
`docs/quality-gate.md` 1.0. Полное ревью объединённого parent выполнено отдельно
в [HOST-integration-bd582-review.md](HOST-integration-bd582-review.md). Здесь
проверяется новая тестовая дельта и её регрессии; балл parent не переносился.

## Разбор исправления

Исторический Windows run
[38061091297/job 114239338404](https://github.com/font-weight/stm32_motortester/actions/runs/38061091297/job/114239338404)
на **3b315** действительно завершился 77/78: старый no-overlap тест прошёл,
новое условие pressure 22 px получило `bottom==viewport.height()==100`.
Этот failed run не переобозначается как PASS. Размер label в его логе не измерен.
Первоначальное [расследование и 63 состояния](evidence/layout-3b315/reachability-followup/README.md)
сохранены без изменения.

Коррекция по существу верна:

1. Старый `test_minimum_window_with_cursors_has_no_overlapping_plot_labels`
   сохранён полностью. В pressure 18/22 px сохранены оба строгих overlap asserts,
   минимальная высота plot, размер окна, наличие прокрутки и её исчезновение
   после восстановления размера/шрифта.
2. Вместо `ensureWidgetVisible(label)` и одного bottom assertion проверяются
   topLeft/bottomLeft по отдельности. Координаты переводятся в content widget,
   `ensureVisible(...,1,1)` оставляет положительный inset, затем проверяется
   полное попадание point в viewport rect. Это **не** замена `<` на `<=`.
3. Новый тест принудительно создаёт label минимум 180 px и scroll widget 100 px;
   отдельно доказывает `label.height()>viewport.height()`, сохраняет оба условия
   separation и проверяет оба края. Одновременное вмещение не требуется.
4. `unittest` assertions остаются активными под `python -O`.

Новый oracle соответствует необходимому поведению прокручиваемой аннотации.
Production-изменение не требуется по найденной причине. Замечаний P0/P1/P2 в
этой дельте не обнаружено.

## Лично выполненные проверки

Среда: Linux x86_64, CPython 3.12.14, PySide6/Qt 6.12.0, pyqtgraph 0.14.0,
NumPy 2.5.3; реальный Qt, платформа offscreen.
[Полная среда](evidence/scroll-local/environment.txt).

| Проверка exact tree | Результат |
|---|---|
| Полный aggregate default | **289/289**, без skips, 32.871 s; [лог](evidence/scroll-local/tests-default.log) |
| Полный aggregate `python -O` | **289/289**, без skips, 38.816 s; [лог](evidence/scroll-local/tests-optimized.log) |
| GUI, `QT_SCALE_FACTOR=2` | **22/22**, без skips, 29.399 s; [лог](evidence/scroll-local/gui-dpi2.log) |
| Независимый geometry probe | **18 конфигураций × 2 края**, default и -O: все доступны; [default](evidence/scroll-local/reachability.json), [optimized](evidence/scroll-local/reachability-optimized.json) |
| Отрицательный контроль | Подмена только runtime `ensureVisible` на no-op вызывает ожидаемый FAIL нового теста; [лог](evidence/scroll-local/noop-mutation.log) |
| Source binding после запусков | Те же bytes/tree, одна тестовая дельта; production не менялся |

Независимый probe покрывает шрифты 13/22 px, viewport 50/100/181 px и label
101/180/400 px. Старое bottom-условие не проходит **14 из 18** конфигураций,
но новое адресное условие достигает обоих краёв во всех 18. Каждый раз отдельно
проверены separation, тождество Capture и неизменность encoded raw data.
Probe использует явные исключения, поэтому собственная проверка тоже не исчезает
под `-O`. [Скрипт](evidence/scroll-local/check_reachability.py).

No-op контроль доказывает, что тест не стал бессодержательным: при потере
прокрутки он падает на проверке видимости. Ожидаемый FAIL сохранён открыто и
отделён от успешных запусков неизменённого кандидата. Исходники при подмене не
правились. Full aggregate также повторяет имеющиеся PNG, CSV, file, wire,
window и controller tests; это регрессия, а не новая аппаратная сертификация.

## Неизменная рубрика

| Критерий | Вес | Балл | Основание в локальной области |
|---|---:|---:|---|
| C1. Корректность | 30% | 10.0 | Дефект oracle устранён по установленной причине, overlap checks сохранены, oversized case проверен |
| C2. Надёжность | 20% | 10.0 | Края доступны при разных размерах; отрицательный no-op контроль, default/-O и DPI регрессии успешны |
| C3. Доказательства | 25% | 10.0 | Независимый archive/tree, собственные полные прогоны, geometry и отрицательное сравнение; Windows отдельно открыт |
| C4. Ясность | 15% | 10.0 | Один тестовый файл, краткий комментарий о причине, понятные assertions; ненужных production изменений нет |
| C5. Воспроизводимость | 10% | 10.0 | Точные parent/tree/blob/SHA-256, patch, версии, команды и логи сохранены; remote pending обозначен явно |

**0.30×10 + 0.20×10 + 0.25×10 + 0.15×10 + 0.10×10 = 10.00.**
Оценка относится к локальному test-oracle исправлению, не к вероятности отсутствия
ошибок или общей готовности приложения.

## Передача и незакрытые проверки

- После публикации требуется прочитать exact remote commit, проверить parent и
  совпадение candidate tree выше. При другой дельте это заключение автоматически
  не действует. Публикацию данный критик не выполнял.
- Нужен новый Windows job для опубликованного exact SHA. Linux offscreen и DPI 2
  не подтверждают Windows fonts, Windows 10, frozen EXE/ZIP или recipe execution.
- USB/железо, калибровка, LV-001 и общая приёмка MVP остаются вне этой итерации.
- Оригинальные layout и Windows-investigation отчёты не исправлялись задним числом.

[Команды воспроизведения и индекс доказательств](evidence/scroll-local/README.md),
[SHA256SUMS](evidence/scroll-local/SHA256SUMS).
