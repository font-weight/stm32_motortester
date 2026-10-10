/* Critic-A supplemental transition oracle. Reviewed author and supporting scope;
 * this model/test was separately written and does not include their fixtures. */
#include "mt_timebase.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BASE UINT32_C(0x40000000)
#define RCC UINT32_C(0x40021000)
static unsigned long long cases, checks;
#define CHECK(x) do { ++checks; assert(x); } while (0)
typedef struct {
    uint32_t timer[12], rcc[8];
    uint32_t active_psc, phase;
    uint64_t elapsed, sampled;
    unsigned reads, writes, ug, delay, corrupt_cnt;
} bus;
static void advance(bus *b, uint32_t cycles) {
    if ((b->timer[0]&1u) && (b->rcc[7]&1u) && !(b->rcc[4]&1u)) {
        uint32_t sum=b->phase+cycles;
        uint32_t ticks=sum/(b->active_psc+1u);
        b->phase=sum%(b->active_psc+1u);
        b->elapsed+=ticks;
        b->timer[9]=(b->timer[9]+ticks)%(b->timer[11]+1u);
    }
}
static uint32_t read32(void *context, uint32_t address) {
    bus *b=context; uint32_t value;
    ++b->reads;
    advance(b,b->delay ? (uint32_t)((b->reads*37u)%719u) : 0u);
    if (address>=BASE && address<BASE+48u) {
        CHECK((address&3u)==0u); value=b->timer[(address-BASE)/4u];
        if (address==BASE+36u) { b->sampled=b->elapsed; if(b->corrupt_cnt) b->rcc[7]&=~1u; }
        return value;
    }
    CHECK(address==RCC || address==RCC+4u || address==RCC+16u || address==RCC+28u);
    return b->rcc[(address-RCC)/4u];
}
static void write32(void *context, uint32_t address, uint32_t value) {
    bus *b=context; ++b->writes;
    CHECK(address>=BASE && address<BASE+48u && (address&3u)==0u);
    b->timer[(address-BASE)/4u]=value;
    if(address==BASE+20u && (value&1u)) {
        b->active_psc=b->timer[10]; b->phase=0; b->timer[9]=0; ++b->ug;
    }
    if(address==BASE+36u) { b->elapsed=value; b->sampled=value; }
}
static void setup(bus *b, mt_timebase *t, unsigned delay) {
    mt_timebase_io io={read32,write32,b};
    memset(b,0,sizeof *b); memset(t,0,sizeof *t);
    b->rcc[0]=0x03030000u; b->rcc[1]=0x001d840au; b->rcc[7]=1u;
    b->delay=delay;
    CHECK(mt_timebase_init(t,&io,72000000u));
    CHECK(b->writes==13u && b->ug==1u && b->active_psc==71u);
    CHECK(t->us==(uint32_t)b->sampled);
}
static void seed(bus *b, mt_timebase *t, uint64_t epoch) {
    b->elapsed=epoch; b->sampled=epoch; b->timer[9]=(uint32_t)(epoch%65536u);
    b->phase=0; t->last_counter=(uint16_t)b->timer[9];
    t->us=(uint32_t)epoch; t->ms=(uint32_t)(epoch/1000u);
    t->remainder_us=(uint32_t)(epoch%1000u);
}
static void verify(bus *b, mt_timebase *t) {
    CHECK(mt_timebase_healthy(t));
    CHECK(t->us==(uint32_t)b->sampled);
    CHECK(t->ms==(uint32_t)(b->sampled/1000u));
    CHECK(t->remainder_us==(uint32_t)(b->sampled%1000u));
    CHECK(t->last_counter==(uint16_t)b->sampled);
    CHECK(b->writes==13u && b->ug==1u);
}
static void every_delta(void) {
    static const uint64_t epochs[]={0u,UINT64_C(65530),UINT64_C(4294967290),UINT64_C(4294967295990)};
    unsigned e; uint32_t delta;
    for(e=0;e<sizeof epochs/sizeof epochs[0];++e) for(delta=0;delta<65536u;++delta) {
        bus b; mt_timebase t; uint32_t old_us,old_ms,old_rem; uint16_t old_cnt;
        setup(&b,&t,0); seed(&b,&t,epochs[e]);
        old_us=t.us; old_ms=t.ms; old_rem=t.remainder_us; old_cnt=t.last_counter;
        advance(&b,delta*72u);
        if(delta<=32000u) { CHECK(mt_timebase_update(&t)); verify(&b,&t); }
        else {
            CHECK(!mt_timebase_update(&t)); CHECK(t.fault==MT_TIMEBASE_SERVICE_FAULT);
            CHECK(t.us==old_us && t.ms==old_ms && t.remainder_us==old_rem && t.last_counter==old_cnt);
            CHECK(mt_timebase_now_us(&t)==old_us && mt_timebase_now_ms(&t)==old_ms);
            CHECK(t.owns_timer && !mt_timebase_healthy(&t));
        }
        ++cases;
    }
}
static void progressing_reads(void) {
    bus b; mt_timebase t; unsigned i;
    setup(&b,&t,1);
    for(i=0;i<200000u;++i) {
        uint32_t result;
        advance(&b,(i%30000u)*72u);
        if(i%3u==0u) { result=mt_timebase_now_us(&t); CHECK(result==(uint32_t)b.sampled); }
        else if(i%3u==1u) { result=mt_timebase_now_ms(&t); CHECK(result==(uint32_t)(b.sampled/1000u)); }
        else CHECK(mt_timebase_update(&t));
        verify(&b,&t); ++cases;
    }
    CHECK(b.elapsed>UINT64_C(2500000000));
    CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer && !mt_timebase_healthy(&t));
    advance(&b,72000u); CHECK(b.elapsed>b.sampled); /* elapsed after final snapshot, before Stop, is allowed */
}
static void failure_atomicity(void) {
    static const uint64_t epochs[]={0u,UINT64_C(4294967295),UINT64_C(4294967295999)};
    unsigned i;
    for(i=0;i<sizeof epochs/sizeof epochs[0];++i) {
        bus b; mt_timebase t;
        setup(&b,&t,0); seed(&b,&t,epochs[i]); advance(&b,71u); CHECK(mt_timebase_update(&t)); verify(&b,&t);
        advance(&b,1u); CHECK(mt_timebase_update(&t)); verify(&b,&t);
        b.corrupt_cnt=1; advance(&b,7200u); CHECK(!mt_timebase_update(&t));
        CHECK(t.us==(uint32_t)(epochs[i]+1u)); CHECK(t.ms==(uint32_t)((epochs[i]+1u)/1000u));
        CHECK(t.fault==MT_TIMEBASE_CLOCK_FAULT && t.owns_timer);
        b.corrupt_cnt=0; b.rcc[7]=1; CHECK(!mt_timebase_update(&t));
        CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer);
        ++cases;
    }
}
int main(void) {
    every_delta(); progressing_reads(); failure_atomicity();
    printf("Critic-A timebase oracle: %llu scenarios, %llu checks passed\n",cases,checks);
    return 0;
}
