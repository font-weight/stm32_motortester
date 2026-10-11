/* Critic-A boundary checks. Reuses disclosed supporting MMIO/guard fixture;
 * fixture's main is renamed only, and no supporting assertion is removed. */
#include "test_admission_fixture.c"
static unsigned span;
static uint32_t boundary_now(void *context) {
    uint32_t value=observed_now(context);
    if (guard_state.depth && guard_state.guarded_now==1u)
        ((fake_t *)context)->tick=value+span;
    return value;
}
int main(void) {
    unsigned count=0;
    const unsigned spans[]={0,1,19,20,21};
    for(unsigned mask=0;mask<2;++mask) {
        for(unsigned wrap=0;wrap<2;++wrap) {
            for(unsigned i=0;i<sizeof(spans)/sizeof(spans[0]);++i) {
                reset_model(mask,INJ_NONE); span=spans[i];
                if(wrap)hw.tick=UINT32_MAX-10u;
                driver.io.now_us=boundary_now;
                mt_adc_dma_start_guard g=model_guard();
                mt_adc_dma_result_t result=mt_adc_dma_start_guarded(&driver,&g);
                assert(result==(span<=20u?MT_ADC_DMA_OK:MT_ADC_DMA_ERROR));
                returned(mask,2u);
                assert((uint32_t)(driver.start_after_us-driver.start_before_us)==span);
                if(span<=20u) {
                    assert(driver.state==MT_ADC_DMA_RUNNING && driver.owns_peripherals);
                    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
                } else check_released(MT_ADC_DMA_FAULT_TIMEBASE);
                if(wrap && span>=19u)assert(driver.start_after_us<driver.start_before_us);
                ++count;
            }
        }
    }
    printf("Critic-A: %u exact commit-span/mask/wrap cases passed; model only\n",count);
    return 0;
}
