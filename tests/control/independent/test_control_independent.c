#include <inttypes.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "firmware/app/mt_control.h"

/* Independent tests: no implementation source is edited. The hardware and
 * irrevocable FIFO acceptance boundary are replaced with deterministic fakes. */
#define NONCE UINT64_C(0x706f736974697665)
#define MAX_MESSAGES 1024u
#define MAX_BYTES 262144u
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"FAIL %s:%d: %s\n", current, __LINE__, #x); longjmp(failed,1); } } while(0)
typedef struct { char kind; unsigned order; unsigned ok; } event;
typedef struct {
    mt_control c;
    unsigned stop_calls, start_calls, profile_calls;
    unsigned stop_ok, start_ok, profile_ok, fake_running;
    unsigned accepted_chunk, writes, blocked, io_fail;
    unsigned order, event_count;
    event events[8192];
    uint8_t bytes[MAX_BYTES], partial[MT_WIRE_FRAME_MAX];
    size_t byte_count, partial_count;
    mt_wire_message messages[MAX_MESSAGES];
    unsigned message_order[MAX_MESSAGES], message_count;
    uint32_t host_seq, host_request, now;
    uint64_t nonce;
} fixture;
static fixture f;
static jmp_buf failed;
static unsigned checks;
static const char *current;
static uint16_t u16(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1]<<8)); }
static uint32_t u32(const uint8_t *p) { return p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
static void p16(uint8_t *p,uint16_t x) { p[0]=(uint8_t)x;p[1]=(uint8_t)(x>>8); }
static void p32(uint8_t *p,uint32_t x) { unsigned i;for(i=0;i<4;i++)p[i]=(uint8_t)(x>>(8*i)); }
static void log_event(fixture *a,char kind,unsigned ok) { event *e;CHECK(a->event_count<8192);e=&a->events[a->event_count++];e->kind=kind;e->ok=ok;e->order=++a->order; }
static int hw_stop(void *v) {fixture *a=v;++a->stop_calls;log_event(a,'T',a->stop_ok);if(a->stop_ok)a->fake_running=0;return (int)a->stop_ok;}
static int hw_start(void *v,uint32_t now) {fixture *a=v;(void)now;++a->start_calls;log_event(a,'S',a->start_ok);/* A failed start can leave hardware partly active. */a->fake_running=1;return (int)a->start_ok;}
static int hw_profile(void *v) {fixture *a=v;++a->profile_calls;log_event(a,'P',a->profile_ok);return (int)a->profile_ok;}
static int sink(void *v,const uint8_t *b,size_t n) {
    fixture *a=v;size_t i;CHECK(n<=64);++a->writes;if(a->io_fail)return -1;if(a->blocked)return 0;
    if(n>a->accepted_chunk) { n=a->accepted_chunk; }
    CHECK(a->byte_count+n<=MAX_BYTES);memcpy(a->bytes+a->byte_count,b,n);a->byte_count+=n;
    log_event(a,'W',1);
    for(i=0;i<n;i++) { if(b[i]) {CHECK(a->partial_count<sizeof a->partial);a->partial[a->partial_count++]=b[i];} else {CHECK(a->partial_count>0);CHECK(a->message_count<MAX_MESSAGES);CHECK(mt_wire_decode(a->partial,a->partial_count,&a->messages[a->message_count])==MT_WIRE_OK);a->message_order[a->message_count++]=a->order;a->partial_count=0;} }
    return (int)n;
}
static void init(void) {mt_control_io io;memset(&f,0,sizeof f);f.stop_ok=f.start_ok=f.profile_ok=1;f.accepted_chunk=64;f.now=1;f.nonce=NONCE;f.host_seq=f.host_request=2;io.profile_ready=hw_profile;io.start=hw_start;io.stop=hw_stop;io.context=&f;io.firmware_build=0x12345678;CHECK(mt_control_init(&f.c,&io)==MT_CONTROL_OK);CHECK(f.event_count==0);}
static mt_control_result receive(mt_wire_message *m) {uint8_t frame[MT_WIRE_FRAME_MAX];size_t n=0;mt_wire_message decoded;CHECK(mt_wire_encode(m,frame,sizeof frame,&n)==MT_WIRE_OK);CHECK(n>1&&frame[n-1]==0);CHECK(mt_wire_decode(frame,n-1,&decoded)==MT_WIRE_OK);return mt_control_receive(&f.c,&decoded,f.now);}
static mt_control_result hello(uint64_t nonce) {mt_wire_message m;unsigned i;memset(&m,0,sizeof m);m.type=1;m.sequence=m.request=1;m.payload_length=8;for(i=0;i<8;i++)m.payload[i]=(uint8_t)(nonce>>(8*i));return receive(&m);}
static mt_wire_message command(uint8_t type,uint16_t len) {mt_wire_message m;memset(&m,0,sizeof m);m.type=type;m.payload_length=len;m.session=f.nonce;m.sequence=f.host_seq++;m.request=f.host_request++;return m;}
static mt_control_result poll(void) {size_t before=f.byte_count;unsigned calls=f.writes;mt_control_result r=mt_control_poll(&f.c,f.now++,sink,&f);CHECK(f.writes-calls<=1);CHECK(f.byte_count-before<=64);CHECK(f.c.state_count<=MT_CONTROL_STATUS_MAX);CHECK(f.c.tx.count<=MT_TX_CAPACITY);return r;}
static void drain(void) {unsigned i=0;while(f.c.ready&&(f.c.tx.count||f.c.state_count|| (f.c.link.phase>=MT_LINK_BEGIN&&f.c.link.phase<=MT_LINK_END_QUEUED))) {CHECK(i++<10000);CHECK(poll()==MT_CONTROL_OK);}CHECK(f.partial_count==0);}
static void boot(void) {init();CHECK(hello(f.nonce)==MT_CONTROL_OK);CHECK(f.stop_calls==1&&f.profile_calls==1&&f.start_calls==0);drain();CHECK(f.message_count==1&&f.messages[0].type==129);}
static mt_wire_message config_message(unsigned mode,unsigned edge,unsigned pre) {mt_wire_message m=command(3,16);p16(m.payload,1);m.payload[2]=(uint8_t)mode;m.payload[4]=(uint8_t)edge;p16(m.payload+6,2048);p16(m.payload+8,(uint16_t)pre);if(mode==0)p32(m.payload+12,100);return m;}
static void configure(unsigned mode,unsigned edge,unsigned pre) {mt_wire_message m=config_message(mode,edge,pre);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.messages[f.message_count-1].type==131);}
static void arm(void) {mt_wire_message m=command(4,4);p32(m.payload,f.c.link.config_id);CHECK(receive(&m)==MT_CONTROL_OK);}
static unsigned types(uint8_t t) {unsigned i,n=0;for(i=0;i<f.message_count;i++)n+=(f.messages[i].type==t);return n;}
static const mt_wire_message *reply(uint32_t r) {unsigned i;for(i=0;i<f.message_count;i++)if(f.messages[i].request==r)return &f.messages[i];return NULL;}
static void expect_error(mt_wire_message *m,unsigned code) {unsigned before=f.start_calls;CHECK(receive(m)==MT_CONTROL_OK);drain();CHECK(reply(m->request));CHECK(reply(m->request)->type==255);CHECK(reply(m->request)->payload[0]==m->type&&reply(m->request)->payload[1]==code);CHECK(f.start_calls==before);}
static void sample(uint16_t v) {CHECK(mt_control_sample(&f.c,v,f.now)==MT_CONTROL_OK);CHECK(f.c.state_count<=MT_CONTROL_STATUS_MAX);}
static void capture(unsigned seed) {unsigned i;unsigned pre=f.c.link.acquisition.config.pretrigger_samples;unsigned falling=f.c.link.acquisition.config.edge==1;uint16_t below=falling?3000:1000,above=falling?1000:3000;
    for(i=0;i<(pre?pre:1);i++)sample(below);
    sample(above);CHECK(f.c.link.acquisition.state==MT_ACQ_CAPTURING);
    for(i=pre+1;i<2048;i++)sample((uint16_t)((i*17u+seed*31u)&4095u));
    CHECK(!f.fake_running&&!f.c.hardware_running);CHECK(f.c.link.phase==MT_LINK_BEGIN);drain();CHECK(f.c.link.phase==MT_LINK_WAIT_RELEASE);
}
static mt_wire_message release_message(void) {mt_wire_message m=command(7,12);p32(m.payload,f.c.link.generation);p32(m.payload+4,f.c.link.active_window);p32(m.payload+8,f.c.link.window_crc);return m;}
static void check_recovery_fresh_nonce(unsigned previous_bytes) {mt_wire_message m;unsigned starts=f.start_calls,stops=f.stop_calls;CHECK(!f.c.ready&&!f.c.session.active&&!f.fake_running);CHECK(poll()==MT_CONTROL_IGNORED);CHECK(f.byte_count==previous_bytes);m=command(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_IGNORED);CHECK(hello(f.nonce)==MT_CONTROL_IGNORED);CHECK(f.start_calls==starts&&f.stop_calls==stops);CHECK(f.byte_count==previous_bytes);f.nonce++;f.host_seq=f.host_request=2;CHECK(hello(f.nonce)==MT_CONTROL_OK);drain();CHECK(f.messages[f.message_count-1].type==129);CHECK(f.messages[f.message_count-1].session==f.nonce);CHECK(f.c.link.config_id==0&&f.c.link.generation==0&&f.c.link.last_window==0);}

static void test_caps(void) {mt_wire_message m;const mt_wire_message *a;boot();a=&f.messages[0];CHECK(a->payload_length==16&&a->session==NONCE&&a->sequence==1&&a->request==1);CHECK(a->payload[0]==0&&a->payload[1]==1&&a->payload[2]==1&&a->payload[3]==12);CHECK(u16(a->payload+4)==512&&u16(a->payload+6)==2048&&u32(a->payload+8)==0x12345678&&u32(a->payload+12)==61);m=command(2,0);CHECK(receive(&m)==MT_CONTROL_OK);drain();a=reply(m.request);CHECK(a&&a->type==130&&a->payload_length==30);CHECK(a->payload[0]==1&&a->payload[1]==0&&u16(a->payload+2)==1);CHECK(u32(a->payload+4)==72000000&&u16(a->payload+8)==71&&u16(a->payload+10)==19);CHECK(u32(a->payload+12)==12000000&&u16(a->payload+16)==143&&u16(a->payload+18)==2048);CHECK(a->payload[20]==3&&a->payload[21]==7&&a->payload[22]==1&&a->payload[23]==3);CHECK(u16(a->payload+24)==0&&u16(a->payload+26)==512&&u16(a->payload+28)==1024);}
static void test_caps_cartesian(void) {unsigned mode,edge,pre;for(mode=0;mode<3;mode++)for(edge=0;edge<2;edge++)for(pre=0;pre<3;pre++){boot();configure(mode,edge,pre*512);arm();drain();CHECK(f.start_calls==1&&f.fake_running);capture(mode+edge+pre);CHECK(types(144)==1&&types(146)==1);}}
static void test_config_echo_and_identity(void) {
    unsigned i;
    boot();
    for(i=1;i<=3;i++) {
        mt_wire_message m=config_message(i-1,(i-1)&1,(i-1)*512);
        const mt_wire_message *r;
        CHECK(receive(&m)==MT_CONTROL_OK);drain();r=reply(m.request);
        CHECK(r&&r->type==131&&r->payload_length==20&&u32(r->payload)==i);
        CHECK(memcmp(r->payload+4,m.payload,16)==0&&f.c.link.config_id==i);
    }
    arm();drain();CHECK(f.start_calls==1&&f.c.link.config_id==3);
}
static void test_auto_timeout_and_rearm(void) {
    mt_wire_message m;const mt_wire_message *begin=NULL;unsigned i;
    boot();configure(0,1,512);arm();drain();
    for(i=0;i<512;i++)sample(1000);
    f.now+=100;sample(1000);
    for(i=513;i<2048;i++)sample(1000);
    drain();CHECK(f.c.link.phase==MT_LINK_WAIT_RELEASE);
    for(i=0;i<f.message_count;i++)if(f.messages[i].type==144)begin=&f.messages[i];
    CHECK(begin&&begin->payload[20]==2&&u16(begin->payload+18)==UINT16_MAX);
    CHECK(u16(begin->payload+26)==1&&u32(begin->payload+36)==0);
    m=release_message();CHECK(receive(&m)==MT_CONTROL_OK);drain();
    CHECK(f.fake_running&&f.start_calls==2&&f.c.link.acquisition.state==MT_ACQ_PREPARING);
}
static void test_rejected_config(void) {mt_wire_message m;unsigned i;boot();configure(1,0,512);for(i=0;i<10;i++){mt_acq_config_t before=f.c.link.acquisition.config;uint32_t id=f.c.link.config_id;m=config_message(1,0,512);switch(i){case 0:p16(m.payload,2);break;case 1:m.payload[2]=3;break;case 2:m.payload[3]=1;break;case 3:m.payload[4]=2;break;case 4:p16(m.payload+6,0);break;case 5:p16(m.payload+6,4095);break;case 6:p16(m.payload+8,1);break;case 7:p32(m.payload+12,100);break;case 8:m.payload[2]=0;p32(m.payload+12,99);break;default:m.payload[2]=0;p32(m.payload+12,60001);break;}expect_error(&m,1);CHECK(f.c.link.config_id==id&&memcmp(&before,&f.c.link.acquisition.config,sizeof before)==0);}CHECK(types(131)==1&&f.start_calls==0);}
static void test_invalid_payload(void) {mt_wire_message m;boot();m=config_message(1,0,0);m.payload[5]=1;expect_error(&m,6);m=command(4,3);expect_error(&m,6);m=command(5,3);expect_error(&m,6);m=command(200,0);expect_error(&m,5);CHECK(types(131)==0&&types(132)==0&&types(133)==0&&f.stop_calls==1);}
static void test_rejected_arm(void) {mt_wire_message m;boot();m=command(4,4);expect_error(&m,3);configure(1,0,0);m=command(4,4);p32(m.payload,2);expect_error(&m,3);arm();drain();m=command(4,4);p32(m.payload,1);expect_error(&m,2);m=config_message(1,1,512);expect_error(&m,2);CHECK(types(132)==1&&types(131)==1&&f.start_calls==1);}
static void test_stale_stop(void) {mt_wire_message m;unsigned stops;uint32_t generation;boot();configure(1,0,0);arm();drain();stops=f.stop_calls;generation=f.c.link.generation;m=command(5,4);p32(m.payload,generation-1);expect_error(&m,4);CHECK(f.stop_calls==stops&&f.fake_running&&f.c.link.armed&&f.c.link.generation==generation);CHECK(types(133)==0);}
static void test_stale_stop_with_pending_states(void) {
    mt_wire_message m;unsigned i,stops;uint32_t generation;
    boot();configure(1,0,512);arm();
    for(i=0;i<512;i++) { sample(1000); }
    sample(3000);
    CHECK(f.c.state_count==3&&f.c.tx.count==1);
    stops=f.stop_calls;generation=f.c.link.generation;
    m=command(5,4);p32(m.payload,generation-1);
    CHECK(receive(&m)==MT_CONTROL_OK);
    CHECK(f.stop_calls==stops&&f.fake_running&&f.c.ready);
    CHECK(f.c.link.generation==generation&&f.c.link.armed);
    drain();CHECK(reply(m.request)&&reply(m.request)->type==255);
    CHECK(reply(m.request)->payload[1]==4&&types(133)==0);
    CHECK(f.stop_calls==stops&&f.fake_running);
}
static void test_stop_order(void) {mt_wire_message a,m;unsigned ack_index=0,i,stop_order;boot();configure(1,0,0);a=command(4,4);p32(a.payload,1);CHECK(receive(&a)==MT_CONTROL_OK);sample(1000);sample(3000);m=command(5,4);p32(m.payload,a.request);CHECK(receive(&m)==MT_CONTROL_OK);CHECK(!f.fake_running);stop_order=f.events[f.event_count-1].order;CHECK(f.events[f.event_count-1].kind=='T');drain();for(i=0;i<f.message_count;i++){if(f.messages[i].type==133)ack_index=i;if(f.messages[i].type==133)CHECK(f.message_order[i]>stop_order);}CHECK(ack_index>=2);CHECK(f.messages[ack_index-2].type==132&&f.messages[ack_index-1].type==147);CHECK(f.messages[ack_index+1].type==134&&f.messages[ack_index+1].payload[0]==0);CHECK(types(146)==0&&types(147)==1);CHECK(f.c.link.phase==MT_LINK_IDLE&&f.c.link.dropped_windows==1);m=command(5,4);p32(m.payload,a.request);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(types(133)==2&&types(147)==1);}
static void test_initial_stop(void) {mt_wire_message m;boot();m=command(5,4);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(types(133)==1&&types(147)==0&&f.c.link.generation==0);}
static void test_duplicates(void) {mt_wire_message m;unsigned starts,stops;size_t bytes;boot();m=config_message(1,0,0);CHECK(receive(&m)==MT_CONTROL_OK);drain();bytes=f.byte_count;CHECK(receive(&m)==MT_CONTROL_IGNORED);drain();CHECK(f.byte_count==bytes&&f.c.link.config_id==1);m=command(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_OK);drain();bytes=f.byte_count;starts=f.start_calls;CHECK(receive(&m)==MT_CONTROL_IGNORED);drain();CHECK(f.byte_count==bytes&&f.start_calls==starts);m=command(5,4);p32(m.payload,f.c.link.generation);CHECK(receive(&m)==MT_CONTROL_OK);drain();bytes=f.byte_count;stops=f.stop_calls;CHECK(receive(&m)==MT_CONTROL_IGNORED);drain();CHECK(f.byte_count==bytes&&f.stop_calls==stops);}
static void test_start_failure(void) {unsigned bytes;boot();configure(1,0,0);bytes=(unsigned)f.byte_count;f.start_ok=0;{mt_wire_message m=command(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_RECOVERY);}CHECK(f.start_calls==1&&f.stop_calls==2&&types(132)==0);f.start_ok=1;check_recovery_fresh_nonce(bytes);CHECK(types(132)==0);}
static void test_stop_failure(void) {unsigned bytes;mt_wire_message m;boot();configure(1,0,0);arm();drain();bytes=(unsigned)f.byte_count;f.stop_ok=0;m=command(5,4);p32(m.payload,f.c.link.generation);CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(f.stop_calls==3&&!f.c.ready&&f.fake_running);CHECK(poll()==MT_CONTROL_IGNORED&&f.byte_count==bytes&&types(133)==0);f.stop_ok=1;CHECK(mt_control_recover(&f.c)==MT_CONTROL_RECOVERY);check_recovery_fresh_nonce(bytes);CHECK(types(133)==0);}
static void test_profile_fail_hello(void) {init();f.profile_ok=0;CHECK(hello(f.nonce)==MT_CONTROL_RECOVERY);CHECK(f.stop_calls==2&&f.start_calls==0);f.profile_ok=1;check_recovery_fresh_nonce(0);CHECK(types(129)==1);}
static void test_profile_fail_arm(void) {unsigned bytes;mt_wire_message m;boot();configure(1,0,0);bytes=(unsigned)f.byte_count;f.profile_ok=0;m=command(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(f.start_calls==0&&types(132)==0);f.profile_ok=1;check_recovery_fresh_nonce(bytes);CHECK(types(132)==0);}
static void test_hello_stop_failure(void) {init();f.stop_ok=0;CHECK(hello(f.nonce)==MT_CONTROL_RECOVERY);CHECK(f.stop_calls==2&&f.profile_calls==0&&f.start_calls==0&&!f.c.ready);f.stop_ok=1;check_recovery_fresh_nonce(0);}
static void test_release_start_failure(void) {unsigned bytes;mt_wire_message m;boot();configure(1,0,0);arm();drain();capture(2);bytes=(unsigned)f.byte_count;f.start_ok=0;m=release_message();CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(types(135)==0&&f.start_calls==2);f.start_ok=1;check_recovery_fresh_nonce(bytes);CHECK(types(135)==0);}
static void test_release_profile_failure(void) {unsigned bytes;mt_wire_message m;boot();configure(1,0,0);arm();drain();capture(2);bytes=(unsigned)f.byte_count;f.profile_ok=0;m=release_message();CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(types(135)==0&&f.start_calls==1);f.profile_ok=1;check_recovery_fresh_nonce(bytes);}
static void test_capture_stop_failure(void) {unsigned i,bytes;boot();configure(1,0,0);arm();drain();bytes=(unsigned)f.byte_count;sample(1000);sample(3000);for(i=1;i<2047;i++)sample(2500);f.stop_ok=0;CHECK(mt_control_sample(&f.c,2500,f.now)==MT_CONTROL_RECOVERY);CHECK(types(144)==0&&types(146)==0);f.stop_ok=1;CHECK(mt_control_recover(&f.c)==MT_CONTROL_RECOVERY);check_recovery_fresh_nonce(bytes);}
static void test_old_partial_hello(void) {mt_wire_message m;unsigned old_count;boot();m=command(2,0);CHECK(receive(&m)==MT_CONTROL_OK);f.accepted_chunk=7;CHECK(poll()==MT_CONTROL_OK);CHECK(f.partial_count==7);old_count=f.message_count;f.nonce++;f.host_seq=f.host_request=2;CHECK(hello(f.nonce)==MT_CONTROL_OK);drain();CHECK(f.message_count==old_count+2);CHECK(f.messages[old_count].type==130&&f.messages[old_count].session==NONCE&&f.messages[old_count].sequence==2);CHECK(f.messages[old_count+1].type==129&&f.messages[old_count+1].session==f.nonce&&f.messages[old_count+1].sequence==1);}
static void test_release_semantics(void) {mt_wire_message m,late;unsigned starts;boot();configure(1,0,0);arm();drain();capture(1);m=release_message();late=m;late.payload[8]^=1;expect_error(&late,7);/* consume a new sequence after malformed release */m.sequence=f.host_seq++;m.request=f.host_request++;starts=f.start_calls;CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.start_calls==starts+1&&f.fake_running);CHECK(receive(&m)==MT_CONTROL_IGNORED);drain();CHECK(f.start_calls==starts+1);late=m;late.sequence=f.host_seq++;late.request=f.host_request++;CHECK(receive(&late)==MT_CONTROL_OK);drain();CHECK(f.start_calls==starts+1&&types(135)==2);m=command(5,4);p32(m.payload,f.c.link.generation);CHECK(receive(&m)==MT_CONTROL_OK);drain();late.sequence=f.host_seq++;late.request=f.host_request++;CHECK(receive(&late)==MT_CONTROL_OK);drain();CHECK(f.start_calls==starts+1&&!f.fake_running&&types(135)==3);}
static void test_old_release_during_next_transfer(void) {
    mt_wire_message old;unsigned i,starts,profiles;
    boot();configure(1,0,0);arm();drain();capture(0);
    old=release_message();CHECK(receive(&old)==MT_CONTROL_OK);drain();
    starts=f.start_calls;profiles=f.profile_calls;
    sample(1000);sample(3000);for(i=1;i<2048;i++)sample(2500);
    CHECK(f.c.link.phase==MT_LINK_BEGIN&&f.c.link.active_window==2);
    CHECK(!f.fake_running&&!f.c.hardware_running&&f.c.link.armed);
    old.sequence=f.host_seq++;old.request=f.host_request++;
    CHECK(receive(&old)==MT_CONTROL_OK);
    fprintf(stderr,"old RELEASE during window2 transfer: starts before=%u after=%u; phase=%u; hardware=%u\n",starts,f.start_calls,(unsigned)f.c.link.phase,f.fake_running);
    CHECK(f.start_calls==starts&&f.profile_calls==profiles);
    CHECK(!f.fake_running&&!f.c.hardware_running&&f.c.link.phase==MT_LINK_BEGIN);
    drain();CHECK(reply(old.request)&&reply(old.request)->type==135);
}
static void test_old_release_transfer_phases_backpressure(void) {
    unsigned phase;
    for(phase=MT_LINK_BEGIN;phase<=MT_LINK_END_QUEUED;phase++) {
        mt_wire_message old;unsigned i,starts,profiles;
        boot();configure(1,0,0);arm();drain();capture(0);
        old=release_message();CHECK(receive(&old)==MT_CONTROL_OK);drain();
        starts=f.start_calls;profiles=f.profile_calls;
        sample(1000);sample(3000);for(i=1;i<2048;i++)sample(2500);
        i=0;
        while((unsigned)f.c.link.phase!=phase) { CHECK(i++<1000);CHECK(poll()==MT_CONTROL_OK); }
        CHECK(f.c.link.tomb_window==1&&f.c.link.active_window==2&&!f.fake_running);
        f.blocked=1;old.sequence=f.host_seq++;old.request=f.host_request++;
        CHECK(receive(&old)==MT_CONTROL_OK);
        fprintf(stderr,"blocked old RELEASE phase%u: starts before=%u after=%u; hardware=%u\n",phase,starts,f.start_calls,f.fake_running);
        CHECK(f.start_calls==starts&&f.profile_calls==profiles&&!f.fake_running);
        for(i=0;i<10;i++)CHECK(poll()==MT_CONTROL_OK);
        CHECK(f.start_calls==starts&&!f.fake_running&&f.c.ready);
        f.blocked=0;drain();CHECK(reply(old.request)&&reply(old.request)->type==135);
        CHECK(f.c.link.tomb_window==2&&f.c.link.phase==MT_LINK_WAIT_RELEASE);
        old.sequence=f.host_seq++;old.request=f.host_request++;
        expect_error(&old,7);CHECK(f.start_calls==starts&&!f.fake_running);
    }
}
static void test_single_release(void) {mt_wire_message m;boot();configure(2,0,0);arm();drain();capture(1);m=release_message();CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(!f.c.link.armed&&!f.fake_running&&f.start_calls==1);arm();drain();CHECK(f.start_calls==2&&f.fake_running);}
static void test_status_transitions(void) {unsigned i,n=0;uint8_t expected[]={1,2,3,4,6};boot();configure(1,0,512);arm();capture(0);for(i=0;i<f.message_count;i++)if(f.messages[i].type==134&&f.messages[i].request==0){CHECK(n<5);CHECK(f.messages[i].payload[0]==expected[n++]);CHECK(f.messages[i].payload[1]==0);CHECK(u32(f.messages[i].payload+12)==0&&u32(f.messages[i].payload+16)==0);}CHECK(n==5);}
static void trace_messages(const char *label) {
    unsigned i;
    fprintf(stderr,"TRACE %s:",label);
    for(i=0;i<f.message_count;i++) {
        const mt_wire_message *m=&f.messages[i];
        fprintf(stderr," %u(req%"PRIu32",seq%"PRIu32,m->type,m->request,m->sequence);
        if(m->type==134)fprintf(stderr,",state%u",m->payload[0]);
        fprintf(stderr,")");
    }
    fprintf(stderr,"\n");
}
static void test_status_query_order(void) {
    mt_wire_message m;unsigned i,n=0;uint8_t expected[]={1,2,3,3};
    boot();configure(1,0,512);arm();drain();
    for(i=0;i<512;i++)sample(1000);
    sample(3000);
    CHECK(f.c.state_count==2&&f.c.tx.count==0);
    m=command(6,0);CHECK(receive(&m)==MT_CONTROL_OK);drain();
    trace_messages("GET_STATUS with two older pending transitions");
    for(i=0;i<f.message_count;i++)if(f.messages[i].type==134){CHECK(n<4);CHECK(f.messages[i].payload[0]==expected[n++]);}
    CHECK(n==4);
}
static void test_release_pending_status_order(void) {mt_wire_message m;unsigned i,n=0;uint8_t states[4]={0};boot();configure(1,0,0);arm();drain();sample(1000);sample(3000);for(i=1;i<2048;i++)sample(2500);while(f.c.link.phase!=MT_LINK_WAIT_RELEASE)CHECK(poll()==MT_CONTROL_OK);CHECK(f.c.state_count>0);m=release_message();CHECK(receive(&m)==MT_CONTROL_OK);drain();for(i=0;i<f.message_count;i++)if(f.messages[i].request==m.request){unsigned j;for(j=i+1;j<f.message_count;j++)if(f.messages[j].type==134){CHECK(n<4);states[n++]=f.messages[j].payload[0];}}trace_messages("RELEASE immediately after END");CHECK(n>0);for(i=0;i<n;i++){if(states[i]==6)fprintf(stderr,"stale WAIT_RELEASE emitted after RELEASE_ACK\n");CHECK(states[i]!=6);}CHECK(states[n-1]==2);}
static void test_status_queue_bound(void) {mt_wire_message m;unsigned i;boot();configure(1,0,512);arm();for(i=0;i<512;i++) { sample(1000); }
    sample(3000);for(i=513;i<2048;i++)sample(2500);CHECK(f.c.state_count==4);CHECK(f.c.tx.count==1);f.blocked=1;for(i=0;i<50;i++)CHECK(poll()==MT_CONTROL_OK);CHECK(f.c.state_count<=4&&f.c.tx.count<=3&&f.c.ready);/* Explicit public-struct corruption tests only the defensive guard. */f.c.state_count=MT_CONTROL_STATUS_MAX;f.c.last_state=0;CHECK(mt_control_sample(&f.c,0,f.now)==MT_CONTROL_IGNORED);/* Hardware stopped, so transition checked by poll. */CHECK(poll()==MT_CONTROL_RECOVERY);CHECK(f.c.state_count==0&&!f.c.ready);m=command(6,0);CHECK(receive(&m)==MT_CONTROL_IGNORED);}
static void test_status_reply_capacity_recovery(void) {
    mt_wire_message m;unsigned i,bytes;
    boot();configure(1,0,512);bytes=(unsigned)f.byte_count;
    arm();for(i=0;i<512;i++) { sample(1000); }
    sample(3000);
    CHECK(f.c.state_count==3&&f.c.tx.count==1);
    m=command(6,0);
    CHECK(receive(&m)==MT_CONTROL_RECOVERY);
    CHECK(!f.c.ready&&!f.fake_running&&reply(m.request)==NULL);
    check_recovery_fresh_nonce(bytes);
    CHECK(types(132)==0);
}
static void test_stop_bypasses_pending_status_capacity(void) {
    mt_wire_message m;unsigned i;
    boot();configure(1,0,512);arm();for(i=0;i<512;i++) { sample(1000); }
    sample(3000);
    CHECK(f.c.state_count==3&&f.c.tx.count==1);
    m=command(5,4);p32(m.payload,f.c.link.generation);
    CHECK(receive(&m)==MT_CONTROL_OK);drain();
    CHECK(f.c.ready&&!f.fake_running&&types(133)==1&&types(147)==1);
    CHECK(f.c.state_count==0);
}
static void test_old_partial_stop_then_new_arm(void) {
    mt_wire_message m;unsigned before,i=0;
    boot();m=command(5,4);CHECK(receive(&m)==MT_CONTROL_OK);
    f.accepted_chunk=5;CHECK(poll()==MT_CONTROL_OK);CHECK(f.partial_count==5);
    CHECK(f.c.tx.slots[0].message.type==133);before=f.message_count;
    f.nonce++;f.host_seq=f.host_request=2;
    CHECK(hello(f.nonce)==MT_CONTROL_OK);
    m=config_message(1,0,0);CHECK(receive(&m)==MT_CONTROL_OK);
    while(f.message_count==before) { CHECK(i++<20);CHECK(poll()==MT_CONTROL_OK); }
    CHECK(f.messages[before].type==133&&f.messages[before].session==NONCE);
    CHECK(f.c.link.phase==MT_LINK_IDLE&&f.c.ready&&f.c.link.config_id==1);
    arm();drain();CHECK(f.fake_running&&f.c.link.armed&&f.c.link.generation==3);
    CHECK(f.messages[before+1].type==129&&f.messages[before+1].sequence==1);
    CHECK(f.messages[before+2].type==131&&f.messages[before+2].sequence==2);
    CHECK(f.messages[before+3].type==132&&f.messages[before+3].sequence==3);
}
static void test_reply_admission(void) {mt_wire_message m;unsigned i;boot();for(i=0;i<3;i++){m=command(6,0);CHECK(receive(&m)==MT_CONTROL_OK);}m=config_message(1,0,0);CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(f.c.link.config_id==0&&f.start_calls==0&&types(131)==0);}
static void test_arm_reply_admission(void) {mt_wire_message m;unsigned i;boot();configure(1,0,0);for(i=0;i<3;i++){m=command(6,0);CHECK(receive(&m)==MT_CONTROL_OK);}m=command(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(f.c.link.generation==0&&f.start_calls==0&&types(132)==0);}
static void test_twenty_windows(void) {unsigned w,i;uint32_t seq=1;FILE *out;boot();configure(1,0,512);arm();drain();for(w=0;w<20;w++){mt_wire_message m;capture(w);CHECK(f.c.link.last_window==w+1);m=release_message();CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.c.link.armed&&f.fake_running);}CHECK(types(144)==20&&types(146)==20&&types(145)==180&&types(135)==20);CHECK(f.start_calls==21&&f.stop_calls==21);for(i=0;i<f.message_count;i++){CHECK(f.messages[i].session==NONCE);CHECK(f.messages[i].sequence==seq++);}out=fopen("twenty-windows.cobs","wb");CHECK(out);CHECK(fwrite(f.bytes,1,f.byte_count,out)==f.byte_count);CHECK(fclose(out)==0);printf("20-window transcript: %zu bytes; %u messages; starts=%u stops=%u\n",f.byte_count,f.message_count,f.start_calls,f.stop_calls);}
static void test_wait_release_timeout(void) {unsigned bytes;boot();configure(1,0,0);arm();drain();capture(0);bytes=(unsigned)f.byte_count;f.now=f.c.link.release_since_ms+5000;CHECK(poll()==MT_CONTROL_RECOVERY);check_recovery_fresh_nonce(bytes);}
static void test_tx_failure(void) {unsigned bytes;boot();configure(1,0,0);arm();bytes=(unsigned)f.byte_count;f.io_fail=1;CHECK(poll()==MT_CONTROL_RECOVERY);CHECK(!f.fake_running);f.io_fail=0;check_recovery_fresh_nonce(bytes);CHECK(types(132)==0);}
static void test_invalid_hello_no_hardware(void) {mt_wire_message m;unsigned stops;boot();configure(1,0,0);arm();drain();stops=f.stop_calls;memset(&m,0,sizeof m);m.type=1;m.sequence=m.request=1;m.payload_length=8;CHECK(receive(&m)==MT_CONTROL_IGNORED);CHECK(f.stop_calls==stops&&f.fake_running);CHECK(hello(NONCE)==MT_CONTROL_IGNORED);CHECK(f.stop_calls==stops&&f.fake_running);}
static void test_stop_status_no_stale_after_ack(void) {mt_wire_message m;unsigned i,seen=0;boot();configure(1,0,512);arm();for(i=0;i<512;i++)sample(1000);m=command(5,4);p32(m.payload,f.c.link.generation);CHECK(receive(&m)==MT_CONTROL_OK);drain();for(i=0;i<f.message_count;i++){if(f.messages[i].type==133)seen=1;else if(seen&&f.messages[i].type==134)CHECK(f.messages[i].payload[0]==0);}CHECK(seen&&f.c.state_count==0);}

typedef void (*test_fn)(void);
static const struct {const char *name;test_fn run;} tests[]={
 {"caps",test_caps},{"caps_cartesian",test_caps_cartesian},{"config_echo_and_identity",test_config_echo_and_identity},{"auto_timeout_and_rearm",test_auto_timeout_and_rearm},{"rejected_config",test_rejected_config},{"invalid_payload",test_invalid_payload},{"rejected_arm",test_rejected_arm},{"stale_stop",test_stale_stop},{"stale_stop_with_pending_states",test_stale_stop_with_pending_states},{"stop_order",test_stop_order},{"initial_stop",test_initial_stop},{"duplicates",test_duplicates},{"start_failure",test_start_failure},{"stop_failure",test_stop_failure},{"profile_fail_hello",test_profile_fail_hello},{"profile_fail_arm",test_profile_fail_arm},{"hello_stop_failure",test_hello_stop_failure},{"release_start_failure",test_release_start_failure},{"release_profile_failure",test_release_profile_failure},{"capture_stop_failure",test_capture_stop_failure},{"old_partial_hello",test_old_partial_hello},{"release_semantics",test_release_semantics},{"old_release_during_next_transfer",test_old_release_during_next_transfer},{"old_release_transfer_phases_backpressure",test_old_release_transfer_phases_backpressure},{"single_release",test_single_release},{"status_transitions",test_status_transitions},{"status_query_order",test_status_query_order},{"release_pending_status_order",test_release_pending_status_order},{"status_queue_bound",test_status_queue_bound},{"status_reply_capacity_recovery",test_status_reply_capacity_recovery},{"stop_bypasses_pending_status_capacity",test_stop_bypasses_pending_status_capacity},{"old_partial_stop_then_new_arm",test_old_partial_stop_then_new_arm},{"reply_admission",test_reply_admission},{"arm_reply_admission",test_arm_reply_admission},{"twenty_windows",test_twenty_windows},{"wait_release_timeout",test_wait_release_timeout},{"tx_failure",test_tx_failure},{"invalid_hello_no_hardware",test_invalid_hello_no_hardware},{"stop_status_no_stale_after_ack",test_stop_status_no_stale_after_ack}};
int main(int argc,char **argv) {volatile unsigned i,passed=0,bad=0;for(i=0;i<sizeof tests/sizeof tests[0];i++){if(argc>1&&strcmp(argv[1],tests[i].name))continue;current=tests[i].name;if(!setjmp(failed)){tests[i].run();++passed;printf("PASS %s\n",current);}else ++bad;}printf("RESULT %u passed; %u failed; %u checks\n",passed,bad,checks);return bad?1:0;}
