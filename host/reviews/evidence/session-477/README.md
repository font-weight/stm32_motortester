# Воспроизведение Critic-B HOST-006

Проверен `477057aadc9bc8e757bc8ef817125377e816d212`, base `c89c12df984390b34785eba81665abd6418658e2`.
Это новые личные логи критика, не копии авторских. Архив после запусков полностью совпал с 85 Git-объектами head.

Из checkout, содержащего commit:

```sh
mkdir /tmp/critic-b-session-477
 git archive 477057aadc9bc8e757bc8ef817125377e816d212 | tar -x -C /tmp/critic-b-session-477
cd /tmp/critic-b-session-477
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/host:/tmp/motortester-deps"
export QT_QPA_PLATFORM=offscreen
python3.12 -m unittest discover -s host/tests -p test_session.py -v
python3.12 -m unittest discover -s host/tests -p test_session_conformance.py -v
python3.12 -m unittest discover -s host/tests -p 'test_session*.py' -v
python3.12 -O -m unittest discover -s host/tests -p 'test_session*.py' -v
python3.12 -m unittest discover -s host/tests -v
sha256sum -c host/tests/fixtures/session/SHA256SUMS
```

`/tmp/motortester-deps` — имеющаяся среда GUI-зависимостей; точные версии в dependencies.log.
Сами 70 session tests и critic-probes.py не требуют Qt/numpy. Полная Linux-регрессия требует GUI-зависимостей.
Архив не включает дополнительные review artifacts, которые появились позже проверенного commit.
Скопируйте `critic-probes.py` из этого каталога в отдельный путь и выполните:

```sh
PYTHONPATH=/tmp/critic-b-session-477/host python3.12 /path/to/critic-probes.py
PYTHONPATH=/tmp/critic-b-session-477/host python3.12 -O /path/to/critic-probes.py
```

Пробы имеют отдельный struct-based oracle, не импортируют авторские tests/reference и не изменяют исходники.
Граничные счётчики выставляются white-box только для достижения UINT32_MAX без миллиардов действий.

В source-verification.json сохранены SHA-256 всех файлов archive, совпадение с исходным manifest и двумя pinned contract sources.
В runs.json — точные команды пяти основных прогонов, cwd, коды завершения и среда.
SHA256SUMS этого каталога проверяется командой `sha256sum -c SHA256SUMS` из самого каталога.

Первый ручной вызов проверки авторского SHA256SUMS ошибочно выполнялся из fixtures/session и дал `No such file or directory`:
его пути относятся к корню репозитория. Это ошибка cwd критика, не дефект исходников или checksum.
Повтор из корня архива успешно проверил все 17 записей; лог сохранён отдельно.
Авторский run_checks.py не запускался, чтобы не переписывать исторические артефакты; лично выполнены его suite-команды.

Никаких production/test-tree правок, Git commits, публикаций или аппаратных действий критик не выполнял.
