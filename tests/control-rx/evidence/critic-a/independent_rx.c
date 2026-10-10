/* Critic-A wrapper oracle: actual stream/wire, independently faked controller. */
#include "firmware/app/mt_control_rx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static mt_control control;
static mt_control_rx rx;
static mt_wire_message expected;
static unsigned calls,recoveries,scenarios;
static mt_control_result callback_result;
mt_control_result mt_control_receive(mt_control *c,const mt_wire_message *m,uint32_t now) {
 (void)now;assert(c==&control);assert(m->type==expected.type && m->session==expected.session && m->sequence==expected.sequence && m->request==expected.request && m->payload_length==expected.payload_length);assert(memcmp(m->payload,expected.payload,m->payload_length)==0);++calls;return callback_result;
}
mt_control_result mt_control_recover(mt_control *c) {assert(c==&control);++recoveries;return MT_CONTROL_RECOVERY;}
static void init(void){assert(mt_control_rx_init(&rx,&control)==MT_CONTROL_OK);calls=0;callback_result=MT_CONTROL_OK;}
static size_t make(uint8_t *out,unsigned length){
 memset(&expected,0,sizeof expected);expected.type=127;expected.session=UINT64_C(0x123456789abcdef);expected.sequence=5;expected.request=9;expected.payload_length=(uint16_t)length;
 for(unsigned i=0;i<length;++i)expected.payload[i]=(uint8_t)(i*83u+length);
 size_t n=0;assert(mt_wire_encode(&expected,out,MT_WIRE_FRAME_MAX,&n)==MT_WIRE_OK);return n;
}
static void ok_bytes(const uint8_t *p,size_t n,uint32_t now,size_t chunk){
 while(n){size_t take=n<chunk?n:chunk,used=99;assert(mt_control_rx_feed(&rx,p,take,now,&used)==MT_CONTROL_OK);assert(used==take);p+=take;n-=take;}
}
int main(void){
 uint8_t frame[MT_WIRE_FRAME_MAX],dual[MT_WIRE_FRAME_MAX*2u];
 for(unsigned length=0;length<=512u;++length){size_t n=make(frame,length);
  for(size_t split=0;split<=n;++split){init();ok_bytes(frame,split,7,64);if(split<n)assert(calls==0);ok_bytes(frame+split,n-split,7,64);assert(calls==1 && rx.rejected_frames==0);++scenarios;}}
 size_t n=make(frame,512);
 for(size_t chunk=1;chunk<=64;++chunk){init();ok_bytes(frame,n,UINT32_MAX-5u,chunk);assert(calls==1);++scenarios;}
 n=make(frame,0);assert(n*2u<=64u);memcpy(dual,frame,n);memcpy(dual+n,frame,n);
 init();size_t used;assert(mt_control_rx_feed(&rx,dual,n*2u,0,&used)==MT_CONTROL_OK && used==n*2u && calls==2);++scenarios;
 for(unsigned kind=0;kind<2;++kind){init();callback_result=kind?MT_CONTROL_ARGUMENT:MT_CONTROL_RECOVERY;assert(mt_control_rx_feed(&rx,dual,n*2u,0,&used)==callback_result);assert(used==n && calls==1);callback_result=MT_CONTROL_OK;assert(mt_control_rx_feed(&rx,dual+used,n,0,&used)==MT_CONTROL_OK && used==n && calls==2);++scenarios;}
 init();uint8_t malformed[]={2,1,0};ok_bytes(malformed,sizeof malformed,0,64);assert(rx.rejected_frames==1 && calls==0);rx.rejected_frames=UINT32_MAX;ok_bytes(malformed,sizeof malformed,0,64);assert(rx.rejected_frames==UINT32_MAX);++scenarios;
 init();uint8_t ones[64];memset(ones,1,sizeof ones);for(unsigned i=0;i<9;++i)ok_bytes(ones,64,0,64);assert(rx.overflows==1 && rx.stream.discarding && calls==0);uint8_t zero=0;ok_bytes(&zero,1,0,1);ok_bytes(frame,n,0,64);assert(calls==1);++scenarios;
 init();ok_bytes(frame,1,UINT32_MAX-499u,1);assert(mt_control_rx_poll(&rx,499)==MT_CONTROL_OK && rx.timeouts==0);assert(mt_control_rx_poll(&rx,500)==MT_CONTROL_OK && rx.timeouts==1);assert(mt_control_rx_poll(&rx,1000)==MT_CONTROL_OK && rx.timeouts==1);ok_bytes(&zero,1,1000,1);ok_bytes(frame,n,1000,64);assert(calls==1);++scenarios;
 init();ok_bytes(frame,1,0,1);rx.timeouts=UINT32_MAX;assert(mt_control_rx_feed(&rx,NULL,0,1000,&used)==MT_CONTROL_OK && used==0 && rx.timeouts==UINT32_MAX);++scenarios;
 init();ok_bytes(frame,1,0,1);rx.rejected_frames=UINT32_MAX;unsigned before=recoveries;assert(mt_control_rx_transport_lost(&rx)==MT_CONTROL_RECOVERY);assert(recoveries==before+1 && rx.stream.used==0 && rx.rejected_frames==UINT32_MAX);ok_bytes(frame,n,0,64);assert(calls==1);++scenarios;
 init();mt_control_rx old=rx;used=77;assert(mt_control_rx_feed(&rx,frame,65,0,&used)==MT_CONTROL_ARGUMENT && used==0);assert(memcmp(&old,&rx,sizeof rx)==0);assert(mt_control_rx_feed(&rx,NULL,1,0,&used)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_feed(&rx,frame,1,0,NULL)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_feed(NULL,frame,1,0,&used)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_init(NULL,&control)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_init(&rx,NULL)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_poll(NULL,0)==MT_CONTROL_ARGUMENT);assert(mt_control_rx_transport_lost(NULL)==MT_CONTROL_ARGUMENT);++scenarios;
 printf("Independent RX wrapper: %u scenarios passed; every split of every payload length0..512, all chunk sizes1..64, coalescing/recovery prefix, rejection/deadline/overflow/transport reset\n",scenarios);return 0;
}
