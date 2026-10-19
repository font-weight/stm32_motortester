/* Isolated production mt_adc_control service tests. Explicit seam doubles
 * inject metadata the production driver's legacy limit normally precludes.
 * This demonstrates the checked narrowing itself, not a hardware pathway. */
#include "mt_adc_control.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static mt_adc_dma_block_t supplied;
static unsigned samples_seen, recoveries;
static uint32_t sample_times[128];
static uint16_t codes[128];
static uint32_t ms(void *context) { (void)context; return 0u; }
static uint32_t enter(void *context) { (void)context; return 0u; }
static int allowed(void *context) { (void)context; return 1; }
static void leave(void *context,uint32_t key) { (void)context;(void)key; }
static int pair(void *context,uint32_t *us,uint32_t *stamp) { (void)context; *us=0u;*stamp=0u;return 1; }

int mt_adc_dma_profile1_ready(mt_adc_dma_t *d) { (void)d; return 1; }
mt_adc_dma_result_t mt_adc_dma_stop(mt_adc_dma_t *d) { (void)d; return MT_ADC_DMA_OK; }
mt_adc_dma_result_t mt_adc_dma_start(mt_adc_dma_t *d) { (void)d; return MT_ADC_DMA_OK; }
mt_adc_dma_result_t mt_adc_dma_start_guarded(mt_adc_dma_t *d,const mt_adc_dma_start_guard *g) { (void)d;(void)g;return MT_ADC_DMA_OK; }
mt_adc_dma_result_t mt_adc_dma_read_block(mt_adc_dma_t *d,mt_adc_dma_block_t *b) { (void)d; *b=supplied;return MT_ADC_DMA_BLOCK; }
mt_control_result mt_control_init(mt_control *c,const mt_control_io *io) { memset(c,0,sizeof(*c));c->io=*io;return MT_CONTROL_OK; }
mt_control_result mt_control_recover(mt_control *c) { (void)c; ++recoveries;return MT_CONTROL_RECOVERY; }
mt_control_result mt_control_acquisition_fault(mt_control *c,uint8_t reason,uint32_t now) { (void)c;(void)reason;(void)now;assert(0);return MT_CONTROL_RECOVERY; }
mt_control_result mt_control_sample(mt_control *c,uint16_t code,uint32_t stamp) { (void)c;assert(code==321u);assert(samples_seen<128u);sample_times[samples_seen++]=stamp;return MT_CONTROL_OK; }

static void run_index(uint64_t first,bool valid_index) {
    mt_adc_dma_t d={0}; mt_adc_control b; mt_control c;
    unsigned i;
    d.initialized=true;
    assert(mt_adc_control_init(&b,&c,&d,ms,NULL,0u)==MT_CONTROL_OK);
    c.ready=1u; c.hardware_running=1u;
    b.epoch_ms=UINT32_C(0xfffffff0); b.first_sample_delay_us=123u;
    for(i=0;i<128u;++i) codes[i]=321u;
    supplied=(mt_adc_dma_block_t){codes,first,128u,50000u,0u,0u,20u};
    samples_seen=0u; recoveries=0u;
    assert(mt_adc_control_service(&b)==(valid_index?MT_CONTROL_OK:MT_CONTROL_RECOVERY));
    if(!valid_index) { assert(samples_seen==0u && recoveries==1u);return; }
    assert(samples_seen==128u && recoveries==0u);
    for(i=0;i<128u;++i) {
        /* Independent u64 nominal-time expression, then protocol ms32 wrap. */
        uint32_t expected=b.epoch_ms+(uint32_t)((UINT64_C(123)+(first+i)*20u)/1000u);
        assert(sample_times[i]==expected);
    }
}
int main(void) {
    mt_adc_dma_t d={0}; mt_adc_control b; mt_control c;
    mt_adc_dma_start_guard g={enter,allowed,leave,NULL,NULL};
    d.initialized=true; d.stream_mode=true;
    assert(mt_adc_control_init(&b,&c,&d,ms,NULL,0u)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init_guarded(&b,&c,&d,ms,NULL,0u,&g)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init_coherent_guarded(&b,&c,&d,ms,NULL,0u,&g,pair)==MT_CONTROL_ARGUMENT);
    run_index(0u,true);
    run_index(UINT64_C(0xffffff00),true);
    run_index(UINT64_C(0xffffff80),true); /* Last entire u32-representable block. */
    run_index(UINT64_C(0xffffff81),false);
    run_index(UINT64_C(0x100000000),false);
    run_index(UINT64_MAX-UINT64_C(127),false);
    run_index(UINT64_MAX,false);
    puts("PASS isolated legacy checked narrowing and stream-mode rejection");
    return 0;
}
