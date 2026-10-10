# Минимальные исходники USB: происхождение и ARM-проверка

Дата: 10.10.2026. Результат: точный минимальный набор зависимостей и воспроизводимая компиляция шести ARM objects. Адаптер, boot/startup, полный link и аппаратные проверки в эту работу не входят.

## Состав и подлинность

В `vendor/` ровно 35 неизменённых upstream-файлов: шесть C-модулей, 26 заголовков и три корневые лицензии. Суммарно 1 521 937 байт; шесть C-модулей — 156 306 байт. Это объём текста, не размер прошивки.

- TinyUSB 0.21.0: `hathach/tinyusb`, commit `dae3f9a366bfcddbf9dcf1b48d7500286a849539`.
- ST CMSIS Device F1: `STMicroelectronics/cmsis-device-f1`, commit `c8e9a4a4f16b6d2cb2a2083cbe5161025280fb22`.
- ARM CMSIS Core: `ARM-software/CMSIS_5`, commit `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c`.

Все файлы получены через официальный GitHub connector с точным commit и base64-кодированием. Git blob SHA-1 вычислен из исходных байтов и совпал с ответом GitHub; дополнительно записан SHA-256. Точные ссылки, hashes и размеры — `source-manifest.json`; повторная проверка — `sha256sum -c SHA256SUMS.sources`. `SOURCES.list` перечисляет шесть C-модулей.

Заголовки проверены по фактическим GCC dependency-файлам `.d`: пропущенных и лишних заголовков нет. `mpu_armv7.h` прочитан при исследовании, но исключён: на F103xB с `__MPU_PRESENT=0` не требуется. Нет host-реализации, других классов, RTOS, HAL, BSP, startup или linker. `get_deps.py` и импортированный код не запускались; VID/PID не выбирались.

## Лицензии и авторство

- TinyUSB: `vendor/tinyusb/LICENSE`, MIT, с сохранением индивидуальных notices.
- ST: `vendor/cmsis-device-f1/License.md`, Apache-2.0.
- ARM: `vendor/cmsis-core/LICENSE.txt`, Apache-2.0.
- У `fsdev_stm32.h` сохранены особые три условия распространения: notices для исходников и бинарного распространения, запрет рекламного использования имени ST без разрешения. Заголовок MIT не заменяет точный текст этих условий.

Notices остаются в каждом исходнике; буквальная сводная копия — `THIRD_PARTY_FILE_NOTICES.txt`. Vendored-код принадлежит upstream. Локально написаны только TEST-ONLY конфигурация, проверочные скрипты и отчёт. Лицензия пользовательского проекта здесь не назначается.

## Тестовая конфигурация

`evidence/config/tusb_config.h`: STM32F103xB, full-speed/device, OPT_OS_NONE, один CDC, остальные классы выключены. RX/TX FIFO и packet size — 64, EP0 — 64, `CFG_TUD_ENDPPOINT_MAX=3`, interface max — 2, очередь — 16 событий, task — одно событие на вызов. Overwrite при DTR=0 и обе persistence-настройки равны нулю. Дескрипторов и VID/PID нет. Эффективные макросы сохранены в `evidence/effective-macros.log`.

Это конфигурация object-проверки, не production default. Стандартное ASSERT-поведение upstream не доказывает fail-closed обработку переполнения очереди; это обязательство будущего адаптера/интеграции.

## Свежие ARM-результаты

Успешный прогон: `evidence/arm-objects-gnu11-restored/`; независимая повторная компиляция: `evidence/arm-objects-gnu11-restored-repeat/`. Все шесть object SHA-256 побитно совпали: `evidence/reproduction.json`.

| Модуль | text + rodata | data | bss |
|---|---:|---:|---:|
| tusb | 1064 | 0 | 8 |
| tusb_fifo | 1482 | 0 | 0 |
| usbd | 4056 | 29 | 310 |
| cdc_device | 1174 | 0 | 188 |
| dcd_stm32_fsdev | 2790 | 0 | 131 |
| fsdev_common | 230 | 0 | 0 |
| Всего, байт | 10796 | 29 | 637 |

Суммы относятся к секциям отдельных ELF REL objects до link/garbage collection и выравнивания. Полная Flash/RAM потребность прошивки не определена. Наибольшие static-storage symbols: очередь 192 байта, CDC interface 188, DCD transfer state 96, EP0 buffer 64. Полный список — `evidence/static-storage-symbols.json`.

Наибольшие отдельные stack frames: `dcd_int_handler` и `tud_task_ext` по 72 байта; FIFO write — 64. `evidence/stack-frames.json` содержит все `.su` записи. Это не максимальный стек цепочки вызовов, callback или IRQ.

Статическая разность множеств global/undefined symbols оставила ровно шесть внешних имён: `memcpy`, `memset`, `tud_descriptor_configuration_cb`, `tud_descriptor_device_cb`, `tud_descriptor_string_cb`, `tusb_time_millis_api`. Дублирующих сильных определений не обнаружено. Это symbol-аудит, не link-проверка; см. `evidence/external-symbols.json`.

Сохранены семь `-Wconversion` diagnostics: пять при компиляции DCD, два при компиляции fsdev_common; они относятся к маскам и 16-битным значениям. Предупреждения не подавлены и исходники не исправлялись; `-Werror` не задавался. Отдельный отрицательный прогон `arm-objects-c11-restored-probe` сохранил ошибку strict `-std=c11`: bare `asm("NOP")` в `fsdev_common.c:43`. GNU11 компилирует этот upstream-код без изменений.

## Воспроизведение и toolchain

Работая из корня пакета, задайте новое имя каталога результата:

```sh
python3 evidence/compile_objects.py arm-check-new
python3 evidence/compile_objects.py arm-check-repeat
```

Runner не перезаписывает прошлые прогоны. Он сохраняет полные argv/stdout/stderr/exit codes в `commands.json` и `commands.log`, а также `.d`, `.su`, size, nm, readelf. Используется GCC 14.2.1, `-std=gnu11 -Os -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -ffreestanding -ffunction-sections -fdata-sections -fno-common -fno-short-enums -fstack-usage -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -DSTM32F103xB`.

В 14:18:27 UTC обнаружен сброс рабочих каталогов. Исходники восстановлены и hashes проверены заново. Ранее наблюдавшиеся числа оставлены отдельно в `evidence/pre-reset-observations.json`; они не подменяют свежие логи.

После отдельного разрешения восстановлен тот же toolchain в `/workspace/shared/arm-resource-evidence/toolchain/root/usr`: три официальных Debian-пакета только распакованы локально, без системной установки и запуска maintainer scripts. Их SHA-256 совпали с официальными страницами Debian и [опубликованным provenance PR39](https://github.com/font-weight/stm32_motortester/blob/aad61d598aa9996b1cd2c0587e936e19118fc087/tests/adc-control/evidence/arm/toolchain-provenance.md). Восемь исполняемых файлов дополнительно совпали с pinned binary hashes PR39. Записи восстановления, checksums и официальные страницы — `evidence/toolchain-recovery/`. Новые прогоны начались только после этих проверок.

Полного ELF/BIN/HEX, проверки USB enumeration/reset/replug, аппаратного времени ISR, питания, pull-up, HSE или конкретной платы здесь нет. USB-адаптер и интеграцию пишет основной автор следующего этапа.
