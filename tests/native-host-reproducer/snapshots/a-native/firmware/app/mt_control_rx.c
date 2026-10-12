#include "mt_control_rx.h"

static void increment(uint32_t *counter) {
    if (*counter != UINT32_MAX) ++*counter;
}
static void observe(mt_control_rx *rx, mt_stream_event event) {
    if (event == MT_STREAM_REJECTED) increment(&rx->rejected_frames);
    else if (event == MT_STREAM_OVERFLOW) increment(&rx->overflows);
    else if (event == MT_STREAM_TIMEOUT) increment(&rx->timeouts);
}
mt_control_result mt_control_rx_init(mt_control_rx *rx, mt_control *control) {
    if (!rx || !control) return MT_CONTROL_ARGUMENT;
    *rx = (mt_control_rx){0};
    rx->control = control;
    mt_stream_init(&rx->stream);
    return MT_CONTROL_OK;
}
mt_control_result mt_control_rx_poll(mt_control_rx *rx, uint32_t now_ms) {
    if (!rx || !rx->control) return MT_CONTROL_ARGUMENT;
    observe(rx, mt_stream_poll(&rx->stream, now_ms));
    return MT_CONTROL_OK;
}
mt_control_result mt_control_rx_feed(mt_control_rx *rx, const uint8_t *bytes,
                                     size_t length, uint32_t now_ms,
                                     size_t *consumed) {
    size_t i;
    if (!consumed) return MT_CONTROL_ARGUMENT;
    *consumed = 0;
    if (!rx || !rx->control || (!bytes && length) || length > MT_CONTROL_RX_CHUNK_MAX)
        return MT_CONTROL_ARGUMENT;
    if (!length) return mt_control_rx_poll(rx, now_ms);
    for (i = 0; i < length; ++i) {
        mt_stream_event event = mt_stream_feed(&rx->stream, bytes[i], now_ms, &rx->message);
        *consumed = i + 1u;
        observe(rx, event);
        if (event == MT_STREAM_MESSAGE) {
            mt_control_result result = mt_control_receive(rx->control, &rx->message, now_ms);
            if (result == MT_CONTROL_RECOVERY || result == MT_CONTROL_ARGUMENT)
                return result;
        }
    }
    return MT_CONTROL_OK;
}
mt_control_result mt_control_rx_transport_lost(mt_control_rx *rx) {
    if (!rx || !rx->control) return MT_CONTROL_ARGUMENT;
    mt_stream_init(&rx->stream);
    return mt_control_recover(rx->control);
}
