#ifndef MT_USB_TIME_H
#define MT_USB_TIME_H
#include "mt_timebase.h"
typedef enum {
    MT_USB_TIME_UNBOUND, MT_USB_TIME_READY, MT_USB_TIME_ARGUMENT,
    MT_USB_TIME_CLOCK_FAULT, MT_USB_TIME_EARLY_CALL, MT_USB_TIME_REENTRY
} mt_usb_time_state;
_Static_assert(sizeof(mt_timebase_state) == 4u && sizeof(mt_timebase_fault) == 4u &&
    sizeof(mt_usb_time_state) == 4u, "TIME ABI requires -fno-short-enums");
/* One privileged foreground owner, no IRQ/concurrent calls. Bind once after
 * genuine reset to runtime's SAME initialized TIME. Object and MMIO context
 * outlive all consumers. No rebind, cleanup, epoch reset or independent clock.
 * Initial bind failure, early hook, or reentrant bind/hook latches invalidity
 * until system reset. Ordinary repeated bind returns 0, preserving binding. */
int mt_usb_time_bind(mt_timebase *clock);
/* Latched validity only, including TIME health; no fresh MMIO or clock update.
 * Check before AND after using the numeric hook. READY is not timing proof. */
int mt_usb_time_valid(void);
mt_usb_time_state mt_usb_time_status(void);
/* TinyUSB OS_NONE signature. Foreground only; returns last good ms on fault,
 * which is diagnostic, NOT valid time. Zero/UINT32_MAX are not error sentinels.
 * Never call weak tusb_time_delay_ms_api: it cannot handle invalid time or
 * service ADC. Pinned USB profile has no init/task/ISR hook callers. */
uint32_t tusb_time_millis_api(void);
#endif
