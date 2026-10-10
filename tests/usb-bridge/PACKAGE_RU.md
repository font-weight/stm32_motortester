# USB005: воспроизводимый пакет черновика

Задача [#60](https://github.com/font-weight/stm32_motortester/issues/60). Автор кода — Dot A. Уровень этой итерации: native guard model и ARM relocatable objects. Независимый итог для пакета ещё ожидается; наличие успешных тестов не означает принятие изменения или готовность прибора.

## База и изменение

Единственный допустимый parent: PR61, `7a721f82b98676d6fd9ab1b58a1d9b7d617b47a5`, tree `0d3f1acec1750cc0c3c9102d56fa211ce7729ead`, 871 blob. Из существующих файлов меняются только `firmware/transport/mt_usb_cdc.c` и `.h`. Остальные исходные файлы, режимы и `host/**` сохраняются. Добавляются bridge, hash-pinned patches/apply tool и свидетельства USB005.

`README.md` содержит авторские границы runtime, config0, cleaned/Stop и будущего owner. Это не принятие lifecycle-контракта. Config0 намеренно fail-closed: ответ и повторная конфигурация до нового физического reset в этой версии не поддерживаются.

## Одна команда воспроизведения

Из чистого checkout итогового commit, с Python 3.10+, GNU patch, GCC 14.2.0 и установленным ARM GNU toolchain 14.2.1 (newlib headers):

```sh
REPO=/absolute/path/to/checkout
ARM=/absolute/path/to/toolchain/usr
OUT=/absolute/path/outside-checkout/new-usb005-run
PYTHONDONTWRITEBYTECODE=1 python3 "$REPO/tests/usb-bridge/reproduce.py" \
  --toolchain "$ARM" --output "$OUT"
```

OUT должен отсутствовать, находиться вне checkout/toolchain и не быть alias исходного дерева. Скрипт проверяет package-manifest и inherited-inputs, сохраняет before/after hashes, команды, exit codes и stderr, в том числе при неудаче. Входы не перезаписываются. Из прежних свидетельств не берутся готовые `usbd.c`, DCD либо объекты.

Цепочка: pristine TinyUSB → USB003 apply → core USB005 apply; отдельно pristine FSDEV → USB004 apply → FSDEV USB005 apply. Новый результат обязан совпасть с SHA-256 в сохранённых manifests. Затем выполняются 47 shim, 40 core и 45 core+adapter сценариев, каждый в native и ASan+UBSan варианте; 40 core случаев входят в 45 adapter, это не новые независимые проверки. Leak detection выключен, LSan не заявляется. Дополнительно Critic-A добавил 10 supplemental scenarios с повторным использованием явно указанного supporting fixture; они выполняются отдельно native и ASan+UBSan, не выдаются за вторую периферийную модель. Проверка аппликатора выполняет 28 вызовов на одноразовых копиях. Следом 12 native tooling, 58 ARM path-isolation и 9 wrapper path-isolation проверок, затем сборка 10 ARM objects: 4 enabled, 3 opt-out, 3 baseline. Все десять object hashes должны совпасть с исходным run-03.

`supporting/run.py` сохранён побайтно (SHA-256 `1cd9834901f15af1226c2a9d79c240a7b6316d82f006d6eb36eba1ac5077d2fb`). Исторические команды с `/workspace/shared` являются записями прежних прогонов; актуальный переносимый entry point — команда выше. Исходный ARM runner имел P2-дефект: создавал output до проверки вложения в source trees. Исправленный авторский runner сначала проверяет canonical roots/subtrees/singleton aliases и наличие всех входов; 61 исходный negative/positive preflight случай прошёл без записи в fixtures. В packaged `arm/verify_arm.py` относительно этого исправленного runner изменён только список preflight/snapshot лишних входов: не копируются два неиспользуемых patch-дубликата и exploratory generator. Оба исторических runner и точный diff сохранены; команды сборки, asserts, flags, input comparisons и audit не изменены. Три проверки отсутствия этих исключённых файлов убраны из fixture list; остальные 58 case expectations не менялись. Это прозрачно отражено в arm/packaging-binding.json и отдельном diff теста. Provisional-reproduction выполнен до исправления ARM tooling и не заменяет финальный повтор.

## Состав и сохранённые неудачи

- `supporting/`: frozen тесты, шесть окончательных receipts с полными командами/результатами, tooling failures, исторический r1 summary.
- `negative-inventory/`: исходные отрицательные наблюдения, исходники probes, точные команды и статус acceptance-not-run. Это baseline-исследование, не результат финального bridge. Исторический probes/run.py пишет в собственный evidence-каталог и содержит старые абсолютные пути; не запускайте его внутри immutable checkout. Он сохранён для provenance, а текущая portable-команда его не вызывает.
- `critic-a/`: независимые supplemental assertions и applicator checks; отчёт критика и отобранные evidence описывают конкретную область и версии. Историческая проверка ARM output alias сохранена вместе с последующим исправлением.
- `evidence-root-adapter-sanitize/`: отдельное повторение автора, 45 случаев.
- `arm/`: отчёт и точные команды, исходники проверки, run-03 summary/inputs/toolchain/relocations/stack/diagnostics, исторические run-01/02 summaries. Пять прежних DCD conversion warnings сохранены; Werror не применялся. Ранние ошибки конфигурации MCU из author build сохранены в `historical-author/`, вместе с negative patch-tool checks.
- `inputs/pr61-adapter/`: исходный adapter из родительского tree для baseline object comparison; это test-only snapshot, не второе production implementation.
- `source-selection.json`: точная карта выбранных файлов и причины исключения остальных. Объекты, executables, preprocess/macro dumps, повторные snapshots и дубли логов генерируются заново. Прежние `.o/.i/.d` не подаются как вход новой проверки. Оригинальные полные evidence directories сохранены локально; итоговый Git-пакет намеренно компактнее.

Исторические supporting/ARM/critic отчёты сохраняют названия каталогов исходного рабочего места. В Git-пакете run-03 представлен как arm/evidence-run-03; полные ARM commands.json и повторные snapshots исключены, вместо них сохранены commands.sh, выбранные точные diagnostics/disassembly, input/toolchain manifests и summaries. После portable-запуска все полные команды и новые intermediates доступны в OUT. Source-selection относится к моменту отбора исходных snapshots; последующее исправление tooling связано отдельным packaging-binding и diff.

## Лицензии и пределы

Pristine vendor tree, `source-manifest.json`, `SHA256SUMS.sources`, `THIRD_PARTY_FILE_NOTICES.txt`, `PROVENANCE_RU.md` и лицензии наследуются без изменений из `firmware/third_party/usb`. TinyUSB имеет MIT license в `vendor/tinyusb/LICENSE`; CMSIS Core — Apache-2.0 в `vendor/cmsis-core/LICENSE.txt`; остальные notices приведены в manifest. Patch содержит фрагменты этих лицензированных исходников и не снимает исходные условия. Новые внешние зависимости не добавляются.

Fake interrupt lock/Stop/cleaned не доказывают PRIMASK, NVIC/vector/priority/PRIGROUP, IRQ latency или физическое прекращение старого TX. ARM evidence — объекты, не полный firmware ELF, не linker map и не суммарный stack. Реальные USB enumeration/reset/ACK/cancellation, PMAOVR, ADC/TIM contention, полный owner/ADC commit gate, Windows-связность и аппаратная проверка остаются открытыми. Девять shim не означают защиту любого public DCD/API; именованные исключения runtime перечислены в README.
