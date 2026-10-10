# Независимые review-артефакты

Эта ветка сохраняет отчёты Critic-B отдельно от проверенного кода. Commit отчётов не является новым принятым кодовым SHA. Автор приложения не менял вердикты.

- [HOST-001 PASS](HOST-001-review.md), строго код `2376d325d573b1aa3da6feaf8fddf05d2e1cb601`; [новые доказательства](evidence/host-2376/).
- [Исторический HOST-001 REWORK](HOST-001-review-7d74cdf.md), код `7d74cdf1cb04d8d3cbd9edcd06d6b23a27007392`; [исходные доказательства](evidence/host-7d74/).
- [Текущий wire/stream BLOCKED_EVIDENCE](WIRE-current-review.md): точные SHA и границы внутри. [Wire](evidence/wire-124b3/) и [stream](evidence/stream-8d5f/) reproducer/results.
- [Историческое wire-ревью](WIRE-draft-review.md).

Временные пути в неизменённых отчётах описывают реальное место запуска. Соответствующие сохраняемые логи/изображения доступны в каталогах выше. Команды могут требовать замены временного пути на свой checkout. Ни один результат не подтверждает Windows 10, USB-прибор или аппаратные измерения.

## Добавленные проверки

- [HOST-002 PASS](HOST-002-review.md), код c89c12df: только opaque byte/stream компонент; [доказательства](evidence/host-wire-c89/).
- [HOST-003 PASS](HOST-003-review.md), код2cf1fb83: PNG на Linux; [доказательства](evidence/png-2cf1/), включая red-регрессию прежнего clipping. [Исторический REWORK](HOST-003-review-ae7e3ffd.md).
- [Shared session BLOCKED_EVIDENCE](WIRE-session-b6ae-review.md), кодb6ae8025; [доказательства](evidence/session-b6ae/). Новые версии общего контракта требуют отдельной проверки.
