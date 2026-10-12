#include "mt_usb_bridge.h"
#include <stddef.h>

static mt_usb_epoch state;
static mt_usb_lock_fn lock_fn;
static mt_usb_unlock_fn unlock_fn;
static uint32_t actor;
static unsigned address_pending, address_seen, address_accepted;

void mt_usb_bridge_init(mt_usb_lock_fn enter, mt_usb_unlock_fn leave) {
    /* Only before USB init, with no queued event or live operation. */
    lock_fn = enter && leave ? enter : NULL;
    unlock_fn = enter && leave ? leave : NULL;
    mt_usb_epoch_init(&state);
    actor = 0; address_pending = address_seen = address_accepted = 0;
}
uint32_t mt_usb_bridge_lock(void) { return lock_fn ? lock_fn() : 0; }
void mt_usb_bridge_unlock(uint32_t previous) { if (unlock_fn) unlock_fn(previous); }
static int permitted(void) {
    return lock_fn && unlock_fn && mt_usb_epoch_core_io(&state, actor);
}
static bool result(bool ok) {
    if (!ok) (void)mt_usb_epoch_fail(&state, actor, MT_USB_EPOCH_SUBMIT_FAILED);
    return ok;
}
uint32_t mt_usb_bridge_ingress(const dcd_event_t *event) {
    uint32_t token = state.current;
    if (event->event_id == DCD_EVENT_BUS_RESET) token = mt_usb_epoch_reset(&state);
    else if (event->event_id == DCD_EVENT_UNPLUGGED) token = mt_usb_epoch_disconnect(&state);
    else if (event->event_id == DCD_EVENT_SUSPEND) (void)mt_usb_epoch_suspend(&state, token);
    else if (event->event_id == DCD_EVENT_RESUME || event->event_id == DCD_EVENT_SOF)
        (void)mt_usb_epoch_resume(&state, token, state.pending_soft);
    else if (event->event_id == DCD_EVENT_XFER_COMPLETE &&
             event->xfer_complete.result != XFER_RESULT_SUCCESS)
        (void)mt_usb_epoch_fail(&state, token, MT_USB_EPOCH_COMPLETION_FAILED);
    return token;
}
void mt_usb_bridge_pma_overrun(void) {
    uint32_t key = mt_usb_bridge_lock();
    if (lock_fn && unlock_fn)
        (void)mt_usb_epoch_fail(&state, state.current, MT_USB_EPOCH_PMA_OVERRUN);
    mt_usb_bridge_unlock(key);
}
void mt_usb_bridge_enqueued(uint32_t token, unsigned reset, unsigned accepted) {
    (void)mt_usb_epoch_enqueued(&state, token, reset, accepted);
}
uint32_t mt_usb_bridge_actor(void) { return actor; }
mt_usb_epoch mt_usb_bridge_snapshot(void) {
    uint32_t key = mt_usb_bridge_lock();
    mt_usb_epoch copy = state;
    mt_usb_bridge_unlock(key); return copy;
}
void mt_usb_bridge_cleaned(uint32_t token) {
    uint32_t key = mt_usb_bridge_lock();
    (void)mt_usb_epoch_cleaned(&state, token);
    mt_usb_bridge_unlock(key);
}
unsigned mt_usb_bridge_dispatch(uint32_t token, unsigned reset, mt_usb_scope *scope) {
    unsigned verdict = MT_USB_DROP;
    uint32_t key = mt_usb_bridge_lock();
    *scope = (mt_usb_scope){0};
    if (lock_fn && unlock_fn && !actor && token && token == state.current &&
        !state.exhausted && !state.faults) {
        if (state.cleaned != token || (!reset && state.pending_soft)) verdict = MT_USB_DEFER;
        else if (mt_usb_epoch_dispatch(&state, token, reset)) {
            scope->previous = actor; scope->entered = 1;
            actor = token; verdict = MT_USB_DISPATCH;
        }
    }
    mt_usb_bridge_unlock(key); return verdict;
}
int mt_usb_bridge_app_enter(mt_usb_scope *scope) {
    uint32_t key = mt_usb_bridge_lock();
    uint32_t token = actor ? actor : state.current;
    *scope = (mt_usb_scope){0};
    if (lock_fn && unlock_fn && mt_usb_epoch_app_io(&state, token)) {
        scope->previous = actor; scope->entered = 1; actor = token;
    }
    mt_usb_bridge_unlock(key); return (int)scope->entered;
}
void mt_usb_bridge_leave(mt_usb_scope *scope) {
    uint32_t key = mt_usb_bridge_lock();
    if (scope->entered) { actor = scope->previous; scope->entered = 0; }
    mt_usb_bridge_unlock(key);
}
void mt_usb_bridge_reset_done(uint32_t token) {
    uint32_t key = mt_usb_bridge_lock();
    if (actor == token) (void)mt_usb_epoch_reset_done(&state, token);
    mt_usb_bridge_unlock(key);
}
int mt_usb_bridge_config_begin(uint8_t old_cfg, uint8_t new_cfg) {
    uint32_t key = mt_usb_bridge_lock();
    int ok = permitted();
    /* First iteration supports config1 enumeration and repeated config1 only.
     * Unsupported deconfiguration/reconfiguration poisons before teardown;
     * no close_all is falsely treated as a physical cancellation barrier. */
    if (ok && (new_cfg != 1u || (old_cfg != 0u && old_cfg != new_cfg))) {
        (void)mt_usb_epoch_fail(&state, actor, MT_USB_EPOCH_DECONFIGURED); ok = 0;
    }
    mt_usb_bridge_unlock(key); return ok;
}
void mt_usb_bridge_config_done(uint8_t cfg, bool status_submitted) {
    uint32_t key = mt_usb_bridge_lock();
    if (permitted()) {
        if (cfg == 1u && status_submitted) (void)mt_usb_epoch_configured(&state, actor);
        else (void)result(false);
    }
    mt_usb_bridge_unlock(key);
}
bool mt_usb_bridge_xfer(uint8_t r, uint8_t ep, uint8_t *buf, uint16_t n, bool is_isr) {
    uint32_t key = mt_usb_bridge_lock(); bool ok = false;
    if (permitted()) ok = result(dcd_edpt_xfer(r, ep, buf, n, is_isr));
    mt_usb_bridge_unlock(key); return ok;
}
bool mt_usb_bridge_fifo(uint8_t r, uint8_t ep, tu_fifo_t *fifo, uint16_t n, bool is_isr) {
    uint32_t key = mt_usb_bridge_lock(); bool ok = false;
    if (permitted()) ok = result(dcd_edpt_xfer_fifo(r, ep, fifo, n, is_isr));
    mt_usb_bridge_unlock(key); return ok;
}
bool mt_usb_bridge_open(uint8_t r, const tusb_desc_endpoint_t *ep) {
    uint32_t key = mt_usb_bridge_lock(); bool ok = false;
    if (permitted()) ok = result(dcd_edpt_open(r, ep));
    mt_usb_bridge_unlock(key); return ok;
}
#define GUARDED_VOID(call) do { uint32_t key = mt_usb_bridge_lock(); \
    if (permitted()) { call; } mt_usb_bridge_unlock(key); } while (0)
void mt_usb_bridge_stall(uint8_t r, uint8_t ep) { GUARDED_VOID(dcd_edpt_stall(r, ep)); }
void mt_usb_bridge_clear_stall(uint8_t r, uint8_t ep) { GUARDED_VOID(dcd_edpt_clear_stall(r, ep)); }
void mt_usb_bridge_status(uint8_t r, const tusb_control_request_t *req) {
    GUARDED_VOID(dcd_edpt0_status_complete(r, req));
}
void mt_usb_bridge_sof(uint8_t r, bool enabled) { GUARDED_VOID(dcd_sof_enable(r, enabled)); }
void mt_usb_bridge_close_all(uint8_t r) { GUARDED_VOID(dcd_edpt_close_all(r)); }
bool mt_usb_bridge_address(uint8_t r, uint8_t address) {
    uint32_t key = mt_usb_bridge_lock(); bool ok = false;
    if (permitted()) {
        address_pending = 1; address_seen = address_accepted = 0;
        dcd_set_address(r, address);
        address_pending = 0;
        ok = result(address_seen && address_accepted);
    }
    mt_usb_bridge_unlock(key); return ok;
}
void mt_usb_bridge_address_result(bool accepted) {
    /* Called by patched FSDEV inside the outer address guard. */
    if (address_pending) { address_seen = 1; address_accepted = accepted ? 1u : 0u; }
}
