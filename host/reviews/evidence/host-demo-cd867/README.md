# Critic-B: HOST-005 cd86784b

Exact source: `cd86784b434662c7aa542713fa61a5bbd6b97938`.
Linux x86-64, Python 3.12.14, Qt offscreen для baseline GUI/просмотра SVG.
Новые wire/demo-модули используют только стандартную библиотеку.

- `normal-tests.log`: 208 тестов, 42,015 с, OK.
- `optimized-tests.log`: 208 тестов под `python -O`, 31,075 с, OK.
- `cli.log`: фактический запуск 20 окон с chunk-size=1, exit 0.
- `cli-no-clobber.log` и `cli-invalid.log`: ожидаемые exit 1.
- `independent-results.json`: 260 положительных сочетаний, 341 поток с отказом
  без файла, ошибки публикации/экспорта и полная проверка 20 CSV/SVG/raw.
- `first-path.tar.gz`: все 61 фактический выход CLI (20 MTCAP, 20 CSV, 20 SVG,
  manifest). Точные hashes MTCAP находятся в manifest внутри архива.
- `synthetic-01.png`: реальное отображение первого SVG через QtSvgRenderer,
  1100×600; это screenshot-подобное доказательство визуальной проверки,
  а не новый output PNG-функции приложения.

Воспроизведение дополнительного независимого harness (Python без `-O`):

```sh
mkdir -p /tmp/host005-repeat
cp check_demo.py /tmp/host005-repeat/
PYTHONPATH=/path/to/exact/source/host python -m motortester.wire_demo \
  /tmp/host005-repeat/first-path --windows 20 --chunk-size 1
PYTHONDONTWRITEBYTECODE=1 python /tmp/host005-repeat/check_demo.py /path/to/exact/source
```

Harness создаёт отдельный probe-run каталог для каждого прогона и намеренно
моделирует файловые ошибки. Он не правит product/source/tests.
`independent-first-run-error.log` сохранён: первая версия собственного harness
искала SVG polyline только как прямого ребёнка root вместо потомка группы `<g>`.
После исправления **review harness**, не продукта, весь прогон повторён успешно.
Это не скрытый product failure.

Небольшое замечание о неверной фразе «иной layout блоков отвергается» подтверждено
положительным alternate layout 249×8+56. Полный отчёт — `HOST-005-review.md`.
USB/handshake/Windows/аппаратура этим набором не проверяются.
