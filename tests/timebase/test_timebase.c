#include "../../firmware/platform/mt_timebase.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define CR 0x40021000u
#define CFGR 0x40021004u
#define RST 0x40021010u
#define ENR 0x4002101cu
#define BASE 0x40000000u
#define CR1 (BASE+0u)
#define DIER (BASE+12u)
#define EGR (BASE+20u)
#define CNT (BASE+36u)
#define PSC (BASE+40u)
#define ARR (BASE+44u)
typedef struct {
    uint32_t cr, cfgr, rst, enr, timer[12];
    uint32_t writes[32][2];
    unsigned count, no_stop, mutate_on_cnt, gate_loss_on_stop;
} model;
static model hw;
static mt_timebase clock_state;
static uint32_t *reg(uint32_t a) {
    if(a==CR) return &hw.cr;
    if(a==CFGR) return &hw.cfgr;
    if(a==RST) return &hw.rst;
    if(a==ENR) return &hw.enr;
    assert(a>=BASE && a<=ARR && (a&3u)==0);
    return &hw.timer[(a-BASE)/4u];
}
static uint32_t read32(void *ctx, uint32_t a) {
    (void)ctx;
    if(a==CNT && hw.mutate_on_cnt) hw.enr &= ~1u;
    return *reg(a);
}
static void write32(void *ctx, uint32_t a, uint32_t v) {
    (void)ctx;
    assert(a>=BASE && a<=ARR && hw.count<32u);
    hw.writes[hw.count][0]=a; hw.writes[hw.count++][1]=v;
    if(a==CR1 && v==0 && hw.gate_loss_on_stop) hw.enr &= ~1u;
    if(a==CR1 && v==0 && hw.no_stop) return;
    *reg(a)=v;
}
static void setup(void) {
    mt_timebase_io io={read32,write32,NULL};
    memset(&hw,0,sizeof hw);
    memset(&clock_state,0,sizeof clock_state); /* New simulated reset. */
    hw.cr=0x03030000u; hw.cfgr=0x001d840au; hw.enr=0x1021u;
    assert(mt_timebase_init(&clock_state,&io,72000000u));
    assert(mt_timebase_healthy(&clock_state));
    assert(hw.count==13u);
    assert(hw.writes[7][0]==PSC && hw.writes[7][1]==71u);
    assert(hw.writes[8][0]==ARR && hw.writes[8][1]==65535u);
    assert(hw.writes[9][0]==EGR && hw.writes[9][1]==1u);
    assert(hw.writes[11][0]==CNT && hw.writes[11][1]==0u);
    assert(hw.writes[12][0]==CR1 && hw.writes[12][1]==1u);
    assert(hw.enr==0x1021u && hw.cr==0x03030000u && hw.cfgr==0x001d840au);
}
static void advance(uint32_t ticks) { *reg(CNT)=(*reg(CNT)+ticks)&65535u; }
static void test_wrap_and_units(void) {
    uint64_t expected=0;
    unsigned i;
    setup();
    for(i=0;i<200000u;++i) {
        uint32_t delta=(i*997u)%32001u;
        advance(delta); expected+=delta;
        assert(mt_timebase_now_us(&clock_state)==(uint32_t)expected);
        assert(mt_timebase_now_ms(&clock_state)==(uint32_t)(expected/1000u));
        assert(clock_state.remainder_us==(uint32_t)(expected%1000u));
    }
    clock_state.us=UINT32_MAX-10u;
    clock_state.ms=UINT32_MAX;
    clock_state.remainder_us=999u;
    advance(21);
    assert(mt_timebase_now_us(&clock_state)==10u);
    assert(mt_timebase_now_ms(&clock_state)==0u && clock_state.remainder_us==20u);
}
static void test_preflight_and_faults(void) {
    mt_timebase_io io={read32,write32,NULL};
    mt_timebase other={0};
    unsigned count;
    setup(); count=hw.count;
    assert(!mt_timebase_init(&clock_state,&io,72000000u));
    assert(clock_state.owns_timer && mt_timebase_healthy(&clock_state) && hw.count==count);
    assert(!mt_timebase_init(&other,&io,72000000u));
    assert(hw.count==count && !other.owns_timer);
    assert(!mt_timebase_init(&other,&io,48000000u)); assert(hw.count==count);
    assert(mt_timebase_stop(&clock_state)); assert(!clock_state.owns_timer);
    *reg(DIER)=1; count=hw.count;
    assert(!mt_timebase_init(&other,&io,72000000u)); assert(hw.count==count);
    setup(); advance(32000); assert(mt_timebase_update(&clock_state));
    advance(32001); assert(!mt_timebase_update(&clock_state));
    assert(clock_state.fault==MT_TIMEBASE_SERVICE_FAULT);
    assert(mt_timebase_now_us(&clock_state)==32000u);
    setup(); *reg(PSC)=70; assert(!mt_timebase_update(&clock_state));
    assert(clock_state.fault==MT_TIMEBASE_TIMER_FAULT);
    setup(); hw.rst|=1; assert(!mt_timebase_update(&clock_state));
    assert(clock_state.fault==MT_TIMEBASE_CLOCK_FAULT);
    setup(); hw.mutate_on_cnt=1; advance(20);
    assert(!mt_timebase_update(&clock_state) && clock_state.us==0u);
    setup(); hw.no_stop=1; assert(!mt_timebase_stop(&clock_state));
    assert(clock_state.owns_timer && clock_state.fault==MT_TIMEBASE_STOP_FAULT);
    hw.no_stop=0; assert(mt_timebase_stop(&clock_state)); assert(!clock_state.owns_timer);
    setup(); hw.gate_loss_on_stop=1; assert(!mt_timebase_stop(&clock_state));
    assert(clock_state.owns_timer && clock_state.fault==MT_TIMEBASE_STOP_FAULT);
    hw.gate_loss_on_stop=0; hw.enr|=1; assert(mt_timebase_stop(&clock_state));
}
static void test_undetectable_missed_wrap(void) {
    setup(); advance(65536u+17u);
    /* Deliberately violated service precondition: CNT alone aliases the gap.
     * A passing read here is the LIMITATION, never evidence of robust detection. */
    assert(mt_timebase_update(&clock_state));
    assert(clock_state.us==17u && clock_state.ms==0u);
}
int main(void) {
    test_wrap_and_units(); test_preflight_and_faults(); test_undetectable_missed_wrap();
    puts("TIM2 clock model: units, wrap, preflight, sticky faults, Stop ownership passed; missed-wrap alias demonstrated");
    return 0;
}
