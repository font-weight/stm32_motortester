/* Independent integration tests. Only the pre-existing register bus is reused. */
#define main register_fixture_main
#include "snapshot/tests/platform/test_adc_timer_dma.c"
#undef main
#include "snapshot/firmware/app/mt_adc_control.h"
#include <inttypes.h>
#include <stdlib.h>

static mt_control ctl;
static mt_adc_control bind;
static uint32_t seq, req, nonce, base_us, base_ms;
static uint64_t origin_phase;
static uint8_t bytes[32768];
static size_t used;
static unsigned starts, writes_total, windows_verified;
static bool change_clock_on_ms;
static uint32_t clock_delay_on_ms;
static bool hold_first_us;

static void put16(uint8_t *p, uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void put32(uint8_t *p, uint32_t v) { unsigned i; for(i=0;i<4;++i)p[i]=(uint8_t)(v>>(8u*i)); }
static uint16_t get16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static uint32_t get32(const uint8_t *p) { return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static void tracked_write(void *ctx,uint32_t addr,uint32_t value) {
    if(addr==T_CR1 && value==1u) ++starts;
    ++writes_total;
    hw.write_count=0; /* Fixture's bounded trace is not cumulative storage. */
    write32(ctx,addr,value);
}
static uint32_t bracket_us(void *ctx) {
    if(hold_first_us) { hold_first_us=false;++((fake_t *)ctx)->time_reads;return ((fake_t *)ctx)->tick; }
    return now_us(ctx);
}
static uint32_t ms(void *ctx) {
    fake_t *f=ctx;
    uint32_t result=base_ms+(uint32_t)((origin_phase+(uint32_t)(f->tick-base_us))/1000u);
    if(change_clock_on_ms) {
        *slot(f,R_CR)=3u|(3u<<24);
        *slot(f,R_CFGR)=2u|8u|(4u<<8)|(1u<<14)|(10u<<18);
        change_clock_on_ms=false;
    }
    f->tick+=clock_delay_on_ms;
    clock_delay_on_ms=0;
    return result;
}
static int collect_bytes(void *ctx,const uint8_t *p,size_t n) {
    (void)ctx; assert(used+n<=sizeof bytes); memcpy(bytes+used,p,n); used+=n; return (int)n;
}
static mt_wire_message command(uint8_t type,uint16_t len) {
    mt_wire_message m={0}; m.type=type;m.payload_length=len;m.session=nonce;m.sequence=seq++;m.request=req++;return m;
}
static void drain(void) {
    unsigned n=0;
    do { assert(mt_control_poll(&ctl,ms(&hw),collect_bytes,NULL)==MT_CONTROL_OK); assert(++n<10000u); }
    while(ctl.tx.count || ctl.state_count || (ctl.link.phase>=MT_LINK_BEGIN && ctl.link.phase<=MT_LINK_END));
}
static void new_hello(void) {
    mt_wire_message m={0}; ++nonce;seq=req=2;m.type=1;m.request=m.sequence=1;m.payload_length=8;put32(m.payload,nonce);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_OK);drain();used=0;
}
static void connected(uint32_t us,uint32_t millis,uint64_t phase) {
    setup();hw.tick=us;base_us=us;base_ms=millis;origin_phase=phase;nonce=99;used=0;starts=writes_total=0;
    change_clock_on_ms=false;clock_delay_on_ms=0;
    driver.io.write32=tracked_write;
    assert(mt_adc_control_init(&bind,&ctl,&driver,ms,&hw,123)==MT_CONTROL_OK);
    assert(writes_total==0);new_hello();
}
static void configure(unsigned mode,unsigned pre) {
    mt_wire_message m=command(3,16);put16(m.payload,1);m.payload[2]=(uint8_t)mode;put16(m.payload+6,2048);put16(m.payload+8,(uint16_t)pre);
    if(mode==MT_ACQ_AUTO)put32(m.payload+12,100);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_OK);drain();used=0;
}
static void arm(void) {
    mt_wire_message m=command(4,4);put32(m.payload,ctl.link.config_id);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_OK);assert(ctl.hardware_running);drain();used=0;
}
static uint16_t code_at(uint32_t i,uint32_t trigger,unsigned salt,bool automatic) {
    if(automatic || i<trigger)return (uint16_t)(10u+(i*37u+salt*17u)%2000u);
    return (uint16_t)(2100u+(i*31u+salt*23u)%1900u);
}
static uint32_t ref_ms(uint32_t index) {
    return bind.epoch_ms+(uint32_t)(((uint64_t)bind.first_sample_delay_us+(uint64_t)index*20u)/1000u);
}
static mt_control_result feed(unsigned block,uint32_t trigger,unsigned salt,bool automatic) {
    unsigned h=block%2u,i;
    uint32_t t=driver.start_before_us+block*2560u;
    hw.tick=t+1000u;*slot(&hw,D_N)=h?78u:206u;
    assert(mt_adc_control_service(&bind)==MT_CONTROL_OK);
    hw.tick=t+2000u;*slot(&hw,D_N)=h?28u:156u;
    assert(mt_adc_control_service(&bind)==MT_CONTROL_OK);
    for(i=0;i<128u;++i)driver.dma_samples[h*128u+i]=code_at(block*128u+i,trigger,salt,automatic);
    hw.tick=t+2570u;*slot(&hw,D_N)=h?256u:128u;*slot(&hw,D_ISR)=h?2u:4u;
    return mt_adc_control_service(&bind);
}
static uint32_t auto_trigger(unsigned pre) {
    uint32_t waiting=pre?ref_ms(pre-1u):ctl.link.acquisition.waiting_since_ms;
    uint32_t i=pre;
    while((uint32_t)(ref_ms(i)-waiting)<100u)++i;
    return i;
}
static void capture_verify(unsigned pre,uint32_t trigger,unsigned salt,bool automatic) {
    uint32_t end=trigger+2047u-pre,first=trigger-pre,i;
    uint16_t sample;
    unsigned block=0,n_begin=0,n_end=0;
    size_t start=0,pos,total=0;
    uint8_t expected[4096];
    mt_wire_message out;
    assert(trigger>=pre && trigger>0u);
    while(ctl.hardware_running) { assert(feed(block++,trigger,salt,automatic)==MT_CONTROL_OK);assert(block<100u); }
    assert(block==(end/128u)+1u);
    assert(ctl.link.phase==MT_LINK_BEGIN);
    assert(driver.state==MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
    assert(ctl.link.acquisition.last_sample_ms==ref_ms(end));
    for(i=0;i<2048u;++i) {
        uint16_t want=code_at(first+i,trigger,salt,automatic);
        assert(mt_acq_read(&ctl.link.acquisition,(uint16_t)i,&sample)==MT_ACQ_OK && sample==want);
        put16(expected+2u*i,want);
    }
    { unsigned w=writes_total; uint32_t last=ctl.link.acquisition.last_sample_ms;
      memset(driver.snapshot,0xff,sizeof driver.snapshot);
      assert(mt_adc_control_service(&bind)==MT_CONTROL_IGNORED);assert(w==writes_total);
      assert(ctl.link.acquisition.last_sample_ms==last); }
    drain();assert(ctl.link.phase==MT_LINK_WAIT_RELEASE);
    for(pos=0;pos<used;++pos)if(bytes[pos]==0) {
        assert(mt_wire_decode(bytes+start,pos-start,&out)==MT_WIRE_OK);start=pos+1u;
        if(out.type==144) {
            ++n_begin;assert(get16(out.payload+14)==2048u);assert(get16(out.payload+16)==pre);
            assert(get16(out.payload+18)==(automatic?UINT16_MAX:pre));assert(out.payload[20]==(automatic?2u:0u));
        }
        if(out.type==145) {
            size_t n=get16(out.payload+10);assert(get16(out.payload+8)==total);assert(total+n<=2048u);
            assert(memcmp(out.payload+12,expected+2u*total,2u*n)==0);total+=n;
        }
        if(out.type==146) {++n_end;assert(get32(out.payload+12)==mt_wire_crc32(expected,sizeof expected));}
    }
    assert(start==used && total==2048u && n_begin==1u && n_end==1u);++windows_verified;used=0;
}
static mt_wire_message release_message(void) {
    mt_wire_message m=command(7,12);put32(m.payload,ctl.link.generation);put32(m.payload+4,ctl.link.active_window);put32(m.payload+8,ctl.link.tomb_crc);return m;
}
static void release_and_check(bool rearm) {
    unsigned started_before=starts;
    mt_wire_message m=release_message(),duplicate;
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_OK);
    assert(starts==started_before+(rearm?1u:0u));assert((ctl.hardware_running!=0u)==rearm);
    if(rearm)assert(driver.first_sample_index==0u && driver.next_half==false);
    duplicate=m;duplicate.sequence=seq++;duplicate.request=req++;
    assert(mt_control_receive(&ctl,&duplicate,ms(&hw))==MT_CONTROL_OK);assert(starts==started_before+(rearm?1u:0u));
    drain();used=0;
}
static void test_window_matrix(void) {
    unsigned mode,pre_i,w;const unsigned pres[]={0,512,1024};
    for(mode=0;mode<3u;++mode)for(pre_i=0;pre_i<3u;++pre_i) {
        unsigned pre=pres[pre_i];
        connected(UINT32_MAX-1000u,UINT32_MAX-30u,997u);configure(mode,pre);arm();
        for(w=0;w<20u;++w) {
            bool automatic=mode==MT_ACQ_AUTO && (w%2u)==0u;
            uint32_t trigger=automatic?auto_trigger(pre):pre+1u+(w*17u)%256u;
            capture_verify(pre,trigger,w,automatic);release_and_check(mode!=MT_ACQ_SINGLE);
            if(mode==MT_ACQ_SINGLE && w!=19u)arm();
        }
    }
    printf("PASS matrix: %u windows; Auto/Normal/Single, pretrigger 0/512/1024, exact samples, wire CRC, wrap, release/rearm\n",windows_verified);
}
static void test_all_partial_end_positions(void) {
    unsigned shift;
    for(shift=0;shift<128u;++shift) {
        connected(999u,17u,999u);configure(MT_ACQ_SINGLE,0);arm();capture_verify(0,1u+shift,shift,false);
    }
    puts("PASS partial freeze: all 128 final sample positions; snapshots poisoned after stop without affecting retained window");
}
static void test_timestamp_arithmetic(void) {
    const uint32_t indexes[]={0,128,4992,214748288u,214748352u,2147483648u,4294966912u,4294967040u};
    unsigned i;
    for(i=0;i<sizeof indexes/sizeof indexes[0];++i) {
        connected(UINT32_MAX-100u,UINT32_MAX-20u,0);configure(MT_ACQ_NORMAL,0);arm();
        driver.first_sample_index=indexes[i];
        ctl.link.acquisition.last_sample_ms=ref_ms(indexes[i]);
        assert(feed(0,UINT32_MAX,0,true)==MT_CONTROL_OK);
        assert(ctl.link.acquisition.last_sample_ms==ref_ms(indexes[i]+127u));
    }
    puts("PASS timestamp: 64-bit oracle over 8 index boundaries, including multiplication overflow and sequence limit approach");
}
static void test_failed_stop(void) {
    mt_wire_message m;size_t before;
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);arm();
    assert(feed(0,1,0,false)==MT_CONTROL_OK);
    hw.stop_stuck=true;hw.tick_step=0;before=used;m=command(5,4);put32(m.payload,ctl.link.generation);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_RECOVERY);
    assert(!ctl.ready && ctl.hardware_running && driver.owns_peripherals && driver.fault==MT_ADC_DMA_FAULT_STOP);
    assert(mt_adc_control_service(&bind)==MT_CONTROL_IGNORED);
    assert(mt_control_poll(&ctl,ms(&hw),collect_bytes,NULL)==MT_CONTROL_IGNORED && used==before);
    hw.stop_stuck=false;hw.tick_step=1;new_hello();assert(!ctl.hardware_running && !driver.owns_peripherals);
    configure(MT_ACQ_NORMAL,0);arm();capture_verify(0,1,0,false);
    /* Stop failure exactly at the partial final block. */
    release_and_check(true);
    { unsigned b;for(b=0;b<16u;++b)assert(feed(b,1,0,false)==MT_CONTROL_OK); }
    hw.stop_stuck=true;hw.tick_step=0;
    assert(feed(16,1,0,false)==MT_CONTROL_RECOVERY);
    assert(!ctl.ready && ctl.hardware_running && driver.owns_peripherals);
    assert(ctl.link.acquisition.state==MT_ACQ_STOPPED);
    assert(mt_control_poll(&ctl,ms(&hw),collect_bytes,NULL)==MT_CONTROL_IGNORED);
    hw.stop_stuck=false;hw.tick_step=1;new_hello();
    puts("PASS stop failure: command and freeze paths retain ownership, suppress success/output, and recover with fresh HELLO");
}
static void test_clocks_and_faults(void) {
    mt_wire_message m;size_t before;unsigned old_writes;
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);before=starts;old_writes=writes_total;
    *slot(&hw,R_CR)=3u|(3u<<24);*slot(&hw,R_CFGR)=2u|8u|(4u<<8)|(1u<<14)|(10u<<18);
    m=command(4,4);put32(m.payload,ctl.link.config_id);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_RECOVERY);assert(starts==before && writes_total==old_writes && !driver.owns_peripherals);
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);arm();*slot(&hw,R_CFGR)^=1u<<14;
    assert(mt_adc_control_service(&bind)==MT_CONTROL_RECOVERY);assert(!ctl.ready && !driver.owns_peripherals);
    /* Change after profile-ready, within binding's time callback. */
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);m=command(4,4);put32(m.payload,ctl.link.config_id);
    { uint32_t now=ms(&hw); change_clock_on_ms=true;
      assert(mt_control_receive(&ctl,&m,now)==MT_CONTROL_RECOVERY); }
    assert(starts==1u && !ctl.ready && !driver.owns_peripherals);
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);arm();*slot(&hw,D_ISR)=8u;
    assert(mt_adc_control_service(&bind)==MT_CONTROL_RECOVERY);assert(!ctl.ready && !driver.owns_peripherals);
    puts("PASS clocks/faults: 48 MHz preflight reject, mid-start noncanonical clock fail-closed, runtime RCC change and DMA transfer error");
}
static void test_start_across_wrap_and_reference_gap(void) {
    const uint32_t gaps[]={0u,20u,21u,5000u};
    const uint32_t origins[]={1000u,UINT32_MAX-10u};
    unsigned i,j;
    mt_wire_message m;
    connected(UINT32_MAX-3u,UINT32_MAX-1u,0);configure(MT_ACQ_SINGLE,0);arm();
    assert(driver.start_before_us<20u && bind.first_sample_delay_us<50u);
    capture_verify(0,137u,19u,false);
    puts("PASS start itself spans microsecond rollover, with millisecond rollover during capture");
    for(j=0;j<2u;++j)for(i=0;i<4u;++i) {
        mt_control_result result;
        unsigned old_writes;
        connected(origins[j],UINT32_MAX-1u,0);configure(MT_ACQ_NORMAL,0);
        m=command(4,4);put32(m.payload,ctl.link.config_id);old_writes=writes_total;
        driver.io.now_us=bracket_us;hold_first_us=true;
        { uint32_t command_time=ms(&hw);clock_delay_on_ms=gaps[i];
          result=mt_control_receive(&ctl,&m,command_time); }
        if(gaps[i]<=20u) {
            assert(result==MT_CONTROL_OK && starts==1u && ctl.hardware_running);
            assert(bind.first_sample_delay_us>=20u+gaps[i]);
            assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
        } else {
            assert(result==MT_CONTROL_RECOVERY && !ctl.ready && starts==0u && !ctl.hardware_running);
            assert(!driver.owns_peripherals && writes_total==old_writes);
            assert(mt_control_poll(&ctl,ms(&hw),collect_bytes,NULL)==MT_CONTROL_IGNORED);
        }
    }
    puts("PASS clock-reference guard: exact widths 0/20 us accepted and 21/5000 us rejected before hardware writes, both ordinary and wrapping clocks");
}
static void test_release_clock_reject_and_stop_retry(void) {
    mt_wire_message m;unsigned before;
    connected(1000u,1u,0);configure(MT_ACQ_NORMAL,0);arm();capture_verify(0,1,0,false);
    m=release_message();before=writes_total;
    *slot(&hw,R_CR)=3u|(3u<<24);*slot(&hw,R_CFGR)=2u|8u|(4u<<8)|(1u<<14)|(10u<<18);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_RECOVERY);
    assert(!ctl.ready && !ctl.hardware_running && !driver.owns_peripherals && starts==1u && writes_total==before);
    connected(1000u,1u,0);configure(MT_ACQ_NORMAL,0);arm();
    hw.stop_stuck=true;hw.tick_step=0;m=command(5,4);put32(m.payload,ctl.link.generation);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_RECOVERY);
    assert(driver.owns_peripherals);
    memset(&m,0,sizeof m);m.type=1;m.payload_length=8;m.request=m.sequence=1;put32(m.payload,++nonce);
    assert(mt_control_receive(&ctl,&m,ms(&hw))==MT_CONTROL_RECOVERY);
    assert(!ctl.ready && driver.owns_peripherals && ctl.hardware_running);
    hw.stop_stuck=false;hw.tick_step=1;new_hello();assert(!driver.owns_peripherals && !ctl.hardware_running);
    puts("PASS release rejects noncanonical clock without MMIO writes; repeated failed Stop/HELLO keeps ownership until confirmed retry");
}
static void test_consistent_64bit_clock_epochs(void) {
    const uint64_t origins[]={UINT64_C(4294967292),UINT64_C(4294967295997)};
    unsigned i;
    for(i=0;i<2u;++i) {
        uint64_t t=origins[i];
        connected((uint32_t)t,(uint32_t)(t/1000u),t%1000u);configure(MT_ACQ_SINGLE,512);arm();
        capture_verify(512u,650u,21u,false);
        assert(ms(&hw)==(uint32_t)((t+(uint32_t)(hw.tick-(uint32_t)t))/1000u));
    }
    puts("PASS true shared-clock epochs: microsecond wrap at 71 min and joint millisecond/microsecond wrap near 49.7 days");
}
static void test_init_preconditions(void) {
    mt_adc_dma_t zero={0};mt_adc_control z={0};mt_control copy;unsigned before;
    setup();memset(&ctl,0xa5,sizeof ctl);copy=ctl;
    assert(mt_adc_control_init(NULL,&ctl,&driver,ms,&hw,0)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init(&bind,NULL,&driver,ms,&hw,0)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init(&bind,&ctl,NULL,ms,&hw,0)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init(&bind,&ctl,&driver,NULL,&hw,0)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init(&bind,&ctl,&zero,ms,&hw,0)==MT_CONTROL_ARGUMENT);
    assert(memcmp(&ctl,&copy,sizeof ctl)==0 && hw.write_count==0);
    assert(mt_adc_control_service(NULL)==MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_service(&z)==MT_CONTROL_ARGUMENT);
    connected(1000,1,0);configure(MT_ACQ_NORMAL,0);arm();before=writes_total;
    assert(mt_adc_control_init(&bind,&ctl,&driver,ms,&hw,0)==MT_CONTROL_OK);
    assert(writes_total==before && driver.owns_peripherals && driver.state==MT_ADC_DMA_RUNNING && !ctl.hardware_running);
    printf("OBSERVATION init: live-driver rebind accepted and controller running flag cleared; violates documented caller precondition, not counted as in-contract defect\n");
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
    puts("PASS init null/uninitialized rejects without mutation or hardware access");
}
int main(void) {
    assert(register_fixture_main()==0);
    test_window_matrix();test_all_partial_end_positions();test_timestamp_arithmetic();test_failed_stop();test_clocks_and_faults();test_start_across_wrap_and_reference_gap();test_release_clock_reject_and_stop_retry();test_consistent_64bit_clock_epochs();test_init_preconditions();
    printf("ALL INDEPENDENT TESTS PASSED (%u exact complete windows)\n",windows_verified);return 0;
}
