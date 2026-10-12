/* Independent TIME001 host register-model tests. Author tests were not read. */
#include "mt_timebase.h"
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d: %s\n", __func__, __LINE__, #x); exit(1); } } while (0)
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
enum reg_id { R_CR, R_CFGR, R_RESET, R_GATE, T_CR1, T_CR2, T_SMCR,
 T_DIER, T_SR, T_EGR, T_CCMR1, T_CCMR2, T_CCER, T_CNT, T_PSC, T_ARR,
 T_CCR1, T_CCR2, T_CCR3, T_CCR4, T_DCR, T_DMAR, GPIO, NVIC, TIM3, REG_COUNT };
static const uint32_t addresses[REG_COUNT] = {
 0x40021000u,0x40021004u,0x40021010u,0x4002101cu,
 0x40000000u,0x40000004u,0x40000008u,0x4000000cu,
 0x40000010u,0x40000014u,0x40000018u,0x4000001cu,
 0x40000020u,0x40000024u,0x40000028u,0x4000002cu,
 0x40000034u,0x40000038u,0x4000003cu,0x40000040u,
 0x40000048u,0x4000004cu,0x40010800u,0xe000e100u,0x40000400u };
struct event { enum reg_id reg; uint32_t value; };
typedef struct {
 uint32_t r[REG_COUNT]; struct event writes[128]; size_t nw;
 uint64_t nr; unsigned cnt_reads, ug_count, order_errors;
 uint32_t active_psc; uint64_t prescaler_phase, physical_ticks, ticks_at_start;
 int ignore_write; enum reg_id ignore_reg;
 int inject, inject_on_write, inject_after, zero_if_inaccessible;
 enum reg_id trigger, target; unsigned skip; uint32_t value;
 uint64_t advance_after_cnt;
} bus;
static enum reg_id identify(uint32_t address) {
 size_t i; CHECK((address & 3u) == 0);
 for (i=0; i<REG_COUNT; ++i) if (addresses[i]==address) return (enum reg_id)i;
 fprintf(stderr,"Unexpected MMIO 0x%08" PRIx32 "\n",address); exit(1);
}
static void cycles(bus *b, uint64_t n) {
 uint64_t phase,ticks,count,period;
 if (!(b->r[T_CR1]&1u) || !(b->r[R_GATE]&1u) || (b->r[R_RESET]&1u)) return;
 phase=n+b->prescaler_phase; ticks=phase/((uint64_t)b->active_psc+1u);
 b->prescaler_phase=phase%((uint64_t)b->active_psc+1u); b->physical_ticks+=ticks;
 count=b->r[T_CNT]+ticks; period=(uint64_t)b->r[T_ARR]+1u;
 if (count>=period) b->r[T_SR]|=1u;
 b->r[T_CNT]=(uint32_t)(count%period);
}
static void ticks(bus *b,uint64_t n) { cycles(b,n*72u); }
static int injection(bus *b, enum reg_id reg, int write) {
 if (b->inject && b->inject_on_write==write && b->trigger==reg) {
  if (b->skip==0u) { b->inject=0; return 1; } --b->skip;
 } return 0;
}
static uint32_t read32(void *context,uint32_t address) {
 bus *b=context; enum reg_id reg=identify(address); uint32_t value;
 int fire=injection(b,reg,0); ++b->nr;
 if (fire && !b->inject_after) b->r[b->target]=b->value;
 value=b->r[reg];
 if (b->zero_if_inaccessible && reg>=T_CR1 && reg<=T_DMAR &&
     (!(b->r[R_GATE]&1u) || (b->r[R_RESET]&1u))) value=0;
 if (fire && b->inject_after) b->r[b->target]=b->value;
 if (reg==T_CNT) { ++b->cnt_reads; if (b->advance_after_cnt) {
  uint64_t n=b->advance_after_cnt; b->advance_after_cnt=0; ticks(b,n); } }
 return value;
}
static void write32(void *context,uint32_t address,uint32_t value) {
 bus *b=context; enum reg_id reg=identify(address); int fire=injection(b,reg,1);
 CHECK(b->nw<COUNT(b->writes)); CHECK(reg>=T_CR1 && reg<=T_ARR);
 b->writes[b->nw++]=(struct event){reg,value};
 if (fire && !b->inject_after) b->r[b->target]=b->value;
 if ((b->ignore_write && reg==b->ignore_reg) || (b->zero_if_inaccessible &&
    (!(b->r[R_GATE]&1u) || (b->r[R_RESET]&1u)))) return;
 if (reg!=T_CR1 && (b->r[T_CR1]&1u)) ++b->order_errors;
 if (reg==T_EGR) {
  if (value&1u) { ++b->ug_count;
   if (b->r[T_CR1]!=0u || b->r[T_PSC]!=71u || b->r[T_ARR]!=65535u) ++b->order_errors;
   b->active_psc=b->r[T_PSC]; b->prescaler_phase=0; b->r[T_CNT]=0; b->r[T_SR]|=1u; }
 } else {
  b->r[reg]=value;
  if (reg==T_CR1 && (value&1u)) {
   if (b->ug_count!=1u || b->active_psc!=71u || b->r[T_ARR]!=65535u || b->r[T_CR2] ||
       b->r[T_SMCR] || b->r[T_DIER] || b->r[T_CCMR1] || b->r[T_CCMR2] || b->r[T_CCER] || b->r[T_SR])
    ++b->order_errors;
   ticks(b,b->ticks_at_start);
  }
 }
 if (fire && b->inject_after) b->r[b->target]=b->value;
}
static void defaults(bus *b) {
 size_t i; memset(b,0,sizeof(*b));
 for (i=0;i<REG_COUNT;++i) b->r[i]=0x51a50000u+(uint32_t)i;
 b->r[R_CR]=0x03030083u; b->r[R_CFGR]=0x001d040au;
 b->r[R_RESET]=0xa0000000u; b->r[R_GATE]=0x1a000003u;
 b->r[T_CR1]=0x80u; b->r[T_DIER]=0; b->r[T_PSC]=17; b->r[T_ARR]=4095;
 b->r[T_CNT]=1789; b->active_psc=17; b->prescaler_phase=13;
}
static mt_timebase_io io_for(bus *b) { mt_timebase_io io={read32,write32,b}; return io; }
static void fresh(bus *b,mt_timebase *t,mt_timebase_io *io) {
 defaults(b); memset(t,0,sizeof(*t)); *io=io_for(b);
}
static void running(bus *b,mt_timebase *t) {
 mt_timebase_io io; fresh(b,t,&io); CHECK(mt_timebase_init(t,&io,72000000));
 CHECK(mt_timebase_healthy(t)); CHECK(t->owns_timer==1u); CHECK(b->order_errors==0u);
}
static void epoch(const mt_timebase *t,uint64_t total) {
 CHECK(t->us==(uint32_t)total); CHECK(t->ms==(uint32_t)(total/1000u));
 CHECK(t->remainder_us==total%1000u); CHECK(t->last_counter==(uint16_t)total);
 CHECK(mt_timebase_healthy(t));
}
static void change(bus *b,enum reg_id trigger,enum reg_id target,uint32_t value,int after,int on_write) {
 b->inject=1; b->trigger=trigger; b->target=target; b->value=value;
 b->skip=0; b->inject_after=after; b->inject_on_write=on_write;
}
static void test_init_order(void) {
 static const struct event expected[]={ {T_CR1,0},{T_CR2,0},{T_SMCR,0},{T_DIER,0},
  {T_CCER,0},{T_CCMR1,0},{T_CCMR2,0},{T_PSC,71},{T_ARR,65535},
  {T_EGR,1},{T_SR,0},{T_CNT,0},{T_CR1,1} };
 bus b; mt_timebase t; mt_timebase_io io; uint32_t old[REG_COUNT]; size_t i;
 fresh(&b,&t,&io); memcpy(old,b.r,sizeof(old)); CHECK(mt_timebase_init(&t,&io,72000000));
 CHECK(b.nw==COUNT(expected));
 for (i=0;i<COUNT(expected);++i) { CHECK(b.writes[i].reg==expected[i].reg); CHECK(b.writes[i].value==expected[i].value); }
 for (i=0;i<REG_COUNT;++i) if (i<T_CR1 || i>T_ARR) CHECK(b.r[i]==old[i]);
 CHECK(b.active_psc==71u && b.prescaler_phase==0 && b.ug_count==1u && b.order_errors==0);
 epoch(&t,0); cycles(&b,71); CHECK(b.r[T_CNT]==0); cycles(&b,1); CHECK(b.r[T_CNT]==1);
 CHECK(mt_timebase_update(&t)); epoch(&t,1); CHECK(b.nw==COUNT(expected));
}
static void test_invalid_args(void) {
 static const uint32_t hz[]={0,1,8000000,36000000,71999999,72000001,UINT32_MAX};
 bus b; mt_timebase t,old; mt_timebase_io io,bad; size_t i;
 fresh(&b,&t,&io); memcpy(&old,&t,sizeof(t));
 CHECK(!mt_timebase_init(NULL,&io,72000000)); CHECK(!mt_timebase_init(&t,NULL,72000000));
 bad=io; bad.read32=NULL; CHECK(!mt_timebase_init(&t,&bad,72000000));
 bad=io; bad.write32=NULL; CHECK(!mt_timebase_init(&t,&bad,72000000));
 for (i=0;i<COUNT(hz);++i) CHECK(!mt_timebase_init(&t,&io,hz[i]));
 CHECK(memcmp(&t,&old,sizeof(t))==0); CHECK(b.nr==0 && b.nw==0);
 CHECK(!mt_timebase_update(NULL)); CHECK(!mt_timebase_healthy(NULL)); CHECK(!mt_timebase_stop(NULL));
 CHECK(mt_timebase_now_us(NULL)==0); CHECK(mt_timebase_now_ms(NULL)==0);
 CHECK(!mt_timebase_update(&t)); CHECK(!mt_timebase_healthy(&t)); CHECK(!mt_timebase_stop(&t));
 CHECK(mt_timebase_now_us(&t)==0); CHECK(mt_timebase_now_ms(&t)==0);
}
static void test_active_rejection(void) {
 bus b; mt_timebase t; mt_timebase_io io; unsigned bit;
 for (bit=0;bit<33u;++bit) {
  fresh(&b,&t,&io); if (bit==32u) b.r[T_CR1]|=1u; else b.r[T_DIER]=UINT32_C(1)<<bit;
  CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.fault==MT_TIMEBASE_TIMER_FAULT);
  CHECK(!mt_timebase_healthy(&t) && !t.owns_timer && b.nw==0); CHECK(mt_timebase_stop(&t)); CHECK(b.nw==0);
 }
}
static void test_clock_profile(void) {
 static const struct event defects[]={ {R_GATE,0},{R_RESET,1},
  {R_CR,0x03030083u^(1u<<16)},{R_CR,0x03030083u^(1u<<17)},
  {R_CR,0x03030083u^(1u<<24)},{R_CR,0x03030083u^(1u<<25)},
  {R_CFGR,0x001d040au^1u},{R_CFGR,0x001d040au^4u},
  {R_CFGR,0x001d040au^(1u<<4)},{R_CFGR,0x001d040au^(1u<<8)},
  {R_CFGR,0x001d040au^(1u<<10)},{R_CFGR,0x001d040au^(1u<<16)} };
 bus b; mt_timebase t; mt_timebase_io io; size_t i;
 for (i=0;i<COUNT(defects);++i) { fresh(&b,&t,&io); b.r[defects[i].reg]=defects[i].value;
  CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.fault==MT_TIMEBASE_CLOCK_FAULT && !t.owns_timer && b.nw==0); }
 /* Frequency is caller-verified: the module does not measure the crystal or infer PLL multiplier. */
 fresh(&b,&t,&io); b.r[R_CFGR]^=1u<<18; CHECK(mt_timebase_init(&t,&io,72000000)); CHECK(mt_timebase_healthy(&t));
}
static void test_arithmetic(void) {
 static const uint32_t deltas[]={0,1,998,1,999,1,2559,2560,32000,32000,0,1,31999};
 bus b; mt_timebase t; uint64_t ref=0; size_t i;
 running(&b,&t);
 for (i=0;i<COUNT(deltas);++i) { ticks(&b,deltas[i]); ref+=deltas[i]; CHECK(mt_timebase_update(&t)); epoch(&t,ref); }
 running(&b,&t); ticks(&b,32000); CHECK(mt_timebase_update(&t)); ticks(&b,32000); CHECK(mt_timebase_update(&t));
 ticks(&b,1534); CHECK(mt_timebase_update(&t)); CHECK(t.last_counter==65534u);
 ticks(&b,3); CHECK(mt_timebase_update(&t)); epoch(&t,65537); CHECK(b.r[T_SR]&1u);
 running(&b,&t); ref=0;
 for (i=0;i<150000u;++i) { uint32_t d=i==134217u?7295u:32000u;
  ticks(&b,d); ref+=d; CHECK(mt_timebase_update(&t)); epoch(&t,ref); }
 CHECK(ref>UINT32_MAX && t.ms>4294967u && b.nw==13u);
}
static void seed(bus *b,mt_timebase *t,uint64_t total) {
 t->us=(uint32_t)total; t->ms=(uint32_t)(total/1000u); t->remainder_us=(uint32_t)(total%1000u);
 t->last_counter=(uint16_t)total; b->r[T_CNT]=(uint16_t)total;
}
static void test_exact_rollovers(void) {
 bus b; mt_timebase t; uint64_t ref; size_t i;
 running(&b,&t); ref=UINT32_MAX-UINT64_C(1); seed(&b,&t,ref);
 for (i=0;i<4;++i) { ticks(&b,1); ++ref; CHECK(mt_timebase_update(&t)); epoch(&t,ref); }
 CHECK(t.us==2u && t.ms==4294967u && t.remainder_us==298u);
 running(&b,&t); ref=(UINT64_C(1)<<32)*1000u-2u; seed(&b,&t,ref);
 for (i=0;i<4;++i) { ticks(&b,1); ++ref; CHECK(mt_timebase_update(&t)); epoch(&t,ref); }
 CHECK(t.ms==0u && t.us==2u && t.remainder_us==2u);
}
static void test_interleaving(void) {
 bus b; mt_timebase t; uint64_t ref=0; uint32_t random=0x45fa0713u; unsigned i;
 running(&b,&t);
 for (i=0;i<100000u;++i) { uint32_t d;
  random^=random<<13; random^=random>>17; random^=random<<5; d=random%32001u;
  ref+=d; ticks(&b,d);
  if ((i%7u)<3u) CHECK(mt_timebase_now_us(&t)==(uint32_t)ref);
  else CHECK(mt_timebase_now_ms(&t)==(uint32_t)(ref/1000u));
  epoch(&t,ref); CHECK(mt_timebase_now_us(&t)==(uint32_t)ref);
  CHECK(mt_timebase_now_ms(&t)==(uint32_t)(ref/1000u)); epoch(&t,ref);
 } CHECK(b.nw==13u);
}
static void test_single_callback_long_lived(void) {
 bus b; mt_timebase t; uint64_t ref; unsigned mode,i;
 for (mode=0;mode<2u;++mode) {
  running(&b,&t); ref=0;
  for (i=0;i<140000u;++i) { ref+=32000u; ticks(&b,32000u);
   if (mode==0u) CHECK(mt_timebase_now_us(&t)==(uint32_t)ref);
   else CHECK(mt_timebase_now_ms(&t)==(uint32_t)(ref/1000u));
   epoch(&t,ref);
  }
  CHECK(ref>UINT32_MAX && b.nw==13u);
 }
}
static void test_postsnapshot_progress(void) {
 bus b; mt_timebase t; running(&b,&t); ticks(&b,32000); CHECK(mt_timebase_update(&t));
 ticks(&b,32000); CHECK(mt_timebase_update(&t)); ticks(&b,1535); b.advance_after_cnt=3;
 CHECK(mt_timebase_now_us(&t)==65535u); epoch(&t,65535); CHECK(b.r[T_CNT]==2u);
 CHECK(mt_timebase_now_ms(&t)==65u); epoch(&t,65538); CHECK(t.remainder_us==538u);
}
static void test_service_fault(void) {
 static const uint32_t bad[]={32001,32768,65535}; bus b; mt_timebase t; size_t i; uint64_t reads;
 for (i=0;i<COUNT(bad);++i) { running(&b,&t); ticks(&b,1234); CHECK(mt_timebase_update(&t));
  ticks(&b,bad[i]); CHECK(!mt_timebase_update(&t)); CHECK(t.fault==MT_TIMEBASE_SERVICE_FAULT && !mt_timebase_healthy(&t));
  CHECK(t.us==1234u && t.ms==1u && t.remainder_us==234u && t.last_counter==1234u);
  reads=b.nr; b.r[T_CNT]=1234; CHECK(mt_timebase_now_us(&t)==1234u); CHECK(mt_timebase_now_ms(&t)==1u);
  CHECK(!mt_timebase_update(&t) && !mt_timebase_healthy(&t)); CHECK(b.nr==reads && b.nw==13u && t.owns_timer);
 }
}
static unsigned fault_scenarios;
static void fault_case(enum reg_id target,uint32_t mask,mt_timebase_fault expected,int during) {
 bus b; mt_timebase t; uint32_t saved; uint64_t reads;
 running(&b,&t); ticks(&b,1999); CHECK(mt_timebase_update(&t)); ticks(&b,1234); saved=b.r[target];
 if (during) change(&b,T_CNT,target,saved^mask,1,0); else b.r[target]^=mask;
 CHECK(!mt_timebase_update(&t) && !mt_timebase_healthy(&t)); CHECK(t.fault==expected);
 CHECK(t.us==1999u && t.ms==1u && t.remainder_us==999u && t.last_counter==1999u);
 CHECK(b.nw==13u && t.owns_timer); b.r[target]=saved; reads=b.nr;
 CHECK(mt_timebase_now_us(&t)==1999u); CHECK(mt_timebase_now_ms(&t)==1u);
 CHECK(!mt_timebase_healthy(&t) && b.nr==reads); ++fault_scenarios;
}
static void test_register_faults(void) {
 static const enum reg_id timer[]={T_CR1,T_CR2,T_SMCR,T_DIER,T_CCER,T_CCMR1,T_CCMR2,T_PSC,T_ARR};
 static const struct event clock[]={{R_CR,0x030f00fbu},{R_CFGR,0x003f3fffu},{R_GATE,1},{R_RESET,1}};
 size_t i; unsigned bit; int during;
 for (during=0;during<=1;++during) {
  for (i=0;i<COUNT(timer);++i) for (bit=0;bit<32u;++bit) fault_case(timer[i],UINT32_C(1)<<bit,MT_TIMEBASE_TIMER_FAULT,during);
  for (i=0;i<COUNT(clock);++i) for (bit=0;bit<32u;++bit)
   if (clock[i].value&(UINT32_C(1)<<bit)) fault_case(clock[i].reg,UINT32_C(1)<<bit,MT_TIMEBASE_CLOCK_FAULT,during);
 }
 printf("  register bit-injection scenarios: %u\n",fault_scenarios);
}
static void test_latched_health_and_unrelated(void) {
 bus b; mt_timebase t; uint64_t reads; running(&b,&t); reads=b.nr; b.r[T_PSC]=70;
 CHECK(mt_timebase_healthy(&t) && b.nr==reads); CHECK(!mt_timebase_update(&t) && !mt_timebase_healthy(&t));
 running(&b,&t); b.r[R_CR]^=~UINT32_C(0x030f00fb); b.r[R_CFGR]^=~UINT32_C(0x003f3fff);
 b.r[R_GATE]^=~UINT32_C(1); b.r[R_RESET]^=~UINT32_C(1); b.r[T_SR]=UINT32_MAX; b.r[T_CCR1]^=0x7a7au;
 ticks(&b,1001); CHECK(mt_timebase_update(&t)); epoch(&t,1001); CHECK(b.nw==13u);
}
static void test_limitations(void) {
 static const uint64_t missed[]={65536u,65536u+100u,131072u+32000u}; bus b; mt_timebase t; size_t i;
 for (i=0;i<COUNT(missed);++i) { running(&b,&t); ticks(&b,missed[i]);
  CHECK(mt_timebase_update(&t) && mt_timebase_healthy(&t)); CHECK(t.us==(uint16_t)missed[i]);
  CHECK(b.physical_ticks==missed[i] && (uint64_t)t.us!=b.physical_ticks); }
 running(&b,&t); b.r[R_GATE]&=~1u; ticks(&b,1000); b.r[R_GATE]|=1u;
 CHECK(mt_timebase_update(&t)); epoch(&t,0); /* Temporary stopped clock is invisible. */
 running(&b,&t); ticks(&b,100); b.r[T_CNT]=7; CHECK(mt_timebase_update(&t)); CHECK(t.us==7u);
 running(&b,&t); CHECK(mt_timebase_update(&t)); CHECK(mt_timebase_update(&t)); epoch(&t,0);
}
static void test_init_failures(void) {
 static const enum reg_id writable[]={T_CR2,T_SMCR,T_CCER,T_CCMR1,T_CCMR2,T_PSC,T_ARR};
 bus b; mt_timebase t; mt_timebase_io io; size_t i;
 for (i=0;i<COUNT(writable);++i) { fresh(&b,&t,&io); b.ignore_write=1; b.ignore_reg=writable[i];
  CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.owns_timer && t.fault==MT_TIMEBASE_TIMER_FAULT && !mt_timebase_healthy(&t));
  b.ignore_write=0; CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer && !(b.r[T_CR1]&1u)); }
 fresh(&b,&t,&io); change(&b,T_CR1,R_CFGR,b.r[R_CFGR]^(1u<<18),1,0);
 CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.owns_timer && t.fault==MT_TIMEBASE_CLOCK_FAULT); CHECK(mt_timebase_stop(&t));
 fresh(&b,&t,&io); b.r[T_CR1]=0; b.ignore_write=1; b.ignore_reg=T_CR1;
 CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.owns_timer && t.fault==MT_TIMEBASE_TIMER_FAULT);
 b.ignore_write=0; CHECK(mt_timebase_stop(&t));
 fresh(&b,&t,&io); b.ticks_at_start=32001;
 CHECK(!mt_timebase_init(&t,&io,72000000)); CHECK(t.owns_timer && t.fault==MT_TIMEBASE_SERVICE_FAULT);
 CHECK(t.us==0u && t.ms==0u && t.last_counter==0u); CHECK(mt_timebase_stop(&t));
 fresh(&b,&t,&io); b.ticks_at_start=32000; CHECK(mt_timebase_init(&t,&io,72000000)); epoch(&t,32000);
}
static void unchanged_reinit(bus *b,mt_timebase *t) {
 mt_timebase old; mt_timebase_io io=io_for(b); size_t nw=b->nw; uint64_t nr=b->nr;
 memcpy(&old,t,sizeof(old)); CHECK(!mt_timebase_init(t,&io,72000000));
 CHECK(memcmp(t,&old,sizeof(old))==0 && b->nw==nw && b->nr==nr);
}
static void test_stop_and_reinit(void) {
 bus b; mt_timebase t; size_t nw; unsigned reason; uint64_t nr;
 running(&b,&t); ticks(&b,1999); CHECK(mt_timebase_update(&t)); unchanged_reinit(&b,&t); nw=b.nw;
 CHECK(mt_timebase_stop(&t)); CHECK(t.state==MT_TIMEBASE_STOPPED && !t.owns_timer);
 CHECK(b.nw==nw+1u && b.writes[nw].reg==T_CR1 && b.writes[nw].value==0);
 CHECK(!mt_timebase_healthy(&t)); nr=b.nr;
 CHECK(mt_timebase_now_us(&t)==1999u && mt_timebase_now_ms(&t)==1u && b.nr==nr);
 CHECK(mt_timebase_stop(&t)); CHECK(b.nw==nw+1u);
 for (reason=0;reason<3u;++reason) { running(&b,&t); nw=b.nw;
  if (reason==0u) b.r[R_GATE]&=~1u;
  if (reason==1u) b.r[R_RESET]|=1u;
  if (reason==2u) { b.ignore_write=1; b.ignore_reg=T_CR1; }
  CHECK(!mt_timebase_stop(&t)); CHECK(t.owns_timer && t.state==MT_TIMEBASE_FAULTED);
  CHECK(t.fault==MT_TIMEBASE_STOP_FAULT && !mt_timebase_healthy(&t)); unchanged_reinit(&b,&t);
  CHECK(b.nw==nw+(reason==2u?1u:0u)); b.r[R_GATE]|=1u; b.r[R_RESET]&=~1u; b.ignore_write=0;
  CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer && t.state==MT_TIMEBASE_STOPPED && !(b.r[T_CR1]&1u));
  CHECK(!mt_timebase_healthy(&t));
 }
 running(&b,&t); ticks(&b,32001); CHECK(!mt_timebase_update(&t)); unchanged_reinit(&b,&t);
 CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer && t.fault==MT_TIMEBASE_SERVICE_FAULT);
}
static void test_stop_access_races(void) {
 bus b; mt_timebase t; unsigned kind,where;
 for (kind=0;kind<2u;++kind) for (where=0;where<3u;++where) {
  enum reg_id target=kind==0u?R_GATE:R_RESET;
  running(&b,&t); b.zero_if_inaccessible=1;
  change(&b,T_CR1,target,kind==0u?(b.r[R_GATE]&~1u):(b.r[R_RESET]|1u),where==1u?1:0,where==0u?1:0);
  CHECK(!mt_timebase_stop(&t)); CHECK(t.owns_timer && t.fault==MT_TIMEBASE_STOP_FAULT && !mt_timebase_healthy(&t));
  unchanged_reinit(&b,&t); b.r[R_GATE]|=1u; b.r[R_RESET]&=~1u;
  CHECK(mt_timebase_stop(&t)); CHECK(!t.owns_timer && !(b.r[T_CR1]&1u));
 }
}
int main(void) {
 static const struct {const char *name;void (*run)(void);} tests[]={
 {"init order, preload/UG, write whitelist",test_init_order},
 {"invalid arguments and objects",test_invalid_args},
 {"active CEN / every DIER bit refused without writes",test_active_rejection},
 {"initial clock profile and caller frequency trust",test_clock_profile},
 {"delta/remainder arithmetic and 150000-step us wrap",test_arithmetic},
 {"exact independent us32/ms32 rollovers",test_exact_rollovers},
 {"100000 callback intervals with shared epoch",test_interleaving},
 {"only us / only ms callbacks independently cross us32 wrap",test_single_callback_long_lived},
 {"counter wrap during post-snapshot checks",test_postsnapshot_progress},
 {"observed service threshold and sticky values",test_service_fault},
 {"all monitored register bits before/during CNT read",test_register_faults},
 {"latched health and unrelated register changes",test_latched_health_and_unrelated},
 {"missed wraps, transient, stuck counter limitations",test_limitations},
 {"initialization failure retains ownership",test_init_failures},
 {"Stop/retry and owned/live reinit rejection",test_stop_and_reinit},
 {"gate/reset races during Stop preserve ownership",test_stop_access_races}};
 size_t i; for (i=0;i<COUNT(tests);++i) { tests[i].run(); printf("PASS %02zu %s\n",i+1u,tests[i].name); }
 printf("PASS all %zu independent host test groups\n",COUNT(tests)); return 0;
}
