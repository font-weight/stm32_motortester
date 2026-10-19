/* Independent combined ADC/DMA + TIME register model. Production modules are
 * compiled separately. No fake read_block, time snapshot, or stop certificate.
 * Fixture ancestry is recorded in PROVENANCE.md. Native logic, not hardware. */
#include "mt_live_adc.h"
#include "mt_live_clock.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *case_name="startup";
static unsigned iteration, scenarios;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL %s:%u %s:%d: %s\n",case_name,iteration,__func__,__LINE__,#x); exit(1); } } while(0)
enum {
 RCC_CR=0x40021000, RCC_CFGR=0x40021004, RCC_APB2RSTR=0x4002100c,
 RCC_APB1RSTR=0x40021010, RCC_AHB=0x40021014, RCC_APB2=0x40021018, RCC_APB1=0x4002101c,
 ADC_CR2=0x40012408, TIM_CR1=0x40000400,
 TIME_CR1=0x40000000, TIME_CNT=0x40000024, TIME_PSC=0x40000028,
 DMA_ISR=0x40020000, DMA_IFCR=0x40020004, DMA_CCR=0x40020008, DMA_N=0x4002000c,
 DMA_TC=2, DMA_HT=4, DMA_TE=8,
 HELLO=1, CAPS=2, STOP=5, START=8, HELLO_ACK=129, CAPS_REPLY=130,
 STOP_ACK=133, START_ACK=136, DATA=152, END=153, ERROR_REPLY=255
};
#define NONCE UINT64_C(0x1020304050607080)
typedef struct { uint32_t address, value; } reg;
typedef struct {
 reg regs[96]; unsigned used, reads, writes, adc_writes, time_reads;
 uint64_t elapsed, transferred, since_start, cen_at;
 unsigned tick, ignore_stop, calibration_stuck, transfer_on_stop;
 unsigned inject_clock_after_index, inject_clock_after_transfer_fault, clock_injected;
 unsigned enter_count, leave_count, allowed_count, timed_count, guard_depth, previous_mask;
 unsigned deny_allowed, deny_timed, actor, captured_actor, change_actor_at_timed;
 unsigned start_count, quiesce_count, isolate_count, write_count, stale_start_generation;
 mt_adc_dma_t driver; mt_timebase time; mt_live_clock live_clock;
 mt_live_device device; mt_live_adc binding; mt_adc_dma_start_guard guard;
 mt_live_snapshot last_quiesce;
 size_t write_cap, output_length; uint8_t output[40000];
} fixture;
static fixture f;
static uint32_t *reg_at(fixture *b,uint32_t address) {
 CHECK(!(address&3u));
 for(unsigned i=0;i<b->used;i++)if(b->regs[i].address==address)return &b->regs[i].value;
 CHECK(b->used<96u); b->regs[b->used]=(reg){address,0}; return &b->regs[b->used++].value;
}
static uint16_t code_at(uint64_t i) { return (uint16_t)(((i*UINT64_C(37))^(i>>4)^UINT64_C(0x5a3))&4095u); }
static void advance_to(fixture *b,uint64_t elapsed) { CHECK(elapsed>=b->elapsed); b->elapsed=elapsed; *reg_at(b,TIME_CNT)=(uint32_t)(elapsed&65535u); }
static void advance(fixture *b,unsigned us) { advance_to(b,b->elapsed+us); }
static void transfer(fixture *b,unsigned count) {
 for(unsigned i=0;i<count;i++) {
  unsigned p=(unsigned)(b->transferred%256u);
  CHECK(*reg_at(b,DMA_CCR)&1u); b->driver.dma_samples[p]=code_at(b->transferred++); ++b->since_start;
  p=(p+1u)%256u; *reg_at(b,DMA_N)=p?256u-p:256u;
  if(p==128u)*reg_at(b,DMA_ISR)|=DMA_HT;
  if(!p)*reg_at(b,DMA_ISR)|=DMA_TC;
 }
}
static uint32_t rd(void *ctx,uint32_t address) {
 fixture *b=ctx; ++b->reads;
 /* Injection occurs only once real ADC work has established the observed
  * transition. It changes hardware registers, never production return values. */
 if(!b->clock_injected &&
    ((b->inject_clock_after_index && b->driver.first_sample_index>=b->inject_clock_after_index) ||
     (b->inject_clock_after_transfer_fault && b->driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER))) {
  *reg_at(b,TIME_PSC)=70u; b->clock_injected=1u;
 }
 if(address>=TIME_CR1 && address<=0x4000002cu)++b->time_reads;
 if(address==ADC_CR2 && !b->calibration_stuck)*reg_at(b,address)&=~12u;
 if(address==TIME_CNT) { uint32_t v=*reg_at(b,address); advance(b,b->tick); return v; }
 return *reg_at(b,address);
}
static void wr(void *ctx,uint32_t address,uint32_t value) {
 fixture *b=ctx; uint32_t *v=reg_at(b,address); ++b->writes;
 if(address<TIME_CR1 || address>0x4000002cu)++b->adc_writes;
 if(address==TIM_CR1 && value==1u)b->cen_at=b->elapsed;
 if(address==TIM_CR1 && value==0u && (*v&1u) && b->transfer_on_stop) {
  transfer(b,b->transfer_on_stop); b->transfer_on_stop=0;
 }
 if(address==DMA_IFCR) { CHECK(!(value&1u)); *reg_at(b,DMA_ISR)&=~value; }
 if(address==TIM_CR1 && value==0u && (*v&1u) && (b->ignore_stop&1u))return;
 if(address==ADC_CR2 && value==0u && (*v&1u) && (b->ignore_stop&2u))return;
 if(address==DMA_CCR && !(value&1u) && (*v&1u) && (b->ignore_stop&4u))return;
 if(address==TIME_CNT)b->elapsed=value;
 *v=value;
}
static uint32_t now_us(void *ctx) { fixture *b=ctx; return mt_timebase_now_us(&b->time); }
static uint32_t guard_enter(void *ctx) { fixture *b=ctx; CHECK(!b->guard_depth); ++b->enter_count; b->guard_depth=1; return b->previous_mask; }
static void guard_leave(void *ctx,uint32_t key) { fixture *b=ctx; CHECK(b->guard_depth==1u && key==b->previous_mask); b->guard_depth=0; ++b->leave_count; }
static int guard_allowed(void *ctx) { fixture *b=ctx; CHECK(b->guard_depth); ++b->allowed_count; return mt_timebase_update(&b->time) && !b->deny_allowed && b->actor==b->captured_actor; }
static int guard_timed(void *ctx,uint32_t *us) {
 fixture *b=ctx; uint32_t observed,ms; CHECK(b->guard_depth); ++b->timed_count;
 if(b->change_actor_at_timed==b->timed_count)++b->actor;
 if(!mt_timebase_snapshot(&b->time,&observed,&ms) || b->deny_timed==b->timed_count || b->actor!=b->captured_actor)return 0;
 *us=observed; return 1;
}
static uint64_t clock_cb(void *ctx) { fixture *b=ctx; return mt_live_clock_now(&b->live_clock); }
static int start_cb(void *ctx,uint32_t generation) { fixture *b=ctx; ++b->start_count; b->captured_actor=b->actor; return mt_live_adc_start(&b->binding,generation+(b->stale_start_generation?1u:0u)); }
static void quiesce_cb(void *ctx,mt_live_snapshot *out) { fixture *b=ctx; ++b->quiesce_count; mt_live_adc_quiesce(&b->binding,out); b->last_quiesce=*out; }
static int isolate_cb(void *ctx) { fixture *b=ctx; ++b->isolate_count; return 1; }
static size_t write_cb(void *ctx,const uint8_t *bytes,size_t count) {
 fixture *b=ctx; ++b->write_count; CHECK(count<=64u); if(count>b->write_cap)count=b->write_cap;
 CHECK(count<=sizeof b->output-b->output_length); memcpy(b->output+b->output_length,bytes,count); b->output_length+=count; return count;
}
static void setup(void) {
 memset(&f,0,sizeof f); f.tick=1; f.write_cap=64; f.actor=7; f.previous_mask=1;
 *reg_at(&f,RCC_CR)=0x03030000u; *reg_at(&f,RCC_CFGR)=0x001d840au;
 *reg_at(&f,RCC_AHB)=1u; *reg_at(&f,RCC_APB2)=(1u<<2)|(1u<<9); *reg_at(&f,RCC_APB1)=3u;
 mt_timebase_io ti={rd,wr,&f}; CHECK(mt_timebase_init(&f.time,&ti,72000000u));
 mt_adc_dma_io_t ai={rd,wr,now_us,&f}; CHECK(mt_adc_dma_init(&f.driver,&ai,8000000u)==MT_ADC_DMA_OK);
 CHECK(mt_live_clock_init(&f.live_clock,&f.time));
 f.guard=(mt_adc_dma_start_guard){guard_enter,guard_allowed,guard_leave,&f,guard_timed};
 CHECK(mt_live_adc_init(&f.binding,&f.driver,&f.time,&f.device,&f.guard));
 mt_live_io io={.context=&f,.clock_ms=clock_cb,.start=start_cb,.quiesce=quiesce_cb,.write=write_cb,.isolate_tx=isolate_cb,.transaction_bound_ms=1,.firmware_build=0x12345678u};
 CHECK(mt_live_device_init(&f.device,&io)); CHECK(f.driver.stream_mode && !f.driver.owns_peripherals); CHECK(!f.start_count && !f.quiesce_count);
}
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static uint32_t r32(const uint8_t *p) { return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static uint64_t r64(const uint8_t *p) { return r32(p)|((uint64_t)r32(p+4)<<32); }
static void w32(uint8_t *p,uint32_t n) { for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(n>>(8u*i)); }
static void w64(uint8_t *p,uint64_t n) { for(unsigned i=0;i<8;i++)p[i]=(uint8_t)(n>>(8u*i)); }
static int request(uint8_t type,uint64_t nonce,uint32_t seq,uint32_t req,const uint8_t *payload,uint16_t n) {
 mt_wire_message m={.type=type,.session=nonce,.sequence=seq,.request=req,.payload_length=n}; uint8_t wire[MT_WIRE_FRAME_MAX]; size_t length;
 if(n)memcpy(m.payload,payload,n);
 CHECK(mt_wire_encode_revision(&m,0,2,wire,sizeof wire,&length)==MT_WIRE_OK);
 return mt_live_device_receive(&f.device,wire,length);
}
static void hello(uint64_t nonce) { uint8_t p[8]; w64(p,nonce); CHECK(request(HELLO,0,1,1,p,8)); }
static void stop(uint32_t generation) { uint8_t p[4]; w32(p,generation); CHECK(request(STOP,f.device.nonce,4,4,p,4)); }
static void start(void) { uint8_t p[4]={1,0,0,0}; CHECK(request(START,NONCE,3,3,p,4)); }
static void pump(void) {
 for(unsigned i=0;i<2000u;i++) {
  CHECK(mt_live_device_step(&f.device));
  mt_live_device *d=&f.device;
  if(!d->tx_length && !d->want_caps && !d->want_start && !d->want_error && !d->want_end && !d->want_ack && d->state!=MT_LIVE_HELLO_PENDING && (d->state!=MT_LIVE_DRAINING || !d->queue.used))return;
 }
 CHECK(0);
}
static void ready(void) { setup(); hello(NONCE); pump(); CHECK(f.device.state==MT_LIVE_STOPPED); CHECK(request(CAPS,NONCE,2,2,NULL,0)); pump(); CHECK(f.device.caps_ready); }
static void begin(unsigned ack) {
 ready(); start(); CHECK(f.driver.state==MT_ADC_DMA_RUNNING && f.driver.owns_peripherals);
 CHECK(f.device.state==MT_LIVE_STARTING && f.device.generation==3u);
 CHECK(f.enter_count==1 && f.leave_count==1 && f.allowed_count==1 && f.timed_count==2 && !f.guard_depth);
 CHECK(f.driver.clock.timer_hz==72000000u && f.driver.clock.adc_hz==12000000u);
 if(ack) { pump(); CHECK(f.device.state==MT_LIVE_STREAMING); }
}
static void transfer_at(unsigned n) { transfer(&f,n); uint64_t target=f.cen_at+f.since_start*20u+2u; if(target>f.elapsed)advance_to(&f,target); }
static void consume(void) {
 transfer_at(127u); CHECK(mt_live_adc_service(&f.binding)==0);
 transfer_at(1u); CHECK(mt_live_adc_service(&f.binding)==1);
 /* The live u64 wrapper has its own obligation, independently of ADC polls. */
 CHECK(mt_live_clock_now(&f.live_clock)!=UINT64_MAX);
}
static void pending(unsigned n) {
 CHECK(n<256u);
 if(n>=127u) { transfer_at(127u); advance(&f,13u); CHECK(mt_live_adc_service(&f.binding)==0); if(n>127u)transfer_at(n-127u); }
 else transfer_at(n);
}
static unsigned decode(mt_wire_message *out,unsigned capacity) {
 size_t start_at=0; unsigned count=0;
 for(size_t i=0;i<f.output_length;i++)if(!f.output[i]) {
  if(i>start_at) { CHECK(count<capacity); CHECK(mt_wire_decode_revision(f.output+start_at,i-start_at,0,2,&out[count])==MT_WIRE_OK); ++count; }
  start_at=i+1;
 }
 CHECK(start_at==f.output_length); return count;
}
static uint32_t oracle_crc(uint64_t count) {
 /* Independent bitwise non-reflected ISO-HDLC oracle with input reflection. */
 uint32_t crc=UINT32_MAX;
 for(uint64_t i=0;i<count;i++)for(unsigned byte=0;byte<2;byte++) {
  uint8_t in=(uint8_t)(code_at(i)>>(8u*byte)), reflected=0;
  for(unsigned bit=0;bit<8;bit++)reflected=(uint8_t)((reflected<<1)|((in>>bit)&1u));
  crc^=(uint32_t)reflected<<24;
  for(unsigned bit=0;bit<8;bit++)crc=(crc<<1)^((crc&0x80000000u)?0x04c11db7u:0);
 }
 uint32_t reflected=0; for(unsigned bit=0;bit<32;bit++)reflected=(reflected<<1)|((crc>>bit)&1u); return ~reflected;
}
static void check_end(uint64_t expected,unsigned reason,unsigned flags,unsigned ack) {
 mt_wire_message messages[128]; unsigned count=decode(messages,128),data_count=0,ends=0,acks=0; uint64_t cursor=0; uint32_t last_data=0,end_seq=0; int seen_end=0;
 CHECK(count>=4u && messages[0].type==HELLO_ACK && messages[1].type==CAPS_REPLY && messages[2].type==START_ACK);
 CHECK(messages[0].payload[0]==0u && messages[0].payload[1]==2u && messages[0].request==1u);
 CHECK(r32(messages[1].payload+8)==50000u && r16(messages[1].payload+16)==128u);
 for(unsigned m=0;m<count;m++) {
  const mt_wire_message *p=&messages[m]; CHECK(p->session==NONCE); CHECK(p->sequence==m+1u);
  if(p->type==DATA) {
   CHECK(!seen_end && r32(p->payload)==3u && r64(p->payload+8)==cursor);
   unsigned n=r16(p->payload+16); CHECK(n && n<=128 && p->payload_length==20u+2u*n);
   for(unsigned j=0;j<n;j++)CHECK(r16(p->payload+20u+2u*j)==code_at(cursor+j));
   cursor+=n; ++data_count; last_data=p->sequence;
  }
  if(p->type==END) {
   ++ends; seen_end=1; end_seq=p->sequence;
   CHECK(p->request==0u && p->payload_length==40u && r32(p->payload)==3u && r32(p->payload+4)==1u);
   CHECK(r64(p->payload+8)==expected && r64(p->payload+16)==0u);
   CHECK(r32(p->payload+24)==last_data && r32(p->payload+28)==oracle_crc(expected));
   CHECK(r16(p->payload+32)==reason && r16(p->payload+34)==flags);
   CHECK(r32(p->payload+36)==(f.device.stop_seen?4u:0u));
  }
  if(p->type==STOP_ACK) {
   ++acks; CHECK(seen_end && p->request==4u && r32(p->payload)==3u);
   CHECK(r64(p->payload+4)==expected && r32(p->payload+12)==end_seq && r32(p->payload+16)==flags);
  }
 }
 CHECK(cursor==expected && ends==1u && acks==ack);
 CHECK(data_count==(expected+127u)/128u); CHECK(flags&1u); CHECK(flags&2u);
 CHECK(f.device.produced_known==0u && f.device.produced_end==0u);
}
static void check_no_terminal(void) {
 mt_wire_message messages[128]; unsigned n=decode(messages,128);
 for(unsigned i=0;i<n;i++)CHECK(messages[i].type!=DATA && messages[i].type!=END && messages[i].type!=STOP_ACK);
}
static void test_tails(void) {
 for(unsigned phase=0;phase<2u;phase++)for(unsigned n=0;n<256u;n++) {
  iteration=phase*256u+n; begin(1); if(phase)consume(); pending(n); stop(3u);
  CHECK(f.last_quiesce.hardware_off && !f.last_quiesce.produced_known && !f.last_quiesce.produced_end);
  CHECK(f.binding.stop_scratch.dma_end_known && f.binding.stop_scratch.dma_end==128u*phase+n);
  CHECK(f.last_quiesce.tail_count==(n+127u)/128u);
  /* Mutating the stopped source ring after snapshot must not affect queue TX. */
  for(unsigned i=0;i<256u;i++)f.driver.dma_samples[i]=65535u;
  f.write_cap=7; pump(); check_end(128u*phase+n,0,3,1); ++scenarios;
 }
}
static void test_queue_full(void) {
 for(unsigned extra=0;extra<=7u;extra+=7u) {
 iteration=extra; begin(1); for(unsigned i=0;i<16u;i++)consume();
 transfer_at(127u); CHECK(mt_live_adc_service(&f.binding)==0); transfer_at(1u);
 /* The source consumes the 17th block before synchronous queue rejection. */
 f.transfer_on_stop=extra; CHECK(mt_live_adc_service(&f.binding)==-1); CHECK(f.driver.first_sample_index==2176u);
 CHECK(f.device.queue.next_index==2048u && f.device.queue.first_fault==MT_SAMPLE_QUEUE_FULL);
 CHECK(f.last_quiesce.hardware_off && f.binding.stop_scratch.first_sample_index==2176u);
 CHECK(f.last_quiesce.tail_count==(extra?1u:0u) && f.device.first_reason==1u);
 CHECK(f.binding.stop_scratch.dma_end==2176u+extra);
 stop(3u); pump(); check_end(2048u,1,3,1); ++scenarios; }
}
static void test_cleanup_failures(void) {
 for(unsigned mask=1;mask<=4;mask*=2) {
  iteration=mask; begin(1); pending(17); f.ignore_stop=mask; stop(3u);
  CHECK(f.driver.owns_peripherals && f.driver.first_fault==MT_ADC_DMA_FAULT_STOP);
  CHECK(!f.last_quiesce.hardware_off && !f.last_quiesce.tail_count);
  pump(); check_end(0,5,7,0);
  f.ignore_stop=0; mt_live_snapshot again; mt_live_adc_quiesce(&f.binding,&again);
  CHECK(again.hardware_off && !again.produced_known && !again.tail_count && again.acquisition_reason==5u); ++scenarios;
 }
}
static void test_transfer_faults(void) {
 for(unsigned mask=0;mask<=4;mask+=4) {
  iteration=mask; begin(1); pending(17); f.ignore_stop=mask; *reg_at(&f,DMA_ISR)|=DMA_TE;
  CHECK(mt_live_adc_service(&f.binding)==-1); CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
  CHECK(f.device.first_reason==2u && f.last_quiesce.acquisition_reason==2u);
  stop(3u); pump(); check_end(0,2,mask?7u:3u,mask?0u:1u); ++scenarios;
 }
}
static void test_missed_service(void) {
 begin(1); advance(&f,2560u); CHECK(mt_live_adc_service(&f.binding)==-1);
 CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_DEADLINE && f.device.first_reason==2u);
 stop(3u); pump(); check_end(0,2,3,1); ++scenarios;
}
static void test_clock_failure(void) {
 begin(1); pending(17); *reg_at(&f,TIME_PSC)=70u;
 CHECK(mt_live_adc_service(&f.binding)==-1); CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_CLOCK);
 CHECK(f.device.state==MT_LIVE_FAULT && f.device.first_reason==3u && f.device.hardware_off);
 CHECK(!f.last_quiesce.tail_count && !f.last_quiesce.produced_known); check_no_terminal(); ++scenarios;
}
static void test_postread_clock(void) {
 begin(1); transfer_at(127u); CHECK(mt_live_adc_service(&f.binding)==0); transfer_at(1u);
 f.inject_clock_after_index=128u; CHECK(mt_live_adc_service(&f.binding)==-1);
 CHECK(f.clock_injected && f.driver.first_sample_index==128u && f.device.queue.next_index==0u);
 CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_CLOCK && f.device.first_reason==3u);
 CHECK(f.device.hardware_off && !f.last_quiesce.tail_count); check_no_terminal(); ++scenarios;
}
static void test_fault_clock_precedence(void) {
 begin(1); pending(17); f.ignore_stop=4u; f.inject_clock_after_transfer_fault=1u;
 *reg_at(&f,DMA_ISR)|=DMA_TE; CHECK(mt_live_adc_service(&f.binding)==-1);
 CHECK(f.clock_injected && f.driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
 CHECK(f.driver.owns_peripherals && !f.device.hardware_off);
 CHECK(f.last_quiesce.acquisition_reason==2u);
 fprintf(stdout,"observed driver_first_fault=%u endpoint_first_reason=%u hardware_off=%u\n",(unsigned)f.driver.first_fault,(unsigned)f.device.first_reason,f.device.hardware_off);
 CHECK(f.device.first_reason==2u); check_no_terminal(); ++scenarios;
}
static void test_stop_fault_clock_precedence(void) {
 begin(1); pending(17); f.inject_clock_after_transfer_fault=1u;
 *reg_at(&f,DMA_ISR)|=DMA_TE; (void)request(STOP,NONCE,4,4,(const uint8_t[]){3,0,0,0},4);
 CHECK(f.clock_injected && f.driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
 CHECK(f.last_quiesce.acquisition_reason==2u && f.device.hardware_off);
 fprintf(stdout,"STOP observed driver_first_fault=%u endpoint_first_reason=%u hardware_off=%u\n",(unsigned)f.driver.first_fault,(unsigned)f.device.first_reason,f.device.hardware_off);
 CHECK(f.device.first_reason==2u); CHECK(f.device.state==MT_LIVE_FAULT); check_no_terminal(); ++scenarios;
}
static void test_failed_profile(void) {
 ready(); f.driver.hse_hz=4000000u; unsigned writes=f.adc_writes; start();
 CHECK(f.start_count==1u && !f.enter_count && f.adc_writes==writes);
 CHECK(!f.driver.owns_peripherals && f.device.state==MT_LIVE_NEEDS_HELLO && f.device.hardware_off);
 pump(); mt_wire_message messages[16]; unsigned n=decode(messages,16);
 CHECK(n==3u && messages[2].type==ERROR_REPLY); check_no_terminal(); ++scenarios;
}
static void test_stop_before_ack(void) {
 begin(0); pending(17); stop(3u); f.write_cap=3; pump(); check_end(17,0,3,1); ++scenarios;
}
static void test_repeated_stop_snapshot(void) {
 begin(1); pending(137); stop(3u); mt_live_snapshot first=f.last_quiesce,second;
 unsigned writes=f.adc_writes; mt_live_adc_quiesce(&f.binding,&second);
 CHECK(f.adc_writes==writes && second.hardware_off==first.hardware_off && !second.produced_known && second.tail_count==first.tail_count && second.tail[0].first_index==first.tail[0].first_index && second.tail[1].count==first.tail[1].count && memcmp(first.tail[0].codes,second.tail[0].codes,sizeof first.tail[0].codes)==0 && memcmp(first.tail[1].codes,second.tail[1].codes,9u*sizeof(uint16_t))==0);
 pump(); check_end(137,0,3,1); ++scenarios;
}
static void test_fault_stop(void) {
 begin(1); pending(17); *reg_at(&f,DMA_ISR)|=DMA_TE; stop(3u);
 CHECK(f.last_quiesce.acquisition_reason==2u && f.driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
 pump(); check_end(0,2,3,1); ++scenarios;
}
static void test_failed_admission(void) {
 for(unsigned mode=0;mode<6u;mode++) {
  iteration=mode; ready();
  if(mode==0)f.deny_allowed=1;
  if(mode==1)f.deny_timed=1;
  if(mode==2)f.deny_timed=2;
  if(mode==3)f.change_actor_at_timed=1;
  if(mode==4)f.change_actor_at_timed=2;
  if(mode==5)f.calibration_stuck=1;
  start(); CHECK(f.device.state==MT_LIVE_NEEDS_HELLO && f.device.hardware_off);
  CHECK(!f.driver.owns_peripherals && !f.guard_depth && f.enter_count==f.leave_count);
  CHECK(f.driver.first_fault==(mode==5?MT_ADC_DMA_FAULT_CALIBRATION:MT_ADC_DMA_FAULT_ADMISSION));
  pump(); mt_wire_message m[16]; unsigned n=decode(m,16); CHECK(n==3u && m[2].type==ERROR_REPLY && m[2].payload[1]==4u);
  CHECK(m[2].request==3u); check_no_terminal(); ++scenarios;
 }
}
static void test_stale_generation(void) {
 ready(); f.stale_start_generation=1u; start(); CHECK(f.device.state==MT_LIVE_NEEDS_HELLO && f.device.hardware_off && !f.enter_count && !f.driver.owns_peripherals); pump(); check_no_terminal(); ++scenarios;
 ready(); unsigned writes=f.adc_writes; CHECK(!mt_live_adc_start(&f.binding,3u)); CHECK(f.adc_writes==writes);
 start(); writes=f.adc_writes; CHECK(!mt_live_adc_start(&f.binding,2u)); CHECK(!mt_live_adc_start(&f.binding,0u)); CHECK(f.adc_writes==writes);
 stop(2u); CHECK(f.device.state==MT_LIVE_FAULT && f.device.hardware_off);
 CHECK(!f.driver.owns_peripherals); check_no_terminal(); ++scenarios;
}
static void test_cold_unowned(void) {
 const uint32_t registers[]={TIM_CR1,ADC_CR2,DMA_CCR};
 for(unsigned i=0;i<3;i++) {
  iteration=i; setup(); *reg_at(&f,registers[i])=1u; unsigned writes=f.adc_writes;
  hello(NONCE); CHECK(f.device.state==MT_LIVE_FAULT && !f.device.hardware_off);
  CHECK(f.output_length==0u && f.adc_writes==writes && !f.driver.owns_peripherals);
  CHECK(*reg_at(&f,registers[i])==1u); ++scenarios;
 }
}
static void test_arguments(void) {
 setup(); unsigned writes=f.adc_writes; mt_live_adc empty={0},probe; mt_adc_dma_start_guard bad=f.guard;
 CHECK(mt_live_adc_service(NULL)==-2 && mt_live_adc_service(&empty)==-2);
 CHECK(!mt_live_adc_start(NULL,3u)); CHECK(!mt_live_adc_init(NULL,&f.driver,&f.time,&f.device,&f.guard));
 CHECK(!mt_live_adc_init(&probe,NULL,&f.time,&f.device,&f.guard));
 bad.timed_allowed=NULL; CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&bad));
 mt_live_snapshot out; memset(&out,0xa5,sizeof out); mt_live_adc_quiesce(&empty,&out);
 CHECK(!out.hardware_off && !out.produced_known && !out.tail_count && out.acquisition_reason==5u);
 mt_live_adc_quiesce(&f.binding,NULL); CHECK(f.adc_writes==writes);
 CHECK(mt_live_adc_service(&f.binding)==0); ++scenarios;
}
static void test_crc_rejection(void) {
 ready(); mt_wire_message m={.type=START,.session=NONCE,.sequence=3,.request=3,.payload_length=4,.payload={1,0,0,0}};
 uint8_t wire[MT_WIRE_FRAME_MAX]; size_t n; CHECK(mt_wire_encode_revision(&m,0,2,wire,sizeof wire,&n)==MT_WIRE_OK);
 wire[n-3]^=1u; CHECK(mt_live_device_receive(&f.device,wire,n)); CHECK(!f.start_count && f.device.state==MT_LIVE_STOPPED);
 ++scenarios;
}
static void test_raw_barrier_causes(void) {
 for(unsigned mode=0;mode<3;mode++) {
  iteration=mode; begin(mode==2?0u:1u); pending(17); *reg_at(&f,DMA_ISR)|=DMA_TE;
  if(mode==0)mt_live_device_transport_lost(&f.device);
  if(mode==1)CHECK(request(77,NONCE,4,4,NULL,0));
  if(mode==2)hello(NONCE+1u);
  CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_TRANSFER && f.last_quiesce.acquisition_reason==2u);
  CHECK(f.device.first_reason==2u && f.device.hardware_off && !f.driver.owns_peripherals);
  CHECK(f.device.state==(mode==2?MT_LIVE_HELLO_PENDING:MT_LIVE_FAULT));
  check_no_terminal(); ++scenarios;
 }
}
static void test_cached_raw_barrier(void) {
 begin(1); pending(137); stop(3u); CHECK(f.device.queue.next_index==137u);
 mt_live_device_transport_lost(&f.device);
 CHECK(f.last_quiesce.tail_count==2u && f.last_quiesce.tail[0].first_index==0u);
 CHECK(f.last_quiesce.acquisition_reason==0u && f.device.first_reason==0u && f.device.hardware_off);
 check_no_terminal(); ++scenarios;
}
static void seed_source_index(uint64_t base) {
 /* Explicit white-box boundary seed, never presented as billions of actual
  * samples. Following register events/read/stop are the real production path. */
 CHECK(base%128u==0u); f.transferred=base; f.since_start=0u; f.cen_at=f.elapsed;
 f.driver.first_sample_index=base; f.driver.next_half=(base/128u)%2u!=0u;
 *reg_at(&f,DMA_N)=f.driver.next_half?128u:256u;
 f.driver.last_remaining=*reg_at(&f,DMA_N); f.driver.last_poll_us=f.time.us; f.driver.last_progress_us=f.time.us;
 mt_sample_queue_init(&f.device.queue,base); f.device.prefix_end=base;
}
static void test_high_indices(void) {
 const uint64_t bases[]={UINT64_C(0xffffff80),UINT64_C(0x100000000),UINT64_MAX-383u};
 for(unsigned k=0;k<3;k++) {
  iteration=k; begin(1); uint64_t base=bases[k]; seed_source_index(base); consume();
  mt_sample_block block; CHECK(mt_sample_queue_peek(&f.device.queue,&block)==MT_SAMPLE_QUEUE_OK);
  CHECK(block.first_index==base && block.count==128u && f.driver.first_sample_index==base+128u);
  for(unsigned i=0;i<128;i++)CHECK(block.samples[i]==code_at(base+i));
  pending(137); mt_live_snapshot out; mt_live_adc_quiesce(&f.binding,&out);
  CHECK(out.hardware_off && !out.produced_known && !out.produced_end && out.tail_count==2u);
  CHECK(out.tail[0].first_index==base+128u && out.tail[0].count==128u);
  CHECK(out.tail[1].first_index==base+256u && out.tail[1].count==9u);
  for(unsigned i=0;i<137;i++)CHECK(out.tail[i/128u].codes[i%128u]==code_at(base+128u+i));
  ++scenarios;
 }
 begin(1); seed_source_index(UINT64_MAX-127u);
 transfer_at(127); CHECK(mt_live_adc_service(&f.binding)==0); transfer_at(1);
 CHECK(mt_live_adc_service(&f.binding)==-1);
 CHECK(f.driver.first_fault==MT_ADC_DMA_FAULT_SEQUENCE_LIMIT && f.device.first_reason==4u);
 CHECK(f.driver.first_sample_index==UINT64_MAX-127u && f.device.queue.next_index==UINT64_MAX-127u);
 CHECK(f.device.hardware_off && !f.last_quiesce.tail_count && !f.last_quiesce.produced_known); ++scenarios;
}
static void test_init_atomicity(void) {
 setup(); mt_live_adc probe,old; memset(&probe,0xa5,sizeof probe); memcpy(&old,&probe,sizeof old);
 mt_adc_dma_start_guard bad=f.guard; bad.enter=NULL;
 CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&bad)); CHECK(!memcmp(&probe,&old,sizeof probe));
 bad=f.guard; bad.leave=NULL; CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&bad)); CHECK(!memcmp(&probe,&old,sizeof probe));
 bad=f.guard; bad.allowed=NULL; CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&bad)); CHECK(!memcmp(&probe,&old,sizeof probe));
 *reg_at(&f,TIME_PSC)=70u; CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&f.guard)); CHECK(!memcmp(&probe,&old,sizeof probe));
 begin(1); CHECK(!mt_live_adc_init(&probe,&f.driver,&f.time,&f.device,&f.guard)); CHECK(!memcmp(&probe,&old,sizeof probe));
 CHECK(f.driver.owns_peripherals); ++scenarios;
}
typedef struct { const char *name; void (*run)(void); } test;
int main(int argc,char **argv) {
 const test tests[]={
  {"arguments",test_arguments},{"tails",test_tails},{"queue_full",test_queue_full},
  {"cleanup_failures",test_cleanup_failures},{"transfer_faults",test_transfer_faults},
  {"missed_service",test_missed_service},{"clock_failure",test_clock_failure},
  {"postread_clock",test_postread_clock},{"fault_clock_precedence",test_fault_clock_precedence},
  {"stop_fault_clock_precedence",test_stop_fault_clock_precedence},{"failed_profile",test_failed_profile},
  {"stop_before_ack",test_stop_before_ack},{"repeated_stop_snapshot",test_repeated_stop_snapshot},{"fault_stop",test_fault_stop},
  {"failed_admission",test_failed_admission},{"stale_generation",test_stale_generation},
  {"cold_unowned",test_cold_unowned},{"crc_rejection",test_crc_rejection},
  {"raw_barrier_causes",test_raw_barrier_causes},{"cached_raw_barrier",test_cached_raw_barrier},
  {"high_indices",test_high_indices},{"init_atomicity",test_init_atomicity}
 };
 CHECK(argc<=2); unsigned ran=0;
 for(unsigned i=0;i<sizeof tests/sizeof tests[0];i++)if(argc==1 || !strcmp(argv[1],tests[i].name)) {
  case_name=tests[i].name; iteration=0; tests[i].run(); printf("PASS %s (%u cumulative scenarios)\n",case_name,scenarios); ++ran;
 }
 CHECK(ran); printf("PASS %u groups, %u scenarios: actual ADC + TIME + live clock + endpoint + queue + codec\n",ran,scenarios); return 0;
}
