# USB-004: состав и повторение черновика

Связано с [issue #58](https://github.com/font-weight/stm32_motortester/issues/58).
Автор производственного патча — Dot A; независимый проверяющий — существующий Critic-A.
Пакет только добавляет файлы поверх [USB-003 draft PR #59](https://github.com/font-weight/stm32_motortester/pull/59):
commit `f31d011d7727279c7356ff9700949bc7a116ab90`, дерево
`339597e0208fe33e65c2232f2159a8635bf167d0`, ветка `dot-a/usb003-setup-accounting-draft`.
Все 741 унаследованный blob и их режимы сохраняются. Vendor, host и workflows
не изменяются. Новая ветка/commit/PR пока не заявлены этим локальным документом.

Цепочка предыдущих draft сохраняет зависимости и историю, но не утверждает готовность
полного USB lifecycle. Патч ограничен двумя операциями в `handle_bus_reset` для
STM32F103xB после настоящего bus/FRES reset: CTR sweep восьми физических slots и очистка
всего `xfer_status` до штатного открытия EP0. Эксклюзивное выполнение reset-handler
является предусловием, которое этот патч сам не реализует.

## Неизменённые источники и исторические свидетельства

- Патч, apply tool, manifest и ARM runner скопированы у автора побайтно. Исходный DCD
  SHA-256 `62b73c5fab0cbda70df6bd1bc1c9a7df0c8a59ea02c38c884b24b55e44ddfc9f` остаётся в vendor;
  build-копия имеет SHA-256 `1326ac072ff0f687f6589e5045184c77d578687f13e58b85b1e783aa2946fe97`.
- `supporting/` сохраняет исходники, инструменты, лицензию TinyUSB и оригинальный
  [отчёт](supporting/REPORT_RU.md), SHA-256
  `2cf810bcf0ddea5257c137f6b87678090556bf0f3889480d34cb9a4058d17f0f`.
  Тестовая семантика и отчёт при упаковке не менялись. Сохранены ранние неудачи,
  промежуточные успешные матрицы и исходники до tooling-only защиты output/hash.
- `supporting/historical-full-package-SHA256SUMS.txt` относится к полному исходному
  локальному пакету, включая исключённые binaries и сгенерированные fragments.
  Это историческое свидетельство; оно не является списком файлов этого checkout.
- `evidence-root-model-final/` и `root-model-final.log` — личный повтор автора после
  окончательной защиты runner. Предыдущий повтор сохранён отдельно. Во всех трёх
  профилях рассматривается модель извлечённых функций, не native whole-DCD runtime.
- `evidence-arm/` сохраняет команды, пять upstream conversion warnings на каждую
  сборку, symbols, sizes, disassembly, stack usage и hashes полных DCD ARM объектов.
  Исходный объект: `21065ac3d4295f783d35b08f2fd7389702ce68111c592a9ebb663378f9441f02`;
  изменённый: `c5f5f547e9cb1a46703b69fee6aac91875733639a39bbdb44773a8d0380453b3`.
  Патч добавляет undefined `memset`; +12 байт относится только к relocatable DCD
  объекту и исключает linked implementation/runtime/stack этой функции. Это не
  полный link, не оценка общего IRQ-стека, не исполнение ARM.
- `research/REPORT_RU.md` и `research/evidence/provenance.json` — оригинальный анализ,
  идентичности и официальные ссылки на первичные документы. ST PDF, сырые извлечения
  руководств и HTTP headers в этот пакет не включены.
- `source-selection.json` перечисляет выбранные и исключённые локальные файлы с
  размерами и SHA-256. Executables/objects, Python cache, повторные generated source
  и копии fixtures опущены. Полные оригинальные пакеты не удалялись. Нужные исходники
  и команды для новой генерации входят в пакет. Сохранённые абсолютные пути описывают
  исторические запуски; их существование в другом checkout не требуется.

## Независимое ревью

[Локальный отчёт Critic-A](critic-a-local/CRITIC_A_FSDEV_RESET_REVIEW.md) сохранён
побайтно: SHA-256 `c34b6028f9c755844cc7cf7b20365f563ec1e00d0b028372d984de0f0d7846a2`.
Его BLOCKED_EVIDENCE / score:null относится к локальному снимку до окончательной
публикационной привязки. `critic-a-local/review-manifest.json` также исторический.
Окончательная оценка требует проверки пакета/базы и immutable remote readback;
этот файл не выдаёт автору PASS или разрешение слияния.

После локального отчёта автор уточнил только абзац README о новом undefined `memset`
и границах +12 байт. Сохранён [точный diff](publication/README-memset-clarification.diff).
README в локальном отчёте: `1896b001c5e763e0d799f218a615547815a507b4919d05a5bd8de640510b0cde`;
текущий README: `37f957983c41dbfb6b882da896055c530b737cceae93c51f3bc59fe4889c8457`.
Патч, тестовая семантика, ARM runner и исходные свидетельства не изменены.
[Дополнение Critic-A](critic-a-local/README_BINDING_ADDENDUM.md), SHA-256
`f5ee1f024e09f1888bde4315307d8bf75d1475821ad69e174606216ec05fb7f2`,
отдельно проверяет эту документационную поправку; нового исполнения кода не заявляет.

Собственные команды и результаты критика сохранены в `critic-a-local/`. Supplemental
CTR test проверяет все 65 536 сочетаний retained RX/TX flags в восьми физических
slots с 3/8 logical endpoints в strict и ASan+UBSan. Он использует ту же reviewed
модель периферии, не является второй независимой моделью или доказательством IRQ.
[Дополнительный EP0 buffer-only trace](critic-a-local/EP0_BUFFER_TRACE_ADDENDUM.md)
проверяет тот же исходный ложный completion при обычных EP0 полях: ff=NULL,
physical index0, без ISO. Четыре strict/ASan+UBSan binaries воспроизводят
original-negative и candidate-clean сценарии RX/TX. Этот тест также использует
общую модель и не является полным реальным host/core сеансом.
Preprocessor controls доказывают ограниченность условного source delta, а не
работоспособность шести других MCU. Patch-tool test проверяет 11 вызовов инструмента.

## Полное повторение из checkout

Требуются Python 3, GCC с ASan/UBSan, GNU patch, sha256sum и уже установленный ARM
GCC 14.2.1 с `bin/arm-none-eabi-*` и `include/newlib`. Автоматической установки, сети,
CI и доступа к плате нет. Vendor/config берутся из неизменённой базы USB-001,
унаследованной через USB-003. Хеши 37 соответствующих файлов сверены с точным base tree.

```sh
# TOOLCHAIN — реальный абсолютный путь к уже установленному ARM toolchain.
# OUT — новый, ещё не существующий каталог вне checkout и toolchain.
python3 tests/usb-fsdev-reset/reproduce.py \
  --toolchain "$TOOLCHAIN" --output "$OUT"
```

Wrapper проверяет `package-manifest.json` (список пакета без самого manifest),
запускает исходные инструменты без изменения логики, сохраняет полные результаты
в OUT, затем повторно сверяет hashes входов. Для path-relative patch-tool test
он копирует тест и четыре fixtures побайтно в OUT. Остальные тесты получают явные
пути; исторические absolute paths не подменяются внутри исходников. Выход внутри
checkout/toolchain или существующий выход отклоняется до создания файлов.

Ожидаются 23 результата модели: 4 успешных запуска исправленного кандидата, 8
воспроизведений нежелательного поведения original/guard-disabled и 11 отвергнутых
мутаций. Exit 0 отрицательных профилей означает ожидаемое проявление дефекта.
Модель исполняет 26 извлечённых функций: 25 тел побайтно сохранены, в ISR ровно
шесть записей и три чтения ISTR заменены модельными access shims. EP0 observation
seam проверяет порядок и однократно вызывает исходную функцию. FIFO/PMA/event sink
тестовые; полной core/CDC enumeration нет. LeakSanitizer отключён (`detect_leaks=0`)
из-за ptrace, при сохранённых ASan+UBSan. Исходная LSan-неудача не скрыта.

Не проверены full firmware link, whole-DCD runtime на MCU, APB/USB синхронизация,
физическая отмена транзакций, IRQ interleavings/latency, board revision/clock, Windows
USB, USB/ADC интеграция, queued callbacks, поздний DCD admission и config0→config1.
Ни software-ready, ни low-voltage-verified, ни ignition-verified не заявляются.
