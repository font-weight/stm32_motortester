/* Native metadata/critical-section model. Not target interrupt evidence. */
#include "mt_usb_bridge.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

enum operation { XFER, FIFO, OPEN, STALL, CLEAR, ADDRESS, STATUS, SOF, CLOSE, NOPS };
static const char *const names[NOPS] = {"xfer", "fifo", "open", "stall", "clear_stall", "address", "status", "sof", "close_all"};
static unsigned primask, nesting, pending_reset, reset_count, mutations[NOPS], active_call, trigger_reset;
static unsigned nvic_enabled, lock_count, unlock_count;
static bool submit_ok, report_address;
static uint32_t observed_actor;
static tu_fifo_t fifo;
static uint8_t fifo_buf[64], bytes[64];
static tusb_desc_endpoint_t ep_desc;
static tusb_control_request_t request;

static uint32_t enter(void) {
    uint32_t old = primask; primask = 1; ++nesting; ++lock_count; return old;
}
static void publish_reset(void);
static void drain_irq(void) {
    if (!primask && pending_reset) { pending_reset = 0; assert(!active_call); publish_reset(); }
}
static void leave(uint32_t old) {
    assert(primask == 1 && nesting); --nesting; ++unlock_count; primask = old; drain_irq();
}
static uint32_t ingress(unsigned id, unsigned result, bool accepted) {
    dcd_event_t e = {.rhport = 0, .event_id = (uint8_t)id};
    e.xfer_complete.result = (uint8_t)result;
    uint32_t key = mt_usb_bridge_lock();
    uint32_t token = mt_usb_bridge_ingress(&e);
    mt_usb_bridge_enqueued(token, id == DCD_EVENT_BUS_RESET, accepted);
    mt_usb_bridge_unlock(key); return token;
}
static void publish_reset(void) { ++reset_count; (void)ingress(DCD_EVENT_BUS_RESET, 0, true); }
static void mutate(enum operation op) {
    assert(primask == 1 && nesting && mt_usb_bridge_actor());
    ++active_call; observed_actor = mt_usb_bridge_actor();
    uint32_t before = mt_usb_bridge_snapshot().current;
    if (trigger_reset) { trigger_reset = 0; pending_reset = 1; drain_irq(); }
    assert(mt_usb_bridge_snapshot().current == before);
    /* Model a DCD/OS_NONE nested NVIC enable: it cannot unmask PRIMASK. */
    nvic_enabled = 1; drain_irq(); assert(primask == 1);
    ++mutations[op]; assert(mt_usb_bridge_snapshot().current == before);
    --active_call;
}
bool dcd_edpt_xfer(uint8_t r, uint8_t ep, uint8_t *buf, uint16_t n, bool isr) {
    (void)r; (void)ep; (void)buf; (void)n; (void)isr; mutate(XFER); return submit_ok;
}
bool dcd_edpt_xfer_fifo(uint8_t r, uint8_t ep, tu_fifo_t *f, uint16_t n, bool isr) {
    (void)r; (void)ep; (void)isr; mutate(FIFO);
    if (n) assert(tu_fifo_read_n(f, bytes, n) == n);
    return submit_ok;
}
bool dcd_edpt_open(uint8_t r, tusb_desc_endpoint_t const *ep) { (void)r; (void)ep; mutate(OPEN); return submit_ok; }
void dcd_edpt_stall(uint8_t r, uint8_t ep) { (void)r; (void)ep; mutate(STALL); }
void dcd_edpt_clear_stall(uint8_t r, uint8_t ep) { (void)r; (void)ep; mutate(CLEAR); }
void dcd_set_address(uint8_t r, uint8_t address) {
    (void)address; mutate(ADDRESS);
    bool ok = dcd_edpt_xfer(r, 0x80, NULL, 0, false);
    if (report_address) mt_usb_bridge_address_result(ok);
}
void dcd_edpt0_status_complete(uint8_t r, tusb_control_request_t const *req) { (void)r; (void)req; mutate(STATUS); }
void dcd_sof_enable(uint8_t r, bool on) { (void)r; (void)on; mutate(SOF); }
void dcd_edpt_close_all(uint8_t r) { (void)r; mutate(CLOSE); }
static bool invoke(enum operation op) {
    switch (op) {
        case XFER: return mt_usb_bridge_xfer(0, 0x80, bytes, 0, false);
        case FIFO: return mt_usb_bridge_fifo(0, 0x82, &fifo, 0, false);
        case OPEN: return mt_usb_bridge_open(0, &ep_desc);
        case STALL: mt_usb_bridge_stall(0, 0x80); break;
        case CLEAR: mt_usb_bridge_clear_stall(0, 0x80); break;
        case ADDRESS: return mt_usb_bridge_address(0, 7);
        case STATUS: mt_usb_bridge_status(0, &request); break;
        case SOF: mt_usb_bridge_sof(0, false); break;
        case CLOSE: mt_usb_bridge_close_all(0); break;
        default: assert(false);
    }
    return true;
}
static void init(void) {
    primask = nesting = pending_reset = reset_count = active_call = trigger_reset = 0;
    nvic_enabled = lock_count = unlock_count = 0; observed_actor = 0;
    memset(mutations, 0, sizeof(mutations)); submit_ok = report_address = true;
    assert(tu_fifo_config(&fifo, fifo_buf, sizeof(fifo_buf), false));
    mt_usb_bridge_init(enter, leave);
}
static void enumeration(bool configured) {
    uint32_t token = ingress(DCD_EVENT_BUS_RESET, 0, true);
    mt_usb_bridge_cleaned(token); mt_usb_scope s;
    assert(mt_usb_bridge_dispatch(token, 1, &s) == MT_USB_DISPATCH);
    mt_usb_bridge_reset_done(token); mt_usb_bridge_leave(&s);
    assert(mt_usb_bridge_dispatch(token, 0, &s) == MT_USB_DISPATCH);
    if (configured) {
        assert(mt_usb_bridge_config_begin(0, 1)); mt_usb_bridge_config_done(1, true);
    }
    mt_usb_bridge_leave(&s);
}
static void clean_end(void) {
    assert(!nesting && !primask && !active_call && !pending_reset);
    assert(lock_count == unlock_count && mt_usb_bridge_actor() == 0);
}
static void report(const char *case_name, const char *op, unsigned mask) {
    printf("{\"case\":\"%s\",\"operation\":\"%s\",\"initial_primask\":%u,\"pass\":true}\n", case_name, op, mask);
}
static void all_guards(void) {
    for (enum operation op = XFER; op < NOPS; ++op) {
        for (unsigned mask = 0; mask <= 1; ++mask) {
            init(); enumeration(true); mt_usb_scope s;
            assert(mt_usb_bridge_app_enter(&s)); uint32_t old = mt_usb_bridge_actor();
            primask = mask; trigger_reset = 1; assert(invoke(op));
            assert(mutations[op] == 1 && observed_actor == old && primask == mask);
            if (mask) { assert(pending_reset && !reset_count); primask = 0; drain_irq(); }
            assert(reset_count == 1 && mt_usb_bridge_snapshot().current == old + 1);
            assert(mt_usb_bridge_actor() == old); mt_usb_bridge_leave(&s); clean_end();
            report("reset_pending_inside_full_call", names[op], mask);
        }
        init(); enumeration(true); mt_usb_scope s;
        assert(mt_usb_bridge_app_enter(&s)); uint32_t old = mt_usb_bridge_actor();
        publish_reset(); assert(mt_usb_bridge_actor() == old); (void)invoke(op);
        assert(!mutations[op] && mt_usb_bridge_snapshot().faults == 0);
        assert(mt_usb_bridge_snapshot().io_failures == 0); mt_usb_bridge_leave(&s); clean_end();
        report("reset_before_guard_denied", names[op], 0);
    }
}
static void false_results(void) {
    const enum operation bool_ops[] = {XFER, FIFO, OPEN, ADDRESS};
    for (unsigned n = 0; n < sizeof(bool_ops)/sizeof(bool_ops[0]); ++n) {
        init(); enumeration(true); mt_usb_scope s; assert(mt_usb_bridge_app_enter(&s));
        submit_ok = false; assert(!invoke(bool_ops[n]));
        mt_usb_epoch state = mt_usb_bridge_snapshot();
        assert(state.faults == MT_USB_EPOCH_SUBMIT_FAILED && state.io_failures == 1);
        unsigned calls = mutations[bool_ops[n]]; assert(!invoke(bool_ops[n]));
        assert(mutations[bool_ops[n]] == calls && mt_usb_bridge_snapshot().io_failures == 1);
        mt_usb_bridge_leave(&s); clean_end(); report("false_result_sticky_once", names[bool_ops[n]], 0);
    }
    init(); enumeration(true); mt_usb_scope s; assert(mt_usb_bridge_app_enter(&s));
    report_address = false; assert(!invoke(ADDRESS)); assert(mt_usb_bridge_snapshot().io_failures == 1);
    mt_usb_bridge_leave(&s); clean_end(); report("missing_address_receipt_fails", "address", 0);
}
static void config_and_scope(void) {
    init(); enumeration(false); mt_usb_scope app, outer, nested;
    assert(!mt_usb_bridge_app_enter(&app));
    uint32_t token = mt_usb_bridge_snapshot().current;
    assert(mt_usb_bridge_dispatch(token, 0, &outer) == MT_USB_DISPATCH);
    assert(invoke(OPEN)); assert(mt_usb_bridge_config_begin(0, 1));
    assert(!mt_usb_bridge_app_enter(&app));
    mt_usb_bridge_config_done(1, true); assert(mt_usb_bridge_app_enter(&nested));
    assert(mt_usb_bridge_actor() == token); mt_usb_bridge_leave(&nested);
    assert(mt_usb_bridge_actor() == token); mt_usb_bridge_leave(&nested);
    assert(mt_usb_bridge_actor() == token); mt_usb_bridge_leave(&outer); clean_end();
    report("enum_before_app_config_and_nested_leave", "scope", 0);
    init(); enumeration(true); assert(mt_usb_bridge_app_enter(&outer)); token = mt_usb_bridge_actor();
    publish_reset(); assert(!mt_usb_bridge_app_enter(&nested));
    assert(mt_usb_bridge_actor() == token); mt_usb_bridge_leave(&nested);
    assert(!invoke(XFER)); assert(mt_usb_bridge_snapshot().faults == 0);
    mt_usb_bridge_leave(&outer); clean_end(); report("nested_old_actor_cannot_recapture", "scope", 0);
    init(); enumeration(false); token = mt_usb_bridge_snapshot().current;
    assert(mt_usb_bridge_dispatch(token, 0, &outer) == MT_USB_DISPATCH);
    assert(mt_usb_bridge_config_begin(0, 1)); mt_usb_bridge_config_done(1, false);
    assert(!mt_usb_bridge_snapshot().configured && mt_usb_bridge_snapshot().io_failures == 1);
    mt_usb_bridge_leave(&outer); assert(!mt_usb_bridge_app_enter(&app)); clean_end();
    report("config_status_false_no_receipt", "config", 0);
    init(); enumeration(true); assert(mt_usb_bridge_app_enter(&outer));
    assert(!mt_usb_bridge_config_begin(1, 0)); (void)invoke(CLOSE); (void)invoke(SOF);
    assert(!mutations[CLOSE] && !mutations[SOF]);
    assert(mt_usb_bridge_snapshot().faults == MT_USB_EPOCH_DECONFIGURED);
    assert(!mt_usb_bridge_config_begin(0, 1)); mt_usb_bridge_config_done(1, true);
    mt_usb_bridge_leave(&outer); assert(!mt_usb_bridge_app_enter(&app)); clean_end();
    report("unsupported_config0_poison_before_teardown", "config", 0);
    init(); enumeration(true); assert(mt_usb_bridge_app_enter(&outer));
    token = mt_usb_bridge_actor(); publish_reset(); mt_usb_bridge_config_done(1, true);
    assert(mt_usb_bridge_snapshot().configured == 0); mt_usb_bridge_leave(&outer); clean_end();
    report("reset_before_config_receipt", "config", 0);
}
static void pause_queue_completion(void) {
    init(); enumeration(true); mt_usb_scope s, denied;
    assert(mt_usb_bridge_app_enter(&s)); uint32_t token = mt_usb_bridge_actor();
    (void)ingress(DCD_EVENT_SUSPEND, 0, true);
    assert(tu_fifo_write_n(&fifo, bytes, sizeof(bytes)) == sizeof(bytes));
    assert(!mt_usb_bridge_fifo(0, 0x82, &fifo, 64, false));
    assert(tu_fifo_count(&fifo) == 64 && !mutations[FIFO]);
    mt_usb_bridge_leave(&s);
    assert(mt_usb_bridge_dispatch(token, 0, &denied) == MT_USB_DEFER);
    (void)ingress(DCD_EVENT_RESUME, 0, true);
    assert(mt_usb_bridge_app_enter(&s)); assert(mt_usb_bridge_fifo(0, 0x82, &fifo, 64, false));
    assert(!tu_fifo_count(&fifo)); mt_usb_bridge_leave(&s); clean_end();
    report("soft_pause_hwfifo_unconsumed_then_resume", "fifo", 0);
    for (unsigned result = XFER_RESULT_FAILED; result <= XFER_RESULT_INVALID; ++result) {
        init(); enumeration(true); (void)ingress(DCD_EVENT_XFER_COMPLETE, result, true);
        assert(mt_usb_bridge_snapshot().faults == MT_USB_EPOCH_COMPLETION_FAILED);
        assert(mt_usb_bridge_snapshot().io_failures == 1); assert(!mt_usb_bridge_app_enter(&s));
        clean_end(); report("non_success_completion_immediate_close", "completion", result);
    }
    init(); token = ingress(DCD_EVENT_BUS_RESET, 0, false); mt_usb_bridge_cleaned(token);
    assert(mt_usb_bridge_dispatch(token, 1, &s) == MT_USB_DROP);
    assert(mt_usb_bridge_snapshot().queue_losses == 1);
    token = ingress(DCD_EVENT_BUS_RESET, 0, true); mt_usb_bridge_cleaned(token);
    assert(mt_usb_bridge_dispatch(token, 1, &s) == MT_USB_DISPATCH);
    mt_usb_bridge_reset_done(token); mt_usb_bridge_leave(&s); clean_end();
    report("lost_reset_requires_fresh_reset", "queue", 0);
}
static void missing_lock_pair(void) {
    for (unsigned missing=0;missing<3;++missing) {
        init(); mt_usb_bridge_init(missing == 0 ? enter : NULL, missing == 1 ? leave : NULL);
        mt_usb_scope s; assert(!mt_usb_bridge_app_enter(&s));
        for(enum operation op=XFER;op<NOPS;++op) (void)invoke(op);
        for(unsigned op=0;op<NOPS;++op) assert(!mutations[op]);
        assert(!primask && !nesting && !lock_count && !unlock_count);
        report("missing_lock_pair_fail_closed", "lock", missing);
    }
}

int main(void) {
    all_guards(); false_results(); config_and_scope(); pause_queue_completion(); missing_lock_pair();
    return 0;
}
