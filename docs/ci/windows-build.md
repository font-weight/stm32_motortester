# Проверка Windows-сборки, без публикации пакета

Черновая задача общей CI команды A. host/** и сама build_windows.py остаются
областью Dot B. Проверяется точный commit PR71
f0822f7b336b0cab0e01632eee33f0f9a24473cc, не движущаяся ветка и не PR merge tree.
Это reviewed viewer integration на родителе PR45
bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed с исправленным тестом scroll reachability;
Auto/Single и новые runtime-ветки в этот запуск не включены.
Recipe blob остаётся 4a1b5d7184253a11d342ba1db909879a2515a184.
Workflow хранит собственный SHA отдельно от SHA проверяемого приложения.

Запуск: pull_request в main только при изменении host-windows.yml или вручную
после доступности workflow_dispatch в основной ветке. Публикация draft PR с этим
файлом запускает проверку; это не слияние и не приёмка проекта. Время ограничено
30 минутами, права только contents:read, credentials не сохраняются. Сторонний
fork и приватный репозиторий исключены условием задачи.

GitHub API 10.10.2026 подтвердил public для font-weight/stm32_motortester.
[Правила GitHub](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
указывают бесплатные стандартные hosted runners для публичных репозиториев.
Поэтому используется только стандартный windows-2022. Larger/self-hosted runners,
платные ресурсы, секреты и изменение настроек учётной записи не применяются.

Artifact storage имеет отдельные квоты, свободный остаток аккаунта не проверен.
Поэтому workflow не использует upload-artifact, remote cache и публикацию release.
Диагностика, manifest и SHA256SUMS выводятся в журнал задачи; по документации GitHub
логи и job summary не расходуют квоту artifact storage. ZIP и PNG существуют только
во временной среде проверки и не являются доставленным или сохранённым пакетом.
Для передачи пользователю пакета нужен отдельный разрешённый путь сохранения.

Точная обычная CPython3.13.16 x64, существующий hash-locked набор B, тесты, PyInstaller
и запуск полученного EXE выполняются неизменной build_windows.py. Сборка Windows
Server и синтетический снимок приложения не заменяют чистую Windows10, проверку
лицензий, настоящий USB, плату и независимый quality gate.

Предыдущий фактический run38061091297 для source3b315ac4e1184c4c7e9216ce5594cff0f9beff33
завершился ошибкой проверки видимости при шрифте22px, до этапа упаковки. Он сохранён
как исторический результат. Новая версия source не считается прошедшей Windows до
отдельного успешного run и проверки его точного source/workflow SHA.

Независимая исходная привязка кандидата: [Critic-B PR71](https://github.com/font-weight/stm32_motortester/pull/71#issuecomment-6101161842).
Просьба Dot B о repin: [PR34](https://github.com/font-weight/stm32_motortester/pull/34#issuecomment-6101128395).
Эти локальные/synthetic результаты не подменяют предстоящий Windows run.
