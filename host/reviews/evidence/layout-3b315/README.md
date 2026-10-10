# Critic-B: layout preflight 3b315ac4

Source `3b315ac4e1184c4c7e9216ce5594cff0f9beff33`,
tree `6fca4af819e48e624f83066cce72347c03f7d5ab`.
Linux x86-64, Python 3.12.14, pinned Qt/pyqtgraph dependencies HOST-001.

- full-tests.log: 78 тестов, 12,251 с, OK.
- dpi2-gui-tests.log: 20 GUI tests, scale factor 2, 21,545 с, OK.
- inspect_layout.py + два JSON: 30 комбинаций resize/font/DPI, scroll reachability,
  separation, возврат к большому окну, курсоры и неизменность raw Capture/path.
- check_old_geometry.py + old-geometry.json: отдельно воспроизведено прежнее
  наложение на immutable 2376 при font18/font22, без изменения source.
- Настоящие PNG: normal minimum size при scale1/2, искусственный pressure22,
  восстановленное большое окно. Это QMainWindow.grab, не mockup и не PNG export feature.
- windows-failure-excerpt.txt: самостоятельно прочитанный старый Windows job,
  run38055733142 / job114223753438, source2376, failure158>=111.
  **Это не Windows-прогон нового candidate.**

Для независимого geometry probe скопировать script в writable temp-каталог:

```sh
QT_QPA_PLATFORM=offscreen XDG_CACHE_HOME=/tmp/layout-cache \
  PYTHONPATH=/path/to/dependencies:/path/to/exact/source/host \
  python inspect_layout.py
# Повторить с QT_SCALE_FACTOR=2 в отдельном процессе.
```

Изображения сохраняются рядом со script. Старый probe запускать с production
модулями source2376, не с новым source; он ожидает именно старое наложение.

Крупношрифтовый stress screenshot всё ещё содержит известное обрезание sidebar
вне изменённой plot-card области. Он не доказывает полноценную large-font
доступность всего приложения; normal/DPI2 screenshots осмотрены отдельно.
