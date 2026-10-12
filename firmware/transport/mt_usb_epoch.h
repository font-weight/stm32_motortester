#ifndef MT_USB_EPOCH_H
#define MT_USB_EPOCH_H
#include <stdint.h>

enum { MT_USB_EPOCH_QUEUE_LOSS = 1u, MT_USB_EPOCH_SUBMIT_FAILED = 2u,
       MT_USB_EPOCH_COMPLETION_FAILED = 4u, MT_USB_EPOCH_DECONFIGURED = 8u,
       MT_USB_EPOCH_DISCONNECTED = 16u, MT_USB_EPOCH_SOFT_EXHAUSTED = 32u,
       MT_USB_EPOCH_EXHAUSTED = 64u, MT_USB_EPOCH_PMA_OVERRUN = 128u };
typedef struct {
    uint32_t current, queued_reset, processed_reset, cleaned, configured;
    uint32_t soft_serial, pending_soft, faults;
    uint32_t queue_losses, io_failures;
    unsigned reset_candidate, exhausted;
} mt_usb_epoch;
/* Pure metadata only. Caller serializes EVERY access in a short, target-proven
 * critical section; no atomics/IRQ masking/MMIO or memory barriers are supplied.
 * Capture tokens at ingress/operation start, never relabel old work as current.
 * Do not reinitialize while any old token/queued event can still exist. */
void mt_usb_epoch_init(mt_usb_epoch *s);
/* Only a real observed physical BUS_RESET calls reset. Returns fresh nonzero
 * token before enqueue; 0 on permanent hard-counter exhaustion. */
uint32_t mt_usb_epoch_reset(mt_usb_epoch *s);
uint32_t mt_usb_epoch_disconnect(mt_usb_epoch *s);
/* Accepted reset must be recorded only after actual successful enqueue.
 * Stale outcomes cannot poison or reopen the new epoch. */
int mt_usb_epoch_enqueued(mt_usb_epoch *s, uint32_t token,
                          unsigned is_reset, unsigned accepted);
/* Foreground completed quarantine/Stop attempt, not proof hardware Stop worked. */
int mt_usb_epoch_cleaned(mt_usb_epoch *s, uint32_t token);
/* False during soft pause means DEFER current events, not drop them. Resume
 * must be established via producer metadata before consuming retained events.
 * A false return does not itself classify stale, poisoned or deferred events. */
int mt_usb_epoch_dispatch(const mt_usb_epoch *s, uint32_t token,
                          unsigned is_reset);
/* Call only AFTER the real reset handler completed successfully. */
int mt_usb_epoch_reset_done(mt_usb_epoch *s, uint32_t token);
/* Only after successful current-epoch SET_CONFIGURATION/CDC open/status. */
int mt_usb_epoch_configured(mt_usb_epoch *s, uint32_t token);
/* Core hardware-operation permit. A dispatch context token must be retained
 * until its DCD call; check and action require the caller's same guard. */
int mt_usb_epoch_core_io(const mt_usb_epoch *s, uint32_t token);
int mt_usb_epoch_app_io(const mt_usb_epoch *s, uint32_t token);
/* Only the four supplied current-epoch fault categories are accepted here. */
int mt_usb_epoch_fail(mt_usb_epoch *s, uint32_t token, uint32_t reason);
/* Soft loss does not advance hard epoch. Matching serial prevents a late ack
 * from clearing a newer suspend. Explicit ack does not prove physical resume. */
uint32_t mt_usb_epoch_suspend(mt_usb_epoch *s, uint32_t token);
int mt_usb_epoch_resume(mt_usb_epoch *s, uint32_t token, uint32_t serial);
#endif
