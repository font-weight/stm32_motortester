/* REVIEWER-OWNED native harness. Published ADC bus fixture included verbatim.
 * Production sources are exact-head copies; this file alone controls injection.
 * No direct control fault entry point is called. */
#define main published_adc_regression_main
#include "../source/tests/platform/test_adc_timer_dma.c"
#undef main
#include "mt_runtime.h"
#include "mt_usb_time.h"
#include "test_integration.h"
#include "fake_dcd.h"
#include <stdlib.h>

static mt_runtime runtime;
static mt_timebase clockbase;
static uint32_t timer2[12];
static unsigned primask, nesting;
static const char *name;
static const char *directory;
static unsigned injection_fired, stop_observed, admitted_observed;
static unsigned stage; /* 1: actual ADC Stop; 2: first post-admission TIM2 read;
                           * 3: first later bridge lock, after time refresh. */
static unsigned kind;  /* 1: TIM2 loss, 2: BUS_RESET, 3: SUSPEND, 4: PMA poison. */
static unsigned cmd_sequence=1;
static size_t injection_host_bytes,injection_submitted_bytes;
static void bytes_file(const char *,const uint8_t *,size_t);
int test_reject_open=-1, test_reject_xfer=-1;

static void inject(void) {
    assert(!injection_fired); injection_fired=1;
    injection_host_bytes=fake_host_size;injection_submitted_bytes=fake_submitted_size;
    printf("{\"case\":\"%s\",\"injection_host_boundary\":%zu,\"injection_submit_boundary\":%zu,\"stage\":%u}\n",name,injection_host_bytes,injection_submitted_bytes,stage);
    if (kind==1u) timer2[0]=0;
    else if (kind==2u) fake_bus_reset(false);
    else if (kind==3u) fake_signal(DCD_EVENT_SUSPEND,false);
    else if (kind==4u) mt_usb_bridge_pma_overrun();
    else assert(!"missing injection kind");
}
static uint32_t lock(void) {
    if(stage==3u && !injection_fired && runtime.control.link.phase==MT_LINK_ACQ_ERROR) {
        admitted_observed=1;assert(!primask);inject();
    }
    uint32_t old=primask; primask=1; ++nesting; return old;
}
static void unlock(uint32_t previous) { assert(primask && nesting);--nesting;primask=previous; }
void test_dcd_call(enum test_operation operation) {
    (void)operation; assert(primask && nesting && mt_usb_bridge_actor());
}
void test_mount_hook(void) { }
static mt_runtime_result step_runtime(void) {
    mt_runtime_result result=mt_runtime_step(&runtime);
    assert(!primask && !nesting && !mt_usb_bridge_actor());
    assert(result!=MT_RUNTIME_ARGUMENT);
    return result;
}
void test_pump(void) {
    unsigned budget=1000;
    while(tud_task_event_ready()) { assert(budget--);(void)step_runtime();
        if(clockbase.state==MT_TIMEBASE_FAULTED || mt_usb_bridge_snapshot().pending_soft)break;
    }
}
static uint32_t runtime_read(void *context,uint32_t address) {
    (void)context;
    if(address>=UINT32_C(0x40000000) && address<=UINT32_C(0x4000002c)) {
        unsigned index=(address-UINT32_C(0x40000000))/4u;
        if(index==9u) {
            uint32_t counter=hw.tick++ & 65535u;
            if(stage==2u && !injection_fired && runtime.control.link.phase==MT_LINK_ACQ_ERROR) {
                admitted_observed=1; assert(runtime.control.ready && !runtime.control.hardware_running);
                assert(!runtime.adc.owns_peripherals);inject();
            }
            return counter;
        }
        return timer2[index];
    }
    return read32(&hw,address);
}
static void runtime_write(void *context,uint32_t address,uint32_t value) {
    (void)context;
    if(address>=UINT32_C(0x40000000) && address<=UINT32_C(0x4000002c)) {
        timer2[(address-UINT32_C(0x40000000))/4u]=value;
        if(address==UINT32_C(0x40000024))hw.tick=value;
        return;
    }
    if(address==T_CR1 && value==0u && runtime.adc.owns_peripherals && runtime.adc.state==MT_ADC_DMA_FAULTED) {
        ++stop_observed;
        if(stage==1u && !injection_fired)inject();
    }
    write32(&hw,address,value);
}
static void drain(void) {
    unsigned i;
    for(i=0;i<((hw.stop_stuck || !strcmp(name,"adc_clock"))?3u:80u);++i) {
        (void)step_runtime();
        if(fake_ep[FAKE_CDC_IN].pending)fake_complete_in(FAKE_CDC_IN,false);
    }
}
static void send_command(mt_wire_message *m) {
    uint8_t framed[600];size_t n=0;
    m->sequence=m->request=cmd_sequence++;
    if(m->type!=1u)m->session=99;
    assert(mt_wire_encode(m,framed,sizeof framed,&n)==MT_WIRE_OK);
    char suffix[64];(void)snprintf(suffix,sizeof suffix,"command-%u.bin",m->type);bytes_file(suffix,framed,n);
    assert(n<=64u);fake_out(framed,n,false);drain();
}
static void begin(void) {
    setup(); /* Published model only; its standalone driver remains unused. */
    *slot(&hw,R_APB1)|=1u;
    mt_timebase_io clockio={runtime_read,runtime_write,NULL};
    assert(mt_timebase_init(&clockbase,&clockio,72000000u));
    assert(mt_usb_time_bind(&clockbase));
    mt_runtime_mmio bus={runtime_read,runtime_write,NULL};
    assert(mt_runtime_init(&runtime,&clockbase,&bus,8000000u,lock,unlock,123,0)==MT_RUNTIME_OK);
    fake_start();(void)step_runtime();
    assert(mt_usb_bridge_snapshot().configured==1u && runtime.usb.state==MT_USB_OPEN);
    mt_wire_message m={0};m.type=1;m.payload_length=8;m.payload[0]=99;send_command(&m);
    assert(runtime.control.ready);
    m=(mt_wire_message){0};m.type=3;m.payload_length=16;m.payload[0]=1;m.payload[2]=1;m.payload[6]=0;m.payload[7]=8;send_command(&m);
    m=(mt_wire_message){0};m.type=4;m.payload_length=4;m.payload[0]=1;send_command(&m);
    assert(runtime.adc.state==MT_ADC_DMA_RUNNING && runtime.control.hardware_running);
    assert(runtime.adc.owns_peripherals);bytes_file("setup-responses.bin",fake_host,fake_host_size);fake_clear_logs();
}
static void capture_first(void) {
    uint32_t base=runtime.adc.start_before_us;
    hw.tick=base+1000u;*slot(&hw,D_N)=206;(void)step_runtime();
    hw.tick=base+2000u;*slot(&hw,D_N)=156;(void)step_runtime();
    for(unsigned i=0;i<128u;++i)runtime.adc.dma_samples[i]=i==0u?100u:3000u;
    hw.tick=base+2570u;*slot(&hw,D_N)=128;*slot(&hw,D_ISR)=4;(void)step_runtime();
    assert(runtime.control.link.active_window && runtime.control.hardware_running);
    drain();bytes_file("capture-prefix.bin",fake_host,fake_host_size);fake_clear_logs();
}
static void bytes_file(const char *suffix,const uint8_t *bytes,size_t n) {
    char path[1024];int len=snprintf(path,sizeof path,"%s/%s.%s",directory,name,suffix);assert(len>0 && (size_t)len<sizeof path);
    FILE *f=fopen(path,"wb");assert(f);assert(fwrite(bytes,1,n,f)==n);assert(!fclose(f));
}
static void observe(unsigned expected_abort,unsigned expected_error,unsigned expected_reason) {
    size_t first=0;unsigned aborts=0,errors=0;
    bytes_file("wire.bin",fake_host,fake_host_size);bytes_file("submitted.bin",fake_submitted,fake_submitted_size);
    printf("{\"case\":\"%s\",\"host_bytes\":%zu,\"submitted_bytes\":%zu,\"runtime_ready\":%u,\"hardware_running\":%u,\"owns_peripherals\":%u,\"adc_fault\":%u,\"time_fault\":%u,\"clock_fault\":%u,\"epoch\":%u,\"pending\":%u,\"injection_fired\":%u,\"actual_fault_stop_writes\":%u,\"admitted_observed\":%u}\n",name,fake_host_size,fake_submitted_size,runtime.control.ready,runtime.control.hardware_running,(unsigned)runtime.adc.owns_peripherals,(unsigned)runtime.adc.fault,(unsigned)clockbase.fault,runtime.clock_fault,mt_usb_bridge_snapshot().current,runtime.pending,injection_fired,stop_observed,admitted_observed);
    for(size_t at=0;at<fake_host_size;++at)if(!fake_host[at]) {
        mt_wire_message m;assert(mt_wire_decode(fake_host+first,at-first,&m)==MT_WIRE_OK);first=at+1;
        printf("{\"case\":\"%s\",\"message_type\":%u,\"session\":%llu,\"sequence\":%u,\"request\":%u,\"payload_hex\":\"",name,m.type,(unsigned long long)m.session,m.sequence,m.request);
        for(unsigned i=0;i<m.payload_length;++i) { printf("%02x",m.payload[i]); }
        puts("\"}");
        assert(m.type!=144u && m.type!=145u && m.type!=146u);
        if(m.type==147u){++aborts;assert(!errors);if(expected_abort)assert(m.payload[8]==expected_reason+1u);}
        if(m.type==134u && m.payload[0]==5u){++errors;if(expected_error){assert(aborts==expected_abort);assert(m.payload[1]==expected_reason);}}
    }
    assert(first==fake_host_size);assert(!fake_invalid_submissions);
    if(aborts!=expected_abort || errors!=expected_error)printf("{\"case\":\"%s\",\"pass\":false,\"unexpected_aborts\":%u,\"unexpected_errors\":%u}\n",name,aborts,errors);
    assert(aborts==expected_abort && errors==expected_error);
    printf("{\"case\":\"%s\",\"pass\":true,\"aborts\":%u,\"errors\":%u}\n",name,aborts,errors);
}
int main(int argc,char **argv) {
    assert(argc==3);setvbuf(stdout,NULL,_IONBF,0);name=argv[1];directory=argv[2];begin();
    bool positive=strstr(name,"transfer_")==name || strstr(name,"deadline_")==name;
    bool capturing=strstr(name,"capture")!=NULL;
    bool deadline=strstr(name,"deadline")!=NULL;
    if(capturing)capture_first();
    if(strstr(name,"time_")==name)kind=1;
    if(strstr(name,"reset_")==name)kind=2;
    if(strstr(name,"suspend_")==name)kind=3;
    if(strstr(name,"poison_")==name)kind=4;
    if(strstr(name,"_stop"))stage=1;
    if(strstr(name,"_admitted"))stage=2;
    if(strstr(name,"_outputgap"))stage=3;
    if(kind && strstr(name,"_before"))inject();
    if(!strcmp(name,"stop_failed"))hw.stop_stuck=true;
    if(!strcmp(name,"adc_clock"))*slot(&hw,R_APB2)&=~(1u<<9);
    else if(!strcmp(name,"adc_clock_plan"))*slot(&hw,R_CFGR)&=~(3u<<14);
    else if(!strcmp(name,"sequence_limit")) {
        runtime.adc.first_sample_index=UINT32_MAX;
        *slot(&hw,D_N)=128;*slot(&hw,D_ISR)=4;
    } else if(deadline)hw.tick+=3000u;
    else *slot(&hw,D_ISR)=8;
    mt_runtime_result first=step_runtime();
    printf("{\"case\":\"%s\",\"first_runtime_result\":%u}\n",name,(unsigned)first);
    for(size_t i=0;i<fake_record_count;++i){const fake_submission *r=&fake_records[i];printf("{\"case\":\"%s\",\"dcd_endpoint\":%u,\"dcd_length\":%u,\"dcd_epoch\":%u,\"dcd_accepted\":%u}\n",name,r->ep_addr,r->length,r->epoch,(unsigned)r->accepted);}
    drain();
    if(positive) {
        assert(runtime.control.ready && !runtime.control.hardware_running && !runtime.adc.owns_peripherals);
        assert(runtime.control.link.phase==MT_LINK_ACQ_ERROR);
        observe(capturing?1u:0u,1u,deadline?2u:1u);
    } else {
        if(kind)assert(injection_fired);
        if(stage==1u)assert(stop_observed);
        if(stage>=2u)assert(admitted_observed);
        observe(0,0,0);
        assert(fake_host_size==0u && fake_submitted_size==0u);
        assert(!runtime.control.ready);
        if(!strcmp(name,"stop_failed"))assert(runtime.adc.owns_peripherals && runtime.control.hardware_running);
    }
    return 0;
}
