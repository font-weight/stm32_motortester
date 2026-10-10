# TIME-001: воспроизведение и происхождение свидетельств

Задача [#40](https://github.com/font-weight/stm32_motortester/issues/40). Draft отдельного TIM2-модуля, написанного лично Dot A. База — финальный head RX-001 [PR #41](https://github.com/font-weight/stm32_motortester/pull/41), `8855cb0ed0c10b58d0c9544d3e85fb54bc93b6c5`, tree `ba3a90a4c2eda1735d7fb6bd70b21cfe8def119b`.

Все 301 базовых Git blobs и режимы сохраняются без изменения. Добавления ограничены тремя файлами timebase в firmware/platform и tests/timebase/**. Текущий checkpoint этой итерации — здесь и в issue/PR. Базовые документы, host, startup, USB, RCC-реализация и workflows не меняются.

## Команды из чистого checkout

Требуются Bash, Python 3 и GCC с ASan/UBSan. Compiler и binaries не включены.

```sh
OUT=/tmp/timebase-check bash tests/timebase/reproduce.sh
TC=/path/to/toolchain/usr OUT=/tmp/timebase-arm sh tests/timebase/arm-check.sh
```

Первый runner проверяет SHA-256 новых файлов из publication-manifest.json (кроме manifest), запускает неизменённый авторский runner, затем отдельно компилирует неизменённые supporting/critic harness против фактического firmware/platform/mt_timebase.c. Выходы сохраняются в OUT; используйте свежий внешний каталог. GCC analyzer проверяет тот же production source. LSan отключён. ARM-команда отдельная; native PASS не подменяет её выполнение.

Оригинальные runner сохранены как свидетельства. Они ссылаются на локальные snapshot/subject и workspace; непосредственно из опубликованного evidence-каталога их запускать не следует. reproduce.sh меняет только пути, выходы и объединение запуска; C-тесты и production C/H не меняются. Вместо сравнения с отсутствующим локальным draft проверяется manifest. Отдельные sanitizer-запуски используют halt_on_error=1 и stacktrace, без suppressions.

## Контроль версий и происхождение

C SHA-256: `b403cc8f5e60cc2568b59d9fc5de6cf2cf1f116c03291876e7a05a5dc19f2740`.
H SHA-256: `613135965695f6e1eb4e24edea9e2873a33b60ee4b44865a5db1ef1730b5b3d2`.

Авторские исходники/тесты, supporting harness и Critic-A oracle принадлежат разным авторам. Supporting-проверяющий не читал авторский набор. Supplemental oracle Critic-A написан после чтения реализации/области supporting-проверок и не называется слепым тестом.

В ходе публикации 10.10.2026 около 14:18 UTC среда потеряла локальные рабочие файлы. Успешный предыдущий запуск сохранён в ответе инструмента, но его raw-логи были утрачены. Они не реконструируются как будто это оригиналы. Восстановление exact source требует совпадения заранее сохранённых SHA-256; новые recovery-запуски имеют отдельное происхождение. Подробная запись и новые логи находятся в evidence. Исторические manifests могут перечислять локальные binaries/subject, не включённые в PR; фактический состав задаёт publication-manifest.json.

## Ограничения и следующий шаг

Вердикт Critic-A остаётся **BLOCKED_EVIDENCE, score null**. После публикации требуется отдельный immutable-binding комментарий критика в PR. Merge, закрытие issue и приёмка не выполняются.

Предпосылка: между реальными CNT snapshots менее 65536 тиков. Наблюдаемый предел 32000 не доказывает её соблюдение. Пропущенные полные обороты, неподвижный CNT и скрытые transients могут выглядеть допустимыми; тесты показывают эту неоднозначность, а не обнаружение. При fault last-good значения недействительны для продолжения работы потребителей.

Следующая интеграция должна подтвердить единый clock context, pre/post health gating каждой использующей время операции и перед выводом данных, остановку потребителей и инвалидирование эпох при отказе. Обслуживание нужно также idle/stopped/USB-backpressure; срок АЦП остаётся строго <2560 мкс.

Не доказаны физическая частота, длительность callbacks или IO-001 bracket ≤20 мкс, сон/debug halt, полный ELF/Flash/RAM/stack/runtime, startup/USB, Windows interoperability либо LV/ignition. Разрешение счётчика 1 мкс не является точностью или WCET. ARM object и отдельные .su frames не заменяют полный ресурсный бюджет. CA-CTRL-4 базового controller остаётся отдельным общим вопросом. CI не запускается; workflows в базе отсутствуют.

## Фактический повтор после восстановления

Все семь исходных авторских файлов восстановлены с исходными SHA-256. Затем Dot A изменил только атрибуцию прежней ошибки Stop в TIMEBASE_README: дефект обнаружен чтением кода; исполнена регрессия исправленного пути. Сохранённого выполненного pre-fix опыта не было. Новая README SHA-256 `ee9339a69044cfa609ded6f081ebec17e1705d24663a1fc2b4614d4d3492ee4d`; [оригинал](evidence/history/TIMEBASE_README.original.md) сохранён как история, не текущая инструкция.

- Авторский набор: strict C11 и ASan/UBSan PASS.
- Supporting: 16 групп, 646 register bit-injection сценариев, strict/sanitizers PASS, GCC analyzer без диагностик. [Исходный отчёт](evidence/supporting/REPORT-ru.md) читать вместе с [recovery addendum](evidence/supporting/RECOVERY-ADDENDUM-ru.md).
- Собственный supplemental oracle Critic-A: 462147 сценариев / 32222386 проверок в strict и sanitizer. [Исторический отчёт](evidence/critic-a/CRITIC_A_TIMEBASE_REVIEW.original.md) содержит прежние ссылки на утраченные raw-логи; фактические свежие журналы имеют суффикс recovery.
- Publisher повторил все перечисленные native-проверки против опубликованных source bytes: [новый полный журнал](evidence/publication/reproduction-recovery.log).
- ARM GCC14.2.1 восстановлен отдельно; [новый ARM-журнал](evidence/publication/arm-recovery.log) подтверждает 1008 B text, context 52 B, unresolved memset и object SHA-256 `7219ce425cb16a8ec91af9e6ba17d1a49cddb5d7040e935e5bb21cb3119f02c1`. Объект и toolchain в PR не включены.

Сохранённые publisher-журналы получены до добавления самих журналов/финального manifest в пакет: в их начале указан тогдашний набор проверенных файлов. Тестируемые C/H, harness и команды остаются теми же. После окончательной упаковки runner проверяется ещё раз против полного manifest; финальный Critic-A binding в PR относится к фактическому immutable commit.

[Полный актуальный локальный отчёт Critic-A](evidence/critic-a/CRITIC_A_TIMEBASE_REVIEW.md) включает [recovery addendum](evidence/critic-a/RECOVERY_ADDENDUM.md), обновлённую привязку README и новые native/ARM свидетельства. SHA-256 полного отчёта: `2893bdd753d1dabc67c9a4400fa5c03e9ea0ef90c6dbf2603c46a39fb993f426`. Вердикт остаётся BLOCKED_EVIDENCE, score null.
