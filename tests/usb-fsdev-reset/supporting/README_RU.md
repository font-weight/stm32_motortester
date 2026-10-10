# Supporting tests USB-004

Native-модель регистров STM32F103 FSDEV с извлечёнными функциями pinned TinyUSB. Полные результаты и точные ограничения: [REPORT_RU.md](REPORT_RU.md).

Воспроизведение из этого каталога:

```sh
python3 tools/test_runner.py
python3 tools/run.py --candidate evidence/final-hardened/candidate-input.c --out /tmp/fsdev-reset-new-run
```

`--out` должен быть новым/пустым и находиться вне `inputs/`, `src/`, `tools/`. Требуются Python 3 и GCC с ASan/UBSan. Доступ к внешнему vendor, сети или плате не нужен. Итоговая полная матрица: `evidence/final-hardened/`; более ранние неудачи/успехи сохранены, не заменяют итоговую матрицу.

Это не полный DCD, не silicon-тест и не заключение независимого критика. Production patch в этом пакете не редактируется; candidate snapshot хранится только для повторения проверки.
