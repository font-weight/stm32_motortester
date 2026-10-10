# Точная карта guard-интеграции TinyUSB / STM32F103

10.10.2026. Репозиторий `font-weight/stm32_motortester`. Это исследование для следующей авторской реализации Dot A, **не production patch, не независимый PASS и не закрытие USB lifecycle**.

## 1. Решение в одном абзаце

Нужны явные вызовы guarded shim из `usbd.c`, контекст исходной операции, queue/dispatch hooks и **одна узкая точка результата внутри FSDEV `dcd_set_address`**. Оборачивания только xfer/FIFO либо только очереди недостаточно. Обычный внешний wrapper вокруг void `dcd_set_address` может защитить действие от IRQ, но не узнает результат внутреннего EP0 submission. `--wrap` дополнительно пропускает same-TU вызовы FIFO и EP0 status-complete. Предлагаемый минимальный вариант сохраняет public DCD ABI: реальные DCD-функции вызываются из явных shim; внутри `dcd_set_address` результат единственного внутреннего xfer передаётся metadata hook до возврата. Не нужен patch CDC для hard-reset admission, если все его пути действительно идут через эти shim и сохраняют исходный actor token.

## 2. Проверенная база и ограничения конфигурации

Все номера строк ниже относятся к **исходному** TinyUSB `dae3f9a366bfcddbf9dcf1b48d7500286a849539`, а не к уже сдвинутым USB003/USB004 файлам.

- U = [`src/device/usbd.c`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c)
- C = [`src/class/cdc/cdc_device.c`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/class/cdc/cdc_device.c)
- T = [`src/tusb.c`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/tusb.c)
- D = [`src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c)
- P = [`src/portable/st/stm32_fsdev/fsdev_stm32.h`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/fsdev_stm32.h)

Прочитаны оба исходных отчёта, `mt_usb_epoch.h/.c`, текущий `mt_usb_cdc.c` и патчи USB003/USB004. Adapter SHA совпадает с publication candidate: `4127541694a00521f0eddce314bbce8383da892b2a5adbb0e5bace6576d0193f`. Все 35 исходных dependency hashes проверены; журнал `evidence/source-verification.log`.

Фактически предоставленный F1 config: `STM32F103xB`, rhport0/device/full-speed, OS_NONE, один CDC, очередь16, один event/run; EP0 и CDC data MPS/buffers64; logical endpoint count3; persistence/overwrite/RX_NEED_ZLP=0. Препроцессор подтвердил HWFIFO=1, CDC_NOTIFY=0, TEST_MODE=0, DCACHE=0, `TUP_DCD_EDPT_ISO_ALLOC` определён. Полный вывод сохранён в `evidence/f1/effective_macros.txt`.

**Важная граница:** предоставленный config прямо помечен test-only; production descriptors/startup/vector table пока отсутствуют. Для динамических probes использован реальный supporting descriptor: notification IN0x81/MPS8, bulk OUT0x02 и IN0x82/MPS64. Это не заявление, что product VID/PID/descriptor уже утверждён. Все остальные классы выключены, app drivers/ISR callbacks должны быть запрещены и проверены интегратором, а не подразумеваться словом «CDC».

## 3. Все обязательные foreground core→DCD действия

Это именно достижимые действия выбранного стека, в том числе из callback. Для каждого guard должен сохранять PRIMASK, проверить **actor E**, исполнить целую DCD-функцию и обработать её результат до восстановления PRIMASK. Проверка непосредственно перед последним EPnR store недостаточна.

| DCD действие | Точный путь/место U | Что делает FSDEV; размер guard |
|---|---|---|
| `dcd_edpt_xfer` | U848/865 → `usbd_edpt_xfer` U1570–1601, вызов1593. Все EP0 data/status, GET_DESCRIPTOR, class control, continuation/status после EP0 DATA. Также buffered CDC через T360–371 | D797–809 → D752–795; ownership buffer/ff/length, PMA/BTABLE и EPnR. Guard начинается **до** D803, заканчивается после результата |
| `dcd_edpt_xfer_fifo` | C364/512/520/522 и C194–209/212–231 → T360–375 → U1608–1641, вызов1624. На F103 это основной bulk IN/OUT и ZLP путь | D811–823 → D752–795. IN копирует/извлекает FIFO в PMA D735–749; нельзя защищать лишь EP write |
| `dcd_edpt_open` | `process_set_config` U1251–1299 → C295–372: notification C321, оба bulk C340 → U1542–1548 | D581–628: allocator, PMA allocation/BTABLE, xfer state, EP write. Даже open notification нужен при CDC_NOTIFY=0: выключен API передачи notification, а не открытие endpoint из descriptor |
| `dcd_edpt_stall` | **Прямые** U752/753 при неудачном SETUP и U944/945 при отказе DATA-stage callback; дополнительно U1661 из endpoint SET_FEATURE U1210 | D825–845: чтение xfer/EP, STAT_STALL, EP write. Нужно закрыть все пять call sites; guard только в `usbd_edpt_stall` пропустит четыре прямых EP0 вызова |
| `dcd_edpt_clear_stall` | Endpoint CLEAR_FEATURE U1208 → U1665–1675, вызов1673 | D847–863: STAT_NAK/DATA0/EP write |
| `dcd_set_address` | Device SET_ADDRESS U969–975, вызов973 | D224–232 вызывает **внутренний** `dcd_edpt_xfer(...EP0 IN,NULL,0,false)` D228, отбрасывает bool. Guard всего внешнего действия + отдельное наблюдение внутреннего результата; `_usbd_dev.addressed=1` не receipt успеха |
| `dcd_edpt0_status_complete` | EP0 completion U897–913, вызов907, до app/class ACK callback | D477–485 реально меняет DADDR для SET_ADDRESS. Нельзя считать «notification-only callback», оставлять без guard или вызывать после failed completion |
| `dcd_sof_enable(false)` | SET_CONFIGURATION смена ненулевой конфигурации U992; даже если user SOF запрещён | D213–221 меняет CNTR.SOFM. Этот обязательный call site не исчезает из-за отсутствия SOF callback |
| `dcd_edpt_close_all` | Тот же teardown U993 | D630–647: отключает IRQ, обходит physical slots1…7, allocator, включает IRQ, **затем** меняет ep_buf_ptr. Guard до входа и после строки646; внутренний re-enable не завершает внешнюю critical section |

### Развёртка всех CDC entry points

- Adapter `write` (стр.40–46) → `tud_cdc_n_write` C216–220 → T425–435, auto-flush T401–423 → FIFO xfer. Positive count означает скопированный в FIFO prefix.
- Adapter `step` (48–71) → `tud_cdc_n_read` C194–198 → T468–471: read **тоже** может rearm OUT. Его explicit flush в70 → C222–226 → write_xfer.
- Adapter `open_epoch` (25–38), когда `rx_flush_pending`: C205–209 clear RX + rearm OUT. Поэтому open_epoch тоже требует operation scope.
- Adapter `hard_loss` (85–98) → C228–231 `write_clear`: только software FIFO clear, DCD не вызывается. Не подменять его read_flush.
- CDC completion OUT C471–512: copy/notify callbacks → OUT rearm. TX C517–522: пользовательский callback → next payload либо ZLP. Вложенный пользовательский `read/write` внутри этих callbacks наследует тот же actor E.
- `cdcd_reset` C282–292 вызывает только software stream close; `tu_edpt_stream_close` в `common/tusb_private.h:127–129` меняет ep_addr=0, не вызывает DCD close. Core BUS_RESET U719 не должен требовать normal core_io для этого software reset.

## 4. Остальные API: lifecycle, producer, запрещённый runtime

Следующая таблица закрывает остальной DCD API; не следует незаметно провести его через enumeration permit.

| API / call sites | Фактическая достижимость и правило |
|---|---|
| `dcd_init` U598; `dcd_int_enable` U599 | Отдельная owner INIT phase, ещё до физического BUS_RESET и processed receipt. D181–202 выполняет FRES/core init, handle_bus_reset, connect. Это **не** событие физического BUS_RESET и не основание `mt_usb_epoch_reset()`/app-open |
| `dcd_int_disable`, `dcd_disconnect`, `dcd_deinit` U614–616 | Отдельная owner DEINIT phase, только вне активных task/adapter scopes. Default weak `dcd_deinit` U87 не доказательство успешного teardown; выбранный FSDEV имеет strong D205–211. Guard не должен заблокировать остановку из-за уже закрытой epoch |
| `dcd_int_enable/disable` U1492/1494 | `usbd_int_set` используется queue и OS_NONE spinlock: U416/419, osal_none.h:77–95,205–225. Это служебная синхронизация, а не submission. Не проверять epoch перед восстановлением IRQ, иначе stale queue path может навсегда выключить USB |
| `dcd_int_handler` T155 | IRQ producer entry из `tusb_int_handler`, не foreground actor. Оба HP/LP должны быть невложенными USB entry с одинаковым preemption priority. Внутри D392–469 выполняются reset, setup, RX/TX/PMA, resume/suspend, ESOF. Не оборачивать весь ISR/CTR loop в foreground PRIMASK guard |
| `dcd_connect/disconnect` U522/517 | Public `tud_connect/disconnect` вызываемы, но adapter их не использует. Для первого bridge запретить в обычном runtime; отдельная разрешённая lifecycle phase если появятся. На F103xB без board override это weak no-op U92–98: FSDEV strong включается только D873–881 при других pull-up macros. Нельзя обещать физический disconnect |
| `dcd_remote_wakeup` U512 | Только explicit `tud_remote_wakeup`, условий host request недостаточно для автоматического вызова. Adapter не использует. Пока запретить runtime; иначе отдельный guarded wake permit, потому что normal core_io закрыт при pending_soft. D234–239 меняет CNTR и countdown |
| `dcd_configure` U535 | Explicit public configure, нет caller из CDC/adapter. На этом FSDEV weak U467–470 возвращает false. Запретить runtime; не считать этот ожидаемый «unsupported» ответ обычной аварией submission |
| `dcd_sof_enable` U1722 | Explicit `tud_sof_cb_enable` U526–527 → `usbd_sof_enable`, либо другие классы. User SOF для первой интеграции запретить; обязательный teardown U992 всё равно guard-ить |
| `dcd_edpt_close` U1702 | **Не скомпилирован в F103 ветке**: при `TUP_DCD_EDPT_ISO_ALLOC` `usbd_edpt_close` U1691–1693 — no-op; FSDEV вообще не определяет этот API. Не добавлять фиктивный обязательный link-wrapper для отсутствующего пути |
| `dcd_edpt_iso_alloc/activate` U1731/1749 | Скомпилированы и видны как unresolved symbols core object; FSDEV предоставляет D649/678. Но ни single CDC, ни adapter их не вызывают. Explicit deny в owner contract/link-callsite audit; не объявлять выключенными препроцессором. Использование = расширение scope и новый guard/тест |
| `dcd_enter_test_mode` U449 | Исключён TEST_MODE=0, не нужен runtime hook при проверенном config |
| `dcd_dcache_*` U100–113 | Weak definitions существуют, достижимых вызовов в выбранном F103 core/CDC/FSDEV нет, DCACHE=0 |

Без проверки запрета произвольных application TinyUSB вызовов это не замкнутая карта: `usbd_app_driver_get_cb`, vendor callback, user SOF/deferred functions могут вызвать почти любой public API. Для первого bridge: app_driver_count=0, CDC `.xfer_isr=NULL`, `.sof=NULL` (U173–183), запрет raw DCD calls из приложения/других ISR, запрет recursive task/deinit и не-USB deferred work. Разрешённые callback API наследуют scope, не создают новый current token.

## 5. Минимальные source hooks, которые действительно закрывают карту

Имена ниже предлагаемые; это обязанности API, не готовая реализация.

### H1. Envelope + producer/queue result (U419,430–434,1391–1484,1527–1535)

- Private envelope `{event,E}`; E берётся на ingress до core mutations. BUS_RESET публикует новую epoch прежде enqueue. Producer E — локальная переменная, **не** перезапись foreground actor.
- Единый enqueue получает исходный E, проверяет его/публикует полный envelope и `enqueued(E,accepted)` в одной короткой metadata/queue critical section. Синтетические RESUME/SOF используют исходный E.
- USB003 rollback SETUP нельзя механически оставить вместе с новой accepted-only accounting: increment только после успешного enqueue; decrement при каждом dequeue SETUP, включая stale drop, ровно один раз. Удалить старые increment/decrement/rollback по совместному patch, не допустить двойного decrement.
- Current failed completion (`result != XFER_RESULT_SUCCESS`) фиксируется **здесь**, до возможности следующего app I/O; metadata-only `fail(E,COMPLETION_FAILED)`. Не ждать, пока event доберётся до task. Повторно не увеличивать io_failures при consumer drop. Если enqueue тоже потерян, обе категории диагностик честны.
- Deferred USB work должно наследовать активный actor E; код после reset не захватывает новый current в `usbd_defer_func`. Отсутствие разрешённого scope — отказ/диагностика, а не auto-current.

### H2. Dispatch scope и обязательная очистка (U704–818)

После pop/accounting фильтр стоит до connected/busy/driver lookup/control parser. Решение должно различать DROP_STALE, DROP_FAULT, DEFER_SOFT, ALLOW; одного bool `mt_usb_epoch_dispatch` недостаточно для выбора поведения.

`mt_usb_dispatch_enter(E)` устанавливает scope на **всё** исполнение снятого event. Метка остаётся E после вызова пользовательского callback и после reset IRQ. DCD shim читает scope token, не current. Прикладной owner scope аналогично открывается на весь `mt_usb_cdc_step`, отдельный standalone write и open_epoch.

Рекомендуемый небольшой структурный приём: switch обработки event вынести в private функцию `dispatch_one`; enter → вызов этой функции → leave остаются в caller. Тогда TU_ASSERT/TU_VERIFY return внутри обработки действительно возвращает через leave. Альтернатива при жёстком events/run=1: source bind + **безусловный owner cleanup после возврата tud_task_ext**, включая U772 TU_ASSERT, и tail-clear на нормальном пути. Просто поставить leave в U820 недостаточно. IRQ handler не может менять/«очищать» foreground scope. Nested callback read/write сохраняет и восстанавливает прежний scope; новый token внутри callback запрещён. NMI/HardFault/longjmp USB-reentry не поддерживается.

BUS_RESET receipt: `reset_done(E)` после успешного software reset U719, speed assignment и проверки current/fault; не от mount/queue-empty. При exact one-CDC driver его reset — ограниченный software путь без DCD и без внешнего reset callback. Если сохраняется возможность app drivers, void `configuration_reset` с TU_ASSERT U653 уже недостаточен как доказательство успеха: потребуется bool propagation/explicit result, а не слепой reset_done.

### H3. Явные guarded DCD shims на девять действий из §3

Самый малый надёжный набор hardware-facing функций: `xfer`, `xfer_fifo`, `open`, `stall`, `clear_stall`, `set_address`, `status_complete`, `sof_enable`, `close_all`. Заменить **все** перечисленные call sites U, включая прямые EP0 stall. Указатели/аргументы DCD ABI сохраняются.

Для `mt_dcd_begin(kind,actor_E)` результат должен содержать admitted/denied reason и сохранённый PRIMASK. При успехе lock остаётся удержанным; `mt_dcd_end(result)` публикует результат и восстанавливает прежний mask. Практически проще реализовать typed shims с одним обязательным cleanup, чем давать каждому caller свободно забываемые begin/end. Никаких TU_ASSERT/ранних return между захваченным mask и cleanup. Logging/user callbacks/controller внутри guard запрещены.

- Под lock: check actor E → пометить core busy, если нужно → **весь** DCD вызов → normal result/fault → core busy rollback/успешное state изменение → restore. Это устраняет окно check→MMIO и не оставляет core status обновление после denied void operation.
- Bool false **после реального допуска и вызова** = `fail(E,SUBMIT_FAILED)` до restore. Для open использовать ту же fail-closed категорию с отдельным operation kind в диагностике. Простой отказ старому E, уже poisoned E либо soft-defer не является новым DCD failure и не отравляет E+1.
- Void shim возвращает внутреннему core признак `executed`, хотя public DCD API остаётся void. Старый SET_ADDRESS не должен после rejected call создавать receipt; аналогично не считать исполненными stall/clear/SOF changes.
- Guard использует core_io для enumeration/первого CDC OUT до app-open; использование app_io на этих местах создаёт deadlock. App writer допускается через app_io при входе owner scope; внутренние DCD операции всё равно используют сохранённый E.
- В runtime оба USB NVIC IRQ намеренно включены между обычными операциями. PRIMASK не отменяет изменения NVIC bits внутри OS_NONE/DCD. INIT/DEINIT и специально masked phases имеют отдельный контракт; не обещать сохранение NVIC state одним PRIMASK.

### H4. Одна узкая FSDEV source-точка для SET_ADDRESS (D224–232)

Рекомендуемое изменение под opt-in F103 bridge: сохранить bool от D228 и вызвать `mt_usb_dcd_submit_result(EP0_IN, result)` **до возврата**, внутри уже удерживаемого outer set_address guard. Hook получает token из неизменяемого admitted operation context, не из current. Он сообщает отказ в outer result, ставит sticky fault один раз; outer shim возвращает успех core только при successful internal submission. Hook не вызывает controller/логирование/DCD.

В этом pinned F103 `edpt_xfer` D752–795 возвращает true без ветки normal failure. Следовательно, из данного тела не следует воспроизведённая hardware ошибка false. Однако контракт `dcd_edpt_xfer` — bool, и guard/fault injection/будущая проверка могут вернуть false; void boundary не даёт его обнаружить. Нельзя выдать «сейчас всегда true» за реализованную детекцию отказа.

**Могут ли хватить F103-specific wrappers без DCD source patch?** Только если shim SET_ADDRESS **заменяет**, а не оборачивает его реализацию: сам вызывает guarded EP0 xfer и возвращает его bool, сохраняя отдельный guarded status_complete для записи адреса. Это дублирование F103 semantics D224–232 с обязательным source-hash/build binding; уже не прозрачный wrapper. Оно работоспособно как осознанный альтернативный дизайн, но хуже для provenance: фактический upstream dcd_set_address больше не используется. Обычный wrapper `check; dcd_set_address(); success=true` не подходит. Рекомендация — маленький explicit result hook в DCD вместо незаметного клонирования.

Необходимость DCD hook не распространяется на переписывание всего port: outer guard покрывает внутренний SET_ADDRESS xfer целиком, поэтому второй IRQ guard внутри D228 не требуется. Не регистрировать одну неудачу одновременно внутренним и внешним hooks как две io_failures.

### H5. Configuration receipt и pre-teardown hook (U983–1017)

Нужно отдельное событие результата, а не mount-as-success. Точный порядок сейчас:

1. При old_cfg != new_cfg и old_cfg !=0: SOF off U992 → close_all U993 → class/core reset U997 → speed restore.
2. `_usbd_dev.cfg_num=new_cfg` U1002, **до** проверки open.
3. Config0: umount U1006. Config1: descriptor parse → notification open → bulk OUT open/first rearm → bulk IN open (порядок соответствует provided descriptor) → binding → mount U1012.
4. Только затем `tud_control_status` U1016; bool игнорируется, функция возвращает true.

В результате mount/ready могут стать true при неудавшемся EP0 status. Patch должен получить действительный результат status, вместе с результатом полного `process_set_config` и E передать `config_result(E,cfg,opened,status_submitted)`. Owner принимает receipt только после возврата task и повторной проверки E/fault/CDC readiness. Callback mount остаётся hint, не открывает adapter. Отрицательный config-result сообщать в ветке U1008–1010 **до** TU_ASSERT/раннего возврата: частично открытая принятая конфигурация не должна остаться пригодной для повторных live I/O; unsupported SETUP, штатно отвечающий stall, не равен такому внутреннему отказу конфигурации. После reset внутри mount следующая status попытка должна быть denied как old E.

Для минимального этапа `configured(E)` можно строго определить как «полное открытие config1 + успешное **submission** EP0 status в E». Это не означает receipt ACK от host. Failed status completion всё равно закрывает epoch через H1. Если автор хочет ждать успешного status **completion**, нужен отдельный pending config/control-transfer serial и его отмена новым SETUP/reset; по одному текущему `ctrl_xfer.request` нельзя без дополнительного доказательства отличать completion разных control transfers одной hard epoch.

**Teardown conflict:** существующий `mt_usb_epoch_fail(E,DECONFIGURED)` закрывает и `core_io`. Вызвать его до U992 и затем обычные guarded close_all/status нельзя: собственный guard их отклонит. Возможный минимальный fail-closed вариант:

- valid принятый переход config1→0 получает pre-teardown gate до U992, закрывающий app/normal dispatch;
- только этому синхронному scope E дать узкую cleanup capability: SOF-off, close_all, software reset и один EP0 IN status ZLP; не давать bulk/open/address/general status-complete;
- capability допускается лишь для того же current E, без exhaustion/queue/submission/completion/soft faults; допускается ровно собственный DECONFIGURED reason; reset немедленно делает capability недействительной;
- config0→1 в poisoned E автоматически не открывать. Нужен новый настоящий reset либо отдельный доказанный configuration-generation/cancellation path; текущий sweep USB004 относится к bus/FRES reset, не к close_all.

Другой допустимый scope — заранее отклонять неподдержанную смену конфигурации и явно не заявлять поддержку этого сценария. Нельзя тихо пропустить teardown, поставить DECONFIGURED после него «для удобства» или считать umount достаточно ранней блокировкой. Проверять тип/recipient/request и допустимый config, не ловить любой SETUP с wValue=0.

## 6. Почему только очередь не закрывает уже начатый callback

Воспроизведено на неизменённых core/FIFO/CDC + неизменённом supporting fake DCD, отдельно buffered и F1:

- TX completion снят; `tud_cdc_tx_complete_cb` публикует reset; после возврата CDC C520–522 отправляет старый ZLP. Fake фиксирует один invalid submission.
- RX callback публикует reset и вызывает nested read; её rearm и затем автоматический C512 оба доходят до DCD старой операции. Два invalid submissions.
- Это точки **после** любого hypothetical dequeue фильтра. Новый token на входе каждого wrapper превратил бы старый callback в «новую» операцию. Только сохранённый actor E + guard возле каждого действия закрывает пример.
- После reset старая функция может закончить software mutations. В выбранной модели новый foreground reset/открытие не исполняется рекурсивно до её возврата; owner немедленно обрабатывает pending loss, а новая конфигурация не принимается по старому receipt. Это ограничение single-owner, не доказательство общей thread-safety.

## 7. Отказы и честный byte accounting

1. `XFER_RESULT_FAILED/STALLED/TIMEOUT/ABORTED/...` проверять до busy-clear и до C460/U897 callback. Сейчас оба игнорируют result. ISR producer latch закрывает app до consumer; consumer не отправляет ZLP/rearm/status_complete для failed event.
2. False xfer/FIFO/open после допуска фиксировать синхронно. Не полагаться на `TU_BREAKPOINT`, return `tud_cdc_write_flush` или повторное чтение ready.
3. T425–435 возвращает prefix, уже скопированный в FIFO, независимо от auto-flush. Adapter46 правильно возвращает этот prefix. Не заменять 64 на0/-1 из-за последовавшего reset/failure; не засчитывать 64 как host delivery.
4. Buffered T411 извлекает FIFO **до** DCD; F103/HWFIFO извлекает внутри D735–736. Normal submission failure не разрешает продолжать ту же live epoch и повторять prefix. Fault остаётся закрытым до нового reset; foreground Stop/recovery не стирает failure diagnostics/failed Stop.
5. `pending_soft` также делает core_io=false. В exact F103/HWFIFO denied guard до DCD не потребляет TX FIFO. Для buffered регрессии soft interruption после T411 требует отдельного решения: короткий pre-consume-through-submit guard либо сохранение/отложенный retry того же буфера. Нельзя спутать ожидаемый soft defer с hard obsolete loss и заявить сохранность живого потока без такого теста. Полная suspend/reconnect policy здесь не закрыта.
6. Pinned FSDEV сообщает только SUCCESS в D297/375; PMAOVR D465–467 — breakpoint/clear, не failed completion event. Проверка event result не превращается в полное обнаружение аппаратных USB ошибок. Отдельный PMAOVR fault hook нужен, если это входит в следующую приёмку; текущая карта не объявляет его реализованным.

## 8. Выполненные наблюдательные проверки

Запуск: `python3 probes/run.py` из любого cwd. Runner пишет только этот пакет. Все vendor/fake/adapter production inputs оставлены неизменными.

| Сценарий | Фактический результат |
|---|---|
| `weak_fifo_bypass` | buffered: actual1, wrapped_xfer1; F1: actual1, wrapped_xfer0, wrapped_fifo0 |
| `active_tx_reset` | Старый ZLP после reset внутри уже активного callback, invalid1 в обоих modes |
| `active_rx_reset` | Nested read + automatic rearm после reset внутри callback, invalid2 в обоих modes |
| `failed_cdc_completion` | FAILED completion вызывает TX callback и ZLP; copied64, host0, ready1 |
| `failed_ep0_completion` | FAILED status вызывает strong status_complete; его `--wrap` счётчик0 из-за weak same-TU call |
| `config_status_false` | Mount уже вызван, status отвергнут, ready1, EP0 transfer/stall отсутствуют |
| `config_teardown_order` | SOF-off/close_all до umount, при close виден ready1; status после teardown |
| `exact_address_false` | Точное извлечение D224–232 + false stub: внутренний вызов1, wrapper0, void возвращается при false; GCC -O0 и -O2 |

Итого 14 real-core scenario runs +2 exact-function runs. Это **отрицательные контрпримеры**, а не PASS будущего bridge. Strict C11 `-pedantic-errors -Wall -Wextra -Werror` для probe/fake; upstream GNU11 с warnings; GCC14.2, `-fno-lto` для real-core runs. Sanitizers, ARM target build/disassembly нового bridge и hardware не запускались. Exact-function probe не исполняет полный MMIO DCD.

`evidence/commands.json`, `commands.log`, `results.json`, `inputs.json`, `f1/core_symbols.txt` сохраняют команды/exit status/hashes. `acceptance_matrix.json` и `probes/run_acceptance.py` ниже — **будущая** исполняемая спецификация harness, не выдуманные положительные результаты.

## 9. Исполняемая матрица следующей реализации

Root подключает свой native test-driver к runner: `python3 probes/run_acceptance.py --driver /absolute/path/to/bridge_test`. Driver принимает `--case ID --mode f1|buffered --operation NAME`, выполняет сценарий на реальных patched core/CDC/FIFO + instrumented fake DCD и выдаёт один JSON `{case,mode,operation,observations}`. Runner сравнивает численные/булевы postconditions из матрицы, а не печатает PASS по одному exit0. Без driver он перечисляет NOT_RUN и возвращает2. Контракт driver — вспомогательный формат, его можно адаптировать к root harness без изменения критериев.

Обязательные группы и точки injection:

- **Каждая из девяти операций:** reset после dispatch admission/до guard → actual DCD writes0; reset pending после permit/перед первой DCD mutation → IRQ отложен до complete operation; итог прежнего PRIMASK0/1. Полный call span включительно PMA/ownership, не один store.
- **Context:** reset внутри TX/RX/user control/mount callback; nested API сохраняет E; старый actor denied без fault в E+1. Ранний return/no-event/TU_ASSERT path не оставляет actor/lock; следующий app scope получает только новый разрешённый E.
- **False/complete:** normal false EP0/buffer/FIFO/CDC initial OUT/open; hidden address false; every non-success completion до callback, включая EP0 SET_ADDRESS status; один fault за одну failed operation, positive write64 сохраняется, host0, повторное old submission0.
- **Config:** enumeration core allowed before app-open; mount не открывает gate; status false/reset-after-mount/open failure не публикуют configured. Config0 закрывает app до close_all; cleanup имеет только перечисленные полномочия; новый reset отменяет capability; config0→1 без reset не оживляет poisoned epoch.
- **Queue:** reset при full queue; две reset; loss после reset enqueue; stale SETUP before reset; dropped setup accounting; synthetic resume/deferred stale E. Никакой retry/empty queue/mount не заменяет новый queued+processed reset. Сохранить bounded event budget.
- **Lifetime/IRQ:** producer не меняет active E; INIT и DEINIT имеют отдельный допуск; queue/spin restore исполняется даже для old E; OS_NONE/DCD nested NVIC re-enable не снимает PRIMASK. Отдельно source/link audit запретов ISO, other classes, raw app DCD calls.

ARM и hardware остаются отдельными acceptance-paths: оба vectors/priority/PRIGROUP, сохранение PRIMASK и generated instructions, latency under ADC/TIM, FSDEV pending CTR/reset cancellation/physical reenumeration. Кандидат USB004 sweep — необходимая отдельная предпосылка с открытой target validation, не доказательство этих пунктов. Config close_all использует zero EP writes вне bus-reset постусловия; его нельзя объявить физическим cancellation barrier по результатам USB004.

## 10. Короткий порядок авторской реализации

1. Связать opt-in patch с точными USB003+USB004 SHA и разрешённым config; не менять pristine reference.
2. H1/H2: envelope, immediate fault, preserved actor, structured scope exit; добавить единый native fake IRQ/PRIMASK scheduler.
3. H3: девять явных shims/все call sites, separate lifecycle/runtime/producer; H4: внутренний результат SET_ADDRESS.
4. H5: реальный config/open/status receipt и явно выбранная deconfiguration policy; owner adapter scopes сохраняют byte accounting.
5. Выполнить matrix/старые regressions, затем отдельные ARM/target evidence и Critic-A. До этого не называть bridge hardware-safe, full-lifecycle-ready или принятым общим reconnect contract.
