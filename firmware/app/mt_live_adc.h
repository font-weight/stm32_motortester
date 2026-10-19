#ifndef MT_LIVE_ADC_H
#define MT_LIVE_ADC_H
#include "../platform/adc_timer_dma.h"
#include "../platform/mt_timebase.h"
#include "../protocol/mt_live_device.h"

/* One static foreground owner. Driver, TIME, endpoint and guard context outlive
 * this binding. No legacy control, concurrent access, or live reinitialization.
 * stop_scratch avoids adding another544-byte local frame below the live core's
 * own snapshot. It is private mutable scratch, never a published sample view. */
typedef struct {
    mt_adc_dma_t *driver;
    mt_timebase *clock;
    mt_live_device *device;
    mt_adc_dma_start_guard guard;
    mt_adc_dma_stop_snapshot_t stop_scratch;
    unsigned initialized;
} mt_live_adc;
/* Fresh binding storage; driver initialized/stopped/unowned, TIME healthy.
 * Enables stream mode. Does not initialize the endpoint, own USB, enable gates
 * or certify physical off. A real epoch-preserving timed guard is mandatory.
 * The board owner must establish reserved ADC/TIM/DMA clocks before HELLO. */
int mt_live_adc_init(mt_live_adc *binding, mt_adc_dma_t *driver,
                     mt_timebase *clock, mt_live_device *device,
                     const mt_adc_dma_start_guard *guard);
/* Call through board-owner wrappers from matching mt_live_io callbacks. The
 * same binding must back both callbacks; no calls into the live core here. */
int mt_live_adc_start(void *binding, uint32_t generation);
void mt_live_adc_quiesce(void *binding, mt_live_snapshot *snapshot);
/* At most one real driver read and immediate synchronous queue offer. Return1
 * accepted,0 no block/inactive,-1 acquisition fault/queue rejection,-2 badarg.
 * A failed offer can synchronously quiesce: never use the old BLOCK view after.
 * Caller owns USB epoch admission and independent TIME/live timer servicing;
 * operation count alone does not prove the <2560us ADC service requirement. */
int mt_live_adc_service(mt_live_adc *binding);
#endif
