# Critic-B: DOC-006 / D-01, status-only delta 6745fb0f

Дата: 10.10.2026. Независимый Critic-B; не автор исправления, реализации или
представленных свидетельств A. [Issue #36](https://github.com/font-weight/stm32_motortester/issues/36),
[draft PR #38](https://github.com/font-weight/stm32_motortester/pull/38).

**D-01 закрыто на уровне документа. Critic-B: PASS, 10,00/10 для status-only DOC-006.**
**Общий wire-контракт: BLOCKED_EVIDENCE, score: null.**
Числовая оценка документационной дельты не передаётся всему контракту, PR35,
будущей интеграции, Windows, USB или прибору. Совместная приёмка/слияние не объявляются.

## Версия, критерий и границы

- Head: **`6745fb0fa9802a510b49980e597e5f3679e025b8`**.
- Единственный parent/base: **`c54bd955aaf3cb863ef3df043ea237ea62e74ad4`**.
- Tree: `2d9185ebc9a7cba3e69f84d6bb247d700bca1b8f`.
- GitHub metadata и `git ls-remote` подтверждают опубликованный head. PR draft, открыт, не слит.
- Собственный свежий `git archive`: `/tmp/critic-b-status-6745`; все **37** файлов
  побайтно сверены с Git. Изменены только wire-v0.1.md и верхнее JSON-поле status;
  все **35** других blobs и все file modes сохранены. Diff: 16 добавлений, 8 удалений.
- Проверяется предзаданная issue36 задача: исправить устаревшие not-compiled/
  no-command/codec-only описания без изменения нормативных правил и vector bytes;
  сослаться на опубликованные evidence с правильными scope/pin/ограничениями.
- Применимы DOC-01/DOC-02, CON-12, D-21; рубрика quality-gate 1.0 и исходные
  веса 30/20/25/15/10 сохранены. Прочитан полный diff и актуальный wire-документ,
  исходное замечание D-01, issue36, PR38 и опубликованный Critic-A review.
  Общие инструкции/требования совпали с ранее прочитанным контекстом HOST-006;
  добавленные исторические записи A-001…A-004 в docs/progress.md прочитаны отдельно.

[Metadata](evidence/wire-status-6745/pr38.json), [remote head](evidence/wire-status-6745/remote-head.txt),
[полный diff](evidence/wire-status-6745/delta.patch),
[машинная сверка](evidence/wire-status-6745/verification.json).

## Лично проверенные свойства дельты

1. **Нормативный текст не изменён.** После Framing весь текст побайтно совпал
   после ровно четырёх явно перечисленных status-подстановок: выполнение parser
   checks, описание оставшейся общей приёмки, статус реализации capabilities,
   статус опубликованного вектора/более широких проверок. Exact до/после каждого
   фрагмента сохранены в verification.json. Форматы, значения, masks, payloads,
   sequence/session/Stop/window, сроки и требования проверки сохранены.
   Вводная часть отдельно прочитана полностью: она сообщает статус и не вводит
   новый wire-переход или разрешение эксплуатации.
2. **JSON-вектор неизменен.** Удаление только верхнего status даёт равные JSON
   значения; исключение единственной status-строки даёт побайтно равные файлы.
   Независимый короткий Python decoder восстановил COBS, проверил CRC-32 через
   zlib, контрольный `123456789`, длину/reserved и все expected поля вектора.
   Код проекта при этой проверке не импортировался.
3. **PR21 evidence соответствует формулировке.** Прочитаны pinned README и native
   report на `8dfc32b2c7558624e8d02dde49404501e3fc2622`; strict/sanitized stages
   имеют exit0 и **3 294 024 assertions в каждом прогоне**, не столько независимых
   сценариев. Все шесть source blobs codec/stream/command из manifest совпадают
   с PR14 base. Сеанс/dispatch/window payloads не приписываются этой серии.
4. **Native session подтверждается отдельным свидетельством.** У сохранённого
   docs/evidence/a004-native.txt лично проверены все шесть SHA-256 входов на базе
   c54; в нём присутствуют авторский строгий и sanitizer прогоны. Это исторические
   native checks, не новые ARM/USB/аппаратные результаты данного ревью.
5. **Поздние модули корректно отделены от базы.** Прочитаны README и наличие
   bridge source PR30 `a451e5a491439fcfed31f298a589643cf7050ec9`, controller source/
   README и финальные журналы PR35 `0ddea1e5992caa6231203838c3956e19675867a8`.
   В базе PR14 этих двух модулей нет. Pinned PR35 logs действительно сообщают
   **39 scenarios**, oracle **20 окон/40 960 кодов**, а открытый exact-SHA
   [review Critic-A](https://github.com/font-weight/stm32_motortester/pull/35#issuecomment-6098054287)
   сохраняет BLOCKED_EVIDENCE/null. Это сверка опубликованных свидетельств,
   не заявление о личном повторном исполнении C-наборов.
6. Сверены неизменяемые source/evidence ссылки и прочитан
   [Critic-A DOC006 review](https://github.com/font-weight/stm32_motortester/pull/38#issuecomment-6098159964).
   Наличие кода, ARM objects и синтетики нигде не превращено в физический USB,
   реальную плату, полный firmware/Windows путь или принятый прибор.

SHA-256 новых файлов:
- wire: `1903c692058e6ba6bdf92e90aba1ca5c913dbddb7a1420f4ec65e989380d4cf4`;
- vectors: `96c7ed5800256e7df50fb310b8fff7c145ed3fc6b83b4b8d04030567e8fe8ef9`.

[Скрипт проверки](evidence/wire-status-6745/verify.py),
[лог](evidence/wire-status-6745/verification.log),
[исторический PR21 report](evidence/wire-status-6745/pr21-native-report.json),
[session evidence](evidence/wire-status-6745/pr14-session-native.txt),
[PR35 oracle](evidence/wire-status-6745/pr35-oracle.json).
Проверка — Python 3.12.14 + git в Linux; новые compile/sanitizer/ARM/Windows
прогоны для неизменённого executable кода не требовались и не заявляются.

## Открытые границы

D-01 об устаревших статусных утверждениях устранено; новых P0/P1 или других
подтверждённых дефектов этой документационной дельты нет. Обязательная
документальная evidence доступна и проверена.

**CA-CTRL-4 остаётся открытым отдельным вопросом shared scheduling.**
Лично прочитаны [предложение A](https://github.com/font-weight/stm32_motortester/issues/2#issuecomment-6098308539)
и [позднее дополнение Critic-A](https://github.com/font-weight/stm32_motortester/pull/35#issuecomment-6098334397).
PR38 не меняет правила допуска GET_STATUS/GET_CAPS/RELEASE во время Stop drain
и не закрывает эту неоднозначность. Сохранённое BLOCKED_EVIDENCE/null этому
не противоречит. Отдельная [проверка текущего host barrier](evidence/session-477/ca-ctrl-4/FINDING.md)
не является принятием общей политики или изменением source pin.

Старый общий gate нельзя закрыть одним исправлением документа: нужны согласованные
точные версии обеих сторон, актуальные независимые заключения и применимые
интеграционные доказательства. Этот status-only review не является повторным
полным ревью всех позже появившихся модулей. Аппаратная готовность и Windows
не проверялись. Critic-B не выполнял GitHub writes, не менял shared spec, production
или product tests; исходные предыдущие отчёты сохранены.

## Фиксированная рубрика — только DOC-006

| Критерий | Вес | Балл | Основание |
|---|---:|---:|---|
| C1. Корректность | 30% | 10 | D-01 устранено, статусы соответствуют опубликованным exact inputs; нормативный wire и vector не меняются |
| C2. Надёжность | 20% | 10 | Ограничения/открытые gates сохранены; нет нового поведения, скрытого принятия или расширения capabilities |
| C3. Доказательства | 25% | 10 | Личные tree/byte/hash/vector проверки и сверка всей новой evidence provenance; собственные действия отделены от исторических отчётов |
| C4. Ясность | 15% | 10 | Различены модули базы, последующие drafts, native/ARM/физические уровни; assertions не названы независимыми сценариями |
| C5. Воспроизводимость | 10% | 10 | Exact head/base/tree, immutable ссылки, два новых hashes, сохраняемый verifier/log/manifest |

Неокруглённый итог DOC-006: **0,30×10 + 0,20×10 + 0,25×10 + 0,15×10 + 0,10×10 = 10,00 ≥ 9,5**.
Вердикт Critic-B на status-only изменение: **PASS**. Порог не снижен, критерии
не удалены, отсутствующее аппаратное доказательство не заменено баллом.
Для общего wire по всем C1–C5 и итогу: **score: null / BLOCKED_EVIDENCE**.
D-01 закрывается; общего PASS, разрешения merge или software-ready отсюда не следует.
