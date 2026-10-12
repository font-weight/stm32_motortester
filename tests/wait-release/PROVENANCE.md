# Происхождение тестового пакета

Исторический источник поведения — PR79 commit
`66fce97ba86de459e6d597deb32130e030f34594`, tree
`c3479382770fe37f6d90ba2d66ef8eb7e3c5bdde`.
Родитель первоначальной поставки PR82 — PR81 `dacc0c37cab54c69dc1852c6b357fe69b018ac94`, tree
`6a0536e91f3cec62528e92351186b7979fbce13c`.
Все 18 native production C/H побайтно совпадали в этих двух исторических деревьях.
Точные пути и SHA закреплены в manifest. Для запуска Git objects не требуются.

Без изменения перенесены:

- исходная characterization `fixtures/test_wait_release.c`;
- исторические `stop/test_stop_barrier.c` и `stop/run.sh`;
- самостоятельный `decode_wire.py`, не использующий firmware/host codec.

В `test_wait_release_proposal.c` и `test_mode_retention.c` изменена только одна
строка include: внешний абсолютный путь заменён на
`fixtures/test_wait_release.c`. Восстановление старой строки даёт исходный SHA.
Assertions, fake hardware, сценарии, sample значения и baseline main сохранены.

В `validate_run.py` добавлен import sys, а запуск decoder заменён с поиска
`python3` в PATH на текущий `sys.executable -I -B`. Assertions и все сравнения
не менялись. Новый reproduce.py выполняет эти проверки вместе с явным полным
inventory и gate для Stop/modes. Свои Python helpers он не запускает с -O.

Внешний исходный mode runner писал два набора wire bytes в один каталог.
Здесь native/sanitizer имеют разные каталоги; они сравниваются побайтно.
Прежний WAIT runner только собирал exit codes. Новый aggregate отвергает любую
ошибку и неполный inventory; default positive run не допускает expected-fail.

Историческая регрессия R1: stale-generation Stop обгонял timeout STATUS.
R2 сузил исключение до валидного текущего Stop. Неуспех R1, отрицательный R8
и supporting manifests сохранены во внешних архивах; их наличие внутри этого
пакета не заявляется. Не нужно менять старые oracle ради прохождения нового.

Root лично подготовил relative include, manifest, orchestration и документы.
Supporting inventory и независимый Critic-A не являются авторами этих правок.
Оценка/приёмка этого delivery-пакета требует отдельного exact-tree вердикта.

## 12.10.2026: новый источник для регрессии

Текущая редакция закрепляет PR95
`b607392cb4f1cc19918e2795359df4680b645643`, tree
`5176f9e50c2231f54c26edc9dcb13d592a04a5b7`. В наборе 18 C/H изменились
шесть: capture_link.c/h, control.c/h и tx.c/h. Их новые SHA внесены в
manifest, затем обновлён единственный manifest pin wrapper. Алгоритм
wrapper, все C fixtures, decoder, aggregate validator и исторические origins
сохранены побайтно. Старый wrapper обоснованно отказывается проверять
изменённые исходники; прежний PR82 и его свидетельства не переписаны.

Fresh replay этой редакции проверяет регрессию WAIT_RELEASE/Stop/режимов
на новых исходниках, а не обработку ADC fault и не общий контракт A/B.
Исторический case inventory остаётся исходным ожидаемым перечнем; новые
логи/receipt создаются каждой командой отдельно и не включены в Git.
Независимый вердикт требуется для точной новой редакции.
