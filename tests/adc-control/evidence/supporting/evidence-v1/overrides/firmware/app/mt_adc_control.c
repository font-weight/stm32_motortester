#include "mt_adc_control.h"

static int canonical(const mt_adc_dma_t *d) {
    return d->clock.timer_hz == 72000000u && d->clock.timer_psc == 71u &&
           d->clock.timer_arr == 19u && d->clock.adc_hz == 12000000u;
}
static int profile(void *context) {
    mt_adc_control *b = context;
    return mt_adc_dma_profile1_ready(b->driver);
}
static int stop(void *context) {
    mt_adc_control *b = context;
    return mt_adc_dma_stop(b->driver) == MT_ADC_DMA_OK;
}
static int start(void *context, uint32_t command_ms) {
    mt_adc_control *b = context;
    mt_adc_dma_t *d = b->driver;
    uint32_t before_us;
    uint32_t delay;
    b->epoch_ms = b->now_ms(b->clock_context);
    if ((uint32_t)(b->epoch_ms - command_ms) >= UINT32_C(0x80000000)) return 0;
    before_us = d->io.now_us(d->io.user);
    if (mt_adc_dma_start(d) != MT_ADC_DMA_OK) return 0;
    delay = d->start_before_us - before_us;
    if (!canonical(d) || delay >= UINT32_C(0x80000000)) {
        (void)mt_adc_dma_stop(d);
        return 0;
    }
    /* Nominal lower edge of the CEN write bracket plus the first20us period.
     * Aperture and the correlation of clock reads are not measured accuracy. */
    b->first_sample_delay_us = delay + MT_ADC_DMA_SAMPLE_US;
    return 1;
}
mt_control_result mt_adc_control_init(mt_adc_control *b,
    mt_control *c, mt_adc_dma_t *d, uint32_t (*now_ms)(void *context),
    void *clock_context, uint32_t firmware_build) {
    mt_control_io io;
    if (!b || !c || !d || !now_ms || !d->initialized)
        return MT_CONTROL_ARGUMENT;
    b->control = c; b->driver = d; b->now_ms = now_ms;
    b->clock_context = clock_context; b->epoch_ms = 0;
    b->first_sample_delay_us = 0;
    io.profile_ready = profile; io.start = start; io.stop = stop;
    io.context = b; io.firmware_build = firmware_build;
    return mt_control_init(c, &io);
}
mt_control_result mt_adc_control_service(mt_adc_control *b) {
    mt_adc_dma_block_t block;
    mt_adc_dma_result_t result;
    uint32_t i;
    if (!b || !b->control || !b->driver || !b->now_ms)
        return MT_CONTROL_ARGUMENT;
    if (!b->control->ready || !b->control->hardware_running)
        return MT_CONTROL_IGNORED;
    result = mt_adc_dma_read_block(b->driver, &block);
    if (result == MT_ADC_DMA_EMPTY) return MT_CONTROL_OK;
    if (result != MT_ADC_DMA_BLOCK || !block.samples ||
        block.count != MT_ADC_DMA_BLOCK_SAMPLES ||
        block.nominal_rate_hz != MT_ADC_DMA_RATE_HZ ||
        block.first_trigger_offset_us != MT_ADC_DMA_SAMPLE_US)
        return mt_control_recover(b->control);
    for (i = 0; i < block.count; ++i) {
        uint32_t index = block.first_sample_index + i;
        uint32_t delay = b->first_sample_delay_us;
        /* Split division avoids a64-bit multiply/divide runtime dependency. */
        uint32_t sample_ms = b->epoch_ms + delay / 1000u + index / 50u +
            ((delay % 1000u + (index % 50u) * 20u) / 1000u);
        mt_control_result pushed = mt_control_sample(b->control, block.samples[i], sample_ms);
        if (pushed != MT_CONTROL_OK) return pushed;
        if (!b->control->hardware_running) break;
    }
    return MT_CONTROL_OK;
}
