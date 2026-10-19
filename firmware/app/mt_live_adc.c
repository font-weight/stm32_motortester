#include "mt_live_adc.h"
#include <stddef.h>

static int valid(const mt_live_adc *b)
{
    return b && b->initialized && b->driver && b->driver->initialized &&
        b->driver->stream_mode && b->clock && b->device;
}
static int clock_ok(mt_live_adc *b)
{
    if (mt_timebase_update(b->clock)) return 1;
    mt_adc_dma_invalidate_stream_history(b->driver, MT_ADC_DMA_FAULT_CLOCK);
    return 0;
}
static uint16_t reason(mt_adc_dma_fault_t fault)
{
    switch (fault) {
    case MT_ADC_DMA_FAULT_NONE: return 0u;
    case MT_ADC_DMA_FAULT_CLOCK:
    case MT_ADC_DMA_FAULT_TIMEBASE: return 3u;
    case MT_ADC_DMA_FAULT_SEQUENCE_LIMIT: return 4u;
    case MT_ADC_DMA_FAULT_STOP: return 5u;
    default: return 2u;
    }
}
int mt_live_adc_init(mt_live_adc *b, mt_adc_dma_t *d, mt_timebase *clock,
                     mt_live_device *device, const mt_adc_dma_start_guard *guard)
{
    if (!b || !d || !clock || !device || !guard || !guard->enter ||
        !guard->leave || !guard->allowed || !guard->timed_allowed ||
        !mt_timebase_update(clock)) return 0;
    if (mt_adc_dma_enable_stream(d) != MT_ADC_DMA_OK) return 0;
    *b = (mt_live_adc){.driver=d, .clock=clock, .device=device,
                     .guard=*guard, .initialized=1u};
    return 1;
}
int mt_live_adc_start(void *context, uint32_t generation)
{
    mt_live_adc *b = context;
    mt_adc_dma_result_t result;
    if (!valid(b) || !generation || b->device->generation != generation ||
        b->device->state != MT_LIVE_STARTING || !clock_ok(b) ||
        !mt_adc_dma_profile1_ready(b->driver)) return 0;
    result = mt_adc_dma_start_guarded(b->driver, &b->guard);
    if (!clock_ok(b)) return 0;
    return result == MT_ADC_DMA_OK;
}
void mt_live_adc_quiesce(void *context, mt_live_snapshot *out)
{
    mt_live_adc *b = context;
    uint32_t i;
    int healthy;
    if (!out) return;
    *out = (mt_live_snapshot){0};
    if (!valid(b)) { out->acquisition_reason=5u; return; }
    healthy = clock_ok(b);
    (void)mt_adc_dma_stop_snapshot(b->driver, &b->stop_scratch);
    if (!clock_ok(b)) healthy=0;
    out->hardware_off = b->stop_scratch.hardware_off ? 1u : 0u;
    out->acquisition_reason = reason(b->driver->first_fault);
    /* The first adapter intentionally leaves wire produced_end unknown.
     * Completed DMA transfers do not prove the final ADC/request drain. */
    if (!healthy || !out->hardware_off || !b->stop_scratch.dma_end_known) return;
    for (i=0u; i<b->stop_scratch.count; ++i) {
        unsigned block = i / MT_ADC_DMA_BLOCK_SAMPLES;
        unsigned offset = i % MT_ADC_DMA_BLOCK_SAMPLES;
        out->tail[block].first_index = b->stop_scratch.first_sample_index +
            (uint64_t)block * MT_ADC_DMA_BLOCK_SAMPLES;
        out->tail[block].codes[offset] = b->stop_scratch.samples[i];
        out->tail[block].count = (uint16_t)(offset + 1u);
        out->tail_count = block + 1u;
    }
}
int mt_live_adc_service(mt_live_adc *b)
{
    mt_adc_dma_block_t block;
    mt_adc_dma_result_t result;
    uint16_t why;
    if (!valid(b)) return -2;
    if (b->device->state != MT_LIVE_STARTING && b->device->state != MT_LIVE_STREAMING)
        return 0;
    if (!clock_ok(b)) {
        why = reason(b->driver->first_fault);
        mt_live_device_fault(b->device, why ? why : 3u); return -1;
    }
    result = mt_adc_dma_read_block(b->driver, &block);
    if (!clock_ok(b)) {
        why = reason(b->driver->first_fault);
        mt_live_device_fault(b->device, why ? why : 3u); return -1;
    }
    if (result == MT_ADC_DMA_EMPTY) return 0;
    if (result != MT_ADC_DMA_BLOCK) {
        why = reason(b->driver->first_fault);
        mt_live_device_fault(b->device, why ? why : 2u);
        return -1;
    }
    if (!block.samples || block.count != MT_ADC_DMA_BLOCK_SAMPLES ||
        block.nominal_rate_hz != MT_ADC_DMA_RATE_HZ ||
        block.first_trigger_offset_us != MT_ADC_DMA_SAMPLE_US) {
        mt_live_device_fault(b->device, 2u); return -1;
    }
    return mt_live_device_offer(b->device, block.first_sample_index,
        block.samples, (uint16_t)block.count) ? 1 : -1;
}
