# Стек HOST-001: кандидат и границы проверки

Дата проверки первичных источников: 10.10.2026. Выбор представлен для независимого ревью Critic-B; это не утверждённая готовность всего MVP.

## Состав

- Ядро/файлы/CLI: стандартная библиотека Python, данные неизменяемы и независимы от UI.
- GUI: PySide6-Essentials 6.12.0 + shiboken6 6.12.0; Qt Widgets, русские подписи, явная тёмная палитра, локальная справка.
- График: pyqtgraph 0.14.0 + NumPy 2.5.3; 2D без требования OpenGL. colorama 0.4.6 — транзитивная зависимость pyqtgraph.
- Фактически доступная среда: Linux x64, CPython 3.12.14. Проверка на Linux не переносится на Windows.
- Кандидат Windows build: обычный CPython 3.13.16 x64, PyInstaller 6.22.3, portable onedir ZIP. Установщик в первой ограниченной задаче не нужен.

Все прямые/транзитивные runtime-версии находятся в `requirements-gui.txt`. Полный Windows lock вместе с build-зависимостями и SHA-256 конкретных wheels — `packaging/requirements-win-x64.lock`. Его wheel hashes получены из официального PyPI; все 13 wheels загружены с проверкой хешей для `win_amd64/cp313` на Linux. Это подтверждает доступность артефактов, а не выполнение Windows-установки/сборки. Зависимости сборки: altgraph 0.17.5, packaging 26.3, pyinstaller-hooks-contrib 2026.8, setuptools 84.0.0, pefile 2024.8.26, pywin32-ctypes 0.2.3; pip 26.2.1 также закреплён.

## Обоснование

Один Python-код позволяет проверять эмулятор, формат, массивы и GUI в доступной Linux-среде до появления Windows/платы. Qt Widgets предоставляет обычное desktop-окно, диалоги и офлайн-текст. PySide Essentials включает нужные QtCore/QtGui/QtWidgets без обязательной установки всего Addons/WebEngine. pyqtgraph предоставляет масштабирование, перемещение и курсоры; это уменьшает объём собственной графической математики. Исходные данные не заменяются графическими преобразованиями.

Собственный QPainter уменьшил бы зависимости, но потребовал бы отдельно реализовать и проверить оси/zoom/pan/курсоры. WPF привязал бы исполняемые проверки GUI к Windows. Выбор Python сделан для ранней проверяемой цепочки и не доказывает производительность будущего USB-потока. Размер ZIP, задержка UI, память и профиль нагрузки под Windows пока TBD; определить до MVP-U10.

Qt 6.12 перечисляет Windows 10 1809+ x64 и названа последней веткой Qt с поддержкой Windows 10. Не обновлять Qt автоматически на следующую ветку. Реальную edition/build/архитектуру целевого ноутбука надо проверить; таблица производителя не является испытанием нашего приложения. [Qt: supported platforms](https://doc.qt.io/qt-6/supported-platforms.html).

Для кандидата используется Windows installer CPython 3.13.16, доступный в официальном релизе. Это отдельная от Linux 3.12.14 среда, поэтому тесты запускаются заново при сборке. [Python 3.13.16](https://www.python.org/downloads/release/python-31316/).

PyInstaller требует сборки на целевой ОС: Linux не создаёт проверенный Windows EXE. onedir сохраняет библиотеки отдельными файлами и упрощает проверку состава/лицензий. Воспроизводимость означает закреплённый рецепт, зависимости, журнал и SHA; побитная детерминированность бинарников не проверена. [PyInstaller manual](https://pyinstaller.org/en/stable/index.html), [релиз 6.22.3](https://pypi.org/project/pyinstaller/6.22.3/).

## Лицензии и источники

Открытый путь PySide/Qt требует соблюдения применимых условий LGPL/GPL; отдельные DLL сами по себе этого не доказывают. До распространения необходим аудит реально попавших в пакет библиотек, уведомлений и соответствующих исходников. У PyInstaller есть специальное исключение для собранного приложения. Порядок проверки закреплён в [LICENSE-REVIEW.md](../packaging/LICENSE-REVIEW.md).

- [PySide6 Essentials 6.12.0: состав, лицензия и Windows wheel](https://pypi.org/project/PySide6-Essentials/6.12.0/)
- [shiboken6 6.12.0](https://pypi.org/project/shiboken6/6.12.0/)
- [pyqtgraph 0.14.0](https://pypi.org/project/pyqtgraph/0.14.0/)
- [NumPy 2.5.3](https://pypi.org/project/numpy/2.5.3/)
- [colorama 0.4.6](https://pypi.org/project/colorama/0.4.6/)
- [Qt for Python: используемые лицензии](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt: обязательства LGPL](https://www.qt.io/development/open-source-lgpl-obligations)
- [PyInstaller: лицензия](https://pyinstaller.org/en/stable/license.html)

Источники подтверждают свойства зависимостей и доступность версий. Они не закрывают Windows smoke, чистую установку, интеграцию USB, аппаратные измерения или независимое ревью конкретного SHA.
