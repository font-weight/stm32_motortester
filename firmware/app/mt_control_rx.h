#ifndef MT_CONTROL_RX_H
#define MT_CONTROL_RX_H
#include "mt_control.h"
#include "../protocol/mt_stream.h"
#define MT_CONTROL_RX_CHUNK_MAX 64u
/* Fixed foreground-only storage, no IRQ/reentrancy. Controller must already be
 * initialized and remain alive. Objects and input/output ranges do not overlap. */
typedef struct {
    mt_control *control;
    mt_stream stream;
    mt_wire_message message;
    /* Local diagnostics only, saturating at UINT32_MAX; not wire counters. */
    uint32_t rejected_frames, overflows, timeouts;
} mt_control_rx;
mt_control_result mt_control_rx_init(mt_control_rx *rx, mt_control *control);
/* At most64 bytes. NULL bytes is permitted only for length0. On every valid
 * call consumed reports exactly the processed prefix. Recovery stops at its
 * triggering delimiter; caller retains the suffix and explicitly services it.
 * now_ms uses the controller/ADC binding's same true monotonic ms clock.
 * OK includes ignored commands and discarded nonfatal parser input; inspect
 * local diagnostics for decoder errors. It is not command acceptance.
 * A byte limit is NOT a proven execution-time/ADC service deadline bound. */
mt_control_result mt_control_rx_feed(mt_control_rx *rx, const uint8_t *bytes,
                                     size_t length, uint32_t now_ms,
                                     size_t *consumed);
/* Poll RX timeouts even while the transport is idle; does not poll TX/ADC. */
mt_control_result mt_control_rx_poll(mt_control_rx *rx, uint32_t now_ms);
/* Explicit transport loss: stop/recover control and reset partial RX framing.
 * Local diagnostic counters remain; controller retains any ordered partial TX.
 * This does not reset physical USB endpoints or prove successful hardware Stop. */
mt_control_result mt_control_rx_transport_lost(mt_control_rx *rx);
#endif
