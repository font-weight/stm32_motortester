#include "mt_usb_epoch.h"
#include <stddef.h>

static int current(const mt_usb_epoch *s, uint32_t token) {
    return s && token && token == s->current && !s->exhausted;
}
static void increment(uint32_t *value) {
    if (*value != UINT32_MAX) ++*value;
}
void mt_usb_epoch_init(mt_usb_epoch *s) {
    if (s) *s = (mt_usb_epoch){0};
}
static uint32_t next(mt_usb_epoch *s, unsigned reset) {
    if (!s || s->exhausted) return 0;
    if (s->current == UINT32_MAX) {
        s->exhausted = 1; s->faults |= MT_USB_EPOCH_EXHAUSTED;
        return 0;
    }
    ++s->current;
    s->queued_reset = s->processed_reset = s->cleaned = s->configured = 0;
    s->soft_serial = s->pending_soft = 0;
    s->reset_candidate = reset;
    s->faults = reset ? 0u : MT_USB_EPOCH_DISCONNECTED;
    return s->current;
}
uint32_t mt_usb_epoch_reset(mt_usb_epoch *s) { return next(s, 1); }
uint32_t mt_usb_epoch_disconnect(mt_usb_epoch *s) { return next(s, 0); }
int mt_usb_epoch_enqueued(mt_usb_epoch *s, uint32_t token,
                          unsigned is_reset, unsigned accepted) {
    if (!current(s, token) || is_reset > 1u || accepted > 1u) return 0;
    if (!accepted) {
        increment(&s->queue_losses);
        s->faults |= MT_USB_EPOCH_QUEUE_LOSS;
        return 0;
    }
    if (s->faults) return 0;
    if (is_reset) {
        if (!s->reset_candidate) return 0;
        s->queued_reset = token;
    }
    return 1;
}
int mt_usb_epoch_cleaned(mt_usb_epoch *s, uint32_t token) {
    if (!current(s, token)) return 0;
    s->cleaned = token;
    return 1;
}
int mt_usb_epoch_dispatch(const mt_usb_epoch *s, uint32_t token,
                          unsigned is_reset) {
    if (!current(s, token) || s->faults || is_reset > 1u ||
        s->cleaned != token) return 0;
    if (is_reset) return s->reset_candidate && s->queued_reset == token &&
        s->processed_reset != token;
    return s->processed_reset == token && !s->pending_soft;
}
int mt_usb_epoch_reset_done(mt_usb_epoch *s, uint32_t token) {
    if (!mt_usb_epoch_dispatch(s, token, 1)) return 0;
    s->processed_reset = token;
    return 1;
}
int mt_usb_epoch_core_io(const mt_usb_epoch *s, uint32_t token) {
    return mt_usb_epoch_dispatch(s, token, 0);
}
int mt_usb_epoch_configured(mt_usb_epoch *s, uint32_t token) {
    if (!mt_usb_epoch_core_io(s, token)) return 0;
    s->configured = token;
    return 1;
}
int mt_usb_epoch_app_io(const mt_usb_epoch *s, uint32_t token) {
    return mt_usb_epoch_core_io(s, token) && s->configured == token;
}
int mt_usb_epoch_fail(mt_usb_epoch *s, uint32_t token, uint32_t reason) {
    if (!current(s, token) ||
        (reason != MT_USB_EPOCH_SUBMIT_FAILED &&
         reason != MT_USB_EPOCH_COMPLETION_FAILED &&
         reason != MT_USB_EPOCH_DECONFIGURED &&
         reason != MT_USB_EPOCH_PMA_OVERRUN)) return 0;
    if (reason != MT_USB_EPOCH_DECONFIGURED) increment(&s->io_failures);
    s->faults |= reason;
    return 1;
}
uint32_t mt_usb_epoch_suspend(mt_usb_epoch *s, uint32_t token) {
    if (!current(s, token) || s->faults) return 0;
    if (s->soft_serial == UINT32_MAX) {
        s->faults |= MT_USB_EPOCH_SOFT_EXHAUSTED;
        return 0;
    }
    s->pending_soft = ++s->soft_serial;
    return s->pending_soft;
}
int mt_usb_epoch_resume(mt_usb_epoch *s, uint32_t token, uint32_t serial) {
    if (!current(s, token) || s->faults || !serial ||
        serial != s->pending_soft) return 0;
    s->pending_soft = 0;
    return 1;
}
