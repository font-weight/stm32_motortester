/* Critic-A supplemental adapter-only call-order tests. All TinyUSB and
 * controller boundaries below are explicit stubs, not USB/firmware evidence. */
#include "mt_usb_cdc.h"
#include "tusb.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static mt_usb_cdc a;
static mt_control_rx rx;
static mt_control c;
static int stable_value, ready_value, connected_value;
static unsigned reads,writes,flushes,rxflushes,clears,polls,rxpolls,recoveries,txinits;
static unsigned feed_bytes,dispatches,drop_after_dispatch,recover_after_dispatch;
static unsigned lose_on_read,lose_on_rxflush,lose_on_write;
static unsigned sequence[16],sequence_n;
static uint8_t input[64],fed[128];
static size_t input_n;
static uint32_t write_accept;
static mt_control_result poll_result;
static unsigned long cases,checks;
#define CHECK(x) do { ++checks; assert(x); } while(0)
static int stable(void *p) { CHECK(p==&a); return stable_value; }
bool tud_cdc_n_ready(uint8_t itf) { CHECK(itf==0); return ready_value!=0; }
bool tud_cdc_n_connected(uint8_t itf) { CHECK(itf==0); return connected_value!=0; }
uint32_t tud_cdc_n_write(uint8_t itf,const void *b,uint32_t n) {
 CHECK(itf==0 && b && n<=64); ++writes; if(lose_on_write) stable_value=0;
 CHECK(write_accept<=n); return write_accept;
}
uint32_t tud_cdc_n_read(uint8_t itf,void *b,uint32_t n) {
 CHECK(itf==0 && b && n==64); ++reads; CHECK(input_n<=n); memcpy(b,input,input_n);
 if(lose_on_read) stable_value=0;
 return (uint32_t)input_n;
}
uint32_t tud_cdc_n_write_flush(uint8_t itf) { CHECK(itf==0); ++flushes; return 61; }
void tud_cdc_n_read_flush(uint8_t itf) {
 CHECK(itf==0 && stable_value && ready_value && a.state!=MT_USB_OPEN);
 ++rxflushes; input_n=0; if(lose_on_rxflush) stable_value=0;
}
bool tud_cdc_n_write_clear(uint8_t itf) {
 CHECK(itf==0 && a.state==MT_USB_QUARANTINED); ++clears; sequence[sequence_n++]=3; return true;
}
mt_control_result mt_control_rx_poll(mt_control_rx *r,uint32_t now) {
 CHECK(r==&rx && now==123); ++rxpolls; return MT_CONTROL_OK;
}
mt_control_result mt_control_rx_feed(mt_control_rx *r,const uint8_t *b,size_t n,uint32_t now,size_t *used) {
 size_t i; CHECK(r==&rx && b && n==1 && now==123 && used); *used=0;
 for(i=0;i<n;++i) {
  CHECK(feed_bytes<sizeof fed && stable_value); fed[feed_bytes++]=b[i]; ++*used;
  if(b[i]==0) {
   ++dispatches;
   if(dispatches==drop_after_dispatch) stable_value=0;
   if(dispatches==recover_after_dispatch) return MT_CONTROL_RECOVERY;
  }
 }
 return MT_CONTROL_OK;
}
mt_control_result mt_control_poll(mt_control *control,uint32_t now,mt_tx_write_fn write,void *context) {
 CHECK(control==&c && now==123 && write==mt_usb_cdc_write && context==&a); ++polls; return poll_result;
}
mt_control_result mt_control_rx_transport_lost(mt_control_rx *r) {
 CHECK(r==&rx && a.state!=MT_USB_OPEN); ++recoveries; sequence[sequence_n++]=1;
 CHECK(c.hardware_running==1); c.ready=0; return MT_CONTROL_RECOVERY;
}
void mt_tx_init(mt_tx *tx) { CHECK(tx==&c.tx); ++txinits; sequence[sequence_n++]=2; }
static void setup(unsigned require_dtr) {
 memset(&a,0,sizeof a); memset(&rx,0,sizeof rx); memset(&c,0,sizeof c);
 reads=writes=flushes=rxflushes=clears=polls=rxpolls=recoveries=txinits=0;
 feed_bytes=dispatches=drop_after_dispatch=recover_after_dispatch=0;
 lose_on_read=lose_on_rxflush=lose_on_write=sequence_n=0; input_n=0; write_accept=0;
 stable_value=ready_value=connected_value=1; poll_result=MT_CONTROL_OK;
 rx.control=&c; c.hardware_running=1;
 CHECK(mt_usb_cdc_init(&a,&rx,stable,&a,require_dtr)==MT_CONTROL_OK);
 CHECK(a.state==MT_USB_CLOSED && !reads && !writes && !flushes);
 CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_OK);
}
static void prefix_counts(void) {
 uint8_t b[64]={0}; unsigned n,dtr;
 for(dtr=0;dtr<2;++dtr) for(n=0;n<=64;++n) {
  setup(dtr); write_accept=n; lose_on_write=1;
  CHECK(mt_usb_cdc_write(&a,b,64)==(int)n); CHECK(writes==1 && !flushes && !stable_value); ++cases;
 }
 setup(0); CHECK(mt_usb_cdc_write(&a,NULL,0)==0); CHECK(mt_usb_cdc_write(&a,NULL,1)==-1);
 CHECK(mt_usb_cdc_write(&a,b,65)==-1); CHECK(!writes); ++cases;
}
static void gates_and_pause(void) {
 uint8_t b[64]={0}; unsigned ready,gate,dtr;
 for(ready=0;ready<2;++ready) for(gate=0;gate<2;++gate) for(dtr=0;dtr<2;++dtr) {
  setup(1); ready_value=(int)ready; stable_value=(int)gate; connected_value=(int)dtr; write_accept=1;
  CHECK(mt_usb_cdc_write(&a,b,64)==(ready&&gate&&dtr?1:0));
  CHECK(writes==(ready&&gate&&dtr?1u:0u));
  CHECK(mt_usb_cdc_soft_pause(&a)==MT_CONTROL_RECOVERY);
  CHECK(a.state==MT_USB_SOFT_PAUSED && a.rx_flush_pending && recoveries==1);
  CHECK(!rxflushes && !clears && !txinits && c.hardware_running==1);
  CHECK(mt_usb_cdc_write(&a,b,64)==0); ++cases;
 }
 setup(0); connected_value=0; write_accept=1; CHECK(mt_usb_cdc_write(&a,b,64)==1); ++cases;
 setup(0); CHECK(mt_usb_cdc_soft_pause(&a)==MT_CONTROL_RECOVERY);
 stable_value=0; CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_IGNORED); CHECK(!rxflushes);
 stable_value=1; ready_value=0; CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_IGNORED); CHECK(!rxflushes);
 ready_value=1; lose_on_rxflush=1; CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_IGNORED);
 CHECK(rxflushes==1 && a.state==MT_USB_SOFT_PAUSED && a.rx_flush_pending);
 stable_value=1; lose_on_rxflush=0; CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_OK);
 CHECK(rxflushes==2 && !a.rx_flush_pending); ++cases;
}
static void receive_order(void) {
 setup(0); input[0]=11;input[1]=0;input[2]=22;input[3]=0; input_n=4;drop_after_dispatch=1;
 CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_IGNORED);
 CHECK(reads==1 && feed_bytes==2 && dispatches==1 && a.offset==2 && a.length==4 && !polls && !flushes); ++cases;
 setup(0); input[0]=11;input[1]=0;input[2]=22;input[3]=0; input_n=4;recover_after_dispatch=1;
 CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_RECOVERY);
 CHECK(a.offset==2 && a.length==4 && feed_bytes==2 && dispatches==1 && !polls && !flushes);
 CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_OK);
 CHECK(reads==1 && a.offset==4 && feed_bytes==4 && dispatches==2 && polls==1 && flushes==1); ++cases;
 setup(0); input[0]=11;input[1]=0;input_n=2;lose_on_read=1;
 CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_IGNORED);CHECK(a.length==2 && !feed_bytes && !polls && !flushes); ++cases;
 setup(0); memset(input,77,64);input_n=64;CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_OK);
 CHECK(feed_bytes==64 && reads==1 && polls==1 && flushes==1);++cases;
 setup(0); stable_value=0;CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_OK);
 CHECK(!reads && !feed_bytes && polls==1 && !flushes && rxpolls==1);++cases;
}
static void hard_loss_order(void) {
 setup(0); CHECK(mt_usb_cdc_soft_pause(&a)==MT_CONTROL_RECOVERY); sequence_n=0;
 CHECK(mt_usb_cdc_hard_loss(&a,2)==MT_CONTROL_RECOVERY);
 CHECK(a.state==MT_USB_QUARANTINED && a.epoch==2 && !a.rx_flush_pending);
 CHECK(sequence_n==3 && sequence[0]==1 && sequence[1]==2 && sequence[2]==3);
 CHECK(c.hardware_running==1 && !rxflushes && txinits==1 && clears==1);
 CHECK(mt_usb_cdc_step(&a,123)==MT_CONTROL_IGNORED);CHECK(!reads && !polls && !flushes);
 CHECK(mt_usb_cdc_open_epoch(&a,1)==MT_CONTROL_ARGUMENT);CHECK(a.state==MT_USB_QUARANTINED);
 CHECK(mt_usb_cdc_hard_loss(&a,2)==MT_CONTROL_RECOVERY);
 CHECK(txinits==2 && clears==2 && a.epoch==2);
 CHECK(mt_usb_cdc_soft_pause(&a)==MT_CONTROL_IGNORED);CHECK(!rxflushes && txinits==2);
 CHECK(mt_usb_cdc_hard_loss(&a,3)==MT_CONTROL_RECOVERY);CHECK(txinits==3 && clears==3 && a.epoch==3);
 CHECK(mt_usb_cdc_open_epoch(&a,3)==MT_CONTROL_OK);CHECK(!rxflushes);
 CHECK(mt_usb_cdc_hard_loss(&a,3)==MT_CONTROL_ARGUMENT);CHECK(a.state==MT_USB_OPEN && txinits==3);++cases;
}
int main(void) {
 prefix_counts();gates_and_pause();receive_order();hard_loss_order();
 printf("Critic-A stub boundary tests: %lu cases / %lu checks passed (not real TinyUSB integration)\n",cases,checks);
 return 0;
}
