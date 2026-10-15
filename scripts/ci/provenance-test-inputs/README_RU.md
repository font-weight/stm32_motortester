# Подготовка точных входов переносимых тестов

Черновой инструмент для воспроизведения тестов PR #176. Он не меняет
17 зафиксированных файлов помощников и не запускает Windows-программу.
Подготовка проверяет размер и SHA-256 всех 23 файлов, затем копирует именно
прочитанные байты в новый каталог. Повторное чтение копий проверяет результат.
Существующий каталог не перезаписывается; при позднем отказе частичный каталог
может остаться. Успех подтверждают код завершения 0 и квитанция stdout.

Это удобство подготовки тестов в доверенной локальной рабочей папке, а не
защита от активно враждебной файловой системы или аттестация ОС. До проверки
всех входов выход не создаётся. Симлинки и reparse-пути не принимаются.
Скрипт ничего не скачивает, не устанавливает и не импортирует из этих входов.

## Получение входов

Нужен checkout с точными файлами commit
`a09f56f92b384ac6ecb2641cadad5cc894edf678`. Полный перечень путей, размеров
и SHA-256 находится в `inputs.json`; сам manifest закреплён хешем в скрипте.

1. Observer: `scripts/ci/observe_native_origins.py`; baseline:
   `scripts/ci/run17-native-baseline.json`; inspector:
   `host/packaging/inspect_candidate_provenance.py`. Эти файлы checkout
   совпадают с закреплёнными входами. Их не нужно заменять новыми версиями.
2. Companion брать из того же репозитория, commit
   `5d9fae69676fc2e1f2a7ec20519e201cac026d87`, путь
   `host/packaging/pyz-replacement-probe/pyz_replacement_probe.py`.
   Для извлечения пригоден `git show COMMIT:PATH`; сохранять байты без
   преобразования кодировки и окончаний строк. В основном checkout этот
   кандидат может отсутствовать. Это не повод подставлять другой файл.
3. Исходный initializer извлекается как данные из официального файла
   `pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl`:
   https://pypi.org/project/PySide6-Essentials/6.12.0/#files
   Размер wheel — 82153795 байт; SHA-256:
   `c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42`.
   После проверки wheel прочитать только `PySide6/__init__.py` через
   стандартный `zipfile`, сохранить исходные байты. Установка и импорт PySide6
   не нужны. Ожидаются 6418 байт и SHA-256
   `3b662ee91f12feb02507dfaff907b7306bf5c851f1374563f70fae19e13711fc`.
4. Создать точный ZIP исходников app244b из Git: 1411889 байт, SHA-256
   `e263fbb3b1734d8ca6d57d1db1493f2c6ce219979e2ffd581af56b4646c48d7f`.
   На Linux с Git 2.52.0 проверена следующая сериализация:

   ```sh
   TZ=Etc/GMT-5 git archive --format=zip -6 \
     --output="$INPUTS/app-source.zip" \
     244b0786a18fed19a9e708bb815150475ddd193f host
   sha256sum "$INPUTS/app-source.zip"
   ```

   Здесь Etc/GMT-5 означает UTC+05:00. Commit должен быть доступен в локальном
   Git-репозитории; использовать полный обычный clone, а не архив без .git.
   Результат независимо совпал с сохранённым ZIP байт-в-байт. Тот же Git с
   TZ=UTC воспроизвёл исторический Windows-хеш
   `6d112f2159fbb28c2f2d5b0bd7c9c512ccb6e2bf3f08400e535be6ae77f48e13`.
   Это подтверждает причину различия для проверенных сериализаций, но не
   обещает идентичность всех версий Git/zlib/ОС. Размер и SHA-256 обязательны;
   при несовпадении остановиться, не менять pin и не подставлять другой ZIP.
   Локальная проверка использовала восстановленную базу Git-объектов с
   проверенными идентификаторами, а не новый полный сетевой clone. Все
   исходные объекты доступны в публичном commit; среда пользователя отдельно
   не проверена. Остальные внешние входы также нужно получить по пунктам выше.

Условия исходного ZIP и границы предыдущей проверки:
https://github.com/font-weight/stm32_motortester/blob/5d9fae69676fc2e1f2a7ec20519e201cac026d87/host/packaging/pyz-replacement-probe/PYZ-REPLACEMENT-RECIPE.md

## Подготовка

В примере CHECKOUT, INPUTS и STAGE — абсолютные пути. INPUTS содержит отдельно
полученные companion.py, original-init.py и app-source.zip. Родитель STAGE
должен существовать; сам STAGE должен быть новым. Использовать доверенный
Python. Для повторения проверенного Linux-запуска:

```sh
python3 -I -S -B "$CHECKOUT/scripts/ci/provenance-test-inputs/stage_inputs.py" \
  --checkout "$CHECKOUT" --output "$STAGE" \
  --observer "$CHECKOUT/scripts/ci/observe_native_origins.py" \
  --baseline "$CHECKOUT/scripts/ci/run17-native-baseline.json" \
  --inspector "$CHECKOUT/host/packaging/inspect_candidate_provenance.py" \
  --companion "$INPUTS/companion.py" \
  --original-init "$INPUTS/original-init.py" \
  --source-zip "$INPUTS/app-source.zip"
```

Квитанция `PORTABLE_TEST_INPUTS_STAGED` означает только копирование точных
байтов: `tests_executed=false`, `candidate_executions=0`,
`actual_windows_execution=false`, `launch_authorized=false`.

## Запуск проверок

После успешной подготовки запускать тесты из STAGE. Это важно: SOURCE-C1
импортирует тестовую PRELAUNCH-реализацию по историческому пути. Подготовщик
создаёт этот путь из проверенных опубликованных файлов, а не из произвольной
внешней копии. Тесты исполняют доверенные проверенные Python-помощники,
observer, inspector и companion. Initializer, исходники приложения из ZIP
и непрозрачные EXE/PYZ/DLL остаются данными, не запускаются.

```sh
export PRELAUNCH_INPUT_ROOT="$STAGE"
export SOURCE_COLLECTOR_INPUT_ROOT="$STAGE"
export LITERAL46_TEST_HELPER="$STAGE/literal46-inputs-20261015/pinned-source/scripts/ci/observe_native_origins.py"
for mode in normal optimized; do
  opt=""
  [ "$mode" = optimized ] && opt="-O"
  python3 -I -S -B $opt "$STAGE/literal46-rebuild-20261015/scripts/ci/literal-native-copy/test_literal_copy.py" || exit 1
  python3 -I -S -B $opt "$STAGE/prelaunch-rebuild-20261015/scripts/ci/replacement-prelaunch/test_prelaunch.py" || exit 1
  python3 -I -S -B $opt "$STAGE/source-collector-rebuild-20261015/scripts/ci/replacement-source-inputs/test_source_inputs.py" || exit 1
  python3 -I -S -B $opt "$CHECKOUT/scripts/ci/provenance-test-inputs/check_composition.py" || exit 1
done
```

Проверенное окружение первоначального повторения: Linux x86_64, Python
3.12.14; файловая система с поддержкой symlink и hardlink. Синтетические
поля win32/3.13.16 внутри сценариев не описывают реально запущенный Python.
Перенос этого тестового запуска на Windows отдельно не подтверждён.

Тесты подготовщика требуют явно указанного каталога с шестью входами в
исторической структуре (например, STAGE):

```sh
STAGING_TEST_EXTERNAL_ROOT="$STAGE" python3 -I -S -B "$CHECKOUT/scripts/ci/provenance-test-inputs/test_stage_inputs.py"
STAGING_TEST_EXTERNAL_ROOT="$STAGE" python3 -I -S -B -O "$CHECKOUT/scripts/ci/provenance-test-inputs/test_stage_inputs.py"
```

Успех переносимых тестов не подтверждает реальные origin DLL, Windows-сборку,
полный wrapper, происхождение установленного source-pool, права распространения
или физическую готовность прибора. Эти условия сохраняются отдельно.
