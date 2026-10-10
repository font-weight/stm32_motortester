# USB005: ARM object verification

Дата: 10.10.2026. Автор production-кода: Dot A. Этот пакет подготовлен вспомогательным проверяющим ресурсов, не Critic-A. Оценка приёмки не выставлялась. Изменения производственных файлов не выполнялись; все записи находятся внутри данного каталога.

## Результат

Все четыре текущие единицы USB005 успешно скомпилированы в ARM relocatable objects. Дополнительно собраны три opt-out и три исходных baseline объекта. Все четыре отдельных прогона дали те же SHA-256 всех десяти объектов. Ошибок компиляции не было. Текущий core и DCD с CFG_MT_USB_BRIDGE=0 побитно совпадают с baseline как после preprocessing, так и в объектном коде. Adapter opt-out намеренно отличается снятием запрета same-epoch hard_loss: это единственная строковая разница препроцессора, text меньше на 16 байт.

Основной полный прогон: `run-04-path-isolation-fixed/`; первоначальные результаты сохранены в `run-01/` и `run-02/`. Во втором прогоне к команде добавлено сохранение зависимостей `-MD -MF`, чтобы включить реально прочитанные системные заголовки в отдельный SHA-manifest. После второго прогона автор дополнил только комментарий cleaned/Stop в mt_usb_bridge.h; третий прогон привязан к этому актуальному заголовку. Все десять объектов остались побитно одинаковыми. Исходные входы не менялись во время каждого прогона.

## Конфигурация и происхождение

Компилятор: `arm-none-eabi-gcc (15:14.2.rel1-1) 14.2.1 20241119`. Cortex-M3, Thumb-2, soft float, GNU11, -Os, freestanding, function/data sections, no-common, int-size enums. Предупреждения: Wall, Wextra, Wpedantic, Wconversion, Wsign-conversion. Werror не применён: пять сохранённых предупреждений исходного FSDEV приведены отдельно ниже.

Закреплённый test-only tusb_config.h: STM32F103xB, OPT_MCU_STM32F1, OS_NONE, USB FS, один CDC, один event per task, очередь16, EP0 64, CDC RX/TX64, неперсистентные FIFO, запрет overwritable TX. Macro dump подтверждает CFG_TUD_EDPT_DEDICATED_HWFIFO=1. Это конфигурация объектной проверки, не рабочая конфигурация платы.

- USB005 snapshot: mt_usb_bridge.c/h, mt_usb_cdc.c/h, текущие generated usbd.c и dcd_stm32_fsdev.c.
- Baseline core: usb-setup-draft/build/usbd.c, USB003.
- Baseline DCD: usb-fsdev-reset-draft/build/dcd_stm32_fsdev.c, USB004.
- Baseline adapter: usb-adapter-draft/firmware/transport/mt_usb_cdc.c.
- Заголовки epoch: usb-epoch-draft; остальные firmware headers: usb-adapter-draft.
- Pinned vendor files: usb-dependencies. Их SHA256SUMS.sources проверен до копирования.

SHA-256 проверенных текущих production sources:

- bridge-on: `7cbbc13e481aa398b28f713f2b3067f27e622c99cd8c1eed833237b04cf59d7e`
- core-on: `f500d3477ef628c15e8ebb117570da64d34d4b6d638cfa47886422b9a04091fe`
- dcd-on: `d46344c2dea3c7eceaa4a1042bd179ef3eda0e835efdf3ae3763f18db44961d8`
- adapter-on: `d262e01072dca405be71af32db4263373c100eb9e618959f1be25e1b7ca3cf9f`

Каждый источник/заголовок скопирован без правок в `run-04-path-isolation-fixed/snapshot/`. `input-manifest.json` связывает original path, snapshot path и SHA. `compilation-inputs-manifest.json` включает реально использованные headers, в том числе GCC/newlib. `toolchain-manifest.json` включает GCC, cc1, assembler и binutils. ELF headers/attributes подтверждают ARM ELF32 REL, little endian, EABI5, v7-M, Thumb-2; это не исполняемый firmware ELF.

Актуальный mt_usb_bridge.h SHA-256: `5894b781971f5e5b3179f612d909f4299905fb997132008aa33225e9f2e35866`. Финальный повторный read-only контроль исходных путей сохранён в `final-source-recheck.json`.

## Размеры и локальные stack frames

Значения GNU size, байты. Text включает соответствующие read-only секции. Stack — максимум одного локального кадра по GCC .su; не максимальная глубина вызовов и не запас под прерывания.

| Объект | text | data | bss | Max local frame | Warning count |
|---|---:|---:|---:|---:|---:|
| bridge-on | 1294 | 0 | 72 | 40 | 0 |
| core-on | 4216 | 29 | 391 | 96 | 0 |
| dcd-on | 2808 | 0 | 131 | 72 | 5 |
| adapter-on | 778 | 0 | 0 | 40 | 0 |
| core-off | 4088 | 29 | 310 | 72 | 0 |
| dcd-off | 2802 | 0 | 131 | 72 | 5 |
| adapter-off | 588 | 0 | 0 | 40 | 0 |
| core-base | 4088 | 29 | 310 | 72 | 0 |
| dcd-base | 2802 | 0 | 131 | 72 | 5 |
| adapter-base | 604 | 0 | 0 | 40 | 0 |

Сумма только четырёх enabled объектов: text9096, data29, bss594. По сравнению с текущими тремя opt-out объектами при исключённом bridge.o: +1618 text, +153 bss, data без изменения. По сравнению с тремя прежними baseline объектами: +1602 text, +153 bss. Это сумма секций отдельных объектов, не итоговый расход Flash/RAM после linker GC/alignment и не размер всей прошивки.

Из дополнительных153байт bss: bridge72; core81 (64байта на token в16 queue elements,16байт held envelope,1байт held flag). Конечное размещение добавит возможное выравнивание. Самое большое локальное увеличение: tud_task_ext с72 до96байт. Локальный максимум bridge40, DCD72, adapter40. Транзитивные вызовы, ISR nesting, функция lock и owner pump не оценены.

## Девять фактических shim callsites

Preprocessed core содержит вызовы всех девяти shim; ARM relocations подтверждают их присутствие после оптимизации. В таблице число relocations не обязано совпадать с числом строк исходника: GCC объединяет/встраивает пути.

| Core shim | Raw DCD target inside bridge | Core call/jump relocations |
|---|---|---:|
| mt_usb_bridge_xfer | dcd_edpt_xfer | 1 |
| mt_usb_bridge_fifo | dcd_edpt_xfer_fifo | 1 |
| mt_usb_bridge_open | dcd_edpt_open | 1 |
| mt_usb_bridge_stall | dcd_edpt_stall | 3 |
| mt_usb_bridge_clear_stall | dcd_edpt_clear_stall | 1 |
| mt_usb_bridge_address | dcd_set_address | 1 |
| mt_usb_bridge_status | dcd_edpt0_status_complete | 1 |
| mt_usb_bridge_sof | dcd_sof_enable | 2 |
| mt_usb_bridge_close_all | dcd_edpt_close_all | 1 |

У core-on.o нет call/jump relocation непосредственно в эти девять raw DCD targets. Weak definitions dcd_edpt_xfer_fifo и dcd_edpt0_status_complete сохранены в core, однако их действительные callsites ссылаются на shim, а не на weak raw symbol. Поэтому доказательство не опирается на linker --wrap.

В каждой из девяти функций bridge объект содержит mt_usb_bridge_lock, permitted, соответствующий raw DCD call и mt_usb_bridge_unlock. Исходный код и ручной просмотр соответствующих ветвлений показывают, что raw call находится между входом в guard и выходом; в transfer path общий unlock расположен раньше вызова в линейном disassembly, но возврат к нему выполняется веткой. Автоматический аудит проверяет присутствие relocations, не выдаёт их линейный порядок за доказательство CFG.

Непокрытые данной девяткой core-вызовы остаются: dcd_int_enable/disable, dcd_remote_wakeup, dcd_connect/disconnect, dcd_configure, dcd_init/deinit, dcd_edpt_iso_alloc/activate. Здесь не доказана защита произвольного публичного DCD API, пользовательского класса, remote-wakeup/lifecycle вызова или другого конфигурационного режима. Набор CDC/F1 и ограничения owner должны оставаться явными.

Adapter-on.o содержит app_enter/leave для public open_epoch, write и step. Передаваемый из step_impl callback — write_impl; он работает внутри scope внешнего public step, а не получает отдельный scope автоматически. Не проверена корректность сторонних вызовов impl symbols или прямого использования TinyUSB вне owner scope.

## Скрытая передача SET_ADDRESS

В DCD dcd_set_address object есть raw dcd_edpt_xfer relocation, затем mt_usb_bridge_address_result relocation. Bridge address object сбрасывает pending/seen/accepted, выполняет dcd_set_address, проверяет receipt и возвращается через unlock. Missing receipt даёт false через result/fault path на уровне исходника и объекта.

Важная граница: pinned F1 edpt_xfer заканчивается unconditional return true. Поэтому GCC после dcd_edpt_xfer выставляет r0=1 перед tail-call адресного receipt hook. Это корректно для текущего исходника и подтверждает подсоединение hook; объект не демонстрирует способность этого DCD сообщать физический отказ submission. Rejection/missing-receipt сценарии могут проверять программную обвязку fake DCD, но не подтверждают аппаратную доставку, ACK хоста, отсутствие старого TX или физическую отмену передачи.

## Предупреждения и незакрытые проверки

bridge/core/adapter compiled без предупреждений. DCD во всех трёх вариантах выдаёт одни и те же пять Wconversion сообщений: btable_set_count в fsdev_common.h; маски CNTR_SOFM, LPMODE, FSUSP, RESUME в DCD. Полные stderr сохранены. Пути/номера строк отличаются из-за вставки patch; тексты сообщений совпали. Новых предупреждений USB005 не выявлено. Эти предупреждения не исправлялись и не подавлялись.

Не выполнялись линковка полного ELF, ARM execution, QEMU, плата, USB enumeration, PRIMASK/vector/priority/PRIGROUP, физические reset/ACK/cancellation, ADC/TIM contention/latency, Windows-связность. Lock/unlock остаются передаваемыми function pointers; наличие wrapper не доказывает корректность реальной interrupt-mask implementation. Epoch implementation, TinyUSB CDC/fifo runtime, owner pump, startup, descriptors, linker script и остальная firmware не включены в приведённые суммы четырёх объектов. Это свидетельство сборки/структуры объектов, не готовность прибора и не PASS независимого критика.

## Воспроизведение

Из любой директории, при доступных указанных input trees и toolchain:

```
python3 /workspace/shared/usb-bridge-arm-evidence/verify_arm.py --output /workspace/shared/usb-bridge-arm-evidence/run-new
python3 /workspace/shared/usb-bridge-arm-evidence/audit_objects.py /workspace/shared/usb-bridge-arm-evidence/run-new --compare-run /workspace/shared/usb-bridge-arm-evidence/run-04-path-isolation-fixed
```

Output directory должен отсутствовать; runner никогда не перезаписывает прежний run. Он сначала проверяет pinned SHA256SUMS.sources, сохраняет snapshot и manifest, затем выполняет точные команды. Path preflight отклоняет ошибочные входы/выход без создания output. После успешного preflight ошибки команд сохраняют уже записанные argv/stderr и создают failure.txt. В выполненных четырёх ARM run ошибок нет.

Точные argv/stdout/stderr/exit codes: `commands.json`; shell equivalent: `commands.sh`. Для каждого объекта: `.o`, `.i`, `.d`, `.su`, macro dump, size/nm/undefined/objdump/readelf. `summary.json` содержит source/object hashes, размеры, каждый stack frame и opt-out comparison. `audit.json`/`selected-relocations.txt` содержат проверяемую матрицу вызовов, список прочих raw DCD relocations и равенство предупреждений. `*-optout.diff` сохраняет все отличия препроцессора.

Для воспроизведения ровно сохранённого snapshot (если author trees уже изменены) использовать argv из run-04-path-isolation-fixed/commands.json или commands.sh: они адресуют сохранённые копии; прежде копировать run-04-path-isolation-fixed в новый каталог и заменить его абсолютный префикс в командах, чтобы не перезаписывать доказательства. Compiler/toolchain должен совпасть по manifest.

## Исправление изоляции output после замечания Critic-A

В прежнем runner mkdir выполнялся до проверки разделения путей. Это создавало риск записи внутрь input tree и рекурсивного снимка самого output. Замечание признано обоснованным; производственные исходники не менялись.

Исправленный `verify_arm.py` SHA-256: `e04248ce32792c6be88280cfdbe9ea463870edaba86897675a1da6e18b6c7f95`. Перед mkdir он разрешает реальные пути, отвергает существующий output (файл, каталог, symlink, в том числе dangling), проверяет существование input roots/copied directories/explicit files и запрещает пересечение output со всеми supplied roots, canonical copied subtrees и родителями canonical singleton files. Отдельно учитываются symlink входного root, symlink firmware/vendor subtree вне root и alias одиночного источника. `--check-paths-only` выполняет тот же read-only preflight без mkdir или запуска инструментов. Входы должны оставаться неизменными; защита не заявлена как граница безопасности против конкурентной злонамеренной подмены symlink.

`test_path_isolation.py` выполняет 61 ограниченный CLI-сценарий только в одноразовых фиктивных деревьях внутри evidence. Каждый имеет timeout 5 секунд. Проверены все input roots/copied trees, существующие/dangling output, output-parent/input-root/subtree/singleton symlink aliases, dot-dot, symlink loop, отсутствующие root/tree/каждый explicit singleton, а также корректные отдельные пути. Все 61 прошли; дерево fixture после каждого побитно/структурно неизменно. Rejection-сценарии вызывают обычный entrypoint без check-only. Полные команды/exit codes/stdout/stderr и сохранённые fixtures: `path-isolation-run-01/`; итог `summary.json`.

ARM `run-04-path-isolation-fixed/` заново собрал 10 объектов с исправленным runner. Все object hashes совпали с run-03; opt-out, 9 shim, receipt hook, предупреждения, размеры и .su результаты не изменились; original_inputs_changed_during_run пуст. Старые версии runner, report и первоначального SHA manifest сохранены под `versions/`; прежние run-01/02/03 не перезаписывались. Точный tooling delta: `path-isolation-fix.diff`.

Воспроизведение path tests: `python3 test_path_isolation.py --runner verify_arm.py --output path-isolation-new` из этого каталога. Новая директория должна отсутствовать.
