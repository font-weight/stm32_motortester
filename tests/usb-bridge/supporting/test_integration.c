/* Genuine core/CDC/FIFO with explicit test-only fake DCD instrumentation. */
#include "test_integration.h"
#include "fake_dcd.h"
#include "device/usbd_pvt.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int test_reject_open = -1, test_reject_xfer = -1;
static unsigned primask, nesting, pending_reset, calls[OP_COUNT], tx_callbacks, rx_callbacks;
static unsigned deferred_calls, mount_blocked, recursive_checks;
static bool tx_reset, rx_reset, mount_reset, control_reset, recursive_task;
static uint32_t callback_actor, callback_current;
static uint8_t bytes[64];
static uint32_t lock(void) { uint32_t old = primask; primask = 1; ++nesting; return old; }
static void unlock(uint32_t old) {
    assert(primask && nesting); --nesting; primask = old;
    if (!primask && pending_reset) { pending_reset = 0; fake_bus_reset(false); }
}
static void reset_irq(void) { pending_reset = 1; if (!primask) { pending_reset = 0; fake_bus_reset(false); } }
void test_dcd_call(enum test_operation op) {
    assert(primask == 1 && nesting && mt_usb_bridge_actor()); ++calls[op];
}
void test_mount_hook(void) {
    mt_usb_scope s;
    assert(!mt_usb_bridge_app_enter(&s)); ++mount_blocked;
    if (mount_reset) { mount_reset = false; reset_irq(); }
}
static void step(void) {
    mt_usb_epoch state = mt_usb_bridge_snapshot();
    if (state.cleaned != state.current) mt_usb_bridge_cleaned(state.current);
    tud_task_ext(0, false);
    assert(!primask && !nesting && mt_usb_bridge_actor() == 0);
}
void test_pump(void) {
    unsigned budget = 0;
    while (tud_task_event_ready()) {
        assert(++budget < 1000); step();
        if (mt_usb_bridge_snapshot().pending_soft) break;
    }
}
static void callback_reset(void) {
    callback_actor = mt_usb_bridge_actor(); assert(callback_actor);
    reset_irq(); callback_current = mt_usb_bridge_snapshot().current;
    assert(callback_current == callback_actor + 1);
    assert(mt_usb_bridge_actor() == callback_actor);
    mt_usb_scope nested; assert(!mt_usb_bridge_app_enter(&nested));
    mt_usb_bridge_leave(&nested); assert(mt_usb_bridge_actor() == callback_actor);
}
void tud_cdc_tx_complete_cb(uint8_t itf) {
    assert(itf == 0); ++tx_callbacks;
    if (recursive_task) {
        recursive_task = false; uint32_t actor = mt_usb_bridge_actor();
        tud_task_ext(0, false); assert(mt_usb_bridge_actor() == actor); ++recursive_checks;
    }
    if (tx_reset) { tx_reset = false; callback_reset(); }
}
void tud_cdc_rx_cb(uint8_t itf) {
    assert(itf == 0); ++rx_callbacks;
    if (rx_reset) {
        rx_reset = false; callback_reset();
        /* Intentionally call raw nested CDC read: it must inherit old actor. */
        uint8_t out[64]; (void)tud_cdc_n_read(0, out, sizeof(out));
    }
}
bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *req) {
    (void)rhport; (void)req;
    if (stage == CONTROL_STAGE_SETUP && control_reset) { control_reset = false; callback_reset(); }
    return false;
}
static void deferred(void *arg) { (void)arg; assert(mt_usb_bridge_actor()); ++deferred_calls; }
static void fixture(bool configured) {
    if (tud_inited()) assert(tud_deinit(0));
    primask = nesting = pending_reset = tx_callbacks = rx_callbacks = deferred_calls = mount_blocked = recursive_checks = 0;
    tx_reset = rx_reset = mount_reset = control_reset = recursive_task = false;
    memset(calls, 0, sizeof(calls)); memset(bytes, 0x5a, sizeof(bytes));
    test_reject_open = test_reject_xfer = -1; callback_actor = callback_current = 0;
    mt_usb_bridge_init(lock, unlock);
    if (configured) fake_start();
    else {
        memset(fake_ep, 0, sizeof(fake_ep)); fake_epoch = fake_invalid_submissions = fake_mounts = fake_umounts = 0;
        fake_on_in_submit = fake_on_out_submit = NULL; fake_reject_next_in = false; fake_clear_logs();
        const tusb_rhport_init_t init = {.role=TUSB_ROLE_DEVICE,.speed=TUSB_SPEED_FULL};
        assert(tusb_rhport_init(0,&init)); fake_bus_reset(true);
    }
    memset(calls, 0, sizeof(calls));
}
static uint32_t write_bytes(unsigned n) {
    mt_usb_scope s; if (!mt_usb_bridge_app_enter(&s)) return 0;
    uint32_t count = tud_cdc_n_write(0, bytes, n); mt_usb_bridge_leave(&s); return count;
}
static void setup(uint8_t type, uint8_t req, uint16_t value, uint16_t index, uint16_t length, bool service) {
    const uint8_t packet[8]={type,req,(uint8_t)value,(uint8_t)(value>>8),(uint8_t)index,(uint8_t)(index>>8),(uint8_t)length,(uint8_t)(length>>8)};
    fake_ep[0].pending = fake_ep[0x80].pending = false;
    fake_ep[0].stalled = fake_ep[0x80].stalled = false;
    dcd_event_setup_received(0, packet, true); if (service) fake_pump();
}
static void end(const char *name) {
    assert(!primask && !nesting && !pending_reset && !mt_usb_bridge_actor());
    assert(fake_invalid_submissions == 0);
    printf("{\"case\":\"%s\",\"mode\":\"f1\",\"pass\":true,\"host_bytes\":%zu,\"records\":%zu,\"io_failures\":%u,\"queue_losses\":%u}\n",name,fake_host_size,fake_record_count,mt_usb_bridge_snapshot().io_failures,mt_usb_bridge_snapshot().queue_losses);
}
static void active_callbacks(void) {
    fixture(true); assert(mount_blocked == 1); assert(write_bytes(64) == 64);
    tx_reset = true; fake_complete_in(FAKE_CDC_IN,true);
    assert(tx_callbacks == 1 && fake_record_count == 1 && fake_host_size == 64);
    assert(!fake_ep[FAKE_CDC_IN].pending && mt_usb_bridge_snapshot().io_failures == 0);
    end("active_tx_reset_no_stale_zlp");
    fixture(true); rx_reset = true; fake_out(bytes,64,true);
    assert(rx_callbacks == 1 && !fake_record_count && !fake_ep[FAKE_CDC_OUT].pending);
    assert(mt_usb_bridge_snapshot().io_failures == 0); end("active_rx_reset_no_nested_or_auto_rearm");
    fixture(true); control_reset = true; setup(0x40,0x55,0,0,0,true);
    assert(callback_current == callback_actor+1 && !calls[OP_STALL] && !calls[OP_XFER]);
    end("active_control_reset_no_stale_stall");
    fixture(false); mount_reset = true; setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(fake_mounts == 1 && !fake_ep[0x80].pending && !mt_usb_bridge_snapshot().configured);
    end("mount_reset_no_config_receipt");
    fixture(true); assert(write_bytes(64) == 64); recursive_task = true;
    fake_complete_in(FAKE_CDC_IN,false); fake_signal(DCD_EVENT_RESUME,false); step();
    assert(recursive_checks == 1 && tud_task_event_ready()); fake_pump();
    end("recursive_task_rejected_without_queue_pop");
}
static void failures(void) {
    fixture(true); fake_reject_next_in = true;
    assert(write_bytes(64) == 64 && fake_host_size == 0 && fake_record_count == 1);
    assert(!fake_records[0].accepted && mt_usb_bridge_snapshot().io_failures == 1);
    assert(write_bytes(64) == 0 && fake_record_count == 1); end("positive_prefix_on_submission_false");
    fixture(true); fake_on_in_submit = reset_irq; assert(write_bytes(64) == 64);
    assert(fake_host_size == 0 && fake_record_count == 1 && fake_records[0].accepted);
    assert(mt_usb_bridge_snapshot().current == 2 && !fake_ep[FAKE_CDC_IN].pending);
    assert(write_bytes(64) == 0); fake_pump(); end("reset_deferred_through_hwfifo_submission");
    fixture(false); test_reject_xfer = 0x80; setup(0,TUSB_REQ_SET_ADDRESS,7,0,0,true);
    assert(mt_usb_bridge_snapshot().io_failures == 1 && !fake_ep[0x80].pending);
    assert(calls[OP_ADDRESS] == 1 && calls[OP_STALL] == 0); end("hidden_address_submission_false");
    fixture(false); test_reject_xfer = 0x80; setup(0x80,TUSB_REQ_GET_DESCRIPTOR,TUSB_DESC_DEVICE<<8,0,18,true);
    assert(mt_usb_bridge_snapshot().io_failures == 1 && !calls[OP_STALL]); end("ep0_data_submission_false");
    fixture(false); test_reject_xfer = 0x80; setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(fake_mounts == 1 && mount_blocked == 1 && !mt_usb_bridge_snapshot().configured);
    assert(mt_usb_bridge_snapshot().io_failures == 1 && !calls[OP_STALL]); end("configuration_status_false_no_receipt");
    fixture(false); test_reject_xfer = FAKE_CDC_OUT; setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(!mt_usb_bridge_snapshot().configured && mt_usb_bridge_snapshot().io_failures == 1);
    end("initial_cdc_out_submission_false");
    for (unsigned i=0; i<3; ++i) {
        const uint8_t endpoints[]={0x81,FAKE_CDC_OUT,FAKE_CDC_IN};
        fixture(false); test_reject_open=endpoints[i]; setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
        assert(!fake_mounts && !mt_usb_bridge_snapshot().configured && mt_usb_bridge_snapshot().io_failures == 1);
        end(i == 0 ? "notification_open_false" : i == 1 ? "bulk_out_open_false" : "bulk_in_open_false");
    }
    for (unsigned result=XFER_RESULT_FAILED; result<=XFER_RESULT_INVALID; ++result) {
        fixture(true); assert(write_bytes(64) == 64); fake_ep[FAKE_CDC_IN].pending = false;
        dcd_event_xfer_complete(0,FAKE_CDC_IN,64,(uint8_t)result,true);
        assert(mt_usb_bridge_snapshot().io_failures == 1 && write_bytes(64) == 0); fake_pump();
        assert(!tx_callbacks && fake_record_count == 1 && fake_host_size == 0);
        char name[80]; (void)snprintf(name,sizeof(name),"failed_cdc_completion_result_%u",result); end(name);
        fixture(false); setup(0,TUSB_REQ_SET_ADDRESS,7,0,0,true); unsigned status_before=calls[OP_STATUS];
        fake_ep[0x80].pending=false; dcd_event_xfer_complete(0,0x80,0,(uint8_t)result,true); fake_pump();
        assert(calls[OP_STATUS] == status_before && mt_usb_bridge_snapshot().io_failures == 1);
        (void)snprintf(name,sizeof(name),"failed_address_status_result_%u",result); end(name);
    }
}
static void configuration(void) {
    fixture(false); setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(mount_blocked == 1 && mt_usb_bridge_snapshot().configured == mt_usb_bridge_snapshot().current);
    assert(fake_ep[0x80].pending); /* submission receipt precedes host ACK by contract */
    end("config_receipt_only_after_actual_status_submission");
    fixture(true); setup(0,TUSB_REQ_SET_CONFIGURATION,0,0,0,true);
    assert(mt_usb_bridge_snapshot().faults == MT_USB_EPOCH_DECONFIGURED);
    assert(!calls[OP_SOF] && !calls[OP_CLOSE] && !calls[OP_STALL]);
    assert(write_bytes(64) == 0); setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(mt_usb_bridge_snapshot().faults == MT_USB_EPOCH_DECONFIGURED && write_bytes(64) == 0);
    end("config0_poison_without_teardown_or_same_epoch_reopen");
}
static void queue_and_scopes(void) {
    fixture(true); mt_usb_scope s; assert(mt_usb_bridge_app_enter(&s));
    usbd_defer_func(deferred,NULL,false); mt_usb_bridge_leave(&s);
    fake_signal(DCD_EVENT_SUSPEND,false); step();
    assert(!deferred_calls && tud_task_event_ready());
    fake_signal(DCD_EVENT_RESUME,false); fake_pump(); assert(deferred_calls == 1);
    end("soft_deferred_envelope_retained_and_ready");
    fixture(true); assert(mt_usb_bridge_app_enter(&s)); usbd_defer_func(deferred,NULL,false);
    fake_bus_reset(false); usbd_defer_func(deferred,NULL,false); mt_usb_bridge_leave(&s);
    fake_pump(); assert(!deferred_calls && mt_usb_bridge_snapshot().processed_reset == 2);
    end("deferred_work_keeps_old_actor");
    fixture(true); for(unsigned i=0;i<16;++i) fake_signal(DCD_EVENT_RESUME,false);
    fake_bus_reset(false); assert(mt_usb_bridge_snapshot().queue_losses == 1);
    fake_pump(); assert(!mt_usb_bridge_snapshot().processed_reset && write_bytes(64) == 0);
    fake_bus_reset(true); assert(mt_usb_bridge_snapshot().processed_reset == 3);
    end("full_queue_lost_reset_requires_fresh_reset");
    fixture(true); fake_bus_reset(false); fake_bus_reset(false); fake_pump();
    assert(mt_usb_bridge_snapshot().processed_reset == 3 && !mt_usb_bridge_snapshot().faults);
    end("two_resets_only_newest_processed");
    fixture(true); fake_bus_reset(false); for(unsigned i=0;i<16;++i) fake_signal(DCD_EVENT_RESUME,false);
    assert(mt_usb_bridge_snapshot().queue_losses == 1); fake_pump();
    assert(!mt_usb_bridge_snapshot().processed_reset); end("queue_loss_after_reset_receipt_poison");
    fixture(true); setup(0x80,TUSB_REQ_GET_DESCRIPTOR,TUSB_DESC_DEVICE<<8,0,18,false);
    fake_bus_reset(false); fake_pump(); assert(!calls[OP_XFER]);
    setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(mt_usb_bridge_snapshot().configured == 2); end("stale_setup_dropped_accounting_recovers");
    fixture(true); dcd_event_xfer_complete(0,0x01,0,XFER_RESULT_SUCCESS,true); step();
    assert(!mt_usb_bridge_actor()); assert(mt_usb_bridge_app_enter(&s)); mt_usb_bridge_leave(&s);
    /* In-range but unmapped endpoint direction forces TU_ASSERT driver return. */
    end("structured_return_scope_cleanup");
}
static void additional_regressions(void) {
    fixture(true); mt_usb_scope s; assert(mt_usb_bridge_app_enter(&s));
    assert(tud_cdc_n_write(0,bytes,3) == 3);
    fake_signal(DCD_EVENT_SUSPEND,false);
    assert(tud_cdc_n_write_flush(0) == 0 && fake_record_count == 0);
    assert(tud_cdc_n_write_available(0) == 61); mt_usb_bridge_leave(&s);
    fake_signal(DCD_EVENT_RESUME,false); fake_pump();
    assert(mt_usb_bridge_app_enter(&s)); assert(tud_cdc_n_write_flush(0) == 3);
    mt_usb_bridge_leave(&s); fake_complete_in(FAKE_CDC_IN,true);
    assert(fake_host_size == 3 && !memcmp(fake_host,bytes,3));
    end("real_hwfifo_soft_defer_preserves_prefix");

    fixture(true); assert(mt_usb_bridge_app_enter(&s));
    usbd_defer_func(deferred,NULL,false); mt_usb_bridge_leave(&s);
    fake_signal(DCD_EVENT_SUSPEND,false); step();
    fake_bus_reset(false); fake_pump();
    assert(!deferred_calls && mt_usb_bridge_snapshot().processed_reset == 2);
    end("held_soft_event_obsoleted_by_reset");

    fixture(true);
    for(unsigned i=0;i<17;++i) setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,false);
    assert(mt_usb_bridge_snapshot().queue_losses == 1); fake_pump();
    fake_bus_reset(true); setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(mt_usb_bridge_snapshot().configured == 2 && fake_mounts == 2);
    end("dropped_setup_does_not_leave_phantom_accounting");

    fixture(true); assert(mt_usb_bridge_app_enter(&s));
    usbd_defer_func(deferred,NULL,false); usbd_defer_func(deferred,NULL,false);
    mt_usb_bridge_leave(&s); step(); assert(deferred_calls == 1 && tud_task_event_ready());
    step(); assert(deferred_calls == 2 && !tud_task_event_ready());
    step(); assert(mt_usb_bridge_actor() == 0); end("bounded_one_event_and_empty_task_cleanup");

    fixture(true); usbd_defer_func(deferred,NULL,false); fake_pump();
    assert(!deferred_calls); end("unscoped_deferred_work_denied");

    fixture(true); setup(0x40,0x55,0,0,0,true);
    assert(!mt_usb_bridge_snapshot().faults && calls[OP_STALL] == 2);
    end("unrelated_value0_setup_not_deconfiguration");

    fixture(true); fake_signal(DCD_EVENT_SUSPEND,false);
    dcd_event_t sof={.rhport=0,.event_id=DCD_EVENT_SOF,.sof.frame_count=123};
    dcd_event_handler(&sof,true); fake_pump();
    assert(!mt_usb_bridge_snapshot().pending_soft && write_bytes(64) == 64);
    end("sof_synthetic_resume_releases_soft_pause");
}

#ifdef TEST_ADAPTER
#include "mt_usb_cdc.h"
static mt_control ctl;
static mt_control_rx rx;
static mt_usb_cdc adapter;
static unsigned stop_calls;
static int stop_ok;
static int stable(void *context) {
    (void)context; mt_usb_epoch state=mt_usb_bridge_snapshot();
    return mt_usb_epoch_app_io(&state,state.current);
}
static int stop(void *context) { (void)context; ++stop_calls; return stop_ok; }
static int profile(void *context) { (void)context; return 1; }
static int start(void *context,uint32_t time) { (void)context; (void)time; return 1; }
static void begin_adapter(void) {
    fixture(true); stop_calls=0; stop_ok=1;
    mt_control_io io={profile,start,stop,NULL,123};
    assert(mt_control_init(&ctl,&io) == MT_CONTROL_OK);
    assert(mt_control_rx_init(&rx,&ctl) == MT_CONTROL_OK);
    assert(mt_usb_cdc_init(&adapter,&rx,stable,NULL,0) == MT_CONTROL_OK);
    assert(mt_usb_cdc_open_epoch(&adapter,1) == MT_CONTROL_OK);
}
static void adapter_tests(void) {
    begin_adapter(); assert(mt_usb_cdc_write(&adapter,bytes,64) == 64);
    assert(!mt_usb_bridge_actor() && fake_record_count == 1);
    fake_complete_in(FAKE_CDC_IN,true); assert(fake_host_size == 64);
    assert(mt_usb_cdc_open_epoch(&adapter,2) == MT_CONTROL_ARGUMENT);
    assert(mt_usb_cdc_open_epoch(&adapter,0) == MT_CONTROL_ARGUMENT);
    assert(mt_usb_cdc_write(NULL,bytes,1) == -1);
    assert(mt_usb_cdc_write(&adapter,NULL,1) == -1);
    assert(mt_usb_cdc_write(&adapter,bytes,65) == -1);
    assert(mt_usb_cdc_step(NULL,0) == MT_CONTROL_ARGUMENT);
    end("adapter_auto_scope_and_argument_cleanup");

    begin_adapter(); fake_reject_next_in=true;
    assert(mt_usb_cdc_write(&adapter,bytes,64) == 64 && fake_host_size == 0);
    assert(mt_usb_bridge_snapshot().io_failures == 1);
    assert(mt_usb_cdc_write(&adapter,bytes,64) == 0);
    ctl.hardware_running=1; stop_ok=0;
    assert(mt_usb_cdc_hard_loss(&adapter,1) == MT_CONTROL_RECOVERY);
    assert(adapter.state == MT_USB_QUARANTINED && adapter.epoch == 1);
    assert(ctl.hardware_running == 1 && stop_calls == 1 && ctl.ready == 0);
    assert(mt_usb_bridge_snapshot().io_failures == 1);
    assert(mt_usb_cdc_open_epoch(&adapter,1) == MT_CONTROL_IGNORED);
    stop_ok=1; assert(mt_usb_cdc_hard_loss(&adapter,1) == MT_CONTROL_RECOVERY);
    assert(ctl.hardware_running == 0 && stop_calls == 2);
    assert(mt_usb_bridge_snapshot().io_failures == 1 && fake_record_count == 1);
    end("adapter_same_epoch_quarantine_preserves_failed_stop_and_fault");

    begin_adapter(); fake_on_in_submit=reset_irq;
    assert(mt_usb_cdc_write(&adapter,bytes,64) == 64 && fake_host_size == 0);
    assert(mt_usb_cdc_write(&adapter,bytes,64) == 0);
    assert(mt_usb_cdc_hard_loss(&adapter,2) == MT_CONTROL_RECOVERY);
    fake_pump(); fake_configuration(1);
    assert(mt_usb_cdc_open_epoch(&adapter,1) == MT_CONTROL_ARGUMENT);
    assert(mt_usb_cdc_open_epoch(&adapter,2) == MT_CONTROL_OK);
    assert(mt_usb_cdc_write(&adapter,bytes,3) == 3);
    end("adapter_reset_preserves_positive_prefix_and_fresh_open");

    begin_adapter(); fake_out(bytes,64,true);
    assert(!fake_ep[FAKE_CDC_OUT].pending); fake_on_out_submit=reset_irq;
    assert(mt_usb_cdc_step(&adapter,0) == MT_CONTROL_IGNORED);
    assert(!mt_usb_bridge_actor() && mt_usb_bridge_snapshot().current == 2);
    assert(!fake_ep[FAKE_CDC_OUT].pending); fake_pump();
    end("adapter_step_read_rearm_retains_old_actor");

    begin_adapter(); fake_out(bytes,64,true);
    assert(mt_usb_cdc_soft_pause(&adapter) == MT_CONTROL_RECOVERY);
    assert(adapter.rx_flush_pending); fake_on_out_submit=reset_irq;
    assert(mt_usb_cdc_open_epoch(&adapter,1) == MT_CONTROL_IGNORED);
    assert(adapter.rx_flush_pending && adapter.state == MT_USB_SOFT_PAUSED);
    assert(mt_usb_bridge_snapshot().current == 2 && !mt_usb_bridge_actor()); fake_pump();
    end("adapter_open_flush_rearm_reset_cannot_reopen_old_epoch");
}
#endif

int main(void) {
    active_callbacks(); failures(); configuration(); queue_and_scopes(); additional_regressions();
#ifdef TEST_ADAPTER
    adapter_tests();
#endif
    if (tud_inited()) assert(tud_deinit(0));
    return 0;
}
