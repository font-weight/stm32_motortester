#include "../../firmware/transport/mt_usb_epoch.h"
#include <assert.h>
#include <stdio.h>

static uint32_t reset_ready(mt_usb_epoch *s) {
    uint32_t e = mt_usb_epoch_reset(s);
    assert(e);
    assert(!mt_usb_epoch_app_io(s, e));
    assert(!mt_usb_epoch_configured(s, e));
    assert(mt_usb_epoch_cleaned(s, e));
    assert(!mt_usb_epoch_reset_done(s, e));
    assert(mt_usb_epoch_enqueued(s, e, 1, 1));
    assert(mt_usb_epoch_dispatch(s, e, 1));
    assert(mt_usb_epoch_reset_done(s, e));
    assert(!mt_usb_epoch_reset_done(s, e));
    assert(!mt_usb_epoch_dispatch(s, e, 1));
    assert(mt_usb_epoch_core_io(s, e));
    assert(mt_usb_epoch_configured(s, e));
    assert(mt_usb_epoch_app_io(s, e));
    return e;
}
int main(void) {
    mt_usb_epoch s;
    uint32_t e, old, a, b;
    mt_usb_epoch_init(&s);
    assert(!mt_usb_epoch_app_io(&s, 0));
    e = reset_ready(&s);
    a = mt_usb_epoch_suspend(&s, e);
    b = mt_usb_epoch_suspend(&s, e);
    assert(a && b && a != b);
    assert(!mt_usb_epoch_app_io(&s, e));
    assert(!mt_usb_epoch_resume(&s, e, a));
    assert(mt_usb_epoch_resume(&s, e, b));
    assert(mt_usb_epoch_app_io(&s, e));
    assert(!mt_usb_epoch_enqueued(&s, e, 0, 0));
    assert(!mt_usb_epoch_app_io(&s, e));
    assert(!mt_usb_epoch_configured(&s, e));
    assert(!mt_usb_epoch_enqueued(&s, e, 1, 1));
    old = e; e = reset_ready(&s);
    assert(e != old);
    assert(!mt_usb_epoch_enqueued(&s, old, 0, 0));
    assert(!mt_usb_epoch_fail(&s, old, MT_USB_EPOCH_SUBMIT_FAILED));
    assert(mt_usb_epoch_app_io(&s, e));
    assert(s.queue_losses == 1 && s.io_failures == 0);
    assert(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_SUBMIT_FAILED));
    assert(!mt_usb_epoch_app_io(&s, e));
    e = reset_ready(&s);
    s.soft_serial = UINT32_MAX;
    assert(!mt_usb_epoch_suspend(&s, e));
    assert(!mt_usb_epoch_core_io(&s, e));
    e = reset_ready(&s);
    s.queue_losses = UINT32_MAX;
    assert(!mt_usb_epoch_enqueued(&s, e, 0, 0));
    assert(s.queue_losses == UINT32_MAX);
    e = mt_usb_epoch_disconnect(&s);
    assert(e && mt_usb_epoch_cleaned(&s, e));
    assert(!mt_usb_epoch_enqueued(&s, e, 1, 1));
    assert(!mt_usb_epoch_app_io(&s, e));
    (void)reset_ready(&s);
    s.current = UINT32_MAX;
    assert(!mt_usb_epoch_reset(&s));
    assert(s.exhausted && !mt_usb_epoch_app_io(&s, UINT32_MAX));
    assert(!mt_usb_epoch_reset(&s) && !mt_usb_epoch_disconnect(&s));
    assert(!mt_usb_epoch_reset(NULL));
    puts("Epoch model: order, poison, stale tokens, soft acknowledgment and saturation passed");
    return 0;
}
