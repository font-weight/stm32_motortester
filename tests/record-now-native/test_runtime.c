/* Explicit Single/record-now derivative; historical fixture is preserved. */
/* Native integration: production TIME/ADC/control/RX/USB/core/CDC/FIFO.
 * Only the register bus, DCD and nested interrupt-mask behavior are modeled.
 * DMA samples are synthetic. No target timing, IRQ, USB or ADC proof. */
#include "mt_runtime.h"
#include "test_integration.h"
#include "fake_dcd.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

enum {
 R_CR=0x40021000, R_CFGR=0x40021004, R_AHB=0x40021014,
 R_APB2=0x40021018, R_APB1=0x4002101c, GPIO=0x40010800,
 A_CR2=0x40012408, T_CR1=0x40000400, D_ISR=0x40020000,
 D_IFCR=0x40020004, D_CCR=0x40020008, D_N=0x4002000c,
 C_CR1=0x40000000, C_CNT=0x40000024, C_PSC=0x40000028
};
enum injection { NONE, RESET_CAL, SUSPEND_CAL, RESET_CEN, DROP_CEN_STUCK,
                 CLOCK_CAL, CLOCK_CEN, RECURSE_CAL, RESET_STOP };
typedef struct { uint32_t address, value; } reg;
static struct {
 reg regs[80]; unsigned count, writes, stops, enables, cen, time_reads;
 uint32_t counter, time_step;
 unsigned stop_stuck, injected;
 enum injection injection;
} hw;
static uint8_t start_operation = MT_CMD_ARM;
static mt_runtime runtime;
static mt_timebase clock_source;
static unsigned primask, nesting, pending_reset, recursive_calls;
static uint32_t injected_actor, injected_current;
static mt_runtime_result last_result;
static unsigned mounts, dcd_calls;
static uint64_t nonce;
static uint32_t sequence;
int test_reject_open = -1, test_reject_xfer = -1;
static uint32_t *slot(uint32_t address) {
 assert(address % 4u == 0u);
 for (unsigned i=0; i<hw.count; ++i) if (hw.regs[i].address == address) return &hw.regs[i].value;
 assert(hw.count < 80u); hw.regs[hw.count]=(reg){address,0}; return &hw.regs[hw.count++].value;
}
static void dispatch_reset(void) {
 if (!primask && pending_reset) { pending_reset=0; fake_bus_reset(false); }
}
static void request_reset(void) { pending_reset=1; dispatch_reset(); }
static uint32_t lock(void) { uint32_t prior=primask; primask=1; ++nesting; return prior; }
static void unlock(uint32_t prior) { assert(primask && nesting); --nesting; primask=prior; dispatch_reset(); }
static void inject(enum injection point) {
 if (hw.injected || hw.injection != point) return;
 hw.injected=1; injected_actor=mt_usb_bridge_actor();
 if (point == RESET_CAL || point == RESET_CEN || point == RESET_STOP) request_reset();
 if (point == SUSPEND_CAL) fake_signal(DCD_EVENT_SUSPEND,false);
 if (point == CLOCK_CAL || point == CLOCK_CEN) *slot(C_PSC)=72;
 if (point == DROP_CEN_STUCK) hw.stop_stuck=1;
 if (point == RECURSE_CAL) {
   assert(runtime.in_step); assert(mt_runtime_step(&runtime) == MT_RUNTIME_ARGUMENT);
   ++recursive_calls; assert(mt_usb_bridge_actor() == injected_actor);
 }
 injected_current=mt_usb_bridge_snapshot().current;
}
static uint32_t read32(void *context, uint32_t address) {
 assert(context == &hw);
 if (address == C_CNT) { uint32_t now=hw.counter; hw.counter=(hw.counter+hw.time_step)&65535u; ++hw.time_reads; return now; }
 uint32_t *value=slot(address);
 if (address == A_CR2) *value &= ~12u; /* Immediate modeled calibration completion. */
 return *value;
}
static void write32(void *context, uint32_t address, uint32_t value) {
 assert(context == &hw); ++hw.writes;
 uint32_t *destination=slot(address); uint32_t requested=value;
 if (address == C_CNT) hw.counter=value;
 if (address == D_IFCR) { assert(!(value&1u)); *slot(D_ISR) &= ~value; }
 if (address == T_CR1 && value == 0u) { assert(!nesting); ++hw.stops; if (runtime.pending == 2u) inject(RESET_STOP); }
 if (address == D_CCR && hw.stop_stuck && (*destination&1u)) value|=1u;
 if (address == A_CR2 && (value&8u)) {
   assert(!nesting); inject(RESET_CAL); inject(SUSPEND_CAL); inject(CLOCK_CAL); inject(RECURSE_CAL);
 }
 if ((address == D_CCR && (requested&1u)) || (address == A_CR2 && (requested&(1u<<20))) || (address == T_CR1 && requested == 1u)) {
   assert(primask && nesting && mt_usb_bridge_actor()); ++hw.enables;
 }
 if (address == T_CR1 && value == 1u) {
   ++hw.cen; inject(RESET_CEN); inject(CLOCK_CEN); inject(DROP_CEN_STUCK);
   if (hw.injection == DROP_CEN_STUCK) return;
 }
 *destination=value;
}
void test_dcd_call(enum test_operation operation) {
 (void)operation; assert(primask && nesting && mt_usb_bridge_actor()); ++dcd_calls;
}
void test_mount_hook(void) { ++mounts; assert(runtime.in_step); }
static mt_runtime_result step(void) {
 last_result=mt_runtime_step(&runtime);
 assert(!primask && !nesting && !mt_usb_bridge_actor() && !runtime.in_step);
 return last_result;
}
void test_pump(void) {
 unsigned iterations=0;
 while (tud_task_event_ready()) {
   assert(++iterations < 1000u); mt_runtime_result result=step();
   if (result == MT_RUNTIME_RECOVERY || result == MT_RUNTIME_TIME_FAULT || mt_usb_bridge_snapshot().pending_soft) break;
 }
}
static void quiescent(void) {
 assert(!runtime.adc.owns_peripherals && runtime.adc.state == MT_ADC_DMA_STOPPED);
 assert(!runtime.control.hardware_running && !runtime.control.ready);
 assert(!(*slot(T_CR1)&1u) && !(*slot(A_CR2)) && !(*slot(D_CCR)&1u));
}
static void fixture_option(unsigned configured, unsigned require_dtr) {
 if (tud_inited()) assert(tud_deinit(0));
 memset(&runtime,0,sizeof runtime); memset(&clock_source,0,sizeof clock_source); memset(&hw,0,sizeof hw);
 primask=nesting=pending_reset=recursive_calls=mounts=dcd_calls=0;
 injected_actor=injected_current=0; nonce=11; sequence=2;
 test_reject_open=test_reject_xfer=-1;
 hw.time_step=1;
 *slot(R_CR)=(3u<<16)|(3u<<24);
 *slot(R_CFGR)=2u|(2u<<2)|(4u<<8)|(2u<<14)|(1u<<16)|(7u<<18);
 *slot(GPIO)=0x44444444u; *slot(R_AHB)=0x14u;
 *slot(R_APB1)=0x00800001u; *slot(R_APB2)=0x00004000u;
 mt_timebase_io clock_io={read32,write32,&hw};
 assert(mt_timebase_init(&clock_source,&clock_io,72000000u));
 mt_runtime_mmio io={read32,write32,&hw};
 assert(mt_runtime_init(&runtime,&clock_source,&io,8000000u,lock,unlock,123,require_dtr) == MT_RUNTIME_OK);
 assert(runtime.binding.driver == &runtime.adc && runtime.binding.control == &runtime.control);
 assert(runtime.binding.guarded);
 assert(runtime.binding.now_ms == mt_timebase_now_ms && runtime.binding.clock_context == &clock_source);
 assert(runtime.adc.io.user == &runtime && runtime.rx.control == &runtime.control && runtime.usb.rx == &runtime.rx);
 assert(mt_runtime_step(&runtime) == MT_RUNTIME_ARGUMENT);
 memset(fake_ep,0,sizeof fake_ep); fake_epoch=fake_invalid_submissions=fake_mounts=fake_umounts=fake_line_calls=0;
 fake_last_dtr=fake_last_rts=fake_reject_next_in=false; fake_on_in_submit=fake_on_out_submit=NULL; fake_clear_logs();
 const tusb_rhport_init_t init={.role=TUSB_ROLE_DEVICE,.speed=TUSB_SPEED_FULL};
 assert(tusb_rhport_init(0,&init));
 if (configured != 2u) {
   fake_bus_reset(false); assert(!mt_usb_bridge_snapshot().cleaned);
   assert(step() == MT_RUNTIME_IDLE);
   assert(runtime.epoch == 1 && mt_usb_bridge_snapshot().cleaned == 1 && mt_usb_bridge_snapshot().processed_reset == 1);
 }
 if (configured == 1u) {
   fake_configuration(1);
   assert(runtime.usb.state == MT_USB_OPEN && mt_usb_bridge_snapshot().configured == 1);
 }
 fake_clear_logs();
}
static void fixture(unsigned configured) { fixture_option(configured,0); }
static void drain(void) {
 for (unsigned i=0;i<500u;++i) {
   if (fake_ep[FAKE_CDC_IN].pending) fake_complete_in(FAKE_CDC_IN,false);
   mt_runtime_result result=step(); assert(result == MT_RUNTIME_OK);
   if (!runtime.control.tx.count && !fake_ep[FAKE_CDC_IN].pending && !tud_task_event_ready()) return;
 }
 assert(!"TX failed to drain in bounded test iterations");
}
static mt_runtime_result send_message(mt_wire_message *message, unsigned drain_reply) {
 uint8_t frame[MT_WIRE_FRAME_MAX]; size_t length;
 assert(mt_wire_encode(message,frame,sizeof frame,&length) == MT_WIRE_OK); assert(length <= 64u);
 fake_out(frame,length,false); mt_runtime_result result=step();
 if (drain_reply) drain();
 return result;
}
static mt_wire_message command(uint8_t type,uint16_t length) {
 mt_wire_message message={0}; message.type=type; message.session=nonce;
 message.sequence=sequence; message.request=sequence++; message.payload_length=length; return message;
}
static void negotiate(void) {
 mt_wire_message message={0}; message.type=MT_CMD_HELLO; message.sequence=message.request=1; message.payload_length=8;
 for (unsigned i=0;i<8u;++i) message.payload[i]=(uint8_t)(nonce>>(8u*i));
 assert(send_message(&message,1) == MT_RUNTIME_OK); assert(runtime.control.ready && runtime.control.session.active);
}
static void configure(void) {
 mt_wire_message message=command(MT_CMD_CONFIG,16);
 message.payload[0]=1; message.payload[2]=MT_ACQ_SINGLE; message.payload[7]=8;
 assert(send_message(&message,1) == MT_RUNTIME_OK); assert(runtime.control.link.config_id == 1);
}
static mt_runtime_result arm(void) {
 mt_wire_message message=command(start_operation,4); message.payload[0]=1; return send_message(&message,0);
}
static void running(void) {
 fixture(1); negotiate(); configure(); assert(arm() == MT_RUNTIME_OK);
 assert(runtime.control.hardware_running && runtime.adc.owns_peripherals && runtime.adc.state == MT_ADC_DMA_RUNNING);
 assert(hw.cen == 1 && hw.enables == 3); drain();
}
static void startup(void) {
 fixture(2); uint32_t before=clock_source.us;
 assert(step() == MT_RUNTIME_IDLE && clock_source.us > before); assert(!runtime.epoch && !runtime.control.ready && !dcd_calls);
 fake_bus_reset(false); assert(step() == MT_RUNTIME_IDLE); fake_configuration(1);
 assert(runtime.usb.state == MT_USB_OPEN && mounts == 1 && !runtime.control.ready);
 negotiate(); configure(); assert(arm() == MT_RUNTIME_OK); assert(hw.cen == 1 && runtime.binding.epoch_ms == 0);
}
static void arguments(void) {
 fixture(2); mt_runtime blank={0}; mt_runtime_mmio io={read32,write32,&hw};
 assert(mt_runtime_step(NULL) == MT_RUNTIME_ARGUMENT && mt_runtime_step(&blank) == MT_RUNTIME_ARGUMENT);
 assert(mt_runtime_init(&blank,&clock_source,&io,8000000,lock,unlock,0,0) == MT_RUNTIME_ARGUMENT); /* USB already live */
 assert(tud_deinit(0));
 assert(mt_runtime_init(&runtime,&clock_source,&io,8000000,lock,unlock,0,0) == MT_RUNTIME_ARGUMENT);
 assert(mt_runtime_init(&blank,&clock_source,&io,8000000,lock,unlock,0,2) == MT_RUNTIME_ARGUMENT);
 assert(mt_runtime_init(&blank,&clock_source,&io,8000000,NULL,unlock,0,0) == MT_RUNTIME_ARGUMENT);
 blank.mmio=io; assert(mt_runtime_init(&blank,&clock_source,&blank.mmio,8000000,lock,unlock,0,0) == MT_RUNTIME_OK);
 assert(blank.mmio.read32 == read32 && blank.mmio.context == &hw);
}
static void dma_block(void) {
 running();
 hw.counter=(hw.counter+1000u)&65535u; *slot(D_N)=200; assert(step() == MT_RUNTIME_OK);
 hw.counter=(hw.counter+1600u)&65535u; *slot(D_N)=128; *slot(D_ISR)=4;
 for (unsigned i=0;i<256u;++i) runtime.adc.dma_samples[i]=(uint16_t)(100u+i);
 assert(step() == MT_RUNTIME_OK); assert(runtime.adc.first_sample_index == 128 && !(*slot(D_ISR)&4u));
 assert(runtime.control.link.acquisition.have_previous && runtime.control.link.acquisition.previous_code == 227);
 assert(runtime.control.link.acquisition.last_sample_ms >= runtime.binding.epoch_ms+2u);
}
static void soft_loss(void) {
 running(); const uint8_t bytes[]={3,17}; fake_out(bytes,sizeof bytes,false); assert(step() == MT_RUNTIME_OK);
 assert(runtime.rx.stream.used == sizeof bytes); uint64_t old_session=runtime.control.tx.session;
 fake_signal(DCD_EVENT_SUSPEND,false); assert(step() == MT_RUNTIME_IDLE); quiescent();
 assert(runtime.usb.state == MT_USB_SOFT_PAUSED && runtime.rx.stream.used == 0 && runtime.usb.rx_flush_pending);
 assert(runtime.epoch == 1 && runtime.control.tx.session == old_session && runtime.soft_serial == 1);
 fake_signal(DCD_EVENT_RESUME,false); test_pump(); assert(runtime.usb.state == MT_USB_OPEN && !runtime.control.ready);
 ++nonce; sequence=2; negotiate(); configure(); assert(arm() == MT_RUNTIME_OK);
}
static void hard_loss(void) {
 running(); fake_bus_reset(false); assert(step() == MT_RUNTIME_IDLE); quiescent();
 assert(runtime.epoch == 2 && runtime.usb.state == MT_USB_QUARANTINED && runtime.usb.epoch == 2);
 assert(runtime.control.tx.session == 0 && runtime.control.tx.count == 0 && mt_usb_bridge_snapshot().cleaned == 2);
 fake_configuration(1); assert(runtime.usb.state == MT_USB_OPEN && !runtime.control.ready);
 ++nonce; sequence=2; negotiate(); configure(); assert(arm() == MT_RUNTIME_OK);
}
static void hard_stop_retry(void) {
 running(); hw.stop_stuck=1; fake_bus_reset(false); unsigned before=hw.stops;
 assert(step() == MT_RUNTIME_RECOVERY && hw.stops > before);
 assert(runtime.pending == 2 && runtime.control.hardware_running && runtime.adc.owns_peripherals);
 assert(!mt_usb_bridge_snapshot().cleaned && !mt_usb_bridge_snapshot().processed_reset && tud_task_event_ready());
 before=hw.stops; assert(step() == MT_RUNTIME_RECOVERY && hw.stops > before);
 assert(!mt_usb_bridge_snapshot().cleaned); hw.stop_stuck=0;
 assert(step() == MT_RUNTIME_IDLE); quiescent(); assert(mt_usb_bridge_snapshot().cleaned == 2 && mt_usb_bridge_snapshot().processed_reset == 2);
}
static void soft_stop_retry(void) {
 running(); hw.stop_stuck=1; fake_signal(DCD_EVENT_SUSPEND,false);
 assert(step() == MT_RUNTIME_RECOVERY && runtime.pending == 1 && runtime.control.hardware_running);
 unsigned before=hw.stops; assert(step() == MT_RUNTIME_RECOVERY && hw.stops > before);
 hw.stop_stuck=0; assert(step() == MT_RUNTIME_IDLE); quiescent(); assert(!runtime.pending);
 fake_signal(DCD_EVENT_RESUME,false); test_pump(); assert(runtime.usb.state == MT_USB_OPEN && !runtime.control.ready);
}
static void failed_initial_start(void) {
 fixture(1); negotiate(); configure(); hw.injection=DROP_CEN_STUCK;
 assert(arm() == MT_RUNTIME_RECOVERY); assert(hw.injected && !runtime.control.hardware_running && runtime.adc.owns_peripherals);
 assert(runtime.pending == 1 && runtime.adc.state == MT_ADC_DMA_FAULTED);
 unsigned before=hw.stops; assert(step() == MT_RUNTIME_RECOVERY && hw.stops > before && runtime.adc.owns_peripherals);
 fake_bus_reset(false); assert(step() == MT_RUNTIME_RECOVERY && runtime.pending == 2 && !mt_usb_bridge_snapshot().cleaned);
 hw.stop_stuck=0; assert(step() == MT_RUNTIME_IDLE); quiescent(); assert(mt_usb_bridge_snapshot().cleaned == 2);
}
static void calibration_reset(void) {
 fixture(1); negotiate(); configure(); hw.injection=RESET_CAL;
 assert(arm() == MT_RUNTIME_RECOVERY); assert(hw.injected && injected_actor == 1 && injected_current == 2);
 assert(!hw.cen && !hw.enables); quiescent();
 assert(step() == MT_RUNTIME_IDLE); assert(mt_usb_bridge_snapshot().processed_reset == 2);
 fake_configuration(1); assert(!runtime.control.ready); ++nonce; sequence=2; negotiate(); configure(); assert(arm() == MT_RUNTIME_OK);
}
static void calibration_suspend(void) {
 fixture(1); negotiate(); configure(); hw.injection=SUSPEND_CAL;
 assert(arm() == MT_RUNTIME_RECOVERY); assert(hw.injected && injected_actor == 1 && injected_current == 1);
 assert(!hw.cen && !hw.enables); quiescent(); assert(runtime.soft_serial == 1);
 fake_signal(DCD_EVENT_RESUME,false); test_pump(); assert(!runtime.control.ready);
}
static void commit_reset(void) {
 fixture(1); negotiate(); configure(); hw.injection=RESET_CEN;
 mt_runtime_result result=arm(); assert(result == MT_RUNTIME_IDLE || result == MT_RUNTIME_RECOVERY);
 assert(hw.injected && injected_actor == 1 && injected_current == 1 && mt_usb_bridge_snapshot().current == 2);
 assert(hw.cen == 1 && !pending_reset); quiescent(); assert(!runtime.control.ready);
 assert(step() == MT_RUNTIME_IDLE); assert(mt_usb_bridge_snapshot().processed_reset == 2);
}
static void terminal_clock(void) {
 running(); fake_bus_reset(false); hw.stop_stuck=1; *slot(C_PSC)=72;
 assert(step() == MT_RUNTIME_TIME_FAULT && runtime.clock_fault && runtime.adc.owns_peripherals);
 assert(!mt_usb_bridge_snapshot().cleaned && !mt_usb_bridge_snapshot().processed_reset);
 unsigned before=hw.stops; *slot(C_PSC)=71;
 assert(step() == MT_RUNTIME_TIME_FAULT && hw.stops > before); hw.stop_stuck=0;
 assert(step() == MT_RUNTIME_TIME_FAULT); quiescent(); assert(!mt_usb_bridge_snapshot().cleaned);
 size_t records=fake_record_count; assert(step() == MT_RUNTIME_TIME_FAULT && fake_record_count == records);
 assert(runtime.initialized && runtime.clock_fault && !mt_timebase_healthy(&clock_source));
}
static void calibration_clock(void) {
 fixture(1); negotiate(); configure(); hw.injection=CLOCK_CAL;
 assert(arm() == MT_RUNTIME_TIME_FAULT && hw.injected && !hw.cen && !hw.enables); quiescent();
 assert(step() == MT_RUNTIME_TIME_FAULT);
}
static void commit_clock(void) {
 fixture(1); negotiate(); configure(); hw.injection=CLOCK_CEN;
 assert(arm() == MT_RUNTIME_TIME_FAULT && hw.injected && hw.cen == 1); quiescent();
 assert(step() == MT_RUNTIME_TIME_FAULT);
}
static void recursion(void) {
 fixture(1); negotiate(); configure(); hw.injection=RECURSE_CAL;
 assert(arm() == MT_RUNTIME_OK && recursive_calls == 1 && runtime.control.hardware_running);
 assert(!runtime.in_step && !mt_usb_bridge_actor());
}
static void deconfigure(void) {
 running(); uint8_t setup[8]={0,TUSB_REQ_SET_CONFIGURATION,0,0,0,0,0,0};
 dcd_event_setup_received(0,setup,true); assert(step() == MT_RUNTIME_IDLE); quiescent();
 assert(runtime.faults == MT_USB_EPOCH_DECONFIGURED && runtime.epoch == 1 && runtime.usb.state == MT_USB_QUARANTINED);
 assert(step() == MT_RUNTIME_IDLE); fake_bus_reset(false); assert(step() == MT_RUNTIME_IDLE); fake_configuration(1);
 assert(runtime.usb.state == MT_USB_OPEN && runtime.epoch == 2 && !runtime.control.ready);
}
static void submission_failure(void) {
 fixture(1); fake_reject_next_in=true;
 mt_wire_message message={0}; message.type=MT_CMD_HELLO; message.sequence=message.request=1; message.payload_length=8; message.payload[0]=(uint8_t)nonce;
 assert(send_message(&message,0) == MT_RUNTIME_IDLE); quiescent();
 assert(runtime.faults == MT_USB_EPOCH_SUBMIT_FAILED && runtime.usb.state == MT_USB_QUARANTINED && !runtime.control.tx.session);
 assert(mt_usb_bridge_snapshot().io_failures == 1 && step() == MT_RUNTIME_IDLE);
}
static void dtr_gate(void) {
 fixture_option(1,1);
 mt_wire_message message={0}; message.type=MT_CMD_HELLO; message.sequence=message.request=1; message.payload_length=8; message.payload[0]=(uint8_t)nonce;
 assert(send_message(&message,0) == MT_RUNTIME_OK); assert(!runtime.control.ready);
 fake_line_state(1); test_pump(); assert(runtime.control.ready); drain();
}
static void dtr_low_running(void) {
 fixture_option(1,1); fake_line_state(1); negotiate(); configure(); assert(arm() == MT_RUNTIME_OK); drain();
 mt_usb_epoch before=mt_usb_bridge_snapshot(); unsigned stops=hw.stops;
 fake_line_state(0); assert(step() == MT_RUNTIME_OK);
 mt_usb_epoch after=mt_usb_bridge_snapshot();
 assert(before.current == after.current && before.soft_serial == after.soft_serial && !after.pending_soft);
 assert(runtime.control.hardware_running && runtime.adc.owns_peripherals && hw.stops == stops);
 mt_wire_message stop=command(MT_CMD_STOP,4); stop.payload[0]=(uint8_t)runtime.control.link.generation;
 size_t records=fake_record_count; assert(send_message(&stop,0) == MT_RUNTIME_OK);
 assert(runtime.control.hardware_running && fake_record_count == records);
 fake_line_state(1); test_pump(); drain(); assert(!runtime.control.hardware_running && !runtime.adc.owns_peripherals);
}
static void no_actor_start(void) {
 fixture(1); negotiate(); configure(); mt_wire_message message=command(start_operation,4); message.payload[0]=1;
 assert(!mt_usb_bridge_actor());
 assert(mt_control_receive(&runtime.control,&message,mt_timebase_now_ms(&clock_source)) == MT_CONTROL_RECOVERY);
 assert(!hw.cen && !hw.enables); quiescent();
}
static void cleanup_reset_race(void) {
 running(); hw.injection=RESET_STOP; fake_bus_reset(false);
 assert(step() == MT_RUNTIME_IDLE && hw.injected);
 assert(runtime.epoch == 3 && mt_usb_bridge_snapshot().current == 3 && mt_usb_bridge_snapshot().cleaned == 3);
 assert(!mt_usb_bridge_snapshot().processed_reset); quiescent();
 assert(step() == MT_RUNTIME_IDLE && mt_usb_bridge_snapshot().processed_reset == 3);
 fake_configuration(1); assert(runtime.usb.state == MT_USB_OPEN && !runtime.control.ready);
}
static void queue_loss(void) {
 running(); for (unsigned i=0;i<16u;++i) fake_signal(DCD_EVENT_RESUME,false);
 fake_bus_reset(false); assert(mt_usb_bridge_snapshot().queue_losses == 1);
 assert(step() == MT_RUNTIME_IDLE); quiescent();
 assert(runtime.faults == MT_USB_EPOCH_QUEUE_LOSS && !mt_usb_bridge_snapshot().processed_reset);
 test_pump(); assert(step() == MT_RUNTIME_IDLE); fake_bus_reset(false);
 assert(step() == MT_RUNTIME_IDLE && mt_usb_bridge_snapshot().processed_reset == 3);
 fake_configuration(1); assert(runtime.usb.state == MT_USB_OPEN && !runtime.control.ready);
}
static uint16_t get16(const uint8_t *bytes) {
 return (uint16_t)((uint16_t)bytes[0] | ((uint16_t)bytes[1]<<8));
}
static uint32_t get32(const uint8_t *bytes) {
 uint32_t value=0; for (unsigned i=0;i<4u;++i) value|=(uint32_t)bytes[i]<<(8u*i); return value;
}
static void put32(uint8_t *bytes,uint32_t value) {
 for (unsigned i=0;i<4u;++i) bytes[i]=(uint8_t)(value>>(8u*i));
}
static void next_half(unsigned crossing) {
 bool second=runtime.adc.next_half;
 unsigned offset=second?128u:0u;
 uint32_t previous_index=runtime.adc.first_sample_index;
 hw.counter=(hw.counter+1200u)&65535u; *slot(D_N)=second?64u:192u;
 assert(step() == MT_RUNTIME_OK && runtime.adc.first_sample_index == previous_index);
 hw.counter=(hw.counter+1360u)&65535u; *slot(D_N)=second?256u:128u; *slot(D_ISR)=second?2u:4u;
 for (unsigned i=0;i<128u;++i) runtime.adc.dma_samples[offset+i]=(crossing && previous_index == 0u && i == 0u)?100u:3000u;
 assert(step() == MT_RUNTIME_OK && runtime.adc.first_sample_index == previous_index+128u);
}
static void short_suspend_resume(void) {
 running(); const uint8_t bytes[]={3,17}; fake_out(bytes,sizeof bytes,false); assert(step() == MT_RUNTIME_OK);
 unsigned stops=hw.stops; uint32_t epoch=runtime.epoch;
 fake_signal(DCD_EVENT_SUSPEND,false); fake_signal(DCD_EVENT_RESUME,false);
 assert(!mt_usb_bridge_snapshot().pending_soft && mt_usb_bridge_snapshot().soft_serial == 1u);
 assert(step() == MT_RUNTIME_OK); quiescent();
 assert(runtime.epoch == epoch && runtime.soft_serial == 1u && hw.stops > stops);
 assert(!runtime.rx.stream.used && !runtime.control.session.active && runtime.usb.state == MT_USB_OPEN);
 test_pump(); assert(!runtime.control.ready); ++nonce; sequence=2; negotiate(); configure(); assert(arm() == MT_RUNTIME_OK);
}
static void backpressure_adc(void) {
 fixture(1); negotiate(); configure(); fake_clear_logs(); assert(arm() == MT_RUNTIME_OK);
 assert(fake_ep[FAKE_CDC_IN].pending);
 mt_wire_message caps=command(MT_CMD_GET_CAPS,0); assert(send_message(&caps,0) == MT_RUNTIME_OK);
 assert(step() == MT_RUNTIME_OK);
 assert(runtime.control.tx.offset > 0 && runtime.control.tx.offset < runtime.control.tx.length);
 size_t accepted=runtime.control.tx.offset; size_t submissions=fake_record_count;
 assert(step() == MT_RUNTIME_OK && runtime.control.tx.offset == accepted);
 next_half(0); next_half(0);
 assert(runtime.adc.first_sample_index == 256u && runtime.control.hardware_running && runtime.control.ready);
 assert(fake_record_count == submissions && fake_host_size == 0 && runtime.control.tx.offset == accepted);
 assert(runtime.control.link.acquisition.previous_code == 3000u);
 drain();
 size_t begin=0; unsigned messages=0; uint32_t expected_sequence=3;
 for (size_t i=0;i<fake_host_size;++i) if (!fake_host[i]) {
   mt_wire_message decoded; assert(mt_wire_decode(fake_host+begin,i-begin,&decoded) == MT_WIRE_OK);
   assert(decoded.session == nonce && decoded.sequence == expected_sequence++);
   assert(decoded.type == (messages==0u?132u:messages==1u?134u:messages==2u?130u:134u));
   ++messages; begin=i+1u;
 }
 assert(begin == fake_host_size && messages == (start_operation == MT_CMD_RECORD_NOW ? 4u : 3u));
}
static void complete_capture_release_stop(void) {
 running(); fake_clear_logs();
 for (unsigned half=0;half<(start_operation == MT_CMD_RECORD_NOW ? 16u : 17u);++half) {
   next_half(1);
   if (fake_ep[FAKE_CDC_IN].pending) fake_complete_in(FAKE_CDC_IN,false);
   assert(step() == MT_RUNTIME_OK);
 }
 assert(!runtime.control.hardware_running && !runtime.adc.owns_peripherals);
 assert(runtime.control.link.acquisition.state == MT_ACQ_FROZEN && runtime.control.link.active_window == 1u);
 drain(); assert(runtime.control.link.phase == MT_LINK_WAIT_RELEASE && runtime.control.link.have_tombstone);
 size_t begin=0; unsigned begins=0,ends=0,samples=0; uint32_t generation=runtime.control.link.generation;
 uint32_t crc=0,expected_sequence=5; uint8_t sample_bytes[4096];
 for (size_t i=0;i<fake_host_size;++i) if (!fake_host[i]) {
   mt_wire_message decoded; assert(mt_wire_decode(fake_host+begin,i-begin,&decoded) == MT_WIRE_OK);
   assert(decoded.session == nonce && decoded.sequence == expected_sequence++);
   if (decoded.type == 144u) {
     assert(!begins && !ends && !samples && decoded.payload_length == 40u);
     assert(get32(decoded.payload) == generation && get32(decoded.payload+4) == 1u);
     assert(get16(decoded.payload+14) == 2048u);
     assert(get16(decoded.payload+16) == 0u && get16(decoded.payload+26) == 0u);
     assert(get16(decoded.payload+18) == (start_operation == MT_CMD_RECORD_NOW ? 0xffffu : 0u));
     assert(decoded.payload[20] == (start_operation == MT_CMD_RECORD_NOW ? 3u : 0u));
     ++begins;
   } else if (decoded.type == 145u) {
     assert(begins == 1u && !ends && get16(decoded.payload+8) == samples);
     unsigned count=get16(decoded.payload+10); assert(count && count<=250u && samples+count<=2048u);
     assert(decoded.payload_length == 12u+count*2u);
     for (unsigned sample=0;sample<count;++sample) {
       assert(get16(decoded.payload+12u+2u*sample) ==
         (start_operation == MT_CMD_RECORD_NOW && samples+sample == 0u ? 100u : 3000u));
       sample_bytes[2u*(samples+sample)]=decoded.payload[12u+2u*sample];
       sample_bytes[2u*(samples+sample)+1u]=decoded.payload[13u+2u*sample];
     }
     samples+=count;
   } else if (decoded.type == 146u) {
     assert(begins == 1u && !ends && samples == 2048u && decoded.payload_length == 16u);
     assert(get32(decoded.payload) == generation && get32(decoded.payload+4) == 1u);
     assert(get16(decoded.payload+8) == 2048u); crc=get32(decoded.payload+12); ++ends;
   } else assert(decoded.type == 134u);
   begin=i+1u;
 }
 assert(begin == fake_host_size && begins == 1u && ends == 1u && samples == 2048u);
 assert(crc == mt_wire_crc32(sample_bytes,sizeof sample_bytes) && crc == runtime.control.link.tomb_crc);
 fake_clear_logs();
 mt_wire_message release=command(MT_CMD_RELEASE_WINDOW,12);
 put32(release.payload,generation);put32(release.payload+4,1);put32(release.payload+8,crc);
 assert(send_message(&release,0) == MT_RUNTIME_OK);
 assert(!runtime.control.hardware_running && !runtime.adc.owns_peripherals && hw.cen == 1u);
 assert(runtime.control.link.acquisition.state == MT_ACQ_STOPPED &&
        runtime.control.link.phase == MT_LINK_IDLE && !runtime.control.link.armed);
 drain(); unsigned before=hw.cen;
 release=command(MT_CMD_RELEASE_WINDOW,12); put32(release.payload,generation);put32(release.payload+4,1);put32(release.payload+8,crc);
 assert(send_message(&release,1) == MT_RUNTIME_OK && hw.cen == before); /* Tombstone replay never re-arms. */
 mt_wire_message stop=command(MT_CMD_STOP,4);put32(stop.payload,generation);
 assert(send_message(&stop,0) == MT_RUNTIME_OK && !runtime.control.hardware_running && !runtime.adc.owns_peripherals);
 drain(); assert(runtime.control.link.phase == MT_LINK_IDLE && !runtime.control.link.armed);
 begin=0;unsigned release_acks=0,stop_acks=0;
 for (size_t i=0;i<fake_host_size;++i) if (!fake_host[i]) {
   mt_wire_message decoded;assert(mt_wire_decode(fake_host+begin,i-begin,&decoded) == MT_WIRE_OK);
   assert(decoded.sequence == expected_sequence++ && decoded.session == nonce);
   if (decoded.type == 135u) { assert(!stop_acks);++release_acks; }
   else if (decoded.type == 133u) { assert(release_acks == 2u && !stop_acks);++stop_acks; }
   else assert(decoded.type == 134u);
   begin=i+1u;
 }
 assert(begin == fake_host_size && release_acks == 2u && stop_acks == 1u);
}
static void active_window_stop(void) {
 running(); next_half(1); drain();
 assert(runtime.control.link.active_window == 1u && runtime.control.hardware_running);
 fake_clear_logs();
 mt_wire_message stop=command(MT_CMD_STOP,4);
 put32(stop.payload,runtime.control.link.generation);
 assert(send_message(&stop,0) == MT_RUNTIME_OK);
 assert(!runtime.control.hardware_running && !runtime.adc.owns_peripherals);
 drain();
 assert(runtime.control.link.dropped_windows == 1u && !runtime.control.link.armed);
 size_t begin=0; unsigned aborts=0,acks=0;
 for (size_t i=0;i<fake_host_size;++i) if (!fake_host[i]) {
   mt_wire_message decoded;
   assert(mt_wire_decode(fake_host+begin,i-begin,&decoded) == MT_WIRE_OK);
   assert(decoded.type != 144u && decoded.type != 145u && decoded.type != 146u);
   if (decoded.type == 147u) { assert(!acks); ++aborts; }
   else if (decoded.type == 133u) { assert(aborts == 1u); ++acks; }
   else assert(decoded.type == 134u);
   begin=i+1u;
 }
 assert(begin == fake_host_size && aborts == 1u && acks == 1u);
}
static void one_event_budget(void) {
 fixture(1); fake_signal(DCD_EVENT_RESUME,false); fake_signal(DCD_EVENT_RESUME,false);
 assert(step() == MT_RUNTIME_OK && tud_task_event_ready());
 assert(step() == MT_RUNTIME_OK && !tud_task_event_ready());
}
static void service_deadline(void) {
 running(); fake_clear_logs(); hw.counter=(hw.counter+MT_ADC_DMA_BLOCK_US)&65535u;
 assert(step() == MT_RUNTIME_OK && runtime.control.ready && !runtime.control.hardware_running);
 assert(runtime.pending == 0u && runtime.control.session.active && !runtime.adc.owns_peripherals);
 assert(runtime.adc.state == MT_ADC_DMA_STOPPED && runtime.adc.fault == MT_ADC_DMA_FAULT_DEADLINE);
 assert(runtime.control.link.phase == MT_LINK_ACQ_ERROR && runtime.control.link.error_reason == 2u);
 assert(!runtime.control.link.armed && runtime.control.link.acquisition.state == MT_ACQ_STOPPED);
 assert(!runtime.control.link.active_window && !runtime.control.link.last_window && !runtime.control.link.dropped_windows);
 assert(hw.cen == 1u && !(*slot(T_CR1)&1u) && !*slot(A_CR2) && !(*slot(D_CCR)&1u));
 assert(!runtime.clock_fault);
 drain(); size_t begin=0; unsigned statuses=0;
 for (size_t i=0;i<fake_host_size;++i) if (!fake_host[i]) {
   mt_wire_message decoded;
   assert(mt_wire_decode(fake_host+begin,i-begin,&decoded) == MT_WIRE_OK);
   assert(decoded.type == 134u && decoded.payload_length == 24u && decoded.sequence == 5u);
   assert(decoded.session == nonce && get32(decoded.payload+4) == runtime.control.link.generation);
   assert(decoded.payload[0] == 5u && decoded.payload[1] == 2u);
   assert(get32(decoded.payload+8) == 0u && get32(decoded.payload+20) == 0u);
   ++statuses; begin=i+1u;
 }
 assert(begin == fake_host_size && statuses == 1u);
 size_t previous=fake_host_size;
 assert(step() == MT_RUNTIME_OK && fake_host_size == previous && hw.cen == 1u);
 assert(!runtime.control.hardware_running && !runtime.adc.owns_peripherals);
}
static void idle_clock_fault(void) {
 fixture(2); hw.counter=(hw.counter+MT_TIMEBASE_MAX_DELTA+1u)&65535u;
 assert(step() == MT_RUNTIME_TIME_FAULT && runtime.clock_fault && !mt_usb_bridge_snapshot().current); quiescent();
 assert(step() == MT_RUNTIME_TIME_FAULT);
}
typedef struct { const char *name; void (*run)(void); } test_case;
static const test_case cases[]={
 {"startup_reset_configuration_and_wire_arm",startup}, {"argument_and_mmio_descriptor_alias",arguments},
 {"one_synthetic_dma_half_through_real_acquisition",dma_block}, {"soft_loss_requires_fresh_hello",soft_loss},
 {"hard_loss_discards_tx_and_reopens_fresh_epoch",hard_loss}, {"hard_loss_retries_stop_before_reset_dispatch",hard_stop_retry},
 {"soft_loss_retries_stop",soft_stop_retry}, {"failed_initial_start_ownership_blocks_cleanup",failed_initial_start},
 {"reset_during_calibration_retains_old_actor",calibration_reset}, {"suspend_during_calibration_denies_commit",calibration_suspend},
 {"reset_pending_at_cen_processed_after_guard",commit_reset}, {"clock_fault_is_terminal_and_retries_stop",terminal_clock},
 {"clock_fault_during_calibration_denies_commit",calibration_clock}, {"clock_fault_at_cen_cleans_after_guard",commit_clock},
 {"recursive_step_rejected_preserves_actor",recursion}, {"same_epoch_deconfiguration_is_quarantined",deconfigure},
 {"actual_usb_submission_failure_is_quarantined",submission_failure}, {"dtr_gates_protocol_consumption",dtr_gate},
 {"dtr_low_retains_active_capture_and_epoch",dtr_low_running}, {"direct_api_start_without_actor_is_denied",no_actor_start},
 {"reset_during_cleanup_cannot_publish_stale_receipt",cleanup_reset_race}, {"queue_loss_requires_fresh_reset",queue_loss},
 {"suspend_resume_between_steps_still_recovers",short_suspend_resume},
 {"positive_prefix_backpressure_keeps_adc_service",backpressure_adc},
 {"complete_capture_crc_release_replay_and_stop_on_wire",complete_capture_release_stop},
 {"active_window_stop_aborts_once",active_window_stop},
 {"step_dispatches_at_most_one_usb_event",one_event_budget}, {"adc_service_deadline_reports_serviceable_error",service_deadline}, {"idle_clock_service_fault_is_terminal",idle_clock_fault}
};
int main(int argc,char **argv) {
 assert(argc == 2 || argc == 3);
 if (argc == 3) {
   assert(!strcmp(argv[2],"4") || !strcmp(argv[2],"8"));
   start_operation = !strcmp(argv[2],"8") ? MT_CMD_RECORD_NOW : MT_CMD_ARM;
 }
 if (!strcmp(argv[1],"--list")) { for (unsigned i=0;i<sizeof cases/sizeof cases[0];++i) puts(cases[i].name); return 0; }
 for (unsigned i=0;i<sizeof cases/sizeof cases[0];++i) if (!strcmp(argv[1],cases[i].name)) {
   cases[i].run(); assert(!primask && !nesting && !pending_reset && !runtime.in_step && !mt_usb_bridge_actor());
   assert(!fake_invalid_submissions);
   printf("{\"case\":\"%s\",\"pass\":true,\"layer\":\"native_real_modules_fake_mmio_dcd_irq\",\"adc_cen_writes\":%u,\"time_reads\":%u,\"usb_calls\":%u}\n",cases[i].name,hw.cen,hw.time_reads,dcd_calls);
   return 0;
 }
 return 2;
}
