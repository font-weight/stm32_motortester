"""Manual callsite resolutions. Every mapping is tied to source initializer and disassembly site."""
B={}
def bind(sites,targets,evidence):
 for s in sites.split():
  assert s not in B
  B[s]={'targets':targets,'evidence':evidence}
MMR=[('mt_clock_startup.o','mt_clock_mmio_read32')];MMW=[('mt_clock_startup.o','mt_clock_mmio_write32')]
bind('0x8000f90 0x8000fa0',[('5-mt_runtime.o','adc_time')],'mt_runtime.c:66 adc_io.now_us=adc_time; mt_adc_control.c:21,24 d->io.now_us')
bind('0x8000f98',[('7-mt_timebase.o','mt_timebase_now_ms')],'mt_runtime.c:71 passes mt_timebase_now_ms; mt_adc_control.c:46 saves b->now_ms, line22 calls')
bind('0x8001246 0x8001438 0x8001790 0x80018cc',[('2-mt_adc_control.o','stop')],'mt_adc_control.c:49 io.stop=stop; mt_control.c:96 copies io; .stop offsets #536')
bind('0x8001452 0x80016da 0x8001890',[('2-mt_adc_control.o','profile')],'mt_adc_control.c:49 io.profile_ready=profile; mt_control.c:96 copies io; offsets #528')
bind('0x8001746',[('2-mt_adc_control.o','start')],'mt_adc_control.c:49 io.start=start; mt_control.c:96 copies io; offset #532')
bind('0x8001af2',MMR,'main:66 runtime_io.read32=mt_clock_mmio_read32; mt_runtime.c:63 saves mmio; adc_read wrapper')
bind('0x8001b00',MMW,'main:66 runtime_io.write32=mt_clock_mmio_write32; mt_runtime.c:63 saves mmio; adc_write wrapper')
bind('0x8001f0a',[('5-mt_runtime.o','adc_read')],'mt_runtime.c:65 adc_io.read32=adc_read; mt_adc_dma_init copies io')
bind('0x8001f14',[('5-mt_runtime.o','adc_write')],'mt_runtime.c:65 adc_io.write32=adc_write; mt_adc_dma_init copies io')
bind('0x8001f1a',[('5-mt_runtime.o','adc_time')],'mt_runtime.c:66 adc_io.now_us=adc_time; mt_adc_dma_init copies io')
bind('0x8002460',[('5-mt_runtime.o','commit_enter')],'mt_runtime.c:69 guard.enter=commit_enter; mt_adc_control.c:48 copies guarded guard; ADC start guard offset0')
bind('0x8002472 0x80024fa',[('5-mt_runtime.o','commit_allowed')],'mt_runtime.c:69 guard.allowed=commit_allowed; mt_adc_control.c:48 copies guard; ADC start guard offset4')
bind('0x800247e',[('5-mt_runtime.o','commit_leave')],'mt_runtime.c:70 guard.leave=commit_leave; mt_adc_control.c:48 copies guard; ADC start guard offset8')
bind('0x8002912 0x80071e6 0x80076d4 0x80078c0',MMR,'main:65 clock_io.read32=mt_clock_mmio_read32; mt_clock_apply copies io; mt_boot_time_start:62 copies same io into timebase; boot/time/USB read wrappers')
bind('0x8002918 0x8007208 0x8007800 0x8007824 0x8007842 0x80078c6',MMW,'main:65 clock_io.write32=mt_clock_mmio_write32; mt_clock_apply copies io; mt_boot_time_start:62 copies same io into timebase; boot/time/USB write wrappers')
bind('0x8003a16',[('15-mt_usb_cdc.o','mt_usb_cdc_write_impl')],'mt_usb_cdc.c:6 macro maps write name to _impl; line75 mt_control_poll passes write; control_poll->capture_link_poll->mt_tx_poll preserves write/context')
bind('0x8003d38 0x8003df2 0x8003e12',[('5-mt_runtime.o','stable')],'mt_runtime.c:74 mt_usb_cdc_init(...stable...); mt_usb_cdc.c:28 saves a->stable; these are its only indirect callsites')
bind('0x80040ae',[('8-mt_irq_guard.o','mt_irq_guard_enter')],'main:82 mt_runtime_init(...mt_irq_guard_enter...); runtime:76 mt_usb_bridge_init; bridge:12 assigns lock_fn')
bind('0x80040be',[('8-mt_irq_guard.o','mt_irq_guard_leave')],'main:82 mt_runtime_init(...mt_irq_guard_leave...); runtime:76 mt_usb_bridge_init; bridge:13 assigns unlock_fn')
bind('0x800560e',[('21-cdc_device.o','cdcd_reset')],'usbd.c:218-230 CDC-only builtin table reset; CFG_TUD_CDC=1/all other classes0; actual weak app_driver_get sets count0')
bind('0x800564c',[('21-cdc_device.o','cdcd_control_xfer_cb')],'usbd.c:1076 invoke_class_control receives get_driver()->control_xfer_cb; CDC-only table')
bind('0x8005682 0x80056a8 0x8005b24 0x8005b46 0x8005eae 0x8005ec4',[('22-usbd.o','usbd_int_set')],'usbd.c:462 OSAL_SPINLOCK_DEF and :468 OSAL_QUEUE_DEF bind interrupt_set=usbd_int_set; osal_none.h:66,189 preserve it')
bind('0x800584c',[('21-cdc_device.o','cdcd_init')],'usbd.c:218-230 CDC-only builtin init; actual app_driver_count=0')
bind('0x8005922',[('21-cdc_device.o','cdcd_deinit')],'usbd.c:218-230 CDC-only builtin deinit; actual app_driver_count=0; tud_deinit itself unreachable from actual roots')
bind('0x8005a82',[],'INFEASIBLE: driver->sof NULL in the only CDC table (usbd.c:229); app_driver_get weak count0, no other table mutation')
bind('0x8005aec',[],'INFEASIBLE: driver->xfer_isr NULL in the only CDC table (usbd.c:228); app_driver_get weak count0, no other table mutation')
bind('0x80061aa',[('21-cdc_device.o','cdcd_open')],'usbd.c:218-230 CDC-only builtin open; actual app_driver_count=0')
bind('0x8006410 0x8006488',[('21-cdc_device.o','cdcd_control_xfer_cb'),('22-usbd.o','tud_vendor_control_xfer_cb')],'usbd.c:1077,1217,1224,1293,1351 complete_cb only NULL/class CDC/vendor; test-mode branch absent CFG_TUD_TEST_MODE=0')
bind('0x80064bc',[('21-cdc_device.o','cdcd_xfer_cb')],'usbd.c:218-230 CDC-only builtin xfer_cb; actual app_driver_count=0')
bind('0x80064f6',[],'INFEASIBLE FROM ACTUAL ROOTS: USBD_EVENT_FUNC_CALL only producer usbd_defer_func (usbd.c:1681), no callers/address references in linked objects; other producers dcd_int_handler and handler SOF/resume enqueue only fixed hardware event tags1..7. Excludes arbitrary debugger/RAM corruption and external new callers.')
