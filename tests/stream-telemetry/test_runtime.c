/* Native composition: real runtime, TIME, ADC, live protocol, TinyUSB core,
 * CDC, FIFO and epoch bridge. Only register bus, DCD, IRQ mask and CTR rearm
 * are modeled. Link wrappers call the real function and observe/inject at its
 * boundary; no controller/parser/ADC snapshot result is replaced. */
#include "mt_live_runtime.h"
#include "mt_usb_ctr.h"
#include "mt_stream_diag.h"
#include "test_integration.h"
#include "fake_dcd.h"
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"FAIL %s %s:%d: %s\n",case_name,__func__,__LINE__,#x); exit(1); } } while(0)
static const char *case_name="setup";
enum { RCC_CR=0x40021000,RCC_CFGR=0x40021004,RCC_APB2RSTR=0x4002100c,RCC_APB1RSTR=0x40021010,RCC_AHB=0x40021014,RCC_APB2=0x40021018,RCC_APB1=0x4002101c,ADC_CR2=0x40012408,TIM_CR1=0x40000400,TIME_CR1=0x40000000,TIME_CNT=0x40000024,TIME_PSC=0x40000028,DMA_ISR=0x40020000,DMA_IFCR=0x40020004,DMA_CCR=0x40020008,DMA_N=0x4002000c,DMA_TC=2,DMA_HT=4,DMA_TE=8,HELLO=1,CAPS=2,STOP=5,START=8,HELLO_ACK=129,CAPS_REPLY=130,STOP_ACK=133,START_ACK=136,DATA=152,END=153 };
#define NONCE UINT64_C(0x1020304050607080)
typedef struct { uint32_t address,value; } reg;
static struct { reg regs[96]; unsigned used,time_reads,writes,stop_writes,cen_writes,ignore_stop,cal_stuck; uint64_t elapsed,transferred,cen_at; unsigned tick; } hw;
static mt_live_runtime runtime;
static mt_timebase clock_source;
static unsigned primask,nesting,pending_reset,reset_observed,recursive_calls;
static unsigned step_calls,step_reads,step_rx_bytes,step_writes,step_flushes,step_services,step_tx,step_adc_reads,step_rearms;
static unsigned flush_returned,stop_in_tx,clock_after_write_fault;
static unsigned total_read_flushes,total_cleaned,total_rearms,total_writes,total_adc_reads,total_services,total_tx,dcd_calls,mounts;
static unsigned in_tx,tx_after_write,time_after_write,write_return,write_known_commit,reset_after_commit;
static unsigned active_step_time_reads;
static uint32_t actor_at_injection,current_at_injection;
static uint64_t nonce; static uint32_t sequence;
static int injection;
/* Independent scripted DWT counter. It never changes the modeled TIME bus. */
static struct { uint32_t demcr,ctrl,cycle,stride; unsigned reads,writes; } dwt;
static unsigned reject_copy,copy_ndtr_reads;
static uint32_t poll_stamp,poll_delta,copy_delta;
static unsigned hook_polls,hook_copies,hook_tx;
static uint32_t dwt_read(void *context,uint32_t address) {
 CHECK(context==&dwt && !primask && !nesting); ++dwt.reads;
 if(address==UINT32_C(0xe000edfc))return dwt.demcr;
 if(address==UINT32_C(0xe0001000))return dwt.ctrl;
 CHECK(address==UINT32_C(0xe0001004)); uint32_t value=dwt.cycle; dwt.cycle+=dwt.stride; return value;
}
static void dwt_write(void *context,uint32_t address,uint32_t value) {
 CHECK(context==&dwt && !primask && !nesting); ++dwt.writes;
 if(address==UINT32_C(0xe000edfc))dwt.demcr=value;
 else { CHECK(address==UINT32_C(0xe0001000)); dwt.ctrl=value; }
}
static void diag_init(void) { dwt.stride=17u; dwt.cycle=UINT32_C(0xfffff000); CHECK(mt_stream_diag_init(dwt_read,dwt_write,&dwt,UINT32_C(0x12345678))); }
void __real_mt_stream_diag_poll(uint32_t);
void __wrap_mt_stream_diag_poll(uint32_t delta) {
 CHECK(runtime.in_step); ++hook_polls; poll_stamp=clock_source.us; poll_delta=delta;
 CHECK(delta==(uint32_t)(poll_stamp-runtime.adc.last_poll_us)); unsigned reads=hw.time_reads;
 __real_mt_stream_diag_poll(delta); CHECK(hw.time_reads==reads);
}
void __real_mt_stream_diag_copy(uint32_t);
void __wrap_mt_stream_diag_copy(uint32_t delta) {
 CHECK(runtime.in_step); ++hook_copies; copy_delta=delta;
 CHECK(delta==(uint32_t)(clock_source.us-poll_stamp)); unsigned reads=hw.time_reads;
 __real_mt_stream_diag_copy(delta); CHECK(hw.time_reads==reads);
}
void __real_mt_stream_diag_tx(uint32_t);
void __wrap_mt_stream_diag_tx(uint32_t begin) {
 CHECK(runtime.in_step && !primask && !nesting && !in_tx); ++hook_tx; unsigned reads=hw.time_reads;
 __real_mt_stream_diag_tx(begin); CHECK(hw.time_reads==reads);
}

enum { NONE, RESET_CAL, RESET_CEN, REENTER_CAL, RESET_READ_BEFORE, RESET_READ_AFTER, RESET_ADC_BEFORE, RESET_ADC_AFTER, RESET_WRITE_BEFORE, RESET_WRITE_AFTER, DEFER_RESET_WRITE_AFTER, RESET_FLUSH_BEFORE, RESET_FLUSH_AFTER, CLOCK_FLUSH_AFTER, CLOCK_STOP, CLOCK_WRITE_AFTER, PMA_REARM_WINDOW, SUSPEND_READ_AFTER, SUSPEND_ADC_AFTER, RESET_POSTCOMMIT };
int test_reject_open=-1,test_reject_xfer=-1;
static uint32_t *slot(uint32_t a) { CHECK(!(a&3u)); for(unsigned i=0;i<hw.used;i++)if(hw.regs[i].address==a)return &hw.regs[i].value; CHECK(hw.used<96u); hw.regs[hw.used]=(reg){a,0}; return &hw.regs[hw.used++].value; }
static uint16_t code_at(uint64_t i) { return (uint16_t)(((i*UINT64_C(37))^(i>>4)^UINT64_C(0x5a3))&4095u); }
static void advance(unsigned us) { hw.elapsed+=us; }
static void dispatch_reset(void) {
 if(!primask && pending_reset) { pending_reset=0; if(tx_after_write) { CHECK(runtime.device.last_write_known && runtime.device.last_write_accepted==write_return); write_known_commit=1; reset_after_commit=1; } fake_bus_reset(false); ++reset_observed; }
}
static uint32_t lock(void) { uint32_t prior=primask; primask=1; ++nesting; return prior; }
static void unlock(uint32_t prior) { CHECK(primask && nesting); --nesting; primask=prior; dispatch_reset(); }
static void inject(int point) {
 if(injection!=point)return;
 injection=NONE; actor_at_injection=mt_usb_bridge_actor();
 if(point==CLOCK_FLUSH_AFTER || point==CLOCK_STOP || point==CLOCK_WRITE_AFTER)*slot(TIME_PSC)=70u;
 else if(point==PMA_REARM_WINDOW)mt_usb_bridge_pma_overrun();
 else if(point==REENTER_CAL) { CHECK(runtime.in_step); CHECK(mt_live_runtime_step(&runtime)==MT_LIVE_RUNTIME_ARGUMENT); ++recursive_calls; }
 else if(point==SUSPEND_READ_AFTER || point==SUSPEND_ADC_AFTER)fake_signal(DCD_EVENT_SUSPEND,false);
 else if(point==DEFER_RESET_WRITE_AFTER || point==RESET_CEN || point==RESET_POSTCOMMIT) { pending_reset=1; dispatch_reset(); }
 else fake_bus_reset(false);
 current_at_injection=mt_usb_bridge_snapshot().current;
 CHECK(mt_usb_bridge_actor()==actor_at_injection);
}
static uint32_t rd(void *context,uint32_t a) {
 CHECK(context==&hw);
 if(a==TIME_CNT) { uint32_t n=(uint32_t)(hw.elapsed&65535u); advance(hw.tick); ++hw.time_reads; if(in_tx && tx_after_write) { CHECK(primask && nesting); ++time_after_write; } return n; }
 if(a==DMA_N && reject_copy && ++copy_ndtr_reads==2u) { *slot(DMA_ISR)|=DMA_TE; reject_copy=0u; }
 if(a==ADC_CR2 && !hw.cal_stuck)*slot(a)&=~12u;
 return *slot(a);
}
static void wr(void *context,uint32_t a,uint32_t v) {
 CHECK(context==&hw); uint32_t *p=slot(a); ++hw.writes;
 if(a==DMA_IFCR) { CHECK(!(v&1u)); *slot(DMA_ISR)&=~v; }
 if(a==TIME_CNT)hw.elapsed=v;
 if(a==TIM_CR1 && v==0u) { ++hw.stop_writes; if(in_tx) { CHECK(primask && nesting); ++stop_in_tx; } inject(CLOCK_STOP); }
 if(a==TIM_CR1 && v==0u && (*p&1u) && (hw.ignore_stop&1u))return;
 if(a==ADC_CR2 && v==0u && (*p&1u) && (hw.ignore_stop&2u))return;
 if(a==DMA_CCR && !(v&1u) && (*p&1u) && (hw.ignore_stop&4u))return;
 if(a==ADC_CR2 && (v&8u)) { inject(RESET_CAL); inject(REENTER_CAL); }
 if((a==DMA_CCR && (v&1u)) || (a==ADC_CR2 && (v&(1u<<20))) || (a==TIM_CR1 && v==1u)) CHECK(primask && nesting && mt_usb_bridge_actor());
 if(a==TIM_CR1 && v==1u) { hw.cen_at=hw.elapsed; ++hw.cen_writes; inject(RESET_CEN); }
 *p=v;
}
static void app_scope(void) { mt_usb_epoch e=mt_usb_bridge_snapshot(); CHECK(runtime.in_step && mt_usb_bridge_actor()); CHECK(mt_usb_epoch_app_io(&e,mt_usb_bridge_actor())); }
uint32_t __real_tud_cdc_n_read(uint8_t,void *,uint32_t);
uint32_t __wrap_tud_cdc_n_read(uint8_t itf,void *p,uint32_t n) {
 app_scope(); CHECK(n<=64u && ++step_reads<=1u); inject(RESET_READ_BEFORE); uint32_t got=__real_tud_cdc_n_read(itf,p,n); step_rx_bytes+=got; CHECK(step_rx_bytes<=64u); inject(RESET_READ_AFTER); inject(SUSPEND_READ_AFTER); return got;
}
void __real_tud_cdc_n_read_flush(uint8_t);
void __wrap_tud_cdc_n_read_flush(uint8_t itf) { app_scope(); ++total_read_flushes; __real_tud_cdc_n_read_flush(itf); }
uint32_t __real_tud_cdc_n_write(uint8_t,const void *,uint32_t);
uint32_t __wrap_tud_cdc_n_write(uint8_t itf,const void *p,uint32_t n) {
 app_scope(); CHECK(n<=64u && ++step_writes<=1u && in_tx && primask && nesting); ++total_writes; inject(RESET_WRITE_BEFORE); uint32_t got=__real_tud_cdc_n_write(itf,p,n); write_return=got; tx_after_write=1; inject(RESET_WRITE_AFTER); inject(DEFER_RESET_WRITE_AFTER); if(injection==CLOCK_WRITE_AFTER)clock_after_write_fault=1; inject(CLOCK_WRITE_AFTER); return got;
}
uint32_t __real_tud_cdc_n_write_flush(uint8_t);
uint32_t __wrap_tud_cdc_n_write_flush(uint8_t itf) {
 app_scope(); ++step_flushes; inject(RESET_FLUSH_BEFORE); uint32_t n=__real_tud_cdc_n_write_flush(itf); inject(RESET_FLUSH_AFTER); inject(CLOCK_FLUSH_AFTER); flush_returned=1; return n;
}
mt_adc_dma_result_t __real_mt_adc_dma_read_block(mt_adc_dma_t *,mt_adc_dma_block_t *);
mt_adc_dma_result_t __wrap_mt_adc_dma_read_block(mt_adc_dma_t *d,mt_adc_dma_block_t *b) { app_scope(); CHECK(++step_adc_reads<=1u); ++total_adc_reads; inject(RESET_ADC_BEFORE); mt_adc_dma_result_t r=__real_mt_adc_dma_read_block(d,b); inject(RESET_ADC_AFTER); inject(SUSPEND_ADC_AFTER); return r; }
int __real_mt_live_adc_service(mt_live_adc *);
int __wrap_mt_live_adc_service(mt_live_adc *b) { app_scope(); CHECK(++step_services<=1u); ++total_services; return __real_mt_live_adc_service(b); }
int __real_mt_live_device_step(mt_live_device *);
int __wrap_mt_live_device_step(mt_live_device *d) { CHECK(primask && nesting && !in_tx && ++step_tx<=1u); ++total_tx; in_tx=1; tx_after_write=0; int r=__real_mt_live_device_step(d); CHECK(primask && nesting); if(tx_after_write) { CHECK(d->last_write_known && d->last_write_accepted==write_return); CHECK(time_after_write || clock_after_write_fault); write_known_commit=1; } in_tx=0; inject(RESET_POSTCOMMIT); return r; }
void __real_mt_usb_bridge_cleaned(uint32_t);
void __wrap_mt_usb_bridge_cleaned(uint32_t token) { CHECK(runtime.device.hardware_off && !runtime.adc.owns_peripherals && runtime.adc.state==MT_ADC_DMA_STOPPED); CHECK(!(*slot(TIM_CR1)&1u) && !*slot(ADC_CR2) && !(*slot(DMA_CCR)&1u)); ++total_cleaned; __real_mt_usb_bridge_cleaned(token); }
mt_usb_epoch __real_mt_usb_bridge_snapshot(void);
mt_usb_epoch __wrap_mt_usb_bridge_snapshot(void) { mt_usb_epoch e=__real_mt_usb_bridge_snapshot(); if(injection==PMA_REARM_WINDOW && flush_returned && !nesting && !mt_usb_bridge_actor())inject(PMA_REARM_WINDOW); return e; }
bool mt_usb_fsdev_ctr_deferred(void) { return true; }
bool mt_usb_fsdev_ctr_rearm(void) { CHECK(primask && nesting && runtime.in_step); CHECK(!tud_task_event_ready()); CHECK(hw.time_reads>active_step_time_reads && mt_timebase_healthy(&clock_source)); CHECK(!runtime.pending && !runtime.clock_fault); mt_usb_epoch e=mt_usb_bridge_snapshot(); CHECK(!e.faults && !e.exhausted && !e.pending_soft && e.cleaned==runtime.epoch); CHECK(e.current==runtime.epoch && e.soft_serial==runtime.soft_serial); if(runtime.adc.owns_peripherals)CHECK(step_services==1u); CHECK(++step_rearms<=1u); ++total_rearms; return true; }
void test_dcd_call(enum test_operation op) { (void)op; CHECK(primask && nesting && mt_usb_bridge_actor()); ++dcd_calls; }
void test_mount_hook(void) { CHECK(runtime.in_step); ++mounts; }
static mt_live_runtime_result step(void) {
 ++step_calls; step_reads=step_rx_bytes=step_writes=step_flushes=step_services=step_tx=step_adc_reads=step_rearms=0; active_step_time_reads=hw.time_reads; flush_returned=0;
 mt_live_runtime_result result=mt_live_runtime_step(&runtime); CHECK(!primask && !nesting && !mt_usb_bridge_actor() && !runtime.in_step && !in_tx); return result;
}
void test_pump(void) { unsigned n=0; while(tud_task_event_ready()) { CHECK(++n<1000u); mt_live_runtime_result result=step(); if(result==MT_LIVE_RUNTIME_RECOVERY || result==MT_LIVE_RUNTIME_TIME_FAULT || mt_usb_bridge_snapshot().pending_soft)break; } }
static void quiet(void) { CHECK(runtime.device.hardware_off && !runtime.adc.owns_peripherals && runtime.adc.state==MT_ADC_DMA_STOPPED); CHECK(!(*slot(TIM_CR1)&1u) && !*slot(ADC_CR2) && !(*slot(DMA_CCR)&1u)); }
static void setup(unsigned configured,unsigned dtr) {
 if(tud_inited())CHECK(tud_deinit(0));
 memset(&runtime,0,sizeof runtime); memset(&clock_source,0,sizeof clock_source); memset(&hw,0,sizeof hw); primask=nesting=pending_reset=reset_observed=recursive_calls=0; step_calls=total_read_flushes=total_cleaned=total_rearms=total_writes=total_adc_reads=total_services=total_tx=dcd_calls=mounts=0; in_tx=tx_after_write=time_after_write=write_return=write_known_commit=reset_after_commit=0; stop_in_tx=clock_after_write_fault=0; actor_at_injection=current_at_injection=0; injection=NONE; nonce=NONCE; sequence=2; test_reject_open=test_reject_xfer=-1; hw.tick=1;
 *slot(RCC_CR)=0x03030000u; *slot(RCC_CFGR)=0x001d840au; *slot(RCC_AHB)=0x14u; *slot(RCC_APB2)=0x4000u; *slot(RCC_APB1)=0x800001u; *slot(0x40010800u)=0x44444444u;
 mt_timebase_io ti={rd,wr,&hw}; CHECK(mt_timebase_init(&clock_source,&ti,72000000u)); mt_live_runtime_mmio io={rd,wr,&hw}; CHECK(mt_live_runtime_init(&runtime,&clock_source,&io,8000000u,lock,unlock,0x12345678u,dtr,1u)==MT_LIVE_RUNTIME_OK); CHECK(runtime.acquisition.driver==&runtime.adc && runtime.acquisition.device==&runtime.device && runtime.adc.stream_mode); CHECK(mt_live_runtime_step(&runtime)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(*slot(RCC_AHB)==0x15u && *slot(RCC_APB2)==0x4204u && *slot(RCC_APB1)==0x800003u); CHECK(!hw.cen_writes);
 memset(fake_ep,0,sizeof fake_ep); fake_epoch=fake_invalid_submissions=fake_mounts=fake_umounts=fake_line_calls=0; fake_last_dtr=fake_last_rts=fake_reject_next_in=false; fake_on_in_submit=fake_on_out_submit=NULL; fake_clear_logs(); const tusb_rhport_init_t init={.role=TUSB_ROLE_DEVICE,.speed=TUSB_SPEED_FULL}; CHECK(tusb_rhport_init(0,&init));
 if(configured!=2u) { fake_bus_reset(false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); CHECK(runtime.epoch==1u && mt_usb_bridge_snapshot().cleaned==1u && mt_usb_bridge_snapshot().processed_reset==1u); }
 if(configured==1u) { fake_configuration(1); CHECK(mt_usb_bridge_snapshot().configured==1u && mounts==1u); if(dtr)fake_line_state(1u); CHECK(step()==MT_LIVE_RUNTIME_OK); CHECK(runtime.open); }
 fake_clear_logs();
}
static int work(void) { mt_live_device *d=&runtime.device; return d->tx_length || d->want_caps || d->want_start || d->want_error || d->want_end || d->want_ack || d->state==MT_LIVE_HELLO_PENDING || ((d->state==MT_LIVE_STREAMING || d->state==MT_LIVE_DRAINING) && d->queue.used); }
static void drain(void) { for(unsigned i=0;i<1000u;i++) { if(fake_ep[FAKE_CDC_IN].pending)fake_complete_in(FAKE_CDC_IN,false); mt_live_runtime_result r=step(); CHECK(r==MT_LIVE_RUNTIME_OK); if(!work() && !fake_ep[FAKE_CDC_IN].pending && !tud_task_event_ready())return; } CHECK(0); }
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static uint32_t r32(const uint8_t *p) { return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static uint64_t r64(const uint8_t *p) { return r32(p)|((uint64_t)r32(p+4)<<32); }
static void w32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8u*i)); }
static void w64(uint8_t *p,uint64_t v) { for(unsigned i=0;i<8;i++)p[i]=(uint8_t)(v>>(8u*i)); }
static mt_live_runtime_result request(uint8_t type,uint64_t session,uint32_t seq,uint32_t req,const uint8_t *payload,uint16_t n) { mt_wire_message m={.type=type,.session=session,.sequence=seq,.request=req,.payload_length=n}; uint8_t wire[MT_WIRE_FRAME_MAX]; size_t length; if(n)memcpy(m.payload,payload,n); CHECK(mt_wire_encode_revision(&m,0,2,wire,sizeof wire,&length)==MT_WIRE_OK && length<=64u); fake_out(wire,length,false); return step(); }
static void hello(void) { uint8_t p[8]; w64(p,nonce); CHECK(request(HELLO,0,1,1,p,8)==MT_LIVE_RUNTIME_OK); drain(); CHECK(runtime.device.state==MT_LIVE_STOPPED && runtime.device.nonce==nonce); }
static void ready(void) { setup(1,0); hello(); CHECK(request(CAPS,nonce,sequence,sequence,NULL,0)==MT_LIVE_RUNTIME_OK); ++sequence; drain(); CHECK(runtime.device.caps_ready); }
static mt_live_runtime_result start(void) { uint8_t p[4]={1,0,0,0}; mt_live_runtime_result r=request(START,nonce,sequence,sequence,p,4); ++sequence; return r; }
static void running(void) { ready(); CHECK(start()==MT_LIVE_RUNTIME_OK); drain(); CHECK(runtime.device.state==MT_LIVE_STREAMING && runtime.adc.owns_peripherals && runtime.adc.state==MT_ADC_DMA_RUNNING && hw.cen_writes==1u); }
static mt_live_runtime_result stop(void) { uint8_t p[4]; w32(p,runtime.device.generation); mt_live_runtime_result r=request(STOP,nonce,sequence,sequence,p,4); ++sequence; return r; }
static void transfer(unsigned count) {
 for(unsigned i=0;i<count;i++) { unsigned p=(unsigned)(hw.transferred%256u); CHECK(*slot(DMA_CCR)&1u); runtime.adc.dma_samples[p]=code_at(hw.transferred++); p=(p+1u)%256u; *slot(DMA_N)=p?256u-p:256u; if(p==128u)*slot(DMA_ISR)|=DMA_HT; if(!p)*slot(DMA_ISR)|=DMA_TC; }
 uint64_t target=hw.cen_at+hw.transferred*20u+2u; if(target>hw.elapsed)hw.elapsed=target;
}
static void full_block(void) { transfer(127u); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(1u); CHECK(step()==MT_LIVE_RUNTIME_OK); }
static unsigned decode(mt_wire_message *out,unsigned capacity) { size_t at=0; unsigned n=0; for(size_t i=0;i<fake_host_size;i++)if(!fake_host[i]) { if(i>at) { CHECK(n<capacity); CHECK(mt_wire_decode_revision(fake_host+at,i-at,0,2,&out[n])==MT_WIRE_OK); ++n; } at=i+1u; } CHECK(at==fake_host_size); return n; }
static uint32_t oracle_crc(uint64_t count) { uint32_t crc=UINT32_MAX; for(uint64_t i=0;i<count;i++)for(unsigned byte=0;byte<2;byte++) { uint8_t in=(uint8_t)(code_at(i)>>(8u*byte)),rev=0; for(unsigned b=0;b<8;b++)rev=(uint8_t)(((uint32_t)rev<<1)|(((uint32_t)in>>b)&1u)); crc^=(uint32_t)rev<<24; for(unsigned b=0;b<8;b++)crc=(crc<<1)^((crc&0x80000000u)?0x04c11db7u:0); } uint32_t rev=0; for(unsigned b=0;b<32;b++)rev=(rev<<1)|((crc>>b)&1u); return ~rev; }
static void check_wire(uint64_t expected) {
 mt_wire_message m[128]; unsigned n=decode(m,128),ends=0,acks=0; uint64_t cursor=0; uint32_t last_data=0,end_seq=0;
 CHECK(n>=5u && m[0].type==HELLO_ACK && m[1].type==CAPS_REPLY && m[2].type==START_ACK); CHECK(r32(m[1].payload+8)==50000u && r16(m[1].payload+16)==128u);
 for(unsigned i=0;i<n;i++) { CHECK(m[i].session==nonce && m[i].sequence==i+1u);
  if(m[i].type==DATA) { CHECK(!ends && r32(m[i].payload)==3u && r64(m[i].payload+8)==cursor); unsigned count=r16(m[i].payload+16); CHECK(count && count<=128u && m[i].payload_length==20u+2u*count); for(unsigned j=0;j<count;j++)CHECK(r16(m[i].payload+20u+2u*j)==code_at(cursor+j)); cursor+=count; last_data=m[i].sequence; }
  if(m[i].type==END) { ++ends; end_seq=m[i].sequence; if(r64(m[i].payload+8)!=expected)fprintf(stderr,"END expected=%" PRIu64 " actual=%" PRIu64 " reason=%u flags=%u elapsed=%" PRIu64 "\n",expected,r64(m[i].payload+8),(unsigned)r16(m[i].payload+32),(unsigned)r16(m[i].payload+34),hw.elapsed); CHECK(m[i].payload_length==40u && m[i].request==0u && r32(m[i].payload)==3u && r64(m[i].payload+8)==expected && r64(m[i].payload+16)==0u); CHECK(r32(m[i].payload+24)==last_data && r32(m[i].payload+28)==oracle_crc(expected)); CHECK(r16(m[i].payload+32)==0u && r16(m[i].payload+34)==3u && r32(m[i].payload+36)==4u); }
  if(m[i].type==STOP_ACK) { ++acks; CHECK(ends==1u && m[i].request==4u && r32(m[i].payload)==3u && r64(m[i].payload+4)==expected && r32(m[i].payload+12)==end_seq && r32(m[i].payload+16)==3u); }
 } CHECK(cursor==expected && ends==1u && acks==1u); CHECK(!runtime.device.produced_known && !runtime.device.produced_end);
}
static void test_handshake(void) { running(); CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); quiet(); check_wire(0); CHECK(!fake_invalid_submissions); }
static void test_samples(void) { running(); for(unsigned i=0;i<4u;i++) { full_block(); drain(); } transfer(17); CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); quiet(); check_wire(529); }
static void test_tail_sweep(void) { for(unsigned count=0;count<256u;count++) { running(); if(count>=127u) { transfer(63); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(64); advance(16); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(count-127u); } else transfer(count); CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); quiet(); check_wire(count); } }
static void test_startup(void) { setup(2,0); unsigned before=hw.time_reads; CHECK(step()==MT_LIVE_RUNTIME_IDLE && hw.time_reads>before && !runtime.epoch && !dcd_calls); fake_bus_reset(false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); CHECK(!runtime.open); fake_configuration(1); CHECK(runtime.open && mounts==1u); hello(); }
static void test_real_enumeration(void) { setup(2,0); fake_start(); CHECK(runtime.open && mounts==1u && runtime.epoch==1u && !runtime.device.nonce); hello(); }
static void test_arguments(void) { setup(2,0); mt_live_runtime blank={0}; mt_live_runtime_mmio io={rd,wr,&hw}; CHECK(mt_live_runtime_step(NULL)==MT_LIVE_RUNTIME_ARGUMENT && mt_live_runtime_step(&blank)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,lock,unlock,0,0,1)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(tud_deinit(0)); CHECK(mt_live_runtime_init(&runtime,&clock_source,&io,8000000u,lock,unlock,0,0,1)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,lock,unlock,0,0,0)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,lock,unlock,0,0,500)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,NULL,unlock,0,0,1)==MT_LIVE_RUNTIME_ARGUMENT); CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,lock,unlock,0,2,1)==MT_LIVE_RUNTIME_ARGUMENT); }
static void test_hard_reset(void) { running(); full_block(); CHECK(fake_ep[FAKE_CDC_IN].pending); unsigned f=total_read_flushes; fake_bus_reset(false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(total_read_flushes==f && runtime.epoch==2u && !runtime.open && mt_usb_bridge_snapshot().cleaned==2u); fake_configuration(1); ++nonce; sequence=2; hello(); CHECK(runtime.device.state==MT_LIVE_STOPPED); }
static void test_stop_retry(void) { running(); hw.ignore_stop=4u; fake_bus_reset(false); unsigned c=total_cleaned; CHECK(step()==MT_LIVE_RUNTIME_RECOVERY && runtime.pending==2u && runtime.adc.owns_peripherals && total_cleaned==c); unsigned before=hw.stop_writes; CHECK(step()==MT_LIVE_RUNTIME_RECOVERY && hw.stop_writes>before && total_cleaned==c); hw.ignore_stop=0; CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(mt_usb_bridge_snapshot().cleaned==2u); }
static void test_soft(void) { running(); const uint8_t p[]={4,8}; fake_out(p,sizeof p,false); CHECK(step()==MT_LIVE_RUNTIME_OK && runtime.device.rx_used==sizeof p); unsigned f=total_read_flushes; fake_signal(DCD_EVENT_SUSPEND,false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(!runtime.open && runtime.rx_flush_pending && !runtime.device.rx_used && total_read_flushes==f); fake_signal(DCD_EVENT_RESUME,false); test_pump(); CHECK(runtime.open && total_read_flushes==f+1u && runtime.epoch==1u); ++nonce; hello(); }
static void test_dtr(void) { setup(1,1); hello(); CHECK(request(CAPS,nonce,2,2,NULL,0)==MT_LIVE_RUNTIME_OK); sequence=3; drain(); CHECK(start()==MT_LIVE_RUNTIME_OK); drain(); fake_line_state(0); quiet(); CHECK(!runtime.open && runtime.rx_flush_pending); unsigned f=total_read_flushes; fake_line_state(1); CHECK(runtime.open && total_read_flushes==f+1u); ++nonce; hello(); }
static void test_time_retry(void) { running(); hw.ignore_stop=4; *slot(TIME_PSC)=70; unsigned c=total_cleaned,r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && runtime.adc.owns_peripherals && total_cleaned==c && total_rearms==r); unsigned before=hw.stop_writes; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && hw.stop_writes>before); hw.ignore_stop=0; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT); quiet(); CHECK(total_cleaned==c && total_rearms==r && runtime.clock_fault && runtime.pending==2u); }
static void test_clock_stop(void) { running(); injection=CLOCK_STOP; fake_bus_reset(false); mt_live_runtime_result r=step(); CHECK(r==MT_LIVE_RUNTIME_TIME_FAULT); quiet(); CHECK(runtime.clock_fault); mt_usb_epoch e=mt_usb_bridge_snapshot(); CHECK(e.cleaned==0u && e.processed_reset==0u && runtime.pending==2u && tud_task_event_ready()); unsigned count=total_cleaned; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && total_cleaned==count); e=mt_usb_bridge_snapshot(); CHECK(e.cleaned==0u && e.processed_reset==0u); }
static void test_reentry(void) { ready(); injection=REENTER_CAL; CHECK(start()==MT_LIVE_RUNTIME_OK && recursive_calls==1u); drain(); CHECK(runtime.device.state==MT_LIVE_STREAMING); }
static void test_reset_cal(void) { ready(); injection=RESET_CAL; (void)start(); CHECK(actor_at_injection==1u && current_at_injection==2u && !hw.cen_writes); (void)step(); quiet(); CHECK(runtime.epoch==2u); }
static void test_reset_cen(void) { ready(); injection=RESET_CEN; (void)start(); CHECK(actor_at_injection==1u && current_at_injection==1u && reset_observed==1u && hw.cen_writes==1u); (void)step(); quiet(); CHECK(runtime.epoch==2u); }
static void probe_reset(int point,unsigned adc) { running(); if(adc) { transfer(127u); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(1); } injection=point; unsigned f=total_read_flushes; (void)step(); CHECK(injection==NONE && actor_at_injection==1u && current_at_injection==2u); (void)step(); quiet(); CHECK(runtime.epoch==2u && total_read_flushes==f && !runtime.open); }
static void test_reset_read_before(void) { probe_reset(RESET_READ_BEFORE,0); }
static void test_reset_read_after(void) { probe_reset(RESET_READ_AFTER,0); }
static void test_reset_adc_before(void) { probe_reset(RESET_ADC_BEFORE,1); }
static void test_reset_adc_after(void) { probe_reset(RESET_ADC_AFTER,1); }
static void probe_write(int point) { ready(); injection=point; (void)start(); CHECK(injection==NONE && write_known_commit); (void)step(); quiet(); CHECK(runtime.epoch==2u); }
static void test_reset_write_before(void) { probe_write(RESET_WRITE_BEFORE); }
static void test_reset_write_after(void) { probe_write(RESET_WRITE_AFTER); CHECK(write_return>0); }
static void test_deferred_write_reset(void) { probe_write(DEFER_RESET_WRITE_AFTER); CHECK(reset_after_commit && write_return>0 && time_after_write>=2u); }
static void test_postcommit_reset(void) { ready(); injection=RESET_POSTCOMMIT; (void)start(); CHECK(reset_after_commit && runtime.epoch==2u); (void)step(); quiet(); }
static void test_reset_flush_before(void) { probe_reset(RESET_FLUSH_BEFORE,0); }
static void test_reset_flush_after(void) { probe_reset(RESET_FLUSH_AFTER,0); }
static void test_clock_flush(void) { running(); injection=CLOCK_FLUSH_AFTER; unsigned r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT); quiet(); CHECK(runtime.clock_fault && total_rearms==r); }
static void test_suspend_adc(void) { running(); injection=SUSPEND_ADC_AFTER; (void)step(); quiet(); CHECK(!runtime.open && runtime.rx_flush_pending && runtime.epoch==1u); fake_signal(DCD_EVENT_RESUME,false); test_pump(); CHECK(runtime.open); }
static void test_suspend_read(void) { running(); injection=SUSPEND_READ_AFTER; (void)step(); quiet(); CHECK(!runtime.open && runtime.rx_flush_pending && runtime.epoch==1u); fake_signal(DCD_EVENT_RESUME,false); test_pump(); CHECK(runtime.open); }
static void test_queue_rearm(void) { running(); fake_signal(DCD_EVENT_RESUME,false); fake_signal(DCD_EVENT_RESUME,false); unsigned r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_OK && tud_task_event_ready() && total_rearms==r); CHECK(step()==MT_LIVE_RUNTIME_OK && !tud_task_event_ready() && total_rearms==r+1u && step_services==1u); }
static void test_rx_bound(void) { setup(1,0); uint8_t p[64]; memset(p,2,sizeof p); fake_out(p,sizeof p,false); CHECK(step()==MT_LIVE_RUNTIME_OK && step_rx_bytes==64u && step_reads==1u && step_tx==1u); CHECK(runtime.device.rx_used==64u); }
static void test_adc_deadline(void) { running(); advance(2561u); (void)step(); CHECK(!runtime.adc.owns_peripherals && runtime.device.first_reason==2u); quiet(); }
static void test_no_rearm_poison(void) { running(); mt_usb_bridge_pma_overrun(); unsigned r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(total_rearms==r && !runtime.open); }
static void test_zero_backpressure(void) { running(); full_block(); unsigned accepted=0; for(unsigned i=0;i<10u;i++) { CHECK(step()==MT_LIVE_RUNTIME_OK); if(runtime.device.last_write_known && runtime.device.last_write_accepted==0u)++accepted; } CHECK(accepted); CHECK(runtime.device.state==MT_LIVE_STREAMING); drain(); CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); check_wire(128); }
static void test_pma_window(void) { running(); injection=PMA_REARM_WINDOW; unsigned r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_OK); CHECK(injection==NONE && total_rearms==r && mt_usb_bridge_snapshot().faults); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); }
static void test_clock_write(void) { ready(); injection=CLOCK_WRITE_AFTER; CHECK(start()==MT_LIVE_RUNTIME_TIME_FAULT); CHECK(write_return>0 && runtime.device.last_write_known && runtime.device.last_write_accepted==write_return && stop_in_tx); quiet(); }
static void test_callback_ownership(void) { running(); const uint8_t p=0; CHECK(runtime.device.io.write(&runtime,&p,1)==SIZE_MAX); CHECK(runtime.device.io.isolate_tx(&runtime)==0); CHECK(!runtime.writer_active && runtime.adc.owns_peripherals); }
static void test_unsafe_init(void) { const uint32_t addresses[]={RCC_APB2RSTR,RCC_APB1RSTR,TIM_CR1,ADC_CR2,DMA_CCR}; const uint32_t values[]={1u<<9,1u<<1,1u,1u,1u}; for(unsigned i=0;i<5;i++) { setup(2,0); CHECK(tud_deinit(0)); mt_live_runtime blank={0}; mt_live_runtime_mmio io={rd,wr,&hw}; *slot(addresses[i])=values[i]; unsigned stops=hw.stop_writes; CHECK(mt_live_runtime_init(&blank,&clock_source,&io,8000000u,lock,unlock,0,0,1)==MT_LIVE_RUNTIME_ADC_UNSAFE); CHECK(*slot(addresses[i])==values[i] && hw.stop_writes==stops && !blank.initialized); } }
static void test_soft_stop_retry(void) { running(); hw.ignore_stop=4u; fake_signal(DCD_EVENT_SUSPEND,false); unsigned c=total_cleaned,r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_RECOVERY && runtime.pending==1u && runtime.adc.owns_peripherals && total_cleaned==c && total_rearms==r); unsigned before=hw.stop_writes; CHECK(step()==MT_LIVE_RUNTIME_RECOVERY && hw.stop_writes>before); hw.ignore_stop=0; CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(!runtime.pending && runtime.rx_flush_pending && total_rearms==r); fake_signal(DCD_EVENT_RESUME,false); test_pump(); CHECK(runtime.open); }
static void test_back_to_back_soft(void) { running(); fake_signal(DCD_EVENT_SUSPEND,false); fake_signal(DCD_EVENT_RESUME,false); CHECK(!mt_usb_bridge_snapshot().pending_soft); (void)step(); quiet(); CHECK(runtime.soft_serial==1u && runtime.device.state==MT_LIVE_FAULT); test_pump(); CHECK(runtime.open); ++nonce; hello(); }
static void test_hard_partial_wire(void) { running(); full_block(); CHECK(fake_ep[FAKE_CDC_IN].pending && fake_ep[FAKE_CDC_IN].length==64u); fake_complete_in(FAKE_CDC_IN,false); size_t old=fake_host_size; CHECK(old>=64u && fake_host[old-1u]!=0); fake_bus_reset(false); test_pump(); quiet(); CHECK(mt_usb_bridge_snapshot().processed_reset==2u); fake_configuration(1); ++nonce; sequence=2; hello(); CHECK(fake_host_size>old && fake_host[old]==0u); mt_wire_message m; CHECK(mt_wire_decode_revision(fake_host+old+1u,fake_host_size-old-2u,0,2,&m)==MT_WIRE_OK && m.type==HELLO_ACK && m.session==nonce); }
static void test_soft_pending_writer(void) { running(); full_block(); CHECK(fake_ep[FAKE_CDC_IN].pending); fake_signal(DCD_EVENT_SUSPEND,false); (void)step(); quiet(); fake_signal(DCD_EVENT_RESUME,false); test_pump(); ++nonce; sequence=2; uint8_t p[8]; w64(p,nonce); CHECK(request(HELLO,0,1,1,p,8)==MT_LIVE_RUNTIME_OK); drain(); CHECK(runtime.device.state==MT_LIVE_STOPPED && runtime.device.nonce==nonce); size_t at=0; mt_wire_message last={0}; unsigned valid=0,invalid=0; for(size_t i=0;i<fake_host_size;i++)if(fake_host[i]==0u) { if(i>at) { mt_wire_result result=mt_wire_decode_revision(fake_host+at,i-at,0,2,&last); if(result==MT_WIRE_OK)++valid; else ++invalid; } at=i+1u; } CHECK(valid>=4u && invalid==1u && last.type==HELLO_ACK && last.session==nonce && at==fake_host_size); }
static void test_queued_usb_services_adc(void) { running(); transfer(127); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(1); for(unsigned i=0;i<8u;i++)fake_signal(DCD_EVENT_RESUME,false); unsigned a=total_adc_reads,r=total_rearms; CHECK(step()==MT_LIVE_RUNTIME_OK && runtime.adc.first_sample_index==128u && total_adc_reads==a+1u && total_rearms==r && tud_task_event_ready()); for(unsigned i=0;i<7u;i++)CHECK(step()==MT_LIVE_RUNTIME_OK); CHECK(total_rearms==r+1u && total_adc_reads==a+8u); drain(); CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); check_wire(128); }
static void test_clock_soft_cleanup(void) { running(); unsigned c=total_cleaned,r=total_rearms; injection=CLOCK_STOP; fake_signal(DCD_EVENT_SUSPEND,false); CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && runtime.clock_fault && runtime.pending==2u); quiet(); CHECK(total_cleaned==c && total_rearms==r); CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && total_cleaned==c); }
static void test_clock_dtr_cleanup(void) { setup(1,1); hello(); CHECK(request(CAPS,nonce,2,2,NULL,0)==MT_LIVE_RUNTIME_OK); sequence=3; drain(); CHECK(start()==MT_LIVE_RUNTIME_OK); drain(); unsigned c=total_cleaned,r=total_rearms; injection=CLOCK_STOP; const uint8_t packet[8]={0x21,CDC_REQUEST_SET_CONTROL_LINE_STATE,0,0,0,0,0,0}; dcd_event_setup_received(0,packet,true); CHECK(step()==MT_LIVE_RUNTIME_OK && fake_ep[0x80].pending); r=total_rearms; fake_complete_in(0x80,false); CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT); quiet(); CHECK(runtime.clock_fault && runtime.pending==2u && total_cleaned==c && total_rearms==r); }
static void critic_stage_short_fifo(void) {
 running(); full_block();
 unsigned n=0;
 while(runtime.device.tx_length) {
  CHECK(++n<10u && fake_ep[FAKE_CDC_IN].pending);
  fake_complete_in(FAKE_CDC_IN,false); CHECK(step()==MT_LIVE_RUNTIME_OK);
 }
 CHECK(runtime.device.prefix_end==128u && fake_ep[FAKE_CDC_IN].pending);
 uint32_t free=tud_cdc_n_write_available(0); CHECK(free>0u && free<64u);
}
static void critic_positive_partial(void) {
 critic_stage_short_fifo(); uint32_t before=tud_cdc_n_write_available(0);
 CHECK(stop()==MT_LIVE_RUNTIME_OK);
 CHECK(runtime.device.last_write_known && runtime.device.last_write_accepted==before && before<64u && before>0u);
 CHECK(write_return==before && runtime.device.tx_offset==before && runtime.device.tx_length>before);
 drain(); quiet(); check_wire(128u);
}
static void critic_soft_fifo(void) {
 critic_stage_short_fifo(); uint32_t before=tud_cdc_n_write_available(0); size_t host_before=fake_host_size;
 fake_signal(DCD_EVENT_SUSPEND,false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet();
 CHECK(tud_cdc_n_write_available(0)==before && fake_host_size==host_before);
 fake_signal(DCD_EVENT_RESUME,false); test_pump(); CHECK(runtime.open);
 CHECK(tud_cdc_n_write_available(0)==before && fake_host_size==host_before);
}

static void test_hard_reset_cancels_soft_flush(void) { running(); fake_signal(DCD_EVENT_SUSPEND,false); CHECK(step()==MT_LIVE_RUNTIME_IDLE && runtime.rx_flush_pending); unsigned flushed=total_read_flushes; fake_bus_reset(false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet(); CHECK(!runtime.rx_flush_pending && total_read_flushes==flushed && runtime.epoch==2u); test_pump(); fake_configuration(1); CHECK(runtime.open && total_read_flushes==flushed); ++nonce; hello(); }

static void diag_identity(void) {
 CHECK(mt_stream_diagnostics.magic==UINT32_C(0x4d544447) && mt_stream_diagnostics.version==1u && mt_stream_diagnostics.bytes==136u);
 CHECK(mt_stream_diagnostics.firmware_build==UINT32_C(0x12345678));
 CHECK(mt_stream_diagnostics.generation==3u && mt_stream_diagnostics.nonce_low==(uint32_t)NONCE && mt_stream_diagnostics.nonce_high==(uint32_t)(NONCE>>32));
}
static void diag_unchanged(mt_stream_diag_record saved,unsigned reads) {
 mt_stream_diag_record now_record=mt_stream_diagnostics;
 CHECK(memcmp(&saved,&now_record,sizeof saved)==0 && dwt.reads==reads);
}
static void telemetry_armed_until_start(void) {
 setup(1,0); CHECK(mt_stream_diagnostics.phase==1u && !mt_stream_diagnostics.step_count);
 hello(); CHECK(mt_stream_diagnostics.phase==1u && !mt_stream_diagnostics.generation);
 CHECK(request(CAPS,nonce,2u,2u,NULL,0)==MT_LIVE_RUNTIME_OK); sequence=3; drain();
 CHECK(mt_stream_diagnostics.phase==1u && !mt_stream_diagnostics.tx_count);
 CHECK(start()==MT_LIVE_RUNTIME_OK); CHECK(mt_stream_diagnostics.phase==2u); diag_identity();
 CHECK(mt_stream_diagnostics.step_count==1u && mt_stream_diagnostics.tx_count==1u && !mt_stream_diagnostics.poll_count);
 CHECK(mt_stream_diagnostics.step_body_max_cycles>0u && mt_stream_diagnostics.tx_max_cycles>0u);
}
static void telemetry_empty_poll_existing_time(void) {
 running(); uint32_t before=mt_stream_diagnostics.poll_count,copies=mt_stream_diagnostics.copy_count;
 unsigned polls=hook_polls,reads=hw.time_reads; advance(41u); CHECK(step()==MT_LIVE_RUNTIME_OK);
 CHECK(mt_stream_diagnostics.poll_count==before+1u && hook_polls==polls+1u && !step_writes);
 CHECK(mt_stream_diagnostics.copy_count==copies && poll_delta>=41u && hw.time_reads>reads);
 CHECK(mt_stream_diagnostics.driver_observed_poll_gap_us>=poll_delta && mt_stream_diagnostics.poll_gap_max_cycles>0u);
 CHECK(!mt_stream_diagnostics.flags && mt_timebase_healthy(&clock_source));
}
static void telemetry_copy_and_pre_tx_queue_peak(void) {
 running(); uint32_t count=mt_stream_diagnostics.copy_count; full_block();
 CHECK(mt_stream_diagnostics.copy_count==count+1u && hook_copies==1u && copy_delta>0u);
 CHECK(mt_stream_diagnostics.copy_max_us==copy_delta && mt_stream_diagnostics.queue_high_water==1u);
 drain(); CHECK(!runtime.device.queue.used && mt_stream_diagnostics.queue_high_water==1u);
 CHECK(runtime.device.prefix_end==128u && mt_stream_diagnostics.phase==2u);
 CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); check_wire(128u);
}
static void telemetry_rejected_copy_still_counted(void) {
 running(); transfer(127u); CHECK(step()==MT_LIVE_RUNTIME_OK); transfer(1u);
 reject_copy=1u; copy_ndtr_reads=0u; CHECK(step()==MT_LIVE_RUNTIME_OK);
 CHECK(!reject_copy && hook_copies==1u && mt_stream_diagnostics.copy_count==1u);
 CHECK(runtime.adc.first_fault==MT_ADC_DMA_FAULT_TRANSFER && mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_TRANSFER);
 CHECK(mt_stream_diagnostics.first_acquisition_reason==2u && mt_stream_diagnostics.first_runtime_result==UINT32_MAX);
 CHECK(!runtime.device.queue.used && runtime.device.prefix_end==0u);
}
static void telemetry_stop_freezes_after_fifo_acceptance(void) {
 running(); full_block(); CHECK(stop()==MT_LIVE_RUNTIME_OK); quiet();
 CHECK(mt_stream_diagnostics.phase==2u && runtime.device.state==MT_LIVE_DRAINING);
 unsigned steps=0; while(mt_stream_diagnostics.phase==2u) {
  CHECK(++steps<100u); if(fake_ep[FAKE_CDC_IN].pending)fake_complete_in(FAKE_CDC_IN,false);
  CHECK(step()==MT_LIVE_RUNTIME_OK);
  if(runtime.device.want_ack || runtime.device.want_end || runtime.device.tx_length)CHECK(mt_stream_diagnostics.phase==2u);
 }
 CHECK(mt_stream_diagnostics.phase==3u && runtime.device.state==MT_LIVE_NEEDS_HELLO && runtime.device.end_complete);
 CHECK(!runtime.device.want_ack && !runtime.device.tx_length && fake_ep[FAKE_CDC_IN].pending);
 CHECK(mt_stream_diagnostics.final_runtime_result==MT_LIVE_RUNTIME_OK && !(mt_stream_diagnostics.flags&8u));
 CHECK(mt_stream_diagnostics.final_core_state==MT_LIVE_NEEDS_HELLO && mt_stream_diagnostics.final_hardware_off==1u);
 CHECK(mt_stream_diagnostics.final_adc_state==MT_ADC_DMA_STOPPED && !mt_stream_diagnostics.final_adc_owned);
 mt_stream_diag_record saved=mt_stream_diagnostics; unsigned reads=dwt.reads;
 drain(); check_wire(128u); diag_unchanged(saved,reads);
}
static void telemetry_first_fault_before_tx(void) {
 running(); advance(2561u); CHECK(step()==MT_LIVE_RUNTIME_OK);
 CHECK(mt_stream_diagnostics.flags&4u); CHECK(mt_stream_diagnostics.first_runtime_result==UINT32_MAX);
 CHECK(mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_DEADLINE && mt_stream_diagnostics.first_acquisition_reason==2u);
 CHECK(mt_stream_diagnostics.first_epoch==1u && !mt_stream_diagnostics.first_local_error);
 CHECK(mt_stream_diagnostics.phase==2u); *slot(TIME_PSC)=70u; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT);
 CHECK(mt_stream_diagnostics.phase==3u && mt_stream_diagnostics.final_runtime_result==MT_LIVE_RUNTIME_TIME_FAULT);
 CHECK(mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_DEADLINE && mt_stream_diagnostics.first_runtime_result==UINT32_MAX);
}
static void telemetry_fault_freeze_waits_actual_off(void) {
 running(); hw.ignore_stop=4u; *slot(TIME_PSC)=70u; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT);
 CHECK(mt_stream_diagnostics.phase==2u && mt_stream_diagnostics.final_adc_owned && !mt_stream_diagnostics.final_hardware_off);
 CHECK(mt_stream_diagnostics.first_runtime_result==MT_LIVE_RUNTIME_TIME_FAULT && mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_CLOCK);
 CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT && mt_stream_diagnostics.phase==2u);
 hw.ignore_stop=0u; CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT); quiet(); CHECK(mt_stream_diagnostics.phase==3u);
 mt_stream_diag_record saved=mt_stream_diagnostics; unsigned reads=dwt.reads;
 CHECK(step()==MT_LIVE_RUNTIME_TIME_FAULT); diag_unchanged(saved,reads);
}
static void telemetry_replacement_hello_separates_runs(void) {
 running(); full_block(); uint32_t count=mt_stream_diagnostics.step_count; ++nonce; sequence=2u;
 uint8_t p[8]; w64(p,nonce); CHECK(request(HELLO,0u,1u,1u,p,8u)==MT_LIVE_RUNTIME_OK);
 quiet(); CHECK(mt_stream_diagnostics.phase==3u && (mt_stream_diagnostics.flags&8u)); diag_identity();
 CHECK(mt_stream_diagnostics.final_runtime_result==UINT32_MAX && mt_stream_diagnostics.final_core_state==MT_LIVE_HELLO_PENDING);
 CHECK(mt_stream_diagnostics.step_count==count && mt_stream_diagnostics.queue_high_water==1u);
 mt_stream_diag_record saved=mt_stream_diagnostics; unsigned reads=dwt.reads;
 drain(); CHECK(runtime.device.nonce==nonce && runtime.device.generation==0u);
 CHECK(request(CAPS,nonce,2u,2u,NULL,0u)==MT_LIVE_RUNTIME_OK); sequence=3u; drain();
 CHECK(start()==MT_LIVE_RUNTIME_OK); drain(); CHECK(runtime.device.state==MT_LIVE_STREAMING); diag_unchanged(saved,reads);
}
static void telemetry_replacement_preserves_precommit_fault(void) {
 running(); advance(2561u); ++nonce; uint8_t p[8]; w64(p,nonce);
 CHECK(request(HELLO,0u,1u,1u,p,8u)==MT_LIVE_RUNTIME_OK);
 CHECK(mt_stream_diagnostics.phase==3u && (mt_stream_diagnostics.flags&12u)==12u); diag_identity();
 CHECK(mt_stream_diagnostics.first_runtime_result==UINT32_MAX && mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_DEADLINE);
 CHECK(mt_stream_diagnostics.first_acquisition_reason==2u && mt_stream_diagnostics.final_core_state==MT_LIVE_HELLO_PENDING);
 mt_stream_diag_record saved=mt_stream_diagnostics; unsigned reads=dwt.reads;
 drain(); CHECK(runtime.device.nonce==nonce && !runtime.device.first_reason && !runtime.device.local_error); diag_unchanged(saved,reads);
}
static void telemetry_stop_tail_queue_peak(void) {
 running(); transfer(17u); CHECK(stop()==MT_LIVE_RUNTIME_OK); quiet();
 CHECK(mt_stream_diagnostics.copy_count==0u && mt_stream_diagnostics.queue_high_water==1u && mt_stream_diagnostics.phase==2u);
 drain(); check_wire(17u); CHECK(mt_stream_diagnostics.phase==3u && mt_stream_diagnostics.queue_high_water==1u);
}
static void telemetry_replacement_cannot_fake_off(void) {
 running(); hw.ignore_stop=4u; ++nonce; uint8_t p[8]; w64(p,nonce);
 (void)request(HELLO,0u,1u,1u,p,8u); CHECK(mt_stream_diagnostics.phase==2u && runtime.adc.owns_peripherals);
 CHECK(!(mt_stream_diagnostics.flags&8u)); hw.ignore_stop=0u; fake_bus_reset(false); (void)step(); quiet();
 CHECK(mt_stream_diagnostics.phase==3u && !mt_stream_diagnostics.final_adc_owned);
}
static void telemetry_failed_start_retains_attempt(void) {
 ready(); hw.cal_stuck=1u; (void)start(); quiet(); diag_identity();
 CHECK(mt_stream_diagnostics.phase==3u && (mt_stream_diagnostics.flags&4u));
 CHECK(mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_CALIBRATION && !hw.cen_writes);
}
static void telemetry_preliminary_quiesce_retains_attempt(void) {
 ready(); CHECK(mt_stream_diagnostics.phase==1u && !mt_stream_diagnostics.generation);
 CHECK(runtime.adc.state==MT_ADC_DMA_STOPPED && !runtime.adc.owns_peripherals);
 /* An unknown active DMA owner must not be stolen by the preliminary barrier. */
 unsigned stops=hw.stop_writes; *slot(DMA_CCR)|=1u;
 CHECK(start()==MT_LIVE_RUNTIME_OK); diag_identity();
 CHECK(runtime.device.state==MT_LIVE_NEEDS_HELLO && !runtime.device.hardware_off);
 CHECK(runtime.adc.state==MT_ADC_DMA_FAULTED && !runtime.adc.owns_peripherals);
 CHECK(runtime.adc.first_fault==MT_ADC_DMA_FAULT_STOP && !hw.cen_writes && hw.stop_writes==stops);
 CHECK(mt_stream_diagnostics.phase==2u && (mt_stream_diagnostics.flags&4u) && !mt_stream_diagnostics.freeze_cycle);
 CHECK(mt_stream_diagnostics.first_runtime_result==UINT32_MAX && mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_STOP);
 CHECK(mt_stream_diagnostics.first_acquisition_reason==5u && mt_stream_diagnostics.first_epoch==1u && !mt_stream_diagnostics.first_local_error);
 CHECK(!mt_stream_diagnostics.final_hardware_off && !mt_stream_diagnostics.final_adc_owned && mt_stream_diagnostics.final_adc_state==MT_ADC_DMA_FAULTED);
 CHECK(step()==MT_LIVE_RUNTIME_OK && mt_stream_diagnostics.phase==2u); diag_identity();
 /* Model the unknown owner releasing DMA, then use real reset cleanup. */
 *slot(DMA_CCR)&=~1u; fake_bus_reset(false); CHECK(step()==MT_LIVE_RUNTIME_IDLE); quiet();
 CHECK(mt_stream_diagnostics.phase==3u && !(mt_stream_diagnostics.flags&8u)); diag_identity();
 CHECK(mt_stream_diagnostics.final_runtime_result==MT_LIVE_RUNTIME_IDLE && mt_stream_diagnostics.final_core_state==MT_LIVE_FAULT);
 CHECK(mt_stream_diagnostics.final_hardware_off==1u && !mt_stream_diagnostics.final_adc_owned && mt_stream_diagnostics.final_adc_state==MT_ADC_DMA_STOPPED);
 CHECK(mt_stream_diagnostics.first_adc_fault==MT_ADC_DMA_FAULT_STOP && mt_stream_diagnostics.first_acquisition_reason==5u);
 mt_stream_diag_record saved=mt_stream_diagnostics; unsigned reads=dwt.reads;
 fake_configuration(1); ++nonce; sequence=2u; hello();
 CHECK(request(CAPS,nonce,sequence,sequence,NULL,0u)==MT_LIVE_RUNTIME_OK); ++sequence; drain();
 CHECK(start()==MT_LIVE_RUNTIME_OK); drain(); CHECK(runtime.device.state==MT_LIVE_STREAMING);
 diag_unchanged(saved,reads);
}
static void telemetry_invalid_start_stays_armed(void) {
 ready(); unsigned stops=hw.stop_writes; uint8_t invalid[4]={2u,0u,0u,0u};
 CHECK(request(START,nonce,sequence,sequence,invalid,4u)==MT_LIVE_RUNTIME_OK); ++sequence; drain(); quiet();
 CHECK(runtime.device.state==MT_LIVE_STOPPED && !runtime.device.generation && !runtime.device.want_start);
 CHECK(mt_stream_diagnostics.phase==1u && !mt_stream_diagnostics.generation && !mt_stream_diagnostics.nonce_low && !mt_stream_diagnostics.nonce_high);
 CHECK(!mt_stream_diagnostics.step_count && !mt_stream_diagnostics.tx_count && !mt_stream_diagnostics.flags);
 CHECK(hw.stop_writes==stops && !hw.cen_writes && runtime.adc.first_fault==MT_ADC_DMA_FAULT_NONE);
 CHECK(start()==MT_LIVE_RUNTIME_OK && mt_stream_diagnostics.phase==2u);
 CHECK(mt_stream_diagnostics.generation==4u && mt_stream_diagnostics.nonce_low==(uint32_t)NONCE && mt_stream_diagnostics.nonce_high==(uint32_t)(NONCE>>32));
}
static void telemetry_dwt_failure_does_not_change_runtime(void) {
 running(); dwt.ctrl=0u; CHECK(step()==MT_LIVE_RUNTIME_OK); CHECK(mt_stream_diagnostics.flags&16u);
 dwt.ctrl=1u; CHECK(step()==MT_LIVE_RUNTIME_OK); CHECK(mt_stream_diagnostics.flags&16u);
 CHECK(mt_timebase_healthy(&clock_source) && runtime.device.state==MT_LIVE_STREAMING);
 CHECK(stop()==MT_LIVE_RUNTIME_OK); drain(); check_wire(0u);
}
static void telemetry_unsupported_dwt_runtime_unchanged(void) {
 CHECK(mt_stream_diagnostics.phase==4u && (mt_stream_diagnostics.flags&1u));
 test_samples(); CHECK(mt_stream_diagnostics.phase==4u && !mt_stream_diagnostics.generation && !mt_stream_diagnostics.step_count);
}
static void telemetry_uninitialized_runtime_unchanged(void) { test_samples(); CHECK(mt_stream_diagnostics.phase==0u && !dwt.reads && !dwt.writes); }

typedef void (*test_fn)(void);
static const struct { const char *name; test_fn fn; } cases[]={
{"telemetry_preliminary_quiesce_retains_attempt",telemetry_preliminary_quiesce_retains_attempt},{"telemetry_invalid_start_stays_armed",telemetry_invalid_start_stays_armed},
{"telemetry_replacement_preserves_precommit_fault",telemetry_replacement_preserves_precommit_fault},{"telemetry_stop_tail_queue_peak",telemetry_stop_tail_queue_peak},{"telemetry_armed_until_start",telemetry_armed_until_start},{"telemetry_empty_poll_existing_time",telemetry_empty_poll_existing_time},{"telemetry_copy_and_pre_tx_queue_peak",telemetry_copy_and_pre_tx_queue_peak},{"telemetry_rejected_copy_still_counted",telemetry_rejected_copy_still_counted},{"telemetry_stop_freezes_after_fifo_acceptance",telemetry_stop_freezes_after_fifo_acceptance},{"telemetry_first_fault_before_tx",telemetry_first_fault_before_tx},{"telemetry_fault_freeze_waits_actual_off",telemetry_fault_freeze_waits_actual_off},{"telemetry_replacement_hello_separates_runs",telemetry_replacement_hello_separates_runs},{"telemetry_replacement_cannot_fake_off",telemetry_replacement_cannot_fake_off},{"telemetry_failed_start_retains_attempt",telemetry_failed_start_retains_attempt},{"telemetry_dwt_failure_does_not_change_runtime",telemetry_dwt_failure_does_not_change_runtime},{"telemetry_unsupported_dwt_runtime_unchanged",telemetry_unsupported_dwt_runtime_unchanged},{"telemetry_uninitialized_runtime_unchanged",telemetry_uninitialized_runtime_unchanged},
 {"hello_caps_start_stop_unknown_end",test_handshake},{"real_dma_samples_crc_and_tail",test_samples},{"all_256_stop_tail_lengths",test_tail_sweep},{"enumeration_before_app_admission",test_startup},{"real_descriptor_address_enumeration",test_real_enumeration},{"arguments_and_live_reinit",test_arguments},{"hard_reset_pending_data",test_hard_reset},{"hard_cleanup_receipt_waits_for_off",test_stop_retry},{"suspend_resume_flush_once",test_soft},{"required_dtr_loss_and_reopen",test_dtr},{"sticky_time_fault_cleanup_retry",test_time_retry},{"clock_fault_during_quiesce",test_clock_stop},{"single_owner_reentry_rejected",test_reentry},{"reset_during_calibration",test_reset_cal},{"deferred_reset_during_cen_commit",test_reset_cen},{"reset_before_cdc_read",test_reset_read_before},{"reset_after_cdc_read",test_reset_read_after},{"reset_before_adc_read",test_reset_adc_before},{"reset_after_adc_read",test_reset_adc_after},{"reset_before_fifo_write",test_reset_write_before},{"reset_after_fifo_write_acceptance",test_reset_write_after},{"guarded_write_through_final_commit",test_deferred_write_reset},{"reset_after_final_tx_commit",test_postcommit_reset},{"reset_before_explicit_flush",test_reset_flush_before},{"reset_after_explicit_flush",test_reset_flush_after},{"time_fault_during_explicit_flush",test_clock_flush},{"suspend_after_adc_read",test_suspend_adc},{"suspend_after_cdc_read",test_suspend_read},{"ctr_rearm_only_queue_empty",test_queue_rearm},{"rx64_and_one_tx_step",test_rx_bound},{"adc_service_deadline_failure",test_adc_deadline},{"no_ctr_rearm_on_pma_poison",test_no_rearm_poison},{"zero_acceptance_backpressure",test_zero_backpressure},{"pma_after_final_sync_before_grant",test_pma_window},{"clock_fault_postwrite_guarded_quiesce",test_clock_write},{"callbacks_reject_external_writer",test_callback_ownership},{"active_or_reset_adc_init_refused",test_unsafe_init},{"soft_cleanup_retries_before_resume",test_soft_stop_retry},{"suspend_resume_between_steps",test_back_to_back_soft},{"hard_reset_delimits_partial_wire",test_hard_partial_wire},{"soft_loss_orders_pending_tx_before_hello",test_soft_pending_writer},{"queued_usb_events_still_service_adc",test_queued_usb_services_adc},{"time_fault_in_soft_cleanup",test_clock_soft_cleanup},{"time_fault_in_dtr_cleanup",test_clock_dtr_cleanup},{"positive_partial_fifo_acceptance",critic_positive_partial},{"soft_pause_preserves_short_fifo",critic_soft_fifo},{"hard_reset_cancels_deferred_rx_flush",test_hard_reset_cancels_soft_flush}
};
int main(int argc,char **argv) { CHECK(argc==2); if(!strcmp(argv[1],"--list")) { for(unsigned i=0;i<sizeof cases/sizeof cases[0];i++)puts(cases[i].name); return 0; } for(unsigned i=0;i<sizeof cases/sizeof cases[0];i++)if(!strcmp(argv[1],cases[i].name)) { case_name=cases[i].name; if(strstr(case_name,"unsupported_dwt")) { dwt.ctrl=1u<<25; CHECK(!mt_stream_diag_init(dwt_read,dwt_write,&dwt,UINT32_C(0x12345678))); } else if(!strstr(case_name,"uninitialized_runtime"))diag_init(); cases[i].fn(); CHECK(!fake_invalid_submissions); printf("{\"case\":\"%s\",\"pass\":true}\n",case_name); return 0; } CHECK(0); return 1; }
