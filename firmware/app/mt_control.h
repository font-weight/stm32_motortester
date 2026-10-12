#ifndef MT_CONTROL_H
#define MT_CONTROL_H
#include "mt_capture_link.h"
#include "../protocol/mt_session.h"

/* Hooks are synchronous, bounded and serialized. Return 1 only on confirmed
 * success. profile_ready proves nominal TIM=72MHz/PSC71/ARR19, ADC12MHz/71.5
 * cycles, analog PA0 and exclusive ownership; it is not measured accuracy.
 * stop must be retryable after failure and must leave DMA storage alive. */
typedef struct {
    int (*profile_ready)(void *context);
    int (*start)(void *context, uint32_t now_ms);
    int (*stop)(void *context);
    void *context;
    uint32_t firmware_build;
} mt_control_io;
typedef enum {
    MT_CONTROL_OK = 0, MT_CONTROL_IGNORED, MT_CONTROL_RECOVERY,
    MT_CONTROL_ARGUMENT
} mt_control_result;
/* Bounded pending state changes; not full 544-byte messages. */
typedef struct {
    uint32_t generation, window, dropped;
    uint8_t state, reason;
} mt_control_status;
#define MT_CONTROL_STATUS_MAX 8u
typedef struct {
    mt_session session;
    mt_tx tx;
    mt_capture_link link;
    mt_control_io io;
    mt_control_status states[MT_CONTROL_STATUS_MAX];
    unsigned state_count;
    uint8_t last_state;
    unsigned ready, hardware_running;
} mt_control;
/* Initialization performs no hardware access. A validated HELLO first stops
 * any previous hardware, validates the canonical profile and negotiates TX. */
mt_control_result mt_control_init(mt_control *control, const mt_control_io *io);
/* Input MUST have passed mt_wire_decode and must not alias control. The caller
 * owns stream framing/timeouts and calls recover on any upstream fatal fault. */
mt_control_result mt_control_receive(mt_control *control,
                                     const mt_wire_message *message,
                                     uint32_t now_ms);
mt_control_result mt_control_sample(mt_control *control, uint16_t code,
                                    uint32_t sample_ms);
mt_control_result mt_control_poll(mt_control *control, uint32_t now_ms,
                                  mt_tx_write_fn write, void *context);
/* Genuine active ADC TRANSFER/DEADLINE only, mapped to reason1/2. Confirms
 * Stop again before entering serviceable ERROR; failed Stop/admission falls
 * back to recovery. Caller/runtime owns live time/transport validation. */
mt_control_result mt_control_acquisition_fault(mt_control *control,
                                               uint8_t reason, uint32_t now_ms);
/* Stops sampling and disables ordinary commands/output until a fresh HELLO.
 * Does not discard a partially accepted frame; HELLO drains it via mt_tx. */
mt_control_result mt_control_recover(mt_control *control);
#endif
