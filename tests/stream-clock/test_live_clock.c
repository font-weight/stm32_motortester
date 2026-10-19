/* Independent wrapper assertions reuse the repository's real TIME register
 * fixture, not a fake snapshot. See README.md and source-manifest.json. */
#define MT_COHERENT_CLOCK_MAIN historical_coherent_suite
#include "test_snapshot.c"
#undef MT_COHERENT_CLOCK_MAIN
#include "mt_live_clock.h"
#include "mt_live_device.h"
#include <inttypes.h>
#include <stdlib.h>

static mt_live_clock live;
static unsigned scenarios;
static const struct { uint32_t address, broken; } faults[] = {
    {ENR, 0u}, {RST, 1u}, {CR, UINT32_C(0x03030001)},
    {CFGR, UINT32_C(0x001d840b)}, {CR1, 0u}, {BASE+4u, 1u},
    {BASE+8u, 1u}, {DIER, 1u}, {BASE+32u, 1u}, {BASE+24u, 1u},
    {BASE+28u, 1u}, {PSC, 70u}, {ARR, 65534u}
};
static void prepare_live(uint64_t elapsed) {
    prepare_snapshot(); seed_time(elapsed);
    memset(&live, 0xa5, sizeof live); /* Fresh storage need not be zeroed. */
    unsigned reads=snapshot_reads, counts=snapshot_cnt_reads, writes=hw.count;
    assert(mt_live_clock_init(&live, &clock_state));
    assert(snapshot_reads-reads==27u && snapshot_cnt_reads-counts==1u);
    assert(hw.count==writes && live.source==&clock_state && !live.failed);
    assert(live.milliseconds==(uint32_t)(elapsed/1000u));
    assert(live.last_ms==clock_state.ms && live.last_us==clock_state.us);
}
static void frozen_live_payload(const mt_live_clock *old) {
    assert(live.source==old->source && live.last_ms==old->last_ms);
    assert(live.last_us==old->last_us && live.milliseconds==old->milliseconds);
}
static void invalid_sticky(const mt_live_clock *old) {
    assert(mt_live_clock_now(&live)==UINT64_MAX);
    assert(live.failed); frozen_live_payload(old);
    unsigned reads=snapshot_reads, writes=hw.count;
    advance(1u);
    assert(mt_live_clock_now(&live)==UINT64_MAX);
    assert(snapshot_reads==reads && hw.count==writes);
    frozen_live_payload(old);
}
static void exact_now(uint64_t expected) {
    unsigned reads=snapshot_reads, counts=snapshot_cnt_reads, writes=hw.count;
    assert(mt_live_clock_now(&live)==expected);
    assert(!live.failed && mt_timebase_healthy(&clock_state));
    assert(snapshot_reads-reads==27u && snapshot_cnt_reads-counts==1u);
    assert(hw.count==writes && live.last_ms==clock_state.ms);
    assert(live.last_us==clock_state.us && live.milliseconds==expected);
}
static void other_client_advance(uint32_t ticks, unsigned mode) {
    while(ticks) {
        uint32_t step=ticks>10000u?10000u:ticks;
        advance(step); ticks-=step;
        if(mode%3u==0u)(void)mt_timebase_now_us(&clock_state);
        else if(mode%3u==1u)(void)mt_timebase_now_ms(&clock_state);
        else { uint32_t us,ms; assert(mt_timebase_snapshot(&clock_state,&us,&ms)); }
        assert(mt_timebase_healthy(&clock_state));
    }
}
static void test_initialization(void) {
    mt_timebase empty={0};
    prepare_snapshot();
    unsigned reads=snapshot_reads, writes=hw.count;
    assert(!mt_live_clock_init(NULL,&clock_state));
    assert(mt_live_clock_now(NULL)==UINT64_MAX);
    assert(snapshot_reads==reads && hw.count==writes);
    assert(!mt_live_clock_init(&live,NULL));
    assert(live.failed && mt_live_clock_now(&live)==UINT64_MAX);
    assert(!mt_live_clock_init(&live,&empty));
    assert(live.failed && mt_live_clock_now(&live)==UINT64_MAX);
    assert(mt_timebase_stop(&clock_state));
    assert(!mt_live_clock_init(&live,&clock_state));
    assert(live.failed && mt_live_clock_now(&live)==UINT64_MAX);
    prepare_snapshot(); advance(12345u); writes=hw.count;
    assert(mt_live_clock_init(&live,&clock_state));
    assert(live.milliseconds==12u && live.last_us==12345u);
    assert(snapshot_reads==27u && snapshot_cnt_reads==1u && hw.count==writes);
    exact_now(12u); advance(655u); exact_now(13u);
    scenarios+=5u;
    puts("PASS initialization: fresh observation, poisoned fresh storage, null/uninitialized/stopped source");
}
static void test_fault_matrix(void) {
    for(unsigned initialization=0;initialization<2u;++initialization)
    for(unsigned post=0;post<2u;++post)
    for(unsigned i=0;i<sizeof faults/sizeof faults[0];++i) {
        prepare_live(999u); advance(33u);
        mt_live_clock old=live;
        uint32_t original=*reg(faults[i].address);
        if(post){mutate_address=faults[i].address;mutate_value=faults[i].broken;}
        else *reg(faults[i].address)=faults[i].broken;
        unsigned reads=snapshot_reads, counts=snapshot_cnt_reads,writes=hw.count;
        if(initialization) {
            mt_live_clock fresh;
            assert(!mt_live_clock_init(&fresh,&clock_state));
            assert(fresh.failed && fresh.source==&clock_state);
            assert(fresh.milliseconds==0u && fresh.last_ms==0u && fresh.last_us==0u);
            assert(mt_live_clock_now(&fresh)==UINT64_MAX);
        } else invalid_sticky(&old);
        assert(snapshot_cnt_reads-counts==post && snapshot_reads>reads);
        assert(hw.count==writes && clock_state.us==999u && clock_state.ms==0u);
        assert(clock_state.fault==(i<4u?MT_TIMEBASE_CLOCK_FAULT:MT_TIMEBASE_TIMER_FAULT));
        *reg(faults[i].address)=original;mutate_address=0u;
        reads=snapshot_reads;
        if(!initialization) assert(mt_live_clock_now(&live)==UINT64_MAX);
        assert(snapshot_reads==reads);
        /* Existing TIME convenience accessors expose stale data; wrapper must not. */
        assert(mt_timebase_now_us(&clock_state)==999u);
        assert(mt_timebase_now_ms(&clock_state)==0u);
        ++scenarios;
    }
    prepare_live(1000u);mt_live_clock old=live;
    clock_state.io.read32=NULL;invalid_sticky(&old);++scenarios;
    prepare_live(1000u);old=live;
    clock_state.io.write32=NULL;invalid_sticky(&old);++scenarios;
    prepare_live(1000u);old=live;
    assert(mt_timebase_stop(&clock_state));invalid_sticky(&old);++scenarios;
    puts("PASS all 13 configuration faults before/after CNT at init/now; sticky sentinel, no new time/write");
}
static void test_wrap_and_other_clients(void) {
    static const uint64_t seeds[]={0u,999u,65530u,UINT64_C(4294967290),
        UINT64_C(4294967294999),UINT64_C(4294967295999),UINT64_C(4294967296001)};
    for(unsigned s=0;s<sizeof seeds/sizeof seeds[0];++s) {
        uint64_t elapsed=seeds[s];
        uint64_t initial=(uint32_t)(elapsed/1000u);
        prepare_live(elapsed);
        for(unsigned i=0;i<10000u;++i) {
            uint32_t delta=(i*997u+s*123u)%32001u;
            uint32_t first=delta/3u;
            other_client_advance(first,i);elapsed+=first;
            advance(delta-first);elapsed+=delta-first;
            exact_now(initial+elapsed/1000u-seeds[s]/1000u);
            assert(clock_state.us==(uint32_t)elapsed);
            assert(clock_state.ms==(uint32_t)(elapsed/1000u));
            assert(clock_state.remainder_us==(uint32_t)(elapsed%1000u));
        }
        ++scenarios;
    }
    prepare_live(UINT64_C(4294967295));advance(1u);exact_now(UINT64_C(4294967));
    assert(clock_state.us==0u && clock_state.ms==4294967u);++scenarios;
    prepare_live(UINT64_C(4294967295999));advance(1u);exact_now(UINT64_C(4294967296));
    assert(clock_state.ms==0u && live.milliseconds==UINT64_C(4294967296));++scenarios;
    /* One coherent earlier sample despite preemption after the physical read. */
    prepare_live(999u);delay_after_counter=5000u;exact_now(0u);
    delay_after_counter=0u;exact_now(5u);++scenarios;
    puts("PASS 70000 independently-oracled mixed-client observations, CNT/us32/ms32 rollover, coherent sample");
}
static void test_service_bounds(void) {
    static const uint64_t seeds[]={0u,1u,999u,65530u,UINT64_C(4294967290),UINT64_C(4294967295999)};
    static const uint32_t accepted[]={0u,31999u,32000u};
    static const uint32_t rejected[]={32001u,32999u,33000u,64000u,65536u};
    for(unsigned s=0;s<sizeof seeds/sizeof seeds[0];++s) {
        for(unsigned i=0;i<sizeof accepted/sizeof accepted[0];++i) {
            prepare_live(seeds[s]);
            other_client_advance(accepted[i],i);
            exact_now((uint64_t)(uint32_t)(seeds[s]/1000u)+(seeds[s]+accepted[i])/1000u-seeds[s]/1000u);
            ++scenarios;
        }
        for(unsigned i=0;i<sizeof rejected/sizeof rejected[0];++i) {
            prepare_live(seeds[s]);mt_live_clock old=live;
            other_client_advance(rejected[i],i);
            assert(mt_timebase_healthy(&clock_state));
            invalid_sticky(&old);
            assert(mt_timebase_healthy(&clock_state));
            ++scenarios;
        }
    }
    prepare_live(0u);advance(32000u);exact_now(32u);
    mt_live_clock old=live;advance(32001u);invalid_sticky(&old);
    assert(clock_state.fault==MT_TIMEBASE_SERVICE_FAULT);++scenarios;
    /* us delta can alias after 2^32 us, but independent ms delta still rejects. */
    prepare_live(0u);old=live;seed_time(UINT64_C(4294967296)+17u);
    invalid_sticky(&old);assert(mt_timebase_healthy(&clock_state));++scenarios;
    puts("PASS own 32000/32001-us boundary despite other clients, independent ms guard and real source limit");
}
static void test_reset_and_alias_limits(void) {
    mt_timebase_io io={snapshot_read32,write32,NULL};
    prepare_live(100000u);mt_live_clock old=live;
    assert(mt_timebase_stop(&clock_state));
    assert(mt_timebase_init(&clock_state,&io,72000000u));
    invalid_sticky(&old);++scenarios;
    prepare_live(999u);old=live;seed_time(998u);
    invalid_sticky(&old);++scenarios;
    /* These are deliberately violated prerequisites, with explicitly limited
     * expectations. No finite counter pair certifies the absent epoch. */
    prepare_live(0u);advance(65536u+17u);exact_now(0u);
    assert(clock_state.us==17u);++scenarios;
    puts("LIMIT demonstrated: missed TIM2 wrap aliases 65553 physical us to 17 observed us");
    prepare_live(999u);seed_time(UINT64_C(4294967296000)+999u+17u);
    exact_now(1u);++scenarios;
    puts("LIMIT demonstrated: injected common us32/ms32 whole-wrap epoch aliases +4294967296000 us");
    prepare_live(0u);
    assert(mt_timebase_stop(&clock_state));
    assert(mt_timebase_init(&clock_state,&io,72000000u));
    exact_now(0u);++scenarios;
    puts("LIMIT demonstrated: forbidden reset to an identical observed epoch cannot be detected");
    puts("PASS ordinary source reset/backstep rejection and honest finite-counter limitation controls");
}
static void prepare_extended_ms(uint64_t extended) {
    /* Inject the unobservable high epoch while preserving the exact congruence
     * between extended ms, ms32, us32, remainder and hardware CNT. */
    prepare_live((uint64_t)(uint32_t)extended*UINT64_C(1000));
    live.milliseconds=extended;
    assert((uint32_t)extended==live.last_ms);
}
static void test_u64_limit(void) {
    static const uint32_t increments[]={0u,1u,2u,31u,32u};
    for(unsigned i=0;i<sizeof increments/sizeof increments[0];++i) {
        prepare_extended_ms((uint64_t)INT64_MAX-increments[i]);
        advance(increments[i]*1000u);exact_now((uint64_t)INT64_MAX);
        exact_now((uint64_t)INT64_MAX); /* nondecreasing means equality is legal */
        mt_live_clock old=live;advance(1000u);invalid_sticky(&old);++scenarios;
    }
    prepare_extended_ms((uint64_t)INT64_MAX-31u);
    mt_live_clock old=live;advance(32000u);invalid_sticky(&old);++scenarios;
    /* Defense even for an externally injected impossible internal state. */
    prepare_live(0u);live.milliseconds=UINT64_MAX;old=live;invalid_sticky(&old);++scenarios;
    puts("PASS injected INT64_MAX exact boundary, 0/1/2/31/32-ms carries and fail-before-overflow");
}

typedef struct {
    mt_live_clock *clock;
    unsigned starts,quiesces,writes,isolates,break_after_write;
    uint8_t output[2048];size_t used;
} live_fixture;
static uint64_t bound_clock(void *p) { live_fixture *f=p;return mt_live_clock_now(f->clock); }
static int bound_start(void *p,uint32_t generation) { live_fixture *f=p;assert(generation==3u);++f->starts;return 1; }
static void bound_quiesce(void *p,mt_live_snapshot *s) {
    live_fixture *f=p;++f->quiesces;memset(s,0,sizeof *s);s->hardware_off=1u;s->produced_known=1u;
}
static int bound_isolate(void *p) { live_fixture *f=p;++f->isolates;return 1; }
static size_t bound_write(void *p,const uint8_t *bytes,size_t n) {
    live_fixture *f=p;assert(n<=64u && f->used+n<=sizeof f->output);
    memcpy(f->output+f->used,bytes,n);f->used+=n;++f->writes;
    if(f->break_after_write)*reg(PSC)=70u;
    return n;
}
static void endpoint_init(mt_live_device *d,live_fixture *f) {
    memset(f,0,sizeof *f);f->clock=&live;
    mt_live_io io={.context=f,.clock_ms=bound_clock,.start=bound_start,
        .quiesce=bound_quiesce,.write=bound_write,.isolate_tx=bound_isolate,
        .transaction_bound_ms=1u,.firmware_build=1u};
    assert(mt_live_device_init(d,&io));assert(!f->quiesces && !f->writes);
}
static void endpoint_request(mt_live_device *d,uint8_t type,uint64_t session,uint32_t request) {
    mt_wire_message m={0};uint8_t bytes[MT_WIRE_FRAME_MAX];size_t length;
    m.type=type;m.session=session;m.sequence=request;m.request=request;
    if(type==1u){m.payload_length=8u;m.payload[0]=1u;}
    if(type==8u){m.payload_length=4u;m.payload[0]=1u;}
    assert(mt_wire_encode_revision(&m,0u,2u,bytes,sizeof bytes,&length)==MT_WIRE_OK);
    assert(mt_live_device_receive(d,bytes,length));
    for(unsigned i=0;i<16u;++i)assert(mt_live_device_step(d));
}
static void endpoint_active(mt_live_device *d,live_fixture *f) {
    endpoint_init(d,f);endpoint_request(d,1u,0u,1u);
    assert(d->state==MT_LIVE_STOPPED);endpoint_request(d,2u,1u,2u);
    assert(d->caps_ready);endpoint_request(d,8u,1u,3u);
    assert(d->state==MT_LIVE_STREAMING && f->starts==1u);
}
static void test_endpoint_sentinel(void) {
    for(unsigned entry=0;entry<3u;++entry) {
        mt_live_device d;live_fixture f;uint16_t samples[128]={0};
        prepare_live(1000u);endpoint_active(&d,&f);
        unsigned writes=f.writes,barriers=f.quiesces;size_t bytes=f.used;
        uint64_t time=d.last_time;
        *reg(PSC)=70u;
        int result=entry==0u?mt_live_device_step(&d):entry==1u?
            mt_live_device_receive(&d,NULL,0u):mt_live_device_offer(&d,0u,samples,128u);
        assert(!result && d.clock_failed && d.state==MT_LIVE_FAULT);
        assert(d.local_error==MT_LIVE_ERR_CLOCK && d.first_reason==3u);
        assert(f.quiesces==barriers+1u && f.writes==writes && f.used==bytes);
        assert(d.hardware_off && d.last_time==time && d.queue.used==0u);
        *reg(PSC)=71u;assert(!mt_live_device_step(&d));
        assert(f.writes==writes && f.quiesces==barriers+1u);++scenarios;
    }
    /* Sentinel after a real write cannot retract bytes. It must stop state
     * publication and subsequent writes and preserve the delimiter requirement. */
    mt_live_device d;live_fixture f;uint16_t samples[128]={0};
    prepare_live(1000u);endpoint_active(&d,&f);
    assert(mt_live_device_offer(&d,0u,samples,128u));
    unsigned writes=f.writes,barriers=f.quiesces;size_t bytes=f.used;
    f.break_after_write=1u;
    assert(!mt_live_device_step(&d));
    assert(d.clock_failed && d.state==MT_LIVE_FAULT && d.needs_delimiter);
    assert(f.used==bytes+64u && f.writes==writes+1u && f.quiesces==barriers+1u);
    assert(d.prefix_end==0u && d.queue.used==1u);
    assert(!mt_live_device_step(&d) && f.writes==writes+1u);++scenarios;
    /* The endpoint intentionally reserves a 500-ms deadline headroom below
     * the wrapper's more general INT64_MAX range. */
    prepare_extended_ms((uint64_t)INT64_MAX-500u);endpoint_init(&d,&f);
    assert(mt_live_device_step(&d));assert(!d.clock_failed);
    advance(1000u);assert(!mt_live_device_step(&d));
    assert(d.clock_failed && !live.failed && live.milliseconds==(uint64_t)INT64_MAX-499u);
    ++scenarios;
    puts("PASS actual endpoint rejects real wrapper sentinel at step/receive/offer and after TX; headroom retained");
}
int main(int argc,char **argv) {
    assert(setvbuf(stdout,NULL,_IONBF,0u)==0);
    const char *choice=argc>1?argv[1]:"all";
    struct {const char *name;void (*run)(void);} groups[]={
        {"init",test_initialization},{"faults",test_fault_matrix},
        {"wrap",test_wrap_and_other_clients},{"service",test_service_bounds},
        {"resets",test_reset_and_alias_limits},{"limit",test_u64_limit},
        {"endpoint",test_endpoint_sentinel}};
    unsigned found=0u;
    for(unsigned i=0;i<sizeof groups/sizeof groups[0];++i)
        if(strcmp(choice,"all")==0 || strcmp(choice,groups[i].name)==0){groups[i].run();++found;}
    assert(found);
    printf("PASS wrapper groups=%u scenarios=%u\n",found,scenarios);
    return 0;
}
