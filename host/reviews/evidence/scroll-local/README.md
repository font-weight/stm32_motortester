# Локальное независимое ревью исправления scroll oracle

Объект — единственная дельта `host/tests/test_gui.py` поверх
`bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed`. Проверенное дерево:
`89ee762ea583e587924ceb15bf7507b9d9afe29b`.

`source-binding.json` фиксирует все 105 tracked files, blob/mode, точные SHA и
отсутствие изменения 104 файлов. `candidate.patch` восстанавливает единственное
изменение. `check_binding.py` дополнительно сравнивает AST: изменён один pressure
тест, добавлен один oversized-label тест, другие методы сохранены.

- `tests-default.log`: полный набор, 289 tests, PASS.
- `tests-optimized.log`: полный набор `python -O`, 289 tests, PASS.
- `gui-dpi2.log`: `QT_SCALE_FACTOR=2`, 22 GUI tests, PASS.
- `check_reachability.py` / `reachability*.json`: 18 независимых конфигураций
  (шрифты 13/22, viewport 50/100/181, label 101/180/400), по два endpoint checks;
  повторено default и -O. Старое условие bottom<height не проходит в 14 из 18
  конфигураций; адресная прокрутка достигает каждого края во всех 18.
- `check_noop_mutation.py` / `noop-mutation.log`: намеренная runtime-подмена
  ensureVisible на no-op. Новый тест обязан упасть и действительно падает.
  Это ожидаемый отрицательный контроль, не ошибка исходного кандидата.
- `environment.txt`: фактические версии Linux/CPython/Qt/NumPy/pyqtgraph.

Ни один probe не изменял production или продуктовые tests. Логи Windows здесь
не создавались. Первоначальное расследование сохранено отдельно в
`../layout-3b315/reachability-followup/`.

## Повторение

Создать чистую временную копию parent через `git archive`, применить
`candidate.patch` через `git apply`, затем проверить SHA-256 `host/tests/test_gui.py`:
`d44e46031d56bbd1be1edae28b063a97c0628aba9a198fa581af0b39e0ed3b03`.
Для проверки полного tree создать временный Git repo, добавить файлы и выполнить
`git write-tree`; ожидается tree выше. Не применять patch в чужом worktree.

Из копии:

```sh
export PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen
export PYTHONPATH=/tmp/motortester-deps:$PWD/host
export XDG_CACHE_HOME=/tmp/critic-scroll-replay-cache
python -m unittest discover -s host/tests -v
python -O -m unittest discover -s host/tests -v
QT_SCALE_FACTOR=2 python -m unittest discover -s host/tests -p test_gui.py -v
python /PATH/TO/check_reachability.py
python -O /PATH/TO/check_reachability.py
python /PATH/TO/check_noop_mutation.py "$PWD"
```

`check_binding.py` содержит два локальных пути к исходному worktree и archive;
при переносе изменить только пути, сохранив parent/tree/hash и все проверки.
Remote commit теперь независимо подтверждён: `f0822f7b336b0cab0e01632eee33f0f9a24473cc`,
[PR #71](https://github.com/font-weight/stm32_motortester/pull/71).
`remote-binding.json`, `check_remote_binding.py` и `remote-fetch.log` фиксируют
совпадение parent/tree и всех 105 файлов с tested snapshot. `remote-pr71.json`
и `remote-commit-f082.json` — отдельно прочитанные GitHub metadata/diff.
`source-binding.json` оставлен как историческая локальная проверка с pending;
`review-local-frozen.md` сохраняет исходный отчёт до публикации.
Тесты заново не запускались: материальной дельты нет. Факт будущего прохождения
Windows этими Linux-логами не предрешён.
