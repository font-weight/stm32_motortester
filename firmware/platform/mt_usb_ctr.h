#ifndef MT_USB_CTR_H
#define MT_USB_CTR_H
#include <stdbool.h>
/* Available only with the separate opt-in CFG_MT_USB_CTR_FAIRNESS profile.
 * Foreground calls require the same IRQ save/restore guard as the real DCD.
 * Rearm only after servicing ADC/TIME and draining queued USB events; preserve
 * lifecycle/epoch admission. One quantum can produce two CTR events. This API
 * does not certify WCET, prevent lifecycle storms, or open application admission.
 * Never clear pending CTR or reinitialize USB as a fairness shortcut. */
bool mt_usb_fsdev_ctr_deferred(void);
bool mt_usb_fsdev_ctr_rearm(void);
#endif
