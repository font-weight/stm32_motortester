# Происхождение и границы поставки

Исходный минимальный экспорт: 31 неизменённый UTF-8 файл, 263761 байт,
без binaries, symlink и исторических logs. Два новых файла этой поставки —
README.md и PROVENANCE.md. Канонический host/** не изменяется; копии B здесь
только фиксированные входы исторического опыта, не новое приложение.

## Точные версии

- A behavior: [PR79](https://github.com/font-weight/stm32_motortester/commit/66fce97ba86de459e6d597deb32130e030f34594).
- A source/fixture package: [PR82](https://github.com/font-weight/stm32_motortester/commit/7382ae7f3490127d8a61733cbca948acd89047f9), tree a3d5722a6a93d463f06b8fa0428fdd35979c442e.
- Пять модулей B: [PR74](https://github.com/font-weight/stm32_motortester/commit/c44bae42b19ec8208c2ebee4a3e662ce1b7ff240).
- B transport.py, только сохранённый источник: [PR51](https://github.com/font-weight/stm32_motortester/commit/ec11d01a42e0cbdc06039ddf274121a4db7202ed).

В snapshots/a-native и snapshots/pr74/pr51 исходные repo-relative пути сохранены.
Все bytes берутся из того же репозитория владельца; новая лицензия или смена
владения не назначаются. Общий LICENSE проекта отсутствовал при подготовке;
публичность репозитория сама по себе не означает произвольную лицензию.

## Замороженные входы

REPRO_MANIFEST.json SHA-256:
b092f7a1e6f0b71dcf4c966af72cab4b2d05e5c5858905f70d8ebd7e18f9b0da

В нём ровно 30 relative paths с SHA-256. Они связывают 21 A-файл, шесть
B-файлов и три файла runner/adapter/helper. Сам manifest не содержит свой hash;
его identity связывается данным текстом и commit поставки, не сетевой проверкой.
Runner сам не закрепляет hash manifest: подмена manifest на пустой объект
убирает его входные проверки. Поэтому используйте только точные опубликованные
bytes и сверяйте этот SHA отдельно; программа не является защитой от изменения
собственного кода/manifest. Это сохранённое ограничение исторического wrapper.

- reproduce_early_release.py: a64696da0ec29ed10b8952f3d2f4d385c2bbcb26e726d1eb994157d64d4112e8
- device_bridge.c: e7d9a9b90767b9afadb3c0c6b7a0054eacf520200d494fa59a92678097173c94
- run_audit.py: a148ac925794dd8b92e9ce49e3b7ddbba7f635bcfa4362e4afd67cfacca94e1e

Native bridge компилирует семь production C units: control, capture_link,
wire, command, session, acquisition, tx. Сохранённый characterization fixture
включён текстом, его старый main переименован и не вызывается. Hardware callbacks
синтетические. Четыре файла stream/RX, wire Markdown и PR51 transport.py
проверяются manifest, но не исполняются в данном опыте. Пять PR74 Python-модулей
импортируются из фиксированного snapshot; сторонние Python-пакеты не нужны.

run_audit.py сохраняет дополнительные исторические функции и старые внешние
пути. Только native_premature_release и emulator_premature_release используются
квалифицированным entry point. Остальные опыты и их старые журналы не заявлены
воспроизводимыми этим пакетом.

## Независимое основание и новая проверка поставки

[Исходное независимое подтверждение](https://github.com/font-weight/stm32_motortester/pull/14#issuecomment-6107029063)
проверило byte acceptance, собственные DATA CRC и успешный нормальный RELEASE
после полного END. Отчёт SHA-256:
14792203b215e3ac2034c0aa38ca1e336ccbe69dc38edcc29e06bbf67d3568b0.
Он не назначал score или severity. Guard A уже существовал до PR79.

Это основание конкретного исторического наблюдения, не автоматический PASS
новой упаковки. Точная поставка/README и доступные команды требуют отдельного
delivery-ревью. Внешние сырые исторические отчёты/логи не включены. Новые outputs
формируются при запуске; никакие измерения Windows, последовательного порта,
физического USB, ADC или платы здесь не подтверждаются.
