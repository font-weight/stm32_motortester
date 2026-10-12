#ifndef MT_USB_CDC_H
#define MT_USB_CDC_H
#include "../app/mt_control_rx.h"

typedef enum { MT_USB_CLOSED, MT_USB_OPEN, MT_USB_SOFT_PAUSED,
               MT_USB_QUARANTINED } mt_usb_cdc_state;
/* Foreground-only, one owner, no reentrancy. stable is a read-only observation
 * supplied by a separately proven lifecycle bridge, NOT implemented here.
 * It must be false once a reset/loss is pending. Never call controller APIs
 * from ISR. All referenced objects must outlive this object and not overlap. */
typedef struct {
    mt_control_rx *rx;
    int (*stable)(void *context);
    void *context;
    uint8_t carry[MT_CONTROL_RX_CHUNK_MAX];
    size_t offset, length;
    uint32_t epoch;
    mt_usb_cdc_state state;
    unsigned require_dtr;
    unsigned rx_flush_pending;
} mt_usb_cdc;
/* rx/control must already be initialized. Starts closed; does no USB I/O.
 * Do not reinitialize a live object: that would abandon its RX carry.
 * All operations except init require an initialized TinyUSB device stack. */
mt_control_result mt_usb_cdc_init(mt_usb_cdc *a, mt_control_rx *rx,
                                 int (*stable)(void *), void *context,
                                 unsigned require_dtr);
/* Explicit assertion by the bridge/test: endpoints configured and all stale
 * events crossed, buffers isolated. ready/mounted/DTR alone cannot prove it.
 * epoch is nonzero and identifies the physical-reset generation; sticky faults
 * may require quarantine again within that generation. Soft pause preserves it. */
mt_control_result mt_usb_cdc_open_epoch(mt_usb_cdc *a, uint32_t epoch);
int mt_usb_cdc_write(void *context, const uint8_t *bytes, size_t length);
/* At most64 RX bytes, one controller poll and one tail flush at most.
 * Does not service TinyUSB task/IRQ or ADC, or prove any execution deadline. */
mt_control_result mt_usb_cdc_step(mt_usb_cdc *a, uint32_t now_ms);
/* Both close the gate before recovery; failed hardware Stop remains recorded.
 * Soft loss preserves TX FIFO/partial frame; hard loss invalidates them.
 * TinyUSB must be initialized and these calls serialized with its task.
 * Hard loss must occur BEFORE dispatching stale completion events. */
mt_control_result mt_usb_cdc_soft_pause(mt_usb_cdc *a);
mt_control_result mt_usb_cdc_hard_loss(mt_usb_cdc *a, uint32_t new_epoch);
#endif
