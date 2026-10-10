# Проверки ADC/TIM3/DMA backend

Проверяется только программная модель регистров и компиляция одного Cortex-M3
объекта. Модель не исполняет прошивку на STM32, не моделирует аналоговый вход,
арбитраж DMA, точную последовательность шинных циклов или USB.

## Host

Из корня проекта:

```sh
ASAN_OPTIONS=detect_leaks=0 tests/platform/run_tests.sh
```

`cc`, C11, `-O2`, `-Wall -Wextra -Werror -Wpedantic -Wconversion -Wshadow`,
явный `-fno-short-enums`; затем ASan/UBSan с `-O1`.
В доступном sandbox LeakSanitizer сам завершается с ошибкой о ptrace, поэтому
проверенный прогон отключает только leak detector. Heap backend не использует.
Это ограничение инструмента не является успешной проверкой LeakSanitizer.
`SANITIZE=0` отключает весь дополнительный sanitizer-прогон, что нужно явно
отмечать в отчёте. По умолчанию он включён.

Покрытие:

- точные clock divisors/ADC channel/sample time/TIM3 trigger/DMA flags;
- clock-ready/source-switch/APB/ADC ошибки и отсутствие подставленного HSE;
- 72 MHz/HSE, 48 MHz/HSI PLL и AHB/APB делители без обязательных 72 MHz;
- существующий активный владелец периферии, сохранение посторонних gate/GPIO bits;
- порядок ADC clock → UG → calibration → DMA → external trigger → TIM start;
- RSTCAL/CAL timeout, frozen microsecond clock, ограничение числа опросов;
- обе половины кольца, порядок/номер блоков, независимость snapshot от DMA;
- все CNDTR 0..257 отдельно для HT и TC, консервативная граница reload;
- обе/неправильные flags, TE, опоздание foreground, отсутствие DMA-прогресса;
- boundary/TE/временной скачок при копировании, неизменность output при отказе;
- Stop, повторный Stop/restart, stuck DMA disable и сохранение владения;
- runtime clock/gate failure, modulo-time wrap и предельный sample index.

## ARM объект и ABI

Использовать уже проверенный официальный toolchain из задачи интеграции.
Указать пути, не скачивать незафиксированный компилятор внутри test script:

```sh
BUILD_DIR=/tmp/mt-platform-build \
ASAN_OPTIONS=detect_leaks=0 \
ARM_CC=/path/to/arm-none-eabi-gcc \
ARM_NEWLIB_INCLUDE=/path/to/newlib/include \
tests/platform/run_tests.sh
arm-none-eabi-size /tmp/mt-platform-build/adc_timer_dma.o
arm-none-eabi-nm -S --size-sort /tmp/mt-platform-build/adc_timer_dma_abi.o
cat /tmp/mt-platform-build/adc_timer_dma.su
```

ARM flags: `-mcpu=cortex-m3 -mthumb -mfloat-abi=soft -Os -ffreestanding`,
section splitting, stack-usage и **-fno-short-enums**. Объект требует `memset`
для инициализации контекста. Linker/runtime обязаны предоставить его.

Результат 10.10.2026: strict host и ASan/UBSan прошли; GCC 14.2.1
(14.2.rel1) создал Cortex-M3 объект. Конечные символы/размеры относятся только
к хешам конкретного прогона; их следует повторить после любого изменения.
Проверка ABI отдельным объектом не исполняет код на ARM.

Полные RAM/Flash/stack цифры берутся из будущей интегрированной сборки, включая
контекст driver, acquisition, transport, USB, IRQ и callbacks. `.bss=0` у этого
объекта не означает нулевой RAM: контекст статически выделяет вызывающая сторона.
Сам по себе этот прогон не закрывает startup, native USB, LV-001 или аппаратные
критерии; независимый Critic-A ещё должен привязать вердикт к версии.
