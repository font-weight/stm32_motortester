#include "mt_stream_diag.h"
#if !CFG_MT_STREAM_DIAGNOSTICS
#error "Compile this module only in an explicitly instrumented diagnostic profile"
#endif
#include <stddef.h>
#define DEMCR UINT32_C(0xe000edfc)
#define DWT_CTRL UINT32_C(0xe0001000)
#define DWT_CYCCNT UINT32_C(0xe0001004)
volatile mt_stream_diag_record mt_stream_diagnostics;
static uint32_t (*read_register)(void *,uint32_t);
static void *bus_context;
static uint32_t last_step, last_poll, last_raw_cycle;
static unsigned have_step, have_poll, have_raw_cycle;
static void publish_frozen(void) {
#if defined(__ARM_ARCH_7M__) && defined(__thumb__)
    __asm__ volatile("dmb sy" : : : "memory");
#else
    /* Native tests exercise ordering in C; target DMB is checked separately. */
    __asm__ volatile("" : : : "memory");
#endif
    mt_stream_diagnostics.phase=3u;
}
static int active(void) { return mt_stream_diagnostics.phase==2u; }
static void maximum(volatile uint32_t *value,uint32_t observed) {
    if(observed>*value)*value=observed;
}
static void increment(volatile uint32_t *value) {
    if(*value==UINT32_MAX)mt_stream_diagnostics.flags|=2u;
    else ++*value;
}
uint32_t mt_stream_diag_cycle(void) {
    uint32_t cycle,control;
    if(mt_stream_diagnostics.phase==3u)return mt_stream_diagnostics.freeze_cycle;
    if(!read_register)return 0u;
    control=read_register(bus_context,DWT_CTRL);
    if(!(read_register(bus_context,DEMCR)&(1u<<24)) || !(control&1u) || (control&(1u<<25)))
        mt_stream_diagnostics.flags|=16u;
    cycle=read_register(bus_context,DWT_CYCCNT);
    if(have_raw_cycle && cycle==last_raw_cycle)mt_stream_diagnostics.flags|=16u;
    last_raw_cycle=cycle; have_raw_cycle=1u;
    return cycle;
}
int mt_stream_diag_init(uint32_t (*read32)(void *,uint32_t),
    void (*write32)(void *,uint32_t,uint32_t),void *context,uint32_t build) {
    uint32_t first,last,i;
    if(!read32 || !write32 || mt_stream_diagnostics.phase)return 0;
    read_register=read32; bus_context=context;
    mt_stream_diagnostics=(mt_stream_diag_record){.magic=UINT32_C(0x4d544447),
        .version=1u,.bytes=sizeof(mt_stream_diag_record),.firmware_build=build,.phase=4u};
    /* Arm DWT: enable trace infrastructure preserving unrelated controls.
     * Do not reset CYCCNT or configure comparator/exception/debug-freeze state. */
    write32(context,DEMCR,read32(context,DEMCR)|(1u<<24));
    if(!(read32(context,DEMCR)&(1u<<24)) || (read32(context,DWT_CTRL)&(1u<<25)))goto bad;
    write32(context,DWT_CTRL,read32(context,DWT_CTRL)|1u);
    if(!(read32(context,DWT_CTRL)&1u))goto bad;
    first=mt_stream_diag_cycle();
    last=first;
    for(i=0u;i<32u && last==first;++i)last=mt_stream_diag_cycle();
    if(last==first || (mt_stream_diagnostics.flags&16u))goto bad;
    mt_stream_diagnostics.phase=1u; return 1;
bad:
    mt_stream_diagnostics.flags|=1u; return 0;
}
void mt_stream_diag_begin(uint32_t generation,uint64_t nonce) {
    if(mt_stream_diagnostics.phase!=1u || !generation)return;
    mt_stream_diagnostics.generation=generation;
    mt_stream_diagnostics.nonce_low=(uint32_t)nonce;
    mt_stream_diagnostics.nonce_high=(uint32_t)(nonce>>32);
    mt_stream_diagnostics.start_cycle=mt_stream_diag_cycle();
    mt_stream_diagnostics.phase=2u;
}
/* Caller has observed complete quiescence and replacement HELLO state.
 * Freeze before new HELLO commit clears diagnostics; do not mix later runs. */
void mt_stream_diag_replaced(uint32_t state,uint32_t epoch,uint32_t adc_state) {
    if(!active())return;
    mt_stream_diagnostics.flags|=8u;
    mt_stream_diagnostics.final_runtime_result=UINT32_MAX; /* Before step return. */
    mt_stream_diagnostics.final_core_state=state;
    mt_stream_diagnostics.final_epoch=epoch;
    mt_stream_diagnostics.final_adc_state=adc_state;
    mt_stream_diagnostics.final_adc_owned=0u;
    mt_stream_diagnostics.final_hardware_off=1u;
    mt_stream_diagnostics.last_cycle=mt_stream_diag_cycle();
    mt_stream_diagnostics.freeze_cycle=mt_stream_diagnostics.last_cycle;
    publish_frozen();
}
void mt_stream_diag_poll(uint32_t delta) {
    uint32_t cycle;
    if(!active())return;
    cycle=mt_stream_diag_cycle();
    increment(&mt_stream_diagnostics.poll_count);
    maximum(&mt_stream_diagnostics.driver_observed_poll_gap_us,delta);
    if(have_poll)maximum(&mt_stream_diagnostics.poll_gap_max_cycles,cycle-last_poll);
    last_poll=cycle; have_poll=1u;
}
void mt_stream_diag_copy(uint32_t delta) {
    if(!active())return;
    increment(&mt_stream_diagnostics.copy_count);
    maximum(&mt_stream_diagnostics.copy_max_us,delta);
}
void mt_stream_diag_tx(uint32_t begin) {
    uint32_t end;
    if(!active())return;
    end=mt_stream_diag_cycle();
    increment(&mt_stream_diagnostics.tx_count);
    maximum(&mt_stream_diagnostics.tx_max_cycles,end-begin);
}
void mt_stream_diag_note(uint32_t result,uint32_t epoch,uint32_t queue_used,
    uint32_t local_error,uint32_t reason,uint32_t adc_fault) {
    if(!active())return;
    maximum(&mt_stream_diagnostics.queue_high_water,queue_used);
    if(!(mt_stream_diagnostics.flags&4u) && (local_error || reason || adc_fault || (result!=UINT32_MAX && result>=3u))) {
        mt_stream_diagnostics.first_runtime_result=result;
        mt_stream_diagnostics.first_local_error=local_error;
        mt_stream_diagnostics.first_acquisition_reason=reason;
        mt_stream_diagnostics.first_adc_fault=adc_fault;
        mt_stream_diagnostics.first_epoch=epoch;
        mt_stream_diagnostics.flags|=4u;
    }
}
void mt_stream_diag_observe(uint32_t begin,uint32_t result,uint32_t state,
    uint32_t epoch,uint32_t adc_state,uint32_t adc_owned,uint32_t hardware_off,
    uint32_t queue_used,uint32_t local_error,uint32_t reason,uint32_t adc_fault,
    unsigned freeze_eligible) {
    uint32_t end;
    if(!active())return;
    end=mt_stream_diag_cycle();
    increment(&mt_stream_diagnostics.step_count);
    maximum(&mt_stream_diagnostics.step_body_max_cycles,end-begin);
    if(have_step)maximum(&mt_stream_diagnostics.step_gap_max_cycles,begin-last_step);
    last_step=begin; have_step=1u;
    mt_stream_diag_note(result,epoch,queue_used,local_error,reason,adc_fault);
    mt_stream_diagnostics.final_runtime_result=result;
    mt_stream_diagnostics.final_core_state=state;
    mt_stream_diagnostics.final_epoch=epoch;
    mt_stream_diagnostics.final_adc_state=adc_state;
    mt_stream_diagnostics.final_adc_owned=adc_owned;
    mt_stream_diagnostics.final_hardware_off=hardware_off;
    mt_stream_diagnostics.last_cycle=end;
    /* Caller supplies the complete actual quiescence predicate and terminal
     * protocol state. No stop, reset or TIME update is performed by telemetry. */
    if(freeze_eligible) {
        mt_stream_diagnostics.freeze_cycle=end;
        publish_frozen(); /* Last publication; read only afterward. */
    }
}
