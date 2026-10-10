#ifndef MT_ADC_CONTROL_H
#define MT_ADC_CONTROL_H
#include "mt_control.h"
#include "../platform/adc_timer_dma.h"

/* This binding owns no second sample buffer. Driver and controller must remain
 * alive even when hardware Stop fails. One foreground owner, no ISR calls. */
typedef struct {
    mt_control *control;
    mt_adc_dma_t *driver;
    uint32_t (*now_ms)(void *context);
    void *clock_context;
    uint32_t epoch_ms, first_sample_delay_us;
} mt_adc_control;
/* driver was initialized with MMIO/time callbacks and verified nominal HSE.
 * now_ms and driver's now_us describe the SAME monotonic clock; each read has
 * its documented quantization. now_ms must be a true wrapping32-bit millisecond
 * clock, not a wrapping32-bit microsecond counter divided by1000.
 * init resets software control only; hardware must be quiescent before rebinding.
 */
mt_control_result mt_adc_control_init(mt_adc_control *binding,
    mt_control *control, mt_adc_dma_t *driver,
    uint32_t (*now_ms)(void *context), void *clock_context,
    uint32_t firmware_build);
/* Call frequently enough to meet driver <2560us service budget, even with USB
 * backpressure. Reads at most one128-code block, no output write or busy wait.
 * Stop consuming the snapshot immediately when the controller freezes/stops it.
 * Other receive/poll calls remain mt_control APIs on the bound controller. */
mt_control_result mt_adc_control_service(mt_adc_control *binding);
#endif
