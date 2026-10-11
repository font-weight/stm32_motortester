# Доказательства HOST-001

Здесь сохраняются воспроизводимые результаты **программной проверки на синтетических данных**. Это не измерения платы, Windows 10, USB или зажигания. Точная ревизия проверенного кода указывается в отчёте Critic-B; содержимое logs не присваивает статус PASS само по себе.

Команды из корня checkout (зависимости — `host/requirements-gui.txt`):

```sh
PYTHONPATH=host python host/scripts/check_first_path.py /tmp/motortester-new-evidence
PYTHONPATH=host python -m unittest discover -s host/tests -p 'test_recording.py' -v
PYTHONPATH=host python -m unittest discover -s host/tests -p 'test_first_path.py' -v
QT_QPA_PLATFORM=offscreen PYTHONPATH=host python -m unittest discover -s host/tests -p 'test_gui.py' -v
QT_QPA_PLATFORM=offscreen PYTHONPATH=host python -m motortester.gui --screenshot /tmp/motortester-window.png
```

На Windows используйте PowerShell-синтаксис переменных среды из `host/README.md`. `offscreen` проверяет Qt в указанной среде, не взаимодействие на рабочем столе пользователя. Результат CLI повторно читает файл с диска; GUI screenshot-сценарий также сохраняет и открывает синтетическую запись перед снимком.

Для fixtures скрипт использует фиксированную дату `2026-10-10T00:00:00+00:00` и фиксированный UUID, чтобы хеши были повторимы. Эта дата не является временем физического измерения. Контрольные коды `[0, 1, 256, 4095]` должны дать uint16-le байты `00 00 01 00 00 01 FF 0F` независимо от сериализатора.

GUI показывается отдельно от проверки точек: 2048 точек при номинальных 50 kS/s, trigger_index 512, диапазон времени −10,24…30,70 мс, разность 40,94 мс. Реальная Fs не измерялась.
