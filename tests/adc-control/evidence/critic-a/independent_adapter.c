/* Critic-A adapter oracle. Real adapter, fake adjacent APIs; no author fixture. */
#include "firmware/app/mt_adc_control.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
static mt_adc_control b;
static mt_control c;
static mt_adc_dma_t d;
static mt_adc_dma_block_t offered;
static mt_adc_dma_result_t read_result;
static uint16_t codes[128];
static uint32_t millis_value, micros_value, start_delay, pushed_count, freeze_after;
static uint32_t expected_first, expected_delay, expected_epoch;
static unsigned start_ok=1, canonical=1, stop_ok=1, recoveries, stops, starts, scenarios;
static mt_control_result push_result=MT_CONTROL_OK;
static uint32_t ms(void *p) { (void)p; return millis_value; }
static uint32_t us(void *p) { (void)p; return micros_value; }
mt_control_result mt_control_init(mt_control *x, const mt_control_io *io) {
 memset(x,0,sizeof *x); x->io=*io; return MT_CONTROL_OK;
}
int mt_adc_dma_profile1_ready(mt_adc_dma_t *x) { assert(x==&d); return (int)canonical; }
mt_adc_dma_result_t mt_adc_dma_stop(mt_adc_dma_t *x) {
 assert(x==&d); ++stops; return stop_ok?MT_ADC_DMA_OK:MT_ADC_DMA_ERROR;
}
mt_adc_dma_result_t mt_adc_dma_start(mt_adc_dma_t *x) {
 assert(x==&d); ++starts; x->start_before_us=micros_value+start_delay;
 x->clock.timer_hz=canonical?72000000u:48000000u;
 x->clock.timer_psc=canonical?71u:47u; x->clock.timer_arr=19;
 x->clock.adc_hz=12000000;return start_ok?MT_ADC_DMA_OK:MT_ADC_DMA_ERROR;
}
mt_adc_dma_result_t mt_adc_dma_read_block(mt_adc_dma_t *x,mt_adc_dma_block_t *out) {
 assert(x==&d);if(read_result==MT_ADC_DMA_BLOCK)*out=offered;return read_result;
}
mt_control_result mt_control_recover(mt_control *x) { assert(x==&c);++recoveries; x->ready=0;x->hardware_running=0;return MT_CONTROL_RECOVERY; }
mt_control_result mt_control_sample(mt_control *x,uint16_t code,uint32_t stamp) {
 uint64_t elapsed=(uint64_t)expected_delay+((uint64_t)expected_first+pushed_count)*20u;
 assert(x==&c);assert(pushed_count<freeze_after);assert(code==(uint16_t)(pushed_count^0xa55au));
 assert(stamp==expected_epoch+(uint32_t)(elapsed/1000u));++pushed_count;
 if(pushed_count==freeze_after){x->hardware_running=0;memset(codes,0,sizeof codes);}
 return push_result;
}
static void prepare(uint32_t epoch,uint32_t delay,uint32_t first) {
 memset(&d,0,sizeof d);d.initialized=1;d.io.now_us=us;
 assert(mt_adc_control_init(&b,&c,&d,ms,NULL,12)==MT_CONTROL_OK);
 c.ready=c.hardware_running=1;b.epoch_ms=epoch;b.first_sample_delay_us=delay;
 expected_epoch=epoch;expected_delay=delay;expected_first=first;pushed_count=0;freeze_after=128;push_result=MT_CONTROL_OK;
 for(unsigned i=0;i<128;++i)codes[i]=(uint16_t)(i^0xa55au);
 offered=(mt_adc_dma_block_t){codes,first,128,50000,0,0,20};read_result=MT_ADC_DMA_BLOCK;
}
int main(void) {
 uint32_t r=123456789;
 for(unsigned n=0;n<10000;++n){r=r*1664525u+1013904223u;uint32_t epoch=r;r=r*1664525u+1013904223u;uint32_t delay=r%1000020u;r=r*1664525u+1013904223u;uint32_t first=r&~127u;if(first>UINT32_MAX-128u)first-=128u;prepare(epoch,delay,first);assert(mt_adc_control_service(&b)==MT_CONTROL_OK);assert(pushed_count==128);++scenarios;}
 for(unsigned n=1;n<=128;++n){prepare(UINT32_MAX-2u,999,123456u);freeze_after=n;assert(mt_adc_control_service(&b)==MT_CONTROL_OK);assert(pushed_count==n);++scenarios;}
 for(unsigned n=0;n<5;++n){prepare(0,20,0);if(n==0)offered.samples=NULL;if(n==1)offered.count=127;if(n==2)offered.nominal_rate_hz=49999;if(n==3)offered.first_trigger_offset_us=0;if(n==4)read_result=MT_ADC_DMA_ERROR;unsigned before=recoveries;assert(mt_adc_control_service(&b)==MT_CONTROL_RECOVERY);assert(recoveries==before+1 && pushed_count==0);++scenarios;}
 prepare(0,20,0);read_result=MT_ADC_DMA_EMPTY;assert(mt_adc_control_service(&b)==MT_CONTROL_OK && pushed_count==0);++scenarios;
 prepare(0,20,0);push_result=MT_CONTROL_RECOVERY;assert(mt_adc_control_service(&b)==MT_CONTROL_RECOVERY && pushed_count==1);++scenarios;
 for(unsigned n=0;n<4;++n){prepare(0,20,0);millis_value=UINT32_MAX-1u;micros_value=UINT32_MAX-5u;start_delay=100u;start_ok=canonical=stop_ok=1;unsigned old_stops=stops;
 if(n==0){assert(c.io.start(c.io.context,millis_value));assert(b.epoch_ms==millis_value && b.first_sample_delay_us==120u);}
 if(n==1){start_ok=0;assert(!c.io.start(c.io.context,millis_value));}
 if(n==2){canonical=0;assert(!c.io.start(c.io.context,millis_value));assert(stops==old_stops+1);}
 if(n==3){start_delay=0x80000000u;assert(!c.io.start(c.io.context,millis_value));assert(stops==old_stops+1);}
 ++scenarios;}
 printf("independent adapter: %u scenarios; 1,280,000 full-block sample timestamps plus every 128 partial-stop boundary passed\n",scenarios);return 0;
}
