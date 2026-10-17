# Ограниченный evaluated-workflow caller: app dcaf, локальная версия r1

Результат: реализован отдельный преемник проверенного app244b/r2, без изменения его файлов. Локальный workflow намеренно отключён (`false &&`). Это готовые к независимой проверке исходники и patch, не результат запуска Windows и не разрешение распространения.

## Зафиксированные входы

- Приложение PR179: `dcafc5638bc6c3ddef6cba25a7a55016fe8e8933`, tree `61a9bd025081acec80997dff950d947eeceb3f4b`; 1501 Git-файл / 306 host-файлов. Исходный build recipe, lock, notice collector, recording и inspector не заменены.
- Retention PR178: `49c271104939a9c6ca6027ec239a904cac11a1a3`, tree `b8b3fb3d4644f00cb7bd3e8d2d149d8cd6c4d549`.
  - `retain.py`: 20066 B, SHA-256 `3ab02204cf20771c1ff67ca0ec05c8d8c6cb639a93ed56a653ba487fc856b08c`.
  - `zip_kit.py`: 8419 B, SHA-256 `1270725f73afe9c947e7ce5835779eee28c7604d429a30f244aff06684fe40b6`.
- Checker v2: 24424 B, SHA-256 `b6264f445d9c7abf3170e3072d25632f3bb6e9b3980c0af5bfa8e206bf0dd8e7`; независимый scoped PASS 10.00 уже получен. Его исходник берётся из **реально evaluated workflow commit**, как и caller. Все Python helper sources хешируются и исполняются из тех же bytes через compile/exec; `pyc` не загружается.
- Неизменённый inspector: commit `54d63be3fdd8eba858bafafbf6cec33c4ed989b8`, SHA-256 `fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53`.
- B support input disposition: [issue33 comment6233217133](https://github.com/font-weight/stm32_motortester/issues/33#issuecomment-6233217133). Точный текст сохранён в `SUPPORT_INPUTS_B.md`. Это описание воспроизводимых входов и отдельного ordinary-candidate scope, не legal/distribution clearance.

## Состав и проверка

`stage_plan.py` принимает только явные пути и сохраняет строгую canonical absolute-path проверку retainer. Доверенный PowerShell строит все пути через Join-Path/GetFullPath. Inert WindowsPath regression подтверждает исправление app244b/r1 для обеих ветвей и не ослабляет rejection произвольных mixed-slash, UNC, dot и reserved paths.

Accepted-ветвь требует 13 реальных GitHub step outcomes `success`. Исходные clean/raw-Git guards, build и frozen source/serial/GUI/recording smoke, строгий archive validator, inspector и независимые job/recording logs сохранены. Новые точные spec/selector blob pins и source-count guard добавлены отдельно.

Неприменимый fixed46 native-origin observer заменён явно:

1. Проверенный checker v2 проверяет 47 exclusions / 7 непустых keepers в финальных onedir и ZIP, все их bytes и correspondence с original manifest/archive report. Старый observer/baseline не подставляется вместо этой проверки.
2. Сохраняется самостоятельная CPython notice-проверка: реальный Windows CPython 3.13.16 x64 из фиксированного hostedtoolcache, original LICENSE source, package copy, build-manifest и исторический notice SHA. Два original `.bin` sidecar сохраняются без изменений и независимо печатаются в ограниченные logs. Это не native-origin attribution.
3. Сохраняется hash join реального `COLLECT-00.toc` с оригинальным inspector/provenance. TOC по-прежнему разбирает исходный inspector.
4. Caller наблюдает фактический selector subset из bounded build.log: ровно один header, count 0..47, уникальные approved destinations. Отсутствие файла в конечном пакете не считается доказательством его удаления.
5. Все 51 literal notice body (176605 B), manifest и guide проверяются в фактическом onedir против pinned dependency manifest и build-manifest. Исходный collector inventory сохраняется byte-for-byte; у PySide6-Essentials и shiboken6 проверяются все 53 dependency references. Также соединены package `_internal/docs/project-license.md`, `_internal/docs/dependency-sources.md` и root `LICENSE-REVIEW.md`. Отдельный root LICENSE не придумывается. ZIP correspondence обеспечивает уже обязательная archive/checker цепочка; fresh source.zip содержит все 306 проверенных Git bodies.

План содержит ровно 11 build-файлов: исходные 8 checksum payload files, включая только две receipt-selected recording leaves, плюс SHA256SUMS, build.log и build-manifest. EXE отдельно связывается hash/size с receipt/provenance, а доставляется внутри исходного candidate ZIP.

При полном production inventory ожидается 23 дополнительных evidence row: workflow context/source; оригинальные archive/provenance/reduced-payload/CPython receipts; два notice sidecar; support-manifest и его 11 файлов; original license inventory; два ограниченных generated observation (notice coverage, selector subset). Исходный archive-verification копируется именно из BUILD_DIR в отдельный evidence directory с hash/readback check. Ни одна запись не заменяет оригинальные schemas/checks.

После exact expected-manifest caller запускает bound retain, проверяет terminal exit/status и OUTER_MANIFEST hash, запускает bound ZIP helper и перепроверяет весь ZIP hash/size. Никаких upload/download delivery claims эти статусы не дают.

## Support materializer

`support-manifest.json` содержит только `path/bytes/sha256`: пять отдельных архивов, пять B guidance/coverage файлов и существующие `host/docs/windows-smoke.md` из exact dcaf. Последний файл является текущей trial-инструкцией. Финальный physical/no-board checklist остаётся отдельной работой, не новым условием inert Windows candidate build.

Пять архивов суммарно 81483468 B, максимальный 51220268 B; действующий 64 MiB cap не увеличен. Guidance/инструкции копируются из raw-Git-verified source checkout. Manifest загружается из evaluated workflow commit и проверяется по SHA, зафиксированному в reviewed workflow, а не вычисленному из produced output для самоодобрения.

Inline materializer находится только в trusted workflow. Он начинает с точных официальных URL B; допускает не более трёх redirects только на тот же точный URL или конкретный retained mirror URL, уже объявленный в pinned dependency manifest. Новый mirror route должен fail closed. Чтение идёт порциями до 1 MiB с expected-length+1 oversize detection, 15-second socket timeout и общим deadline 240 секунд; workflow step имеет 5-minute timeout. Проверяются status/content encoding, exact bytes/SHA и final readback. Exclusive files не перезаписываются. Архивы не извлекаются и не исполняются. Здесь никакой live download не выполнялся; изменение маршрута официального сервера может потребовать отдельного reviewed URL update.

Никакого magic approval JSON нет. Значения legal/source-completeness/replacement/distribution остаются false/qualified; перечисленные source archives не доказывают соответствие native build/configuration. Ограничение Mesa retained-byte digest и unresolved Tika/PRONOM qualification сохраняются в исходной guidance.

## Ошибки и transport

При неуспехе сохраняется только явный whitelist из 8 build-text paths и 3 report paths, каждый до 1 MiB, плюс исходные outcomes. Ни archive, EXE, PNG, notice-bin, recording payload, support body, ни wildcard в diagnostics не допускаются. Missing/oversize/nontext отражаются как incomplete; diagnostic CLI всегда завершает процесс кодом 1, сохраняя failure. Partial output нельзя потреблять.

В основном patch нет upload. Отдельный optional patch использует immutable `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9`, ровно один canonical `CALLER_ZIP`, `archive: true`, один день, overwrite false, hidden false, missing error. Его upload switch остаётся false до закрытия distribution/integration gates; job также отключён. Root подтвердил, что owner public-binary permission уже получено. False switch не означает отсутствия этого разрешения. Contents permission остаётся read. Никакого release, нового token или credential нет.

Для реального использования root должен опубликовать caller/checker/support-manifest по указанным script paths в evaluated workflow source, получить независимую exact-composition проверку, затем отдельно решить включение workflow/upload с учётом уже полученного owner permission и ещё открытого third-party distribution disposition. Distribution acceptance, Windows 10, physical board/configuration и delivery остаются отдельными воротами. Этот deliverable ничего не dispatch/upload/merge. Поле caller `public_binary_publication_authorized: false` означает, что helper сам не выдаёт такое разрешение; оно не оценивает существующее разрешение владельца.

## Локальные результаты

Все тесты выполнены на Linux, только inert data:

- caller: 50/50 normal и 50/50 `-O`;
- canonical Windows path model: 9/9 + 9/9;
- YAML, exact original-step preservation, inline-Python syntax, patch application и reproduction: 17/17 + 17/17;
- actual generated support materializer с in-memory HTTP responses: 12/12 + 12/12. Сокеты и subprocess запрещены тестовыми mocks; проверены changed/oversize bodies, encoding, redirect, unavailable source, existing output, old app и manifest pin.

Положительные notice fixtures используют явно test-only digest inert text: исторического Windows installer LICENSE body локально нет. Production NOTICE_HASH не меняется. Отдельный реальный CLI negative test подтверждает rejection этих fixture bytes. Это не успешный Windows CPython notice run.

Исходные app244b/r1 и r2, их manifest и logs сохранены в соседнем `workflow-caller/`; их hashes проверены regression suite. Точные текущие размеры/SHA всех deliverables и входных pins перечислены в `DELIVERABLE_MANIFEST.json`.

## Явные входы для повторного запуска tests/generator

Тесты и generator не предполагают наличие соседних private-workspace каталогов. Перед запуском задаются абсолютные пути:

- `CALLER_BASE_WORKFLOW`: файл исходного workflow SHA-256 `7341772216fa98fc98946e466cf36038a66dfec15977b3118d76e28109a998b6`.
- `CALLER_TEST_HELPERS`: каталог с точными `retain.py` / `zip_kit.py` из PR178/49c2711, hashes указаны выше.
- `CALLER_TEST_APP_SOURCE`: checkout/экспорт exact appdcaf. Selector и guidance bodies проверяются по pinned SHA/bytes; тесты не исполняют код приложения.
- `CALLER_TEST_BASELINE`: сохранённый app244b/r2 caller evidence bundle с `stage_plan.py`, r2 YAML и `history/r1/` для исторического Windows-path regression. Нужные исторические hashes проверяются тестом.
- `CALLER_TEST_OLD_RETAINER`: exact app244b/a26bd0072 retainer source для old-helper rejection, SHA-256 `9e51a4bc9d8cde8ad52c3f286dfff1f262eeb322d96b10f7f0263fc811c11dd7`.
- `CALLER_TEST_CHECKER`: checker v2 file с указанными выше 24424 B / SHA.

В каталоге caller выполняются `python -B build_workflow_draft.py` и по одному `python -B test_stage_plan.py`, `test_windows_paths.py`, `test_workflow_static.py`, `test_support_materializer.py`; повтор с `python -O -B`. Static/support suites требуют PyYAML, patch-application test также требует утилиту `patch`. Это Linux data-only replay, не PowerShell или Windows smoke. Необязательных молчаливых skips для отсутствующих pinned inputs нет: caller должен явно предоставить эти inputs. Без исторического evidence bundle весь historical regression suite не заявляется runnable из одного repository checkout.

Портируемость изменила только resolution входных путей tests/generator. Production caller, generated YAML, оба patches и support-manifest byte-identical предыдущему freeze. Предыдущие файлы/logs сохранены в `history/portability-r1/`; исправление wording о существующем owner permission сохранено отдельно в `history/documentation-r1/`.
