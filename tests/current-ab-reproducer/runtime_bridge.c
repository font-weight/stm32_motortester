/* One bounded full-runtime -> frozen-host composition. Test-only I/O models. */
#define main historical_runtime_main
#include "supporting_runtime.c"
#undef main
#include <inttypes.h>

static unsigned first_fault, first_pending, recovery_steps;
static int fault_step = -1;
static unsigned nibble(char c) {
 if (c >= '0' && c <= '9') return (unsigned)(c-'0');
 if (c >= 'a' && c <= 'f') return (unsigned)(c-'a')+10u;
 assert(!"invalid hex"); return 0;
}
static size_t decode_hex(const char *hex, uint8_t *bytes) {
 size_t n=strlen(hex); assert(n && !(n%2u) && n/2u<=64u);
 for (size_t i=0;i<n/2u;++i) bytes[i]=(uint8_t)((nibble(hex[2u*i])<<4)|nibble(hex[2u*i+1u]));
 return n/2u;
}
static void finish(void) {
 for (unsigned i=0;i<500u;++i) {
  if(fake_ep[FAKE_CDC_IN].pending)fake_complete_in(FAKE_CDC_IN,false);
  mt_runtime_result r=step();
  if(r==MT_RUNTIME_RECOVERY || r==MT_RUNTIME_TIME_FAULT)++recovery_steps;
  if(!runtime.control.tx.count && !fake_ep[FAKE_CDC_IN].pending && !tud_task_event_ready())return;
 }
 assert(!"bounded drain exceeded");
}
static void crossing_half(void) {
 bool second=runtime.adc.next_half;unsigned offset=second?128u:0u;
 uint32_t before=runtime.adc.first_sample_index;
 hw.counter=(hw.counter+1200u)&65535u;*slot(D_N)=second?64u:192u;
 assert(step()==MT_RUNTIME_OK && runtime.adc.first_sample_index==before);
 hw.counter=(hw.counter+1360u)&65535u;*slot(D_N)=second?256u:128u;*slot(D_ISR)=second?2u:4u;
 for(unsigned i=0;i<128u;++i)runtime.adc.dma_samples[offset+i]=i?3000u:100u;
 assert(step()==MT_RUNTIME_OK && runtime.adc.first_sample_index==before+128u);
 finish();
 assert(runtime.control.link.active_window==1u && runtime.control.hardware_running);
}
int main(void) {
 char line[4096], op[20], hex[2048];
 while(fgets(line,sizeof line,stdin)) {
  size_t before=fake_host_size;assert(sscanf(line,"%19s",op)==1);
  if(!strcmp(op,"INIT")) { fixture(1);before=0; }
  else if(!strcmp(op,"RX") || !strcmp(op,"STOP_TE")) {
   uint8_t bytes[64];assert(sscanf(line,"%19s %2047s",op,hex)==2);size_t n=decode_hex(hex,bytes);
   assert(!fake_ep[FAKE_CDC_IN].pending && !runtime.control.tx.count);
   fake_out(bytes,n,false);
   if(!strcmp(op,"STOP_TE")) {
    assert(runtime.control.hardware_running && runtime.control.link.active_window==1u);
    assert(tud_task_event_ready());*slot(D_ISR)|=8u;
    fault_step=(int)step();first_fault=(unsigned)runtime.adc.fault;first_pending=runtime.pending;
   } else { (void)step(); }
   finish();
  } else if(!strcmp(op,"PARTIAL")) { crossing_half(); }
  else {
   assert(!strcmp(op,"POLL"));
   for(unsigned i=0;i<20u;++i)finish();
  }
  mt_usb_epoch e=mt_usb_bridge_snapshot();
  printf("{\"now_ms\":%"PRIu32",\"result\":%d,\"fault_step\":%d,\"first_fault\":%u,\"transfer_selected\":%u,\"first_pending\":%u,\"recovery_steps\":%u,\"ready\":%u,\"session_active\":%u,\"session\":%"PRIu64",\"generation\":%"PRIu32",\"window\":%"PRIu32",\"last_window\":%"PRIu32",\"dropped\":%"PRIu32",\"hardware_running\":%u,\"owned\":%u,\"starts\":%u,\"stops\":%u,\"adc_state\":%u,\"link_phase\":%u,\"armed\":%u,\"pending\":%u,\"epoch\":%"PRIu32",\"soft_serial\":%"PRIu32",\"transport_faults\":%"PRIu32",\"clock_healthy\":%u,\"in_pending\":%u,\"tx_count\":%zu,\"wire\":\"",
   mt_timebase_now_ms(&clock_source),(int)last_result,fault_step,first_fault,(unsigned)(first_fault==(unsigned)MT_ADC_DMA_FAULT_TRANSFER),first_pending,recovery_steps,
   runtime.control.ready,runtime.control.session.active,runtime.control.session.nonce,
   runtime.control.link.generation,runtime.control.link.active_window,runtime.control.link.last_window,runtime.control.link.dropped_windows,
   runtime.control.hardware_running,(unsigned)runtime.adc.owns_peripherals,hw.cen,hw.stops,(unsigned)runtime.adc.state,(unsigned)runtime.control.link.phase,runtime.control.link.armed,runtime.pending,
   e.current,e.soft_serial,e.faults,(unsigned)mt_timebase_healthy(&clock_source),(unsigned)fake_ep[FAKE_CDC_IN].pending,runtime.control.tx.count);
  for(size_t i=before;i<fake_host_size;++i)printf("%02x",fake_host[i]);
  puts("\"}");fflush(stdout);
 }
 return 0;
}
