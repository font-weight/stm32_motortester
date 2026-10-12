#include "../../firmware/app/mt_capture_link.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint8_t bytes[16384]; size_t used,step; int error; } sink;
static int output(void *ctx,const uint8_t *p,size_t n) {
 sink *s=ctx; if(s->error) return -1; if(n>s->step)n=s->step;
 assert(n<=64u && s->used+n<=sizeof s->bytes);
 memcpy(s->bytes+s->used,p,n); s->used+=n; return (int)n;
}
static uint16_t u16(const uint8_t*p){return (uint16_t)((uint16_t)p[0]|(uint16_t)((uint16_t)p[1]<<8));}
static uint32_t u32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static void drain(mt_capture_link*l,sink*s,uint32_t now){
 unsigned budget=12000; do {mt_link_result r=mt_capture_link_poll(l,now,output,s);
 assert(r==MT_LINK_OK||r==MT_LINK_TX_BLOCKED); assert(budget--);
 }while(l->tx->count || (l->phase>=MT_LINK_BEGIN&&l->phase<=MT_LINK_END));
}
static size_t decode(sink*s,mt_wire_message*out,size_t max){size_t start=0,n=0,i;
 for(i=0;i<s->used;++i)if(!s->bytes[i]){assert(n<max); assert(mt_wire_decode(s->bytes+start,i-start,&out[n])==MT_WIRE_OK); ++n;start=i+1;}
 assert(start==s->used);return n;
}
static void setup(mt_capture_link*l,mt_tx*t,sink*s,uint8_t mode,uint16_t pre){
 mt_wire_message hello={0}; mt_acq_config_t cfg={0};
 memset(s,0,sizeof *s);s->step=64;mt_tx_init(t);hello.type=129;hello.request=1;hello.payload_length=16;
 assert(mt_tx_hello(t,123,&hello,0)==MT_TX_OK);mt_capture_link_init(l,t);drain(l,s,0);s->used=0;
 cfg.profile_id=1;cfg.pretrigger_samples=pre;cfg.level_code=2048;cfg.mode=mode;cfg.auto_timeout_ms=mode==0u?100u:0u;
 assert(mt_capture_link_configure(l,&cfg,5)==MT_LINK_OK);
 assert(mt_capture_link_arm(l,5,7,0)==MT_LINK_OK);
}
static void capture(mt_capture_link*l,uint16_t expected[2048],uint32_t now,unsigned seed){
 unsigned i,pre=l->acquisition.config.pretrigger_samples;
 for(i=0;i<pre+1u;++i)assert(mt_capture_link_sample(l,100,now)==MT_LINK_OK);
 assert(mt_capture_link_sample(l,3000,now)==MT_LINK_TRIGGERED);
 for(i=0;i<pre;++i) { expected[i]=100; }
 expected[pre]=3000;
 for(i=pre+1u;i<2048u;++i){mt_link_result r;expected[i]=(uint16_t)((i+seed)%4096u);r=mt_capture_link_sample(l,expected[i],now);assert(r==(i==2047u?MT_LINK_WINDOW_READY:MT_LINK_OK));}
}
static void twenty_windows(void){mt_capture_link l;mt_tx t;sink s;uint16_t expected[2048];uint8_t raw[4096];mt_wire_message frames[16];unsigned w,i;setup(&l,&t,&s,1,512);
 for(w=1;w<=20u;++w){size_t n,j,offset=0;uint32_t crc;capture(&l,expected,w,w*37u);s.step=(w%3u==0u)?1u:((w%3u==1u)?7u:64u);drain(&l,&s,w);
 assert(l.phase==MT_LINK_WAIT_RELEASE);n=decode(&s,frames,16);assert(n==11u);assert(frames[0].type==144u&&frames[10].type==146u);assert(u32(frames[0].payload+4)==w);assert(u16(frames[0].payload+18)==512u);
 for(j=1;j<10;++j){uint16_t count=u16(frames[j].payload+10);assert(frames[j].type==145u&&u16(frames[j].payload+8)==offset);for(i=0;i<count;++i){assert(u16(frames[j].payload+12+2u*i)==expected[offset+i]);}memcpy(raw+2u*offset,frames[j].payload+12,2u*count);offset+=count;}
 assert(offset==2048u);crc=mt_wire_crc32(raw,sizeof raw);assert(u32(frames[10].payload+12)==crc);assert(mt_capture_link_sample(&l,0,w)==MT_LINK_BUSY);
 assert(mt_capture_link_release(&l,7,w,crc^1u,10u+w,w)==MT_LINK_BAD_RELEASE);assert(l.phase==MT_LINK_WAIT_RELEASE);
 assert(mt_capture_link_release(&l,7,w,crc,40u+w,w)==MT_LINK_OK);drain(&l,&s,w);s.used=0;assert(l.armed&&l.active_window==0u);
 assert(mt_capture_link_release(&l,7,w,crc,70u+w,w)==MT_LINK_OK);drain(&l,&s,w);s.used=0;assert(l.acquisition.state==MT_ACQ_PREPARING);
 }
}
static void stop_cases(void){mt_capture_link l;mt_tx t;sink s;uint16_t expected[2048];mt_wire_message frames[16];size_t n,i;unsigned which;
 for(which=0;which<5u;++which){setup(&l,&t,&s,1,0);
 if(which==1u){assert(mt_capture_link_sample(&l,100,0)==MT_LINK_OK);assert(mt_capture_link_sample(&l,3000,0)==MT_LINK_TRIGGERED);}
 if(which>=2u){capture(&l,expected,0,0);if(which==2u){s.step=0;assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_TX_BLOCKED);}else if(which==3u){s.step=1;while(!l.have_tombstone)assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_OK);}else drain(&l,&s,0);}
 assert(mt_capture_link_stop(&l,7,3,0)==MT_LINK_OK);assert(mt_capture_link_stop(&l,7,4,0)==MT_LINK_BUSY);s.step=64;drain(&l,&s,0);n=decode(&s,frames,16);assert(frames[n-2].type==133u&&frames[n-1].type==134u);assert(!l.armed&&l.phase==MT_LINK_IDLE);for(i=0;i<n;++i)if(frames[i].type==147u)assert(which==1u||which==2u);
 if(which==1u||which==2u) { assert(frames[0].type==147u); }
 if(which==0u)assert(n==2u);
 s.used=0;assert(mt_capture_link_stop(&l,7,5,0)==MT_LINK_OK);drain(&l,&s,0);assert(decode(&s,frames,16)==2u);
 if(which>=3u){s.used=0;assert(mt_capture_link_release(&l,7,1,l.tomb_crc,6,0)==MT_LINK_OK);drain(&l,&s,0);assert(!l.armed&&l.acquisition.state==MT_ACQ_STOPPED);}
 }
}
static void release_deadline(void) {
 mt_capture_link l; mt_tx t; sink s;
 uint16_t expected[2048], retained[2048], code = 1234;
 mt_wire_message frames[2]; uint32_t crc;
 setup(&l,&t,&s,1,0); capture(&l,expected,0,0); drain(&l,&s,0);
 crc=l.window_crc; memcpy(retained,l.acquisition.samples,sizeof retained);
 s.used=0;
 assert(mt_capture_link_poll(&l,4999,output,&s)==MT_LINK_OK);
 assert(l.phase==MT_LINK_WAIT_RELEASE && l.armed && l.active_window==1u);
 assert(mt_capture_link_poll(&l,5000,output,&s)==MT_LINK_OK);
 assert(l.phase==MT_LINK_RELEASE_EXPIRED && !l.armed);
 assert(l.session==123u && t.session==123u && l.active_window==1u);
 assert(l.have_tombstone && l.tomb_generation==7u && l.tomb_window==1u);
 assert(l.tomb_crc==crc && l.dropped_windows==0u);
 assert(l.acquisition.state==MT_ACQ_STOPPED);
 assert(memcmp(retained,l.acquisition.samples,sizeof retained)==0);
 assert(mt_acq_read(&l.acquisition,0,&code)==MT_ACQ_NOT_READY && code==1234u);
 assert(mt_capture_link_sample(&l,100,5000)==MT_LINK_BUSY);
 assert(mt_capture_link_configure(&l,&l.acquisition.config,6)==MT_LINK_BUSY);
 assert(mt_capture_link_arm(&l,5,8,5000)==MT_LINK_BUSY);
 /* Link expiry preserves usable TX; the controller owns ERROR status. */
 assert(s.used==0u && mt_capture_link_expire_release(&l,5001)==0);
 assert(mt_capture_link_release(&l,7,1,crc,3,5001)==MT_LINK_OK);
 drain(&l,&s,5001);
 assert(decode(&s,frames,2)==1u && frames[0].type==135u);
 assert(frames[0].request==3u && u32(frames[0].payload)==7u);
 assert(u32(frames[0].payload+4)==1u);
 assert(l.phase==MT_LINK_RELEASE_EXPIRED && !l.armed && l.active_window==0u);
 assert(memcmp(retained,l.acquisition.samples,sizeof retained)==0);
 s.used=0;
 assert(mt_capture_link_release(&l,7,1,crc,4,5002)==MT_LINK_OK);
 drain(&l,&s,5002);
 assert(decode(&s,frames,2)==1u && frames[0].type==135u);
 assert(frames[0].request==4u && !l.armed && l.phase==MT_LINK_RELEASE_EXPIRED);
 assert(l.acquisition.state==MT_ACQ_STOPPED);
}
static void timeouts_single_auto(void){mt_capture_link l;mt_tx t;sink s;uint16_t expected[2048];unsigned i;
 setup(&l,&t,&s,2,1024);capture(&l,expected,0,3);drain(&l,&s,0);assert(mt_capture_link_release(&l,7,1,l.window_crc,3,1)==MT_LINK_OK);assert(!l.armed);drain(&l,&s,1);
 setup(&l,&t,&s,1,0);capture(&l,expected,0,0);s.step=0;assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_TX_BLOCKED);assert(mt_capture_link_poll(&l,1000,output,&s)==MT_LINK_RECOVERY);
 setup(&l,&t,&s,0,0);assert(mt_capture_link_sample(&l,100,0)==MT_LINK_OK);assert(mt_capture_link_sample(&l,100,100)==MT_LINK_TRIGGERED);for(i=1;i<2048u;++i)assert(mt_capture_link_sample(&l,100,100)==(i==2047u?MT_LINK_WINDOW_READY:MT_LINK_OK));drain(&l,&s,100);assert(l.acquisition.window.trigger_kind==MT_ACQ_TRIGGER_AUTO_TIMEOUT);assert(l.acquisition.window.trigger_index==UINT16_MAX);
}
int main(void){twenty_windows();stop_cases();release_deadline();timeouts_single_auto();puts("capture link: 20 full-array windows, Stop commitment, tombstone release, Single/Auto and timeout suites passed");return 0;}
