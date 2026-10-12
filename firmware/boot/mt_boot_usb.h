#ifndef MT_BOOT_USB_H
#define MT_BOOT_USB_H
#include "mt_clock_startup.h"
typedef enum {
    MT_BOOT_USB_OK, MT_BOOT_USB_ARGUMENT, MT_BOOT_USB_BUSY,
    MT_BOOT_USB_CLOCK, MT_BOOT_USB_NOT_RESET, MT_BOOT_USB_CONFLICT,
    MT_BOOT_USB_WAKE_PENDING, MT_BOOT_USB_PRIORITY, MT_BOOT_USB_GATE,
    MT_BOOT_USB_RESET_ASSERT, MT_BOOT_USB_RESET_RELEASE,
    MT_BOOT_USB_FINAL_STATE
} mt_boot_usb_result;
_Static_assert(sizeof(mt_clock_hse_mode) == 4u && sizeof(mt_clock_result) == 4u &&
    sizeof(mt_clock_recovery) == 4u && sizeof(mt_boot_usb_result) == 4u,
    "BOOT/CLK requires 32-bit enums; use -fno-short-enums");
typedef struct {
    mt_boot_usb_result cause;
    unsigned attempted, active, ready;
} mt_boot_usb;
/* Genuine-reset privileged foreground; caller keeps PRIMASK=1, no concurrent
 * MMIO writers, live USB/CAN, IRQ/STOP wake users, or reentry. Zero boot once;
 * startup is the unchanged successful CLK receipt, with verified board inputs.
 * Call before tusb_init. Same prompt aligned32 callbacks/context as CLK.
 * priority is an explicit logical 0..15, not a timing qualification.
 * Leaves IRQs disabled; does not initialize DCD/runtime/descriptors or admit
 * CPU interrupts. Valid attempts are one-shot until system reset, also on
 * failure. Preserve all state; no implicit cleanup, retry or physical attach.
 * Objects/context are nonoverlapping and remain alive for their consumers.
 */
mt_boot_usb_result mt_boot_usb_prepare(mt_boot_usb *boot,
    const mt_clock_startup *startup, uint32_t priority);
#endif
