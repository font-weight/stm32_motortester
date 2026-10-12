# USB-001: воспроизведение опубликованного черновика

База: PR #44, `06178f31be79c3e7dbc5b46281a67a5c1d1da7f0`.
Этот пакет добавляет только foreground-адаптер, тестовые исходники, неизменённый
минимальный набор зависимостей и текстовые доказательства. Существующие файлы
базы и `host/**` не изменяются. Это не полноценная прошивка и не приёмка.

## Одна воспроизводимая команда

Из корня checkout, Python 3.9+ и GCC для native-проверок:

```sh
python3 tests/usb-cdc/verify-package.py
python3 tests/usb-cdc/reproduce.py --out /tmp/usb001-native-new
```

Вторая команда заново запускает восемь вариантов реального TinyUSB core/FIFO/CDC
с fake DCD: core smoke и адаптер, buffered и dedicated-HWFIFO, обычный и ASan/UBSan.
Затем выполняет отдельные call-order тесты Critic-A с явными stub-границами.
LSan выключен; это не проверка утечек. Выходной каталог должен быть новым и
находиться вне checkout; старые свидетельства не перезаписываются.

Для дополнительных ARM-object проверок нужен документированный GCC 14.2.1,
binutils 2.44 и newlib headers. `TC` указывает каталог `usr`, содержащий
`bin/arm-none-eabi-gcc` и `include/newlib`. Скрипт ничего не загружает и не
устанавливает. Происхождение и hashes пакетов сохранены в
`firmware/third_party/usb/evidence/toolchain-recovery/`.

```sh
TC=/absolute/toolchain/usr python3 tests/usb-cdc/reproduce.py \
  --arm --out /tmp/usb001-native-and-arm-new
```

ARM-этап включает авторский strict-C11 адаптер, валидную конфигурацию и пять
отрицательных конфигураций Critic-A, шесть неизменённых upstream-модулей GNU11,
проверку их object SHA-256 и чисел секций, а также ожидаемый отказ strict-C11
для `fsdev_common.c`. Семь upstream `-Wconversion` предупреждений сохраняются.
Другой toolchain может изменить object hashes; такой результат требует
отдельного анализа и не выдаётся за совпадение с зафиксированной сборкой.

## Какие файлы что доказывают

- `package-manifest.json` и `verify-package.py`: точные байты новых файлов,
  32 унаследованных firmware-входов и 35 upstream-файлов. Manifest не хеширует
  сам себя; его собственный hash фиксируется при публикации и ревью.
- `supporting/`: неизменённые тесты, конфигурации, логи и receipts supporting
  исполнителя. `supporting/evidence/matrix.json` содержит восемь успешных запусков.
- `critic/`: независимые call-order/negative-configuration тесты и исходные
  логи Critic-A. Исходные standalone runner'ы ожидают локальный `snapshot/`;
  `reproduce.py` создаёт его из проверенных файлов checkout только в `--out`.
  Опубликованной второй копии production-кода нет.
- `critic/CRITIC_A_USB_CDC_REVIEW.md`: неизменённый независимый отчёт с
  `BLOCKED_EVIDENCE`, score null. Его входные hashes и `REVIEW_SHA256SUMS`
  проверяет общий verifier. Привязка к окончательному Git SHA выполняется
  отдельно после readback, без переписывания локального отчёта.
- `evidence/root-f1-sanitized.log`: отдельный повтор полного F1-HWFIFO sanitizer
  адаптера основным автором. Абсолютные пути в исходных логах исторические;
  новые пути задаёт воспроизводящая команда выше.
- `firmware/third_party/usb/PACKAGING.md`: границы сокращённого архива исходных
  dependency-доказательств и замена старых команд воспроизведения.

Исходный supporting bundle содержал 12 двоичных захватов байтов по 11 520 байт.
В этом текстовом пакете каждый представлен как `.bin.hex`, а не как исходный
raw-файл. `supporting/text-encoding.json` сохраняет исходные имя, размер и SHA-256.
`verify-package.py` декодирует hex в памяти и сверяет каждый исходный hash,
неизменённый `SHA256SUMS.harness` и `matrix.json`. Исходные README/логи supporting
сохранены буквально, поэтому упомянутые там `.bin` следует читать с этой оговоркой.
Новые тесты создают настоящие raw-захваты лишь в отдельном выходном каталоге.

Компиляторы, object/executable/firmware binaries и сырые binary-захваты в Git
не включены. Тестовые descriptors с нулевыми VID/PID непригодны для устройства.

## Что остаётся заблокировано

Production reset-latch до event queue, overflow containment, барьер старых
endpoint/completion событий, time-health integration и аппаратные испытания
не реализованы и не подтверждены этим пакетом. Нет startup, полного link/map,
физического USB/Windows 10, доказанного срока ADC <2560 мкс или прошивки платы.
Вызов foreground `hard_loss` в тесте не доказывает автоматический ISR путь.
Общая recovery-политика требует также Critic-B. Draft не означает PASS, merge
или готовность уровня MVP.
