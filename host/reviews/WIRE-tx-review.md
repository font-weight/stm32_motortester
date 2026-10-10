# Critic-B: независимая проверка TX-001 / Stop

Дата: 10.10.2026. Независимый scoped Critic-B, gpt-6-astra xhigh.
Критик не писал и не исправлял проверяемую реализацию или авторские тесты.
Изменены только этот отчёт и его собственные воспроизводимые review-артефакты.

## Вердикт и граница

**Компонент TX-001 на `826bef255362b258d5dcde2100c90dc7d591edb3`: PASS, 9.90/10.**
Область: ограниченная native C-очередь, mock FIFO, порядок байтов/ответов,
точка commit sequence/terminal, Stop/HELLO и локальные отказные пути.
Новых P0/P1 в этой области не обнаружено. Это независимое заключение B;
обязательное актуальное заключение Critic-A и решение интегратора оно не заменяет.

**Общий контракт / сквозной результат: BLOCKED_EVIDENCE, score: null.**
Теперь неверно говорить, что исполняемого свидетельства TX/Stop вообще нет:
transport-часть E-01 из [предыдущего отчёта](WIRE-session-b6ae-review.md)
подтверждена ниже. Связка RX → parser → dispatcher → захват → TX → host,
полномочия generation, STOP ERROR/recovery и освобождение настоящего буфера
этим модулем не реализуются и данным отчётом не подтверждаются.
Аппаратное USB, Windows 10, ARM/link/map и готовность всего firmware не оценивались.

## Точная версия и происхождение

- Репозиторий: [font-weight/stm32_motortester](https://github.com/font-weight/stm32_motortester).
- [Draft PR #19](https://github.com/font-weight/stm32_motortester/pull/19):
  actual head **`826bef255362b258d5dcde2100c90dc7d591edb3`**, проверен GitHub API
  при начале и повторно перед подготовкой отчёта; получен `git fetch` и архивирован.
- Base, возвращённый PR API, и фактический общий предок с текущим PR #14:
  `b6ae8025baf1a47e8173997029d2d8b858ada74c`.
- Предыдущий кандидат и непосредственный родитель:
  `06b67622d9ef9dbcd462478eeaa46d01dc8fe4ad`.
- [Общий контракт PR #14](https://github.com/font-weight/stm32_motortester/pull/14):
  current head `c54bd955aaf3cb863ef3df043ea237ea62e74ad4` проверен отдельно.
  Delta b6ae→c54 — одно уточнение external_edge_id по trigger_kind;
  C/header/test-файлы не менялись. TX head основан на b6ae и не включает это
  документальное уточнение. Итог слияния с c54 данным отчётом не проверен.

Diff base→head: только шесть новых файлов `firmware/transport/{mt_tx.c,mt_tx.h,README.md}`
и `tests/transport/{test_mt_tx.c,run.sh,evidence-native.txt}`. Codec неизменен.
Delta 06b→826 меняет проверку завершения Stop ACK с учётом session и добавляет
авторскую регрессию. Точные patches, API snapshots, хеши и команды находятся
в [пакете доказательств](evidence/tx-06b/README.md). Имя каталога сохранено
по исходному поручению; текущий вердикт относится исключительно к 826bef25.

PR body всё ещё называет 06b; его текст не использовался как authority для head.
[Комментарий автора об исправлении](https://github.com/font-weight/stm32_motortester/pull/19#issuecomment-6097773273)
прочитан, затем дефект и исправление воспроизведены независимо.

## Критерии и источники

Применена неизменная [рубрика 1.0](https://github.com/font-weight/stm32_motortester/blob/826bef255362b258d5dcde2100c90dc7d591edb3/docs/quality-gate.md):
C1/C2/C3/C4/C5 = 30/20/25/15/10%. Порог сравнивается до округления.
Проверены AGENTS.md, task.md, requirements.md, decisions/progress, mvp-spec,
coordination, quality-gate, session-guard, wire, транспортные исходники/guide/tests,
предыдущие B-отчёты, PR #19 и координация #2.

Применимые требования — частичная программная область
[MVP-F04/F06/F07](https://github.com/font-weight/stm32_motortester/blob/826bef255362b258d5dcde2100c90dc7d591edb3/docs/mvp-spec.md#3-прошивка).
Основания конкретных ожиданий:

- [Wire: сеанс, sequence и ответы](https://github.com/font-weight/stm32_motortester/blob/c54bd955aaf3cb863ef3df043ea237ea62e74ad4/protocol/wire-v0.1.md#сеанс-и-порядок).
- [Wire: Stop/terminal/barrier](https://github.com/font-weight/stm32_motortester/blob/c54bd955aaf3cb863ef3df043ea237ea62e74ad4/protocol/wire-v0.1.md#stop-и-отказы).
- [API: irrevocable callback, ownership, reserved queue, timeout](https://github.com/font-weight/stm32_motortester/blob/826bef255362b258d5dcde2100c90dc7d591edb3/firmware/transport/mt_tx.h#L32-L124).
- [Guide: обязанности caller и локальная область](https://github.com/font-weight/stm32_motortester/blob/826bef255362b258d5dcde2100c90dc7d591edb3/firmware/transport/README.md#зачем-отдельная-очередь).

Полная семантика payload, допустимость команд/окон и реально необратимый FIFO
являются входными обязанностями dispatcher/адаптера, а не скрыто объявленной
функцией этой очереди. Поэтому отсутствие реального USB не превращено в
недостаток native-компонента и не объявлено выполненным испытанием MVP.

## Лично выполненные проверки

Linux x86-64, GCC 14.2.0, Python 3.12.14. Полный исходный код архивирован
по SHA вне рабочего checkout. Строгие флаги независимой сборки:
`-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Werror`.

1. Авторский suite: **14 групп**, native `-O2` и ASan+UBSan `-O1 -g`, exit 0.
2. Собственный harness напрямую вызывает C API через ctypes. Ожидаемые wire-байты
   строятся отдельными Python COBS/header/CRC функциями с `struct`/`zlib`,
   без использования production encode/decode для oracle.
3. **988 Stop byte-boundary сценариев:** каждый префикс от 0 до полного кадра
   для replies, STATUS, BEGIN, максимального DATA, END, ABORT, ERROR.
   Нулевая запись не расходует sequence; неначатый END заменяется ABORT;
   начатый/полный END не получает второй terminal. Проверены prior-reply order,
   сохранение queued ABORT и исходной причины, неизменность при DRAINING.
4. **988 HELLO byte-boundary сценариев:** удаление неначатых replies/events,
   сохранение старого частичного кадра, несколько замен HELLO,
   новый ACK sequence1, Stop новой session во время старого partial ACK.
5. **55 повторных Stop** при каждом префиксе STATUS после ACK: барьер освобождается
   на последнем байте ACK; неначатый STATUS можно убрать, начатый завершается;
   прежний reply остаётся перед новым Stop ACK. Отдельно проверены Stop(0)
   до ARM и ранний ABORT выделенного окна без BEGIN.
6. **2 000 воспроизводимых случайных передач**, seed 20261010: разные чётные
   DATA payload длины 14..512, zero/partial/full writes, мутация исходного
   сообщения после enqueue. FIFO побитно совпадает с независимым oracle.
7. **11 264 shape-admission случая**, плюс нулевые generation/window,
   чужие caller session/sequence, NULL guards и **28 atomic invalid Stop**.
   Ошибочный ввод не меняет очередь, неположительный poll не меняет event.
8. Queue limit/reserved Stop достигает ровно **6** слотов; FULL неизменен.
   Проверены 24 I/O recovery случая до/после prefix, latched fault без следующих
   writes, физический reset и новое HELLO. Stall/frame/Stop deadlines проверены
   до и ровно на пороге, включая uint32 wrap; MAX−1 выдаётся, MAX не выдаётся.
9. Весь независимый harness повторён с C ASan+UBSan: **2 369 244 проверки**,
   тот же JSON-результат, без sanitizer diagnostics. Stderr финального скрипта
   содержит только shell trace `cc -print-file-name=libasan.so`. `detect_leaks=0`;
   результат LeakSanitizer не заявляется. Количество проверок само по себе
   не является основанием балла.
10. Host-resource проверка: `sizeof(mt_tx)=3928`, slot=552; `-Os -fstack-usage`
    показывает собственный stack frame Stop=600, poll=160, encode=568 байт.
    Undefined symbols не включают allocator. Это host ABI и локальные кадры,
    не ARM map, не сумма всей прошивки и не worst-case interrupt stack.

Все команды и вывод сохранены; [воспроизведение](evidence/tx-06b/README.md).
Два сбоя при разработке самого независимого harness отдельно сохранены и
объяснены: неверный подсчёт уже завершённого старого ACK и tuple accessor.
Они не выдаются за дефекты C; после исправления только review-harness выполнен
полный повтор. Production/test-файлы кандидата не изменялись.

## Старый дефект и оставшееся замечание

### CA-TX-1: закрыто в проверенной новой версии

В точном 06b старый partially accepted STOP_ACK после HELLO мог снять барьер
новой session. Собственный HELLO-пробник воспроизводит failure
`old Stop ACK released new session barrier` на 06b, exit 1.
Тот же сценарий и все byte boundaries проходят на 826bef25.
Исправление — [mt_tx.c:219](https://github.com/font-weight/stm32_motortester/blob/826bef255362b258d5dcde2100c90dc7d591edb3/firmware/transport/mt_tx.c#L219):
ACK освобождает Stop только своей текущей session. Это новая проверка,
а не перенос прежнего «нет известных дефектов» на другой SHA.

### TX-D1, P2, неблокирующее: указатели доказательств устарели

README:90 всё ещё сообщает 13 групп. `evidence-native.txt` хранит хеши и лог
прежнего 06b, PR body называет его текущим кандидатом. Исторический лог
сам по себе достоверен; комментарий PR уже сообщает исправление и 14 групп.
Но читателю приходится самостоятельно различать две версии. Рекомендуется
явно пометить старое свидетельство историческим и дать текущие SHA/лог/число
групп, сохранив прежнюю запись. Здесь точная provenance восстановлена;
известного нарушения поведения или подмены результатов на 826bef25 не найдено.

## Рубрика 1.0: компонент

| Критерий | Вес | Балл | Основание |
|---|---:|---:|---|
| C1. Корректность | 30% | 10.0 | Native API выполняет заявленные commit/order/Stop/HELLO правила; старый ACK regression устранён и воспроизведён на обеих версиях |
| C2. Надёжность | 20% | 10.0 | Проверены zero/partial writes, сроки/wrap, latched failures, bounds/reserves, invalid input и восстановление в рамках callback contract |
| C3. Доказательства | 25% | 10.0 | Лично выполнены strict suite, независимый byte oracle и оба sanitizer-прогона; обязательных неизвестных проверок в заявленной native-области не осталось |
| C4. Ясность | 15% | 10.0 | Небольшая single-owner очередь, явные обязанности caller, отсутствие heap; FIFO acceptance не выдан за USB/host delivery |
| C5. Воспроизводимость | 10% | 9.0 | Команды, SHA/diff, logs и harness воспроизводимы; TX-D1 оставляет конкретный небольшой пробел актуальности исходных указателей |

Неокруглённый итог: **0.30×10 + 0.20×10 + 0.25×10 + 0.15×10 + 0.10×9 = 9.90**.
Вердикт относится только к проверенному native TX-компоненту на названном SHA.

## Что изменилось для общего gate

E-01 больше не означает отсутствие исполняемых TX-тестов. Локально доказаны
cancel/commit sequence, END/ABORT, early/no-window/repeated Stop, queued ABORT,
prior replies, последний байт ACK и Stop после HELLO. Но остаются отдельные
входные/выходные обязанности, которые нужно проверять интеграционно:

- корректная выделенность окна и generation, disable sampling до enqueue;
- реальные RX command/state order и отказ ARM → STOP ERROR;
- немедленное host recovery после ERROR на STOP, без ARM/STOP старой session;
- удержание/освобождение frozen capture buffer и END CRC tombstone;
- адаптер, действительно выполняющий irrevocable ordered write contract.

[CTRL-001 #28](https://github.com/font-weight/stm32_motortester/issues/28)
назван A следующим местом command/fake-hardware evidence в
[актуальной координации](https://github.com/font-weight/stm32_motortester/issues/2#issuecomment-6097886770).
Связанный bridge PR #30 и новые host-критерии здесь не пересматривались;
их отдельные текущие отчёты нужно учесть при общей приёмке, а не объявлять
их результаты отсутствующими по старому списку.

Для общего объекта C1–C5 сохраняют веса 30/20/25/15/10, но все баллы и итог
**null**, вердикт **BLOCKED_EVIDENCE**: нет проверенного этим ревью полного
набора обязательных сквозных свидетельств и двух актуальных PASS на общий
результат. W-02 о trigger_kind закрыт текстом c54 согласно предыдущему
addendum; старый D-01 о текущем статусе общего draft остаётся отдельным P2.
Ни software-ready, ни физический Stop/USB, ни Windows acceptance не выданы.
