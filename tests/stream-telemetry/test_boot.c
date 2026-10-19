/* Boot composition. Real boot main/clock/TIME/runtime/descriptors/TinyUSB.
 * Seams: register bus, Cortex-M CPU mask/admit/halt, DCD initialization/IRQ
 * registers, CTR rearm. Runtime loop exits only via a host-test longjmp. */
#include "mt_board_inputs.h"
#include "mt_stream_timing.h"
#include "mt_boot_cpu.h"
#include "mt_boot_time.h"
#include "mt_boot_usb.h"
#include "mt_live_runtime.h"
#include "mt_usb_time.h"
#include "mt_irq_guard.h"
#include "mt_usb_ctr.h"
#include "mt_stream_diag.h"
#include "tusb.h"
#include "device/dcd.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"FAIL %s %s:%d: %s\n",name,__func__,__LINE__,#x); exit(1); } } while(0)
static const char *name="setup";
static jmp_buf jump;
extern int mt_stream_boot_entry(void);
extern volatile unsigned mt_main_boot_failure,mt_main_irq_admitted,mt_main_usb_init_result;
extern volatile mt_live_runtime_result mt_main_last_runtime;
extern const unsigned fixture_expected_failure;
enum { CR=0x40021000,CFGR=0x40021004,APB2R=0x4002100c,APB1R=0x40021010,AHB=0x40021014,APB2=0x40021018,APB1=0x4002101c,FLASH=0x40022000,TIME_CNT=0x40000024,TIME_PSC=0x40000028,TIM3_CR1=0x40000400,ADC_CR2=0x40012408,DMA_CCR=0x40020008,CNTR=0x40005c40,BTABLE=0x40005c50 };
#define ISER0 UINT32_C(0xe000e100)
#define ISER1 UINT32_C(0xe000e104)
#define ISPR0 UINT32_C(0xe000e200)
#define ISPR1 UINT32_C(0xe000e204)
#define IABR0 UINT32_C(0xe000e300)
#define IABR1 UINT32_C(0xe000e304)
#define IPR4 UINT32_C(0xe000e410)
#define IPR5 UINT32_C(0xe000e414)
typedef struct { uint32_t address,value; } reg;
static reg regs[120]; static unsigned used,mask=1,nesting,admitted,steps,writes,time_reads,pending_writes;
static char order[64]; static unsigned order_used;
static mt_live_runtime *live; static mt_timebase *clock_ptr;
static unsigned fail_stage,expected_failure,dwt_reads,dwt_writes;
#define DEMCR UINT32_C(0xe000edfc)
#define DWT_CTRL UINT32_C(0xe0001000)
#define DWT_CNT UINT32_C(0xe0001004)
typedef struct { const char *name; uint32_t address,value; unsigned operation; } mutation;
static const mutation mutations[]={
 {"final_clock_hse",CR,1u<<17,1},{"final_clock_divider",CFGR,1u<<22,1},{"final_flash",FLASH,0x30,0},
 {"final_usb_gate",APB1,0x800000,2},{"final_can_gate",APB1,0x2000000,3},{"final_tim2_gate",APB1,1,2},{"final_tim3_gate",APB1,2,2},
 {"final_dma_gate",AHB,1,2},{"final_adc_gate",APB2,0x200,2},{"final_gpio_gate",APB2,4,2},
 {"final_adc_reset",APB2R,0x200,3},{"final_gpio_reset",APB2R,4,3},{"final_tim2_reset",APB1R,1,3},{"final_tim3_reset",APB1R,2,3},{"final_usb_reset",APB1R,0x800000,3},{"final_can_reset",APB1R,0x2000000,3},
 {"final_tim3_running",TIM3_CR1,1,3},{"final_adc_active",ADC_CR2,1,3},{"final_dma_active",DMA_CCR,1,3},
 {"final_nvic_extra_enable",ISER0,1,3},{"final_nvic_missing_usb",ISER0,0x80000,2},{"final_nvic_bank1",ISER1,1,3},
 {"final_can_pending21",ISPR0,0x200000,3},{"final_can_pending22",ISPR0,0x400000,3},{"final_usb_active",IABR0,0x80000,3},{"final_wake_pending",ISPR1,0x400,3},{"final_wake_active",IABR1,0x400,3},
 {"final_priority_hp",IPR4,0xf0000000,1},{"final_priority_lp",IPR5,0xf0,1},
 {"final_exti_imr",0x40010400,0x40000,3},{"final_exti_emr",0x40010404,0x40000,3},{"final_exti_rtsr",0x40010408,0x40000,3},{"final_exti_ftsr",0x4001040c,0x40000,3},{"final_exti_swier",0x40010410,0x40000,3},{"final_exti_pending",0x40010414,0x40000,3},
 {"final_usb_control",CNTR,0x100,1},{"final_btable",BTABLE,8,0},{"final_time_prescaler",TIME_PSC,70,0},{"final_time_gap",TIME_CNT,40000,0}
};
static uint32_t *slot(uint32_t a) { CHECK(!(a&3u)); for(unsigned i=0;i<used;i++)if(regs[i].address==a)return &regs[i].value; CHECK(used<120); regs[used]=(reg){a,0}; return &regs[used++].value; }
static void note(char c) { CHECK(order_used+1u<sizeof order); order[order_used++]=c; order[order_used]=0; }
uint32_t __wrap_mt_clock_mmio_read32(void *ctx,uint32_t a) { CHECK(ctx==NULL); if(a==DWT_CNT) { ++dwt_reads; if(!strcmp(name,"dwt_stopped"))return *slot(a); return (*slot(a))++; } if(a==TIME_CNT) { ++time_reads; return (*slot(a))++&65535u; } return *slot(a); }
void __wrap_mt_clock_mmio_write32(void *ctx,uint32_t a,uint32_t v) { CHECK(ctx==NULL && mask); ++writes; if(a==DWT_CNT)CHECK(0); if(a==DEMCR || a==DWT_CTRL) { ++dwt_writes; if((a==DEMCR && !strcmp(name,"dwt_demcr_denied")) || (a==DWT_CTRL && !strcmp(name,"dwt_enable_denied")))return; } if(a==ISPR0 || a==ISPR1 || a==0xe000e280u || a==0xe000e284u)++pending_writes; if(a==CR) { if(v&0x10000u)v|=0x20000u; else v&=~0x20000u; if(v&0x1000000u)v|=0x2000000u; else v&=~0x2000000u; } if(a==CFGR)v=(v&~12u)|((v&3u)<<2); if(a==0x40010414u) { *slot(a)&=~v; return; } *slot(a)=v; }
uint32_t mt_irq_guard_enter(void) { uint32_t previous=mask; mask=1; ++nesting; return previous; }
void mt_irq_guard_leave(uint32_t previous) { CHECK(mask && nesting); --nesting; mask=previous; }
int mt_boot_cpu_masked(void) { return fail_stage==1u?0:(mask==1u && !admitted); }
int mt_boot_cpu_admit(void) { note('A'); CHECK(mask && !nesting && live && clock_ptr && mt_timebase_healthy(clock_ptr)); if(fail_stage==11u)return 0; admitted=1; mask=0; return 1; }
_Noreturn void mt_boot_cpu_halt(void) { CHECK(!admitted && !mt_main_irq_admitted); mask=1; longjmp(jump,1); }
static void mutate_final(void) {
 for(unsigned i=0;i<sizeof mutations/sizeof mutations[0];i++)if(!strcmp(name,mutations[i].name)) { const mutation *m=&mutations[i]; uint32_t *p=slot(m->address); if(m->operation==0)*p=m->value; if(m->operation==1)*p^=m->value; if(m->operation==2)*p&=~m->value; if(m->operation==3)*p|=m->value; }
 if(!strcmp(name,"pending_usb_irqs_allowed"))*slot(ISPR0)=0x180000u;
 if(!strcmp(name,"daddr_and_ep0_not_pristine")) { *slot(0x40005c4cu)=0x87u; *slot(0x40005c00u)=0x3230u; }
 if(!strcmp(name,"usb_reserved_bits_ignored")) { *slot(CNTR)|=0xabcd00e0u; *slot(BTABLE)|=0xbeef0007u; }
 if(!strcmp(name,"state_runtime_uninitialized"))live->initialized=0;
 if(!strcmp(name,"state_adc_uninitialized"))live->adc.initialized=0;
 if(!strcmp(name,"state_binding_uninitialized"))live->acquisition.initialized=0;
 if(!strcmp(name,"state_binding_driver"))live->acquisition.driver=NULL;
 if(!strcmp(name,"state_binding_clock"))live->acquisition.clock=NULL;
 if(!strcmp(name,"state_binding_device"))live->acquisition.device=NULL;
 if(!strcmp(name,"state_in_step"))live->in_step=1;
 if(!strcmp(name,"state_writer_active"))live->writer_active=1;
 if(!strcmp(name,"state_clock_fault"))live->clock_fault=1;
 if(!strcmp(name,"state_clock_failed"))live->clock64.failed=1;
 if(!strcmp(name,"state_clock_source"))live->clock64.source=NULL;
 if(!strcmp(name,"state_runtime_clock"))live->clock=NULL;
 if(!strcmp(name,"state_adc_running"))live->adc.state=MT_ADC_DMA_RUNNING;
 if(!strcmp(name,"state_adc_owned"))live->adc.owns_peripherals=1;
 if(!strcmp(name,"state_not_stream"))live->adc.stream_mode=0;
 if(!strcmp(name,"state_live_session"))live->device.state=MT_LIVE_STOPPED;
}
int __real_mt_usb_descriptors_init(const mt_usb_identity *);
int __wrap_mt_usb_descriptors_init(const mt_usb_identity *id) { note('D'); CHECK(mask && !tud_inited()); int result=__real_mt_usb_descriptors_init(id); if(result && !strcmp(name,"reentrant_boot_after_descriptor"))(void)mt_stream_boot_entry(); return result; }
mt_clock_result __real_mt_clock_apply(mt_clock_startup *,const mt_clock_io *,uint32_t,mt_clock_hse_mode,uint32_t);
mt_clock_result __wrap_mt_clock_apply(mt_clock_startup *s,const mt_clock_io *io,uint32_t hz,mt_clock_hse_mode mode,uint32_t polls) { note('C'); CHECK(mask && !tud_inited()); mt_clock_result r=__real_mt_clock_apply(s,io,hz,mode,polls); if(fail_stage==5u)*slot(APB1)|=1u; return r; }
mt_boot_time_result __real_mt_boot_time_start(mt_boot_time *,const mt_clock_startup *,mt_timebase *);
mt_boot_time_result __wrap_mt_boot_time_start(mt_boot_time *b,const mt_clock_startup *s,mt_timebase *t) { note('T'); clock_ptr=t; CHECK(mask && s->applied==1u && !tud_inited()); mt_boot_time_result r=__real_mt_boot_time_start(b,s,t); if(fail_stage==6u)(void)tusb_time_millis_api(); return r; }
int __real_mt_usb_time_bind(mt_timebase *);
int __wrap_mt_usb_time_bind(mt_timebase *t) { note('B'); CHECK(mask && t==clock_ptr && !tud_inited()); int r=__real_mt_usb_time_bind(t); if(fail_stage==7u)*slot(ADC_CR2)=1; return r; }
int __real_mt_stream_diag_init(uint32_t (*)(void *,uint32_t),void (*)(void *,uint32_t,uint32_t),void *,uint32_t);
int __wrap_mt_stream_diag_init(uint32_t (*read32)(void *,uint32_t),void (*write32)(void *,uint32_t,uint32_t),void *context,uint32_t build) {
 note('G'); CHECK(mask && !admitted && !live && clock_ptr && mt_timebase_healthy(clock_ptr) && mt_usb_time_valid() && !tud_inited());
 unsigned before=time_reads; int result=__real_mt_stream_diag_init(read32,write32,context,build);
 CHECK(time_reads==before && mt_stream_diagnostics.firmware_build==mt_board.firmware_build); return result;
}
mt_live_runtime_result __real_mt_live_runtime_init(mt_live_runtime *,mt_timebase *,const mt_live_runtime_mmio *,uint32_t,mt_usb_lock_fn,mt_usb_unlock_fn,uint32_t,unsigned,uint32_t);
mt_live_runtime_result __wrap_mt_live_runtime_init(mt_live_runtime *r,mt_timebase *t,const mt_live_runtime_mmio *io,uint32_t hz,mt_usb_lock_fn enter,mt_usb_unlock_fn leave,uint32_t build,unsigned dtr,uint32_t bound) { note('R'); CHECK(mt_stream_diagnostics.phase==1u && dwt_reads>=2u); live=r; CHECK(mask && !tud_inited() && t==clock_ptr && mt_usb_time_valid() && bound==mt_stream_timing.declared_transaction_bound_ms); mt_live_runtime_result result=__real_mt_live_runtime_init(r,t,io,hz,enter,leave,build,dtr,bound); if(fail_stage==8u)*slot(APB1)|=0x2000000u; if(result==MT_LIVE_RUNTIME_OK && !strcmp(name,"duplicate_boot_after_runtime"))(void)mt_stream_boot_entry(); return result; }
mt_boot_usb_result __real_mt_boot_usb_prepare(mt_boot_usb *,const mt_clock_startup *,uint32_t);
mt_boot_usb_result __wrap_mt_boot_usb_prepare(mt_boot_usb *b,const mt_clock_startup *s,uint32_t p) { note('U'); CHECK(mask && live && live->initialized && live->adc.stream_mode && !tud_inited()); return __real_mt_boot_usb_prepare(b,s,p); }
bool __real_tusb_rhport_init(uint8_t,const tusb_rhport_init_t *);
bool __wrap_tusb_rhport_init(uint8_t p,const tusb_rhport_init_t *init) { note('I'); CHECK(mask && live && live->initialized && mt_usb_time_valid()); bool r=__real_tusb_rhport_init(p,init); mutate_final(); return r; }
mt_live_runtime_result __real_mt_live_runtime_step(mt_live_runtime *);
mt_live_runtime_result __wrap_mt_live_runtime_step(mt_live_runtime *r) { note('S'); CHECK(admitted && mt_main_irq_admitted && !mask && r==live && r->initialized); ++steps; if(!strcmp(name,"recovery_keeps_storage_and_steps")) { if(steps==1u) { *slot(DMA_CCR)=1u; dcd_event_bus_reset(0,TUSB_SPEED_FULL,true); } if(steps==3u)*slot(DMA_CCR)=0u; } if(!strcmp(name,"time_fault_keeps_storage_and_steps") && steps==1u)*slot(TIME_PSC)=70u; mt_live_runtime_result result=__real_mt_live_runtime_step(r); if(!strcmp(name,"time_fault_keeps_storage_and_steps")) { CHECK(result==MT_LIVE_RUNTIME_TIME_FAULT && r->clock_fault && r->pending==2u); if(steps>1u)CHECK(mt_main_last_runtime==MT_LIVE_RUNTIME_TIME_FAULT); } else if(!strcmp(name,"recovery_keeps_storage_and_steps")) { CHECK(result==(steps<3u?MT_LIVE_RUNTIME_RECOVERY:MT_LIVE_RUNTIME_IDLE)); if(steps>1u)CHECK(mt_main_last_runtime==MT_LIVE_RUNTIME_RECOVERY); CHECK(!r->adc.owns_peripherals); } else CHECK(result==MT_LIVE_RUNTIME_IDLE); if(steps==3u)longjmp(jump,2); return result; }
bool mt_usb_fsdev_ctr_rearm(void) { CHECK(mask && nesting && admitted); return true; }
bool mt_usb_fsdev_ctr_deferred(void) { return true; }
bool dcd_init(uint8_t p,const tusb_rhport_init_t *init) { CHECK(mask && p==0 && init && live && live->initialized); if(fail_stage==9u)return false; *slot(CNTR)=0xdd00u; *slot(BTABLE)=0; return true; }
bool dcd_deinit(uint8_t p) { (void)p; return true; }
void dcd_int_enable(uint8_t p) { CHECK(p==0); *slot(ISER0)|=0x180000u; }
void dcd_int_disable(uint8_t p) { CHECK(p==0); }
void dcd_int_handler(uint8_t p) { (void)p; CHECK(0); }
void dcd_connect(uint8_t p) { CHECK(p==0); }
void dcd_disconnect(uint8_t p) { (void)p; }
void dcd_remote_wakeup(uint8_t p) { (void)p; CHECK(0); }
void dcd_sof_enable(uint8_t p,bool en) { (void)p; (void)en; }
void dcd_set_address(uint8_t p,uint8_t a) { (void)p; (void)a; CHECK(0); }
void dcd_edpt0_status_complete(uint8_t p,const tusb_control_request_t *r) { (void)p; (void)r; CHECK(0); }
bool dcd_edpt_open(uint8_t p,const tusb_desc_endpoint_t *d) { (void)p; (void)d; CHECK(0); return false; }
void dcd_edpt_close_all(uint8_t p) { (void)p; }
bool dcd_edpt_xfer(uint8_t p,uint8_t a,uint8_t *b,uint16_t n,bool isr) { (void)p; (void)a; (void)b; (void)n; (void)isr; CHECK(0); return false; }
void dcd_edpt_stall(uint8_t p,uint8_t a) { (void)p; (void)a; CHECK(0); }
void dcd_edpt_clear_stall(uint8_t p,uint8_t a) { (void)p; (void)a; CHECK(0); }
void dcd_edpt_close(uint8_t p,uint8_t a) { (void)p; (void)a; CHECK(0); }
bool dcd_edpt_iso_alloc(uint8_t p,uint8_t a,uint16_t n) { (void)p; (void)a; (void)n; CHECK(0); return false; }
bool dcd_edpt_iso_activate(uint8_t p,const tusb_desc_endpoint_t *d) { (void)p; (void)d; CHECK(0); return false; }
static const char *basic[]={"dwt_unsupported","dwt_demcr_denied","dwt_enable_denied","dwt_stopped","success_order_and_real_init","reentrant_boot_after_descriptor","duplicate_boot_after_runtime","pending_usb_irqs_allowed","daddr_and_ep0_not_pristine","usb_reserved_bits_ignored","time_fault_keeps_storage_and_steps","recovery_keeps_storage_and_steps","cpu_not_masked","clock_not_reset","time_already_enabled","usb_time_early_call","adc_already_active","usb_can_conflict","dcd_init_failure","cpu_admit_rejected","state_runtime_uninitialized","state_adc_uninitialized","state_binding_uninitialized","state_binding_driver","state_binding_clock","state_binding_device","state_in_step","state_writer_active","state_clock_fault","state_clock_failed","state_clock_source","state_runtime_clock","state_adc_running","state_adc_owned","state_not_stream","state_live_session","invalid_inputs"};
int main(int argc,char **argv) {
 CHECK(argc==2); if(!strcmp(argv[1],"--list")) { for(unsigned i=0;i<sizeof basic/sizeof basic[0];i++)puts(basic[i]); for(unsigned i=0;i<sizeof mutations/sizeof mutations[0];i++)puts(mutations[i].name); return 0; }
 name=argv[1]; *slot(CR)=0x83u; *slot(FLASH)=0x30u; *slot(AHB)=0x14u; expected_failure=0; if(!strncmp(name,"dwt_",4))expected_failure=12u; if(!strcmp(name,"dwt_unsupported"))*slot(DWT_CTRL)=1u<<25;
 if(!strcmp(name,"reentrant_boot_after_descriptor") || !strcmp(name,"duplicate_boot_after_runtime"))expected_failure=3;
 if(!strcmp(name,"cpu_not_masked"))fail_stage=expected_failure=1;
 if(!strcmp(name,"clock_not_reset")) { *slot(CFGR)=1; fail_stage=expected_failure=4; }
 if(!strcmp(name,"time_already_enabled"))fail_stage=expected_failure=5;
 if(!strcmp(name,"usb_time_early_call"))fail_stage=expected_failure=6;
 if(!strcmp(name,"adc_already_active"))fail_stage=expected_failure=7;
 if(!strcmp(name,"usb_can_conflict"))fail_stage=expected_failure=8;
 if(!strcmp(name,"dcd_init_failure"))fail_stage=expected_failure=9;
 if(!strcmp(name,"cpu_admit_rejected"))fail_stage=expected_failure=11;
 if(!strncmp(name,"final_",6) || !strncmp(name,"state_",6))expected_failure=10;
 if(!strcmp(name,"invalid_inputs"))expected_failure=fixture_expected_failure;
 int outcome=setjmp(jump); if(!outcome) { (void)mt_stream_boot_entry(); CHECK(0); }
 if(expected_failure) { CHECK(outcome==1 && mt_main_boot_failure==expected_failure && !admitted && !steps && !mt_main_irq_admitted); if(expected_failure<=2u)CHECK(!writes && !order_used); if(expected_failure==12u)CHECK(!strcmp(order,"DCTBG") && mt_stream_diagnostics.phase==4u && (mt_stream_diagnostics.flags&1u) && !live && !tud_inited()); if(expected_failure==10u)CHECK(!strcmp(order,"DCTBGRUI")); if(expected_failure==11u)CHECK(!strcmp(order,"DCTBGRUIA")); }
 else { CHECK(outcome==2 && !mt_main_boot_failure && mt_main_usb_init_result==1u && admitted && steps==3u && !strcmp(order,"DCTBGRUIASSS")); CHECK(live->initialized && live->acquisition.driver==&live->adc && live->acquisition.clock==clock_ptr && live->acquisition.device==&live->device); }
 if(!strcmp(name,"reentrant_boot_after_descriptor"))CHECK(!strcmp(order,"DD") && !writes);
 if(!strcmp(name,"duplicate_boot_after_runtime"))CHECK(!strcmp(order,"DCTBGRD") && live && live->initialized && live->acquisition.driver==&live->adc && live->clock==clock_ptr && !live->adc.owns_peripherals);
 if(expected_failure<=6u && expected_failure && strcmp(name,"duplicate_boot_after_runtime"))CHECK(!dwt_reads && !dwt_writes && !mt_stream_diagnostics.phase);
 CHECK(!pending_writes); if(!strcmp(name,"pending_usb_irqs_allowed"))CHECK(*slot(ISPR0)==0x180000u);
 printf("{\"case\":\"%s\",\"pass\":true,\"failure_stage\":%u,\"order\":\"%s\"}\n",name,expected_failure,order); return 0;
}
