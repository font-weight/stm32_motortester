#include "mt_usb_time.h"
#include <stddef.h>
static mt_timebase *bound_clock;
static uint32_t last_good_ms;
static unsigned active;
static mt_usb_time_state state;

static void fail(mt_usb_time_state cause) {
    if (state == MT_USB_TIME_UNBOUND || state == MT_USB_TIME_READY) state = cause;
}
int mt_usb_time_valid(void) {
    if (state == MT_USB_TIME_READY && !mt_timebase_healthy(bound_clock))
        fail(MT_USB_TIME_CLOCK_FAULT);
    return state == MT_USB_TIME_READY;
}
mt_usb_time_state mt_usb_time_status(void) {
    (void)mt_usb_time_valid();
    return state;
}
int mt_usb_time_bind(mt_timebase *clock) {
    uint32_t value;
    if (active) { fail(MT_USB_TIME_REENTRY); return 0; }
    if (state != MT_USB_TIME_UNBOUND) return 0;
    if (clock == NULL) { fail(MT_USB_TIME_ARGUMENT); return 0; }
    active = 1;
    bound_clock = clock;
    if (!mt_timebase_healthy(clock)) fail(MT_USB_TIME_CLOCK_FAULT);
    else {
        value = mt_timebase_now_ms(clock);
        if (!mt_timebase_healthy(clock)) fail(MT_USB_TIME_CLOCK_FAULT);
        else if (state == MT_USB_TIME_UNBOUND) {
            last_good_ms = value;
            state = MT_USB_TIME_READY;
        }
    }
    active = 0;
    return state == MT_USB_TIME_READY;
}
uint32_t tusb_time_millis_api(void) {
    uint32_t value;
    if (active) { fail(MT_USB_TIME_REENTRY); return last_good_ms; }
    if (state == MT_USB_TIME_UNBOUND) fail(MT_USB_TIME_EARLY_CALL);
    if (!mt_usb_time_valid()) return last_good_ms;
    active = 1;
    value = mt_timebase_now_ms(bound_clock);
    if (!mt_timebase_healthy(bound_clock)) fail(MT_USB_TIME_CLOCK_FAULT);
    if (state == MT_USB_TIME_READY) last_good_ms = value;
    active = 0;
    return last_good_ms;
}
