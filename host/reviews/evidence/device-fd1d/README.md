# Свидетельства независимого Critic-B для HOST-007

Проверяется `fd1d0ad6f131e1f08be64893da9c1d667dc15eed`, не рабочая копия автора.
`source.tar` получен `git archive` этого SHA. Все 112 файлов сопоставлены с Git blob
ID и SHA-256 в `source-verification.json`; два общих исходных документа сравнены
с Git-объектами c54bd955. Сверены 97 + 97 исходных хешей и 14 хешей журналов
предъявленного manifest. `source-after.log` подтверждает неизменность после проверок
как извлечённого архива, так и замороженного worktree.

`issue42.json` — прочитанная постановка/приёмка; `pr47.json` — метаданные GitHub,
включая фактический head/base и draft-статус, полученные в ходе ревью.
`rerun-manifest.json` хранит точные команды, версии среды и хеши повторных журналов.

## Личные дополнительные пробы

`critic_probes.py` импортирует только DeviceEmulator/DeviceError из production.
Нет импорта авторских или conformance тестов, их reference или production codec.
Упаковка/распаковка COBS и заголовков самостоятельная; samples CRC дополнительно
вычисляется побитно без production CRC. Объявленная синтетическая формула является
выбором модели, не физическим эталоном АЦП.

11 основных проверок: 4 699 разрезов Stop полного окна; 919 разрезов старых кадров
окна и 399 разрезов управляющих кадров при свежем HELLO; 80 полных RELEASE-циклов
для двух фронтов и крайних порогов; все 256 типов current/foreign session;
600 обязательных ответов с псевдослучайными размерами порций/обратным давлением;
точные управляющие payloads, повтор/предельные ID, tombstone и retained memory.
Случаи представлены параметризованными циклами, не 6 000 отдельными unittest.

Одна дополнительная проверка `--provisional` отдельно проверяет атомарность
неверных API-вызовов на условной границе hold=5000 ms. Она НЕ принимает точку
отсчёта/STATUS.reason общего контракта.

`critic-initial.log` и `critic_probes_initial.py` сохраняют первый успешный набор
из девяти проверок. Затем условная проверка выделена отдельно и добавлены точные
управляющие payloads/разрезы всех типов управляющих кадров. Исправлений production
и неуспешных прогонов не было; исторический первый журнал не заменяет финальный.

## Воспроизведение

Нужны Python 3.12 и доступные GUI-зависимости полного host-набора. В проверенной
среде: PySide6 6.12.0, pyqtgraph 0.14.0, NumPy 2.5.3, Linux x86-64, Qt offscreen.
Путь `/tmp/motortester-deps` относится только к использованной среде.

Из корня репозитория с этим отчётом:

```sh
EVIDENCE="$PWD/host/reviews/evidence/device-fd1d"
EXACT="$(mktemp -d)"
tar -xf "$EVIDENCE/source.tar" -C "$EXACT"
python "$EVIDENCE/verify_sources.py" "$EXACT"
cd "$EXACT"
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="host:/tmp/motortester-deps" QT_QPA_PLATFORM=offscreen
python host/tests/fixtures/device_emulator/suite.py focused
python host/tests/fixtures/device_emulator/suite.py provisional
python host/tests/fixtures/device_emulator/suite.py new
python -O host/tests/fixtures/device_emulator/suite.py new
python host/tests/fixtures/device_emulator/suite.py full
python "$EVIDENCE/critic_probes.py"
python -O "$EVIDENCE/critic_probes.py"
python "$EVIDENCE/critic_probes.py" --provisional
python "$EVIDENCE/verify_sources.py" "$EXACT"
```

Fixture `run_evidence.py` намеренно не запускался: он перезаписывает предъявленные
авторские evidence. Повторены его исполняемые suite-команды с выводом в отдельную
область критика. Хеши окончательных файлов — `SHA256SUMS`.

Это проверка Python-модели. Windows, физические USB/АЦП/время, память MCU,
Auto/Single/external/pretrigger, интеграция GUI/WindowReceiver/файлов и выпуск
не входят в подтверждённый результат.
