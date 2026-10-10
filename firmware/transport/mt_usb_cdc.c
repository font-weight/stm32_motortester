#include "mt_usb_cdc.h"
#include "tusb.h"
#if CFG_TUD_CDC != 1 || CFG_TUD_CDC_TX_OVERWRITABLE_IF_NOT_CONNECTED != 0 || \
    CFG_TUD_CDC_RX_PERSISTENT != 0 || CFG_TUD_CDC_TX_PERSISTENT != 0
#error "USB adapter requires one non-overwritable, nonpersistent CDC interface"
#endif

static int valid(const mt_usb_cdc *a) {
    return a && a->rx && a->rx->control && a->stable;
}
static int usable(mt_usb_cdc *a) {
    return a->state == MT_USB_OPEN && a->stable(a->context) &&
        tud_cdc_n_ready(0) && (!a->require_dtr || tud_cdc_n_connected(0));
}
mt_control_result mt_usb_cdc_init(mt_usb_cdc *a, mt_control_rx *rx,
                                 int (*stable)(void *), void *context,
                                 unsigned require_dtr) {
    if (!a || !rx || !rx->control || !stable || require_dtr > 1u)
        return MT_CONTROL_ARGUMENT;
    *a = (mt_usb_cdc){0};
    a->rx = rx; a->stable = stable; a->context = context;
    a->require_dtr = require_dtr;
    return MT_CONTROL_OK;
}
mt_control_result mt_usb_cdc_open_epoch(mt_usb_cdc *a, uint32_t epoch) {
    if (!valid(a) || !epoch) return MT_CONTROL_ARGUMENT;
    if (a->state != MT_USB_CLOSED && epoch != a->epoch)
        return MT_CONTROL_ARGUMENT;
    if (!a->stable(a->context) || !tud_cdc_n_ready(0))
        return MT_CONTROL_IGNORED;
    if (a->rx_flush_pending) {
        tud_cdc_n_read_flush(0);
        if (!a->stable(a->context) || !tud_cdc_n_ready(0))
            return MT_CONTROL_IGNORED;
        a->rx_flush_pending = 0;
    }
    a->epoch = epoch; a->state = MT_USB_OPEN;
    return MT_CONTROL_OK;
}
int mt_usb_cdc_write(void *context, const uint8_t *bytes, size_t length) {
    mt_usb_cdc *a = context;
    if (!valid(a) || (!bytes && length) || length > MT_TX_CHUNK_MAX) return -1;
    if (!length || !usable(a)) return 0;
    /* A reset after the copy does not retroactively undo its accepted prefix.
     * TinyUSB may auto-flush here. Do not substitute flush/completion counts. */
    return (int)tud_cdc_n_write(0, bytes, (uint32_t)length);
}
mt_control_result mt_usb_cdc_step(mt_usb_cdc *a, uint32_t now_ms) {
    mt_control_result result;
    size_t consumed = 0;
    if (!valid(a)) return MT_CONTROL_ARGUMENT;
    result = mt_control_rx_poll(a->rx, now_ms);
    if (result == MT_CONTROL_RECOVERY || result == MT_CONTROL_ARGUMENT) return result;
    if (a->state != MT_USB_OPEN) return MT_CONTROL_IGNORED;
    if (usable(a)) {
        if (a->offset == a->length) {
            a->offset = 0;
            a->length = tud_cdc_n_read(0, a->carry, sizeof a->carry);
        }
        while (a->length > a->offset) {
            if (!usable(a)) return MT_CONTROL_IGNORED;
            result = mt_control_rx_feed(a->rx, a->carry + a->offset,
                                        1, now_ms, &consumed);
            a->offset += consumed;
            if (result == MT_CONTROL_RECOVERY || result == MT_CONTROL_ARGUMENT)
                return result;
        }
    }
    result = mt_control_poll(a->rx->control, now_ms, mt_usb_cdc_write, a);
    if (usable(a)) (void)tud_cdc_n_write_flush(0);
    return result;
}
mt_control_result mt_usb_cdc_soft_pause(mt_usb_cdc *a) {
    mt_control_result result;
    if (!valid(a)) return MT_CONTROL_ARGUMENT;
    if (a->state == MT_USB_QUARANTINED || a->state == MT_USB_CLOSED)
        return MT_CONTROL_IGNORED;
    a->state = MT_USB_SOFT_PAUSED;
    a->offset = a->length = 0;
    a->rx_flush_pending = 1;
    result = mt_control_rx_transport_lost(a->rx);
    /* Defer rearming until explicit open confirms a live stable endpoint. */
    return result;
}
mt_control_result mt_usb_cdc_hard_loss(mt_usb_cdc *a, uint32_t epoch) {
    mt_control_result result;
    if (!valid(a) || !epoch ||
        (epoch == a->epoch && a->state != MT_USB_QUARANTINED))
        return MT_CONTROL_ARGUMENT;
    a->state = MT_USB_QUARANTINED; a->epoch = epoch;
    a->offset = a->length = 0;
    a->rx_flush_pending = 0;
    result = mt_control_rx_transport_lost(a->rx);
    mt_tx_init(&a->rx->control->tx);
    (void)tud_cdc_n_write_clear(0);
    /* read_flush would rearm an endpoint which may belong to the dead epoch.
     * The bridge must cross reset/reopen (nonpersistent RX) before open_epoch. */
    return result;
}
