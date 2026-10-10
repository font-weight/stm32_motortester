#include <inttypes.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "firmware/app/mt_control_rx.h"

#define NONCE UINT64_C(0x1234567812345678)
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"FAIL %s:%d: %s\n",current,__LINE__,#x); longjmp(failed,1); } } while (0)
typedef struct {
    mt_control control;
    mt_control_rx rx;
    unsigned starts, stops, profiles, running, stop_ok, start_ok, profile_ok;
    unsigned writes, out_count;
    uint8_t out[32768];
    size_t out_used, write_limit;
    uint32_t now, seq, req;
    uint64_t nonce;
} fixture;
static fixture f;
static uint64_t checks;
static const char *current;
static jmp_buf failed;
static int profile(void *p) { fixture *x=p; ++x->profiles; return (int)x->profile_ok; }
static int start(void *p,uint32_t now) { fixture *x=p; (void)now; ++x->starts; x->running=1; return (int)x->start_ok; }
static int stop(void *p) { fixture *x=p; ++x->stops; if(x->stop_ok)x->running=0; return (int)x->stop_ok; }
static void p16(uint8_t *p,uint16_t x) {p[0]=(uint8_t)x;p[1]=(uint8_t)(x>>8);}
static void p32(uint8_t *p,uint32_t x) {for(unsigned i=0;i<4u;i++)p[i]=(uint8_t)(x>>(8u*i));}
static void p64(uint8_t *p,uint64_t x) {for(unsigned i=0;i<8u;i++)p[i]=(uint8_t)(x>>(8u*i));}
static uint32_t u32(const uint8_t *p) {return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
/* Independent wire oracle: neither mt_wire_encode nor its CRC is used. */
static uint32_t crc(const uint8_t *p,size_t n) {
    uint32_t r=UINT32_MAX;
    for(size_t i=0;i<n;i++) { r^=p[i]; for(unsigned j=0;j<8u;j++)r=(r>>1)^((r&1u)?UINT32_C(0xedb88320):0u); }
    return ~r;
}
static size_t cobs(const uint8_t *raw,size_t n,uint8_t *out) {
    size_t pos=1, mark=0; unsigned code=1;
    for(size_t i=0;i<n;i++) {
        if(raw[i]==0u) { out[mark]=(uint8_t)code;mark=pos++;code=1; }
        else {out[pos++]=raw[i];++code;if(code==255u){out[mark]=255;mark=pos++;code=1;}}
    }
    out[mark]=(uint8_t)code;out[pos++]=0;return pos;
}
static size_t raw_message(const mt_wire_message *m,uint8_t *raw) {
    size_t n=24u+m->payload_length;
    memset(raw,0,600);raw[1]=1;raw[2]=m->type;p16(raw+4,m->payload_length);
    p64(raw+8,m->session);p32(raw+16,m->sequence);p32(raw+20,m->request);
    memcpy(raw+24,m->payload,m->payload_length);p32(raw+n,crc(raw,n));return n+4u;
}
static size_t encode(const mt_wire_message *m,uint8_t *out) {uint8_t raw[600];return cobs(raw,raw_message(m,raw),out);}
static mt_wire_message msg(uint8_t type,uint16_t length) {
    mt_wire_message m={0};m.type=type;m.payload_length=length;m.session=f.nonce;m.sequence=f.seq++;m.request=f.req++;return m;
}
static mt_wire_message hello_msg(uint64_t nonce) {mt_wire_message m={0};m.type=1;m.sequence=m.request=1;m.payload_length=8;p64(m.payload,nonce);return m;}
static mt_control_result bytes(const uint8_t *p,size_t n,size_t chunk) {
    size_t off=0;mt_control_result r=MT_CONTROL_OK;
    while(off<n){size_t take=n-off,used=999;if(take>chunk)take=chunk;r=mt_control_rx_feed(&f.rx,p+off,take,f.now,&used);CHECK(used<=take);if(r!=MT_CONTROL_OK)return r;CHECK(used==take);off+=used;}
    return r;
}
static mt_control_result receive(const mt_wire_message *m) {uint8_t b[600];size_t n=encode(m,b);return bytes(b,n,64);}
static int sink(void *p,const uint8_t *b,size_t n) {fixture *x=p;CHECK(n<=64u);++x->writes;if(n>x->write_limit)n=x->write_limit;CHECK(x->out_used+n<=sizeof x->out);memcpy(x->out+x->out_used,b,n);x->out_used+=n;return (int)n;}
static void drain(void) {
    unsigned guard=10000;
    while(f.control.tx.count||f.control.state_count||(f.control.link.phase>=MT_LINK_BEGIN&&f.control.link.phase<=MT_LINK_END_QUEUED)) {
        CHECK(guard--);CHECK(mt_control_poll(&f.control,f.now,sink,&f)==MT_CONTROL_OK);
    }
}
static unsigned count_type(uint8_t type) {
    size_t first=0;unsigned count=0;
    for(size_t i=0;i<f.out_used;i++)if(f.out[i]==0u){mt_wire_message m;CHECK(mt_wire_decode(f.out+first,i-first,&m)==MT_WIRE_OK);if(m.type==type)++count;first=i+1u;}
    CHECK(first==f.out_used);return count;
}
static void init(void) {
    mt_control_io io;memset(&f,0,sizeof f);f.stop_ok=f.start_ok=f.profile_ok=1;f.nonce=NONCE;f.seq=f.req=2;f.write_limit=64;f.now=10;
    io.profile_ready=profile;io.start=start;io.stop=stop;io.context=&f;io.firmware_build=42;
    CHECK(mt_control_init(&f.control,&io)==MT_CONTROL_OK);CHECK(mt_control_rx_init(&f.rx,&f.control)==MT_CONTROL_OK);
    CHECK(f.starts==0&&f.stops==0&&f.profiles==0);
}
static void boot(void) {mt_wire_message m;init();m=hello_msg(f.nonce);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(count_type(129)==1);CHECK(f.control.ready&&f.stops==1);}
static mt_wire_message config_msg(void) {mt_wire_message m=msg(3,16);p16(m.payload,1);m.payload[2]=1;p16(m.payload+6,2048);return m;}
static void configure(void) {mt_wire_message m=config_msg();CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.control.link.config_id==1);}
static void armed(void) {mt_wire_message m;boot();configure();m=msg(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.running&&f.control.link.armed&&f.starts==1);}
static void unchanged(const mt_control *before,unsigned stops,unsigned starts) {CHECK(memcmp(before,&f.control,sizeof *before)==0);CHECK(stops==f.stops&&starts==f.starts);}

static void test_arguments(void) {
    mt_control_rx before,zero={0};mt_control ctrl;uint8_t b[65]={0};size_t used;
    init();before=f.rx;ctrl=f.control;
    CHECK(mt_control_rx_init(NULL,&f.control)==MT_CONTROL_ARGUMENT);
    CHECK(mt_control_rx_init(&f.rx,NULL)==MT_CONTROL_ARGUMENT);CHECK(memcmp(&before,&f.rx,sizeof before)==0);
    CHECK(mt_control_rx_poll(NULL,0)==MT_CONTROL_ARGUMENT);CHECK(mt_control_rx_poll(&zero,0)==MT_CONTROL_ARGUMENT);
    CHECK(mt_control_rx_transport_lost(NULL)==MT_CONTROL_ARGUMENT);CHECK(mt_control_rx_transport_lost(&zero)==MT_CONTROL_ARGUMENT);
    for(size_t n=0;n<=65u;n++) {
        used=123;CHECK(mt_control_rx_feed(NULL,b,n,0,&used)==MT_CONTROL_ARGUMENT&&used==0);
        used=123;CHECK(mt_control_rx_feed(&zero,b,n,0,&used)==MT_CONTROL_ARGUMENT&&used==0);
        CHECK(mt_control_rx_feed(&f.rx,b,n,0,NULL)==MT_CONTROL_ARGUMENT);
        used=123;
        if(n==0)CHECK(mt_control_rx_feed(&f.rx,NULL,n,0,&used)==MT_CONTROL_OK&&used==0);
        else CHECK(mt_control_rx_feed(&f.rx,NULL,n,0,&used)==MT_CONTROL_ARGUMENT&&used==0);
        used=123;
        CHECK(mt_control_rx_feed(&f.rx,b,n,0,&used)==(n<=64u?MT_CONTROL_OK:MT_CONTROL_ARGUMENT));CHECK(used==(n<=64u?n:0u));
    }
    used=123;CHECK(mt_control_rx_feed(&f.rx,b,SIZE_MAX,0,&used)==MT_CONTROL_ARGUMENT&&used==0);
    used=123;CHECK(mt_control_rx_feed(&f.rx,b,(size_t)UINT32_MAX,0,&used)==MT_CONTROL_ARGUMENT&&used==0);
    CHECK(memcmp(&before,&f.rx,sizeof before)==0);unchanged(&ctrl,0,0);
}
static void test_every_split(void) {
    for(unsigned kind=0;kind<4u;kind++)for(size_t split=0;split<64u;split++) {
        mt_wire_message m;uint8_t b[600];size_t n,used;
        if(kind==0){init();m=hello_msg(NONCE);}else if(kind==1){boot();m=config_msg();}else if(kind==2){boot();configure();m=msg(4,4);p32(m.payload,1);}else{armed();m=msg(5,4);p32(m.payload,f.control.link.generation);}
        n=encode(&m,b);if(split>n)continue;
        CHECK(mt_control_rx_feed(&f.rx,b,split,f.now,&used)==MT_CONTROL_OK&&used==split);
        CHECK(mt_control_rx_feed(&f.rx,b+split,n-split,f.now,&used)==MT_CONTROL_OK&&used==n-split);drain();
        if(kind==0)CHECK(f.control.ready&&count_type(129)==1);
        if(kind==1)CHECK(f.control.link.config_id==1&&count_type(131)==1);
        if(kind==2)CHECK(f.running&&f.starts==1&&count_type(132)==1);
        if(kind==3)CHECK(!f.running&&f.stops==2&&count_type(133)==1);
    }
}
static void test_chunk_sizes(void) {
    for(size_t chunk=1;chunk<=64u;chunk++) {
        uint8_t b[1024];size_t n=0;mt_wire_message m;init();
        m=hello_msg(NONCE);n+=encode(&m,b+n);
        m=config_msg();n+=encode(&m,b+n);
        /* Drain command replies in two batches to respect finite TX capacity. */
        CHECK(bytes(b,n,chunk)==MT_CONTROL_OK);drain();n=0;
        m=msg(4,4);p32(m.payload,1);uint32_t generation=m.request;n+=encode(&m,b+n);
        m=msg(5,4);p32(m.payload,generation);n+=encode(&m,b+n);
        CHECK(bytes(b,n,chunk)==MT_CONTROL_OK);drain();
        CHECK(f.starts==1&&f.stops==2&&!f.running);CHECK(count_type(129)==1&&count_type(131)==1&&count_type(132)==1&&count_type(133)==1);
    }
}
static void test_coalesced_frames(void) {
    uint8_t b[600];size_t n=0,used;mt_wire_message m;boot();
    m=msg(2,0);n+=encode(&m,b+n);m=msg(6,0);n+=encode(&m,b+n);CHECK(n<=64u);
    CHECK(mt_control_rx_feed(&f.rx,b,n,f.now,&used)==MT_CONTROL_OK&&used==n);drain();CHECK(count_type(130)==1&&count_type(134)==1);
}
static void test_codec_rejections(void) {
    for(unsigned kind=0;kind<12u;kind++) {
        mt_wire_message m;uint8_t raw[600],b[650];size_t n;mt_control before;mt_wire_message cached;unsigned stops,starts;
        armed();m=msg(5,4);p32(m.payload,f.control.link.generation);n=raw_message(&m,raw);
        switch(kind) {
        case 0:raw[0]=1;break;case 1:raw[1]=2;break;case 2:raw[3]=1;break;case 3:raw[6]=1;break;case 4:raw[7]=1;break;
        case 5:p16(raw+4,3);break;case 6:p16(raw+4,513);break;case 7:n=27;break;case 8:n=28;break;
        case 9:raw[n-1u]^=1;break;case 10:b[0]=5;b[1]=1;b[2]=0;n=3;break;default:n=541;break;
        }
        if(kind!=10u) {if(kind<9u)p32(raw+n-4u,crc(raw,n-4u));n=cobs(raw,n,b);}
        before=f.control;cached=f.rx.message;stops=f.stops;starts=f.starts;
        CHECK(bytes(b,n,64)==MT_CONTROL_OK);CHECK(f.rx.rejected_frames==1);CHECK(f.rx.overflows==0&&f.rx.timeouts==0);
        unchanged(&before,stops,starts);CHECK(memcmp(&cached,&f.rx.message,sizeof cached)==0);
        /* Retry here is a test probe: rejected codec input never reached session. */
        CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.stops==stops+1u&&!f.running&&count_type(133)==1);
    }
}
static void test_all_version_reserved_values(void) {
    for(unsigned field=0;field<5u;field++)for(unsigned value=0;value<256u;value++) {
        const unsigned offsets[5]={0,1,3,6,7};unsigned offset=offsets[field];uint8_t raw[600],b[650];size_t n;mt_wire_message m;mt_control before;
        if(value==(offset==1u?1u:0u))continue;
        armed();m=msg(5,4);p32(m.payload,f.control.link.generation);n=raw_message(&m,raw);raw[offset]=(uint8_t)value;p32(raw+n-4u,crc(raw,n-4u));n=cobs(raw,n,b);
        before=f.control;CHECK(bytes(b,n,64)==MT_CONTROL_OK);CHECK(f.rx.rejected_frames==1);unchanged(&before,1,1);
    }
}
static void test_all_wire_lengths(void) {
    /* Every representable payload length, except the matching four bytes. */
    armed();mt_wire_message m=msg(5,4);p32(m.payload,f.control.link.generation);mt_control before=f.control;
    for(unsigned length=0;length<=UINT16_MAX;length++) {
        uint8_t raw[600],b[650];size_t n;if(length==4u)continue;n=raw_message(&m,raw);p16(raw+4,(uint16_t)length);p32(raw+n-4u,crc(raw,n-4u));n=cobs(raw,n,b);
        CHECK(bytes(b,n,64)==MT_CONTROL_OK);unchanged(&before,1,1);
    }
    CHECK(f.rx.rejected_frames==UINT16_MAX);
}
static void test_empty_noise_stale_message(void) {
    uint8_t zeros[64]={0},bad[3]={4,1,0};mt_control before;mt_wire_message cache;armed();before=f.control;cache=f.rx.message;
    for(unsigned i=0;i<100u;i++){CHECK(bytes(zeros,sizeof zeros,64)==MT_CONTROL_OK);CHECK(bytes(bad,sizeof bad,64)==MT_CONTROL_OK);}
    CHECK(f.rx.rejected_frames==100u);unchanged(&before,1,1);CHECK(memcmp(&cache,&f.rx.message,sizeof cache)==0);
}
static void test_overflow(void) {
    uint8_t b[64];size_t used;mt_control before;memset(b,1,sizeof b);armed();before=f.control;
    CHECK(bytes(b,64,64)==MT_CONTROL_OK);for(unsigned i=1;i<8u;i++)CHECK(bytes(b,64,64)==MT_CONTROL_OK);
    CHECK(mt_control_rx_feed(&f.rx,b,31,f.now,&used)==MT_CONTROL_OK&&used==31);CHECK(f.rx.stream.used==543u&&f.rx.overflows==0);
    CHECK(bytes(b,1,1)==MT_CONTROL_OK);CHECK(f.rx.overflows==1&&f.rx.stream.discarding&&f.rx.stream.used==0);
    for(unsigned i=0;i<20u;i++)CHECK(bytes(b,64,64)==MT_CONTROL_OK);CHECK(f.rx.overflows==1);
    f.now+=10000;CHECK(mt_control_rx_poll(&f.rx,f.now)==MT_CONTROL_OK);CHECK(f.rx.timeouts==0);unchanged(&before,1,1);
    b[0]=0;CHECK(bytes(b,1,1)==MT_CONTROL_OK);CHECK(!f.rx.stream.discarding);
    mt_wire_message m=msg(5,4);p32(m.payload,f.control.link.generation);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(count_type(133)==1);
}
static void test_timeout_interbyte_wrap(void) {
    const uint32_t starts[3]={0,UINT32_MAX-500u,UINT32_MAX};
    for(unsigned trial=0;trial<3u;trial++)for(unsigned mode=0;mode<3u;mode++) {
        uint8_t b=1;size_t used=99;mt_control before;armed();before=f.control;f.now=starts[trial];
        CHECK(bytes(&b,1,1)==MT_CONTROL_OK);CHECK(mt_control_rx_poll(&f.rx,f.now+999u)==MT_CONTROL_OK);CHECK(f.rx.timeouts==0&&f.rx.stream.used==1);
        if(mode==0)CHECK(mt_control_rx_poll(&f.rx,f.now+1000u)==MT_CONTROL_OK);
        else if(mode==1)CHECK(mt_control_rx_feed(&f.rx,NULL,0,f.now+1000u,&used)==MT_CONTROL_OK&&used==0);
        else {b=0;CHECK(mt_control_rx_feed(&f.rx,&b,1,f.now+1000u,&used)==MT_CONTROL_OK&&used==1);}
        CHECK(f.rx.timeouts==1&&f.rx.stream.used==0);CHECK(f.rx.stream.discarding==(mode!=2u));unchanged(&before,1,1);
        CHECK(mt_control_rx_poll(&f.rx,f.now+2000u)==MT_CONTROL_OK&&f.rx.timeouts==1);
        f.now+=2001u;b=0;CHECK(bytes(&b,1,1)==MT_CONTROL_OK);
        mt_wire_message m=msg(5,4);p32(m.payload,f.control.link.generation);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(count_type(133)==1);
    }
}
static void test_timeout_total_wrap(void) {
    for(unsigned wrap=0;wrap<2u;wrap++) {
        uint8_t b=1;mt_control before;armed();before=f.control;f.now=wrap?UINT32_MAX-1500u:0;uint32_t first=f.now;
        for(unsigned i=0;i<4u;i++){f.now=first+i*900u;CHECK(bytes(&b,1,1)==MT_CONTROL_OK);CHECK(f.rx.timeouts==0);}
        CHECK(mt_control_rx_poll(&f.rx,first+2999u)==MT_CONTROL_OK&&f.rx.timeouts==0);
        CHECK(mt_control_rx_poll(&f.rx,first+3000u)==MT_CONTROL_OK&&f.rx.timeouts==1&&f.rx.stream.discarding);unchanged(&before,1,1);
    }
}
static void test_saturation(void) {
    uint8_t bad[3]={4,1,0},b[64];mt_control before;armed();before=f.control;
    f.rx.rejected_frames=f.rx.overflows=f.rx.timeouts=UINT32_MAX-1u;
    for(unsigned i=0;i<3u;i++){CHECK(bytes(bad,sizeof bad,64)==MT_CONTROL_OK);CHECK(f.rx.rejected_frames==UINT32_MAX);}
    memset(b,1,sizeof b);
    for(unsigned trial=0;trial<3u;trial++){for(unsigned i=0;i<9u;i++)CHECK(bytes(b,64,64)==MT_CONTROL_OK);CHECK(f.rx.overflows==UINT32_MAX);uint8_t z=0;CHECK(bytes(&z,1,1)==MT_CONTROL_OK);}
    for(unsigned trial=0;trial<3u;trial++){CHECK(bytes(b,1,1)==MT_CONTROL_OK);f.now+=1000u;CHECK(mt_control_rx_poll(&f.rx,f.now)==MT_CONTROL_OK);CHECK(f.rx.timeouts==UINT32_MAX);uint8_t z=0;CHECK(bytes(&z,1,1)==MT_CONTROL_OK);}
    unchanged(&before,1,1);CHECK(f.control.link.dropped_windows==0);CHECK(mt_control_rx_transport_lost(&f.rx)==MT_CONTROL_RECOVERY);CHECK(f.rx.rejected_frames==UINT32_MAX&&f.rx.overflows==UINT32_MAX&&f.rx.timeouts==UINT32_MAX);
    CHECK(mt_control_rx_init(&f.rx,&f.control)==MT_CONTROL_OK);CHECK(f.rx.rejected_frames==0&&f.rx.overflows==0&&f.rx.timeouts==0);
}
static void test_valid_guards(void) {
    mt_wire_message m;unsigned stops;armed();stops=f.stops;
    m=msg(5,4);p32(m.payload,f.control.link.generation);m.session++;CHECK(receive(&m)==MT_CONTROL_OK);CHECK(f.running&&f.stops==stops);m.session--;CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(!f.running&&f.stops==stops+1u);
    mt_control before=f.control;CHECK(receive(&m)==MT_CONTROL_OK);unchanged(&before,stops+1u,1);
    m.payload[0]^=1;CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(!f.control.ready&&!f.control.session.active);
}
static void test_recovery_suffix(void) {
    const unsigned causes[4]={0,1,2,3};
    for(unsigned trial=0;trial<4u;trial++) {
        uint8_t bad[600],fresh[600],combined[64];size_t nb,nh,used,pre;mt_wire_message m,h;armed();
        m=msg(6,0);if(causes[trial]==0u)m.sequence++;if(causes[trial]==1u)m.request=1;if(causes[trial]==2u)m.type=129;if(causes[trial]==3u)m.sequence=UINT32_MAX;
        nb=encode(&m,bad);h=hello_msg(NONCE+1u);nh=encode(&h,fresh);CHECK(nb+nh>64u);pre=nb+nh-64u;
        CHECK(bytes(bad,pre,64)==MT_CONTROL_OK);memcpy(combined,bad+pre,nb-pre);memcpy(combined+nb-pre,fresh,nh);
        CHECK(mt_control_rx_feed(&f.rx,combined,sizeof combined,f.now,&used)==MT_CONTROL_RECOVERY);CHECK(used==nb-pre);CHECK(!f.control.ready&&!f.control.session.active&&f.control.session.nonce==NONCE&&!f.running);
        CHECK(f.rx.stream.used==0&&f.rx.rejected_frames==0);
        size_t suffix=sizeof combined-used,readback=99;CHECK(mt_control_rx_feed(&f.rx,combined+used,suffix,f.now,&readback)==MT_CONTROL_OK&&readback==suffix);drain();CHECK(f.control.ready&&f.control.session.nonce==NONCE+1u&&f.control.link.config_id==0);CHECK(count_type(129)==2);
    }
}
static void test_corruption_followed_by_gap(void) {
    uint8_t raw[600],b[650];size_t n;mt_wire_message m;armed();m=msg(6,0);n=raw_message(&m,raw);raw[n-1u]^=1;n=cobs(raw,n,b);CHECK(bytes(b,n,64)==MT_CONTROL_OK&&f.control.ready&&f.running);
    m=msg(6,0);CHECK(receive(&m)==MT_CONTROL_RECOVERY);CHECK(!f.control.ready&&!f.running&&f.rx.rejected_frames==1);
}
static void test_transport_loss(void) {
    for(unsigned failure=0;failure<2u;failure++) {
        uint8_t b=1;unsigned stops,writes;armed();f.rx.rejected_frames=4;f.rx.overflows=5;f.rx.timeouts=6;CHECK(bytes(&b,1,1)==MT_CONTROL_OK);CHECK(f.rx.stream.used==1);stops=f.stops;writes=f.writes;f.stop_ok=!failure;
        CHECK(mt_control_rx_transport_lost(&f.rx)==MT_CONTROL_RECOVERY);CHECK(f.stops==stops+1u&&!f.control.ready&&!f.control.session.active&&!f.control.link.armed);CHECK(f.control.hardware_running==failure&&f.running==failure);CHECK(f.rx.stream.used==0&&!f.rx.stream.discarding);CHECK(f.rx.rejected_frames==4&&f.rx.overflows==5&&f.rx.timeouts==6);
        CHECK(mt_control_poll(&f.control,f.now,sink,&f)==MT_CONTROL_IGNORED&&f.writes==writes);
        mt_wire_message m=msg(4,4);p32(m.payload,1);CHECK(receive(&m)==MT_CONTROL_OK&&f.starts==1);
        CHECK(mt_control_rx_transport_lost(&f.rx)==MT_CONTROL_RECOVERY&&f.stops==stops+2u);
        f.stop_ok=1;m=hello_msg(NONCE+1u);CHECK(receive(&m)==MT_CONTROL_OK);drain();CHECK(f.control.ready&&!f.running&&!f.control.hardware_running&&f.control.link.config_id==0);CHECK(count_type(129)==2);
    }
}
static void test_stop_failure_suffix(void) {
    uint8_t b[600],h[600],both[64];size_t n,nh,pre,used;armed();f.stop_ok=0;mt_wire_message m=msg(5,4);p32(m.payload,f.control.link.generation);n=encode(&m,b);m=hello_msg(NONCE+1u);nh=encode(&m,h);pre=n+nh-64u;
    CHECK(bytes(b,pre,64)==MT_CONTROL_OK);memcpy(both,b+pre,n-pre);memcpy(both+n-pre,h,nh);CHECK(mt_control_rx_feed(&f.rx,both,64,f.now,&used)==MT_CONTROL_RECOVERY&&used==n-pre);CHECK(f.stops==3&&f.running&&!f.control.ready&&f.control.hardware_running);CHECK(count_type(133)==0);
    f.stop_ok=1;size_t remain=64u-used,consumed;CHECK(mt_control_rx_feed(&f.rx,both+used,remain,f.now,&consumed)==MT_CONTROL_OK&&consumed==remain);drain();CHECK(!f.running&&f.control.ready&&count_type(133)==0&&count_type(129)==2);
}
static void test_partial_tx_retained(void) {
    mt_wire_message m;boot();m=msg(6,0);CHECK(receive(&m)==MT_CONTROL_OK);f.write_limit=5;size_t before=f.out_used;CHECK(mt_control_poll(&f.control,f.now,sink,&f)==MT_CONTROL_OK);CHECK(f.out_used==before+5u);mt_tx tx=f.control.tx;
    CHECK(mt_control_rx_transport_lost(&f.rx)==MT_CONTROL_RECOVERY);CHECK(memcmp(&tx,&f.control.tx,sizeof tx)==0);m=hello_msg(NONCE+1u);CHECK(receive(&m)==MT_CONTROL_OK);f.write_limit=64;drain();CHECK(count_type(134)==1&&count_type(129)==2);
    size_t first=0;unsigned index=0;for(size_t i=0;i<f.out_used;i++)if(f.out[i]==0u){mt_wire_message decoded;CHECK(mt_wire_decode(f.out+first,i-first,&decoded)==MT_WIRE_OK);if(index==1)CHECK(decoded.type==134&&decoded.session==NONCE);if(index==2)CHECK(decoded.type==129&&decoded.session==NONCE+1u&&decoded.sequence==1);++index;first=i+1u;}CHECK(index==3);
}
static void test_poll_no_control_service(void) {
    uint8_t b=1;armed();CHECK(mt_control_sample(&f.control,1000,f.now)==MT_CONTROL_OK);mt_control before=f.control;unsigned writes=f.writes;CHECK(bytes(&b,1,1)==MT_CONTROL_OK);
    CHECK(mt_control_rx_poll(&f.rx,f.now+1000u)==MT_CONTROL_OK);unchanged(&before,1,1);CHECK(f.writes==writes&&f.running&&f.rx.timeouts==1);
}
static void test_payload_length_dispatch(void) {
    const uint8_t types[7]={1,2,3,4,5,6,7};const unsigned legal[7]={8,0,16,4,4,0,12};
    for(unsigned type=0;type<7u;type++)for(unsigned length=0;length<=512u;length++) {
        mt_wire_message m;boot();if(length==legal[type])continue;m=msg(types[type],(uint16_t)length);if(type==0){m.session=0;m.sequence=m.request=1;p64(m.payload,NONCE+1u);}CHECK(receive(&m)==MT_CONTROL_OK);drain();
        CHECK(f.rx.rejected_frames==0&&f.rx.overflows==0&&f.rx.timeouts==0&&f.control.ready);CHECK(f.starts==0&&f.stops==1);
        if(type==0)CHECK(count_type(129)==1&&count_type(255)==0);else CHECK(count_type(255)==1);
    }
}
static void test_independent_codec_oracle(void) {
    const uint8_t input[]="123456789";CHECK(crc(input,9)==UINT32_C(0xcbf43926));
    for(unsigned length=0;length<=512u;length++) {
        mt_wire_message m={0},decoded;uint8_t b[650];m.type=200;m.session=NONCE;m.sequence=2;m.request=2;m.payload_length=(uint16_t)length;
        for(unsigned i=0;i<length;i++)m.payload[i]=(uint8_t)(i*17u);size_t n=encode(&m,b);CHECK(n<=MT_WIRE_FRAME_MAX);CHECK(mt_wire_decode(b,n-1u,&decoded)==MT_WIRE_OK);CHECK(decoded.type==m.type&&decoded.session==m.session&&decoded.sequence==m.sequence&&decoded.request==m.request&&decoded.payload_length==m.payload_length);CHECK(memcmp(decoded.payload,m.payload,length)==0);
    }
}
typedef void(*test_fn)(void);
static const struct {const char *name;test_fn run;} tests[]={
 {"arguments_all_legal_sizes",test_arguments},{"every_command_split",test_every_split},{"every_chunk_size_1_64",test_chunk_sizes},{"coalesced_frames",test_coalesced_frames},{"codec_rejections_no_state_or_stale_dispatch",test_codec_rejections},{"all_version_reserved_values",test_all_version_reserved_values},{"all_header_payload_lengths",test_all_wire_lengths},{"empty_noise_stale_message",test_empty_noise_stale_message},{"overflow_threshold_and_recovery",test_overflow},{"interbyte_timeout_wrap_and_empty_poll",test_timeout_interbyte_wrap},{"total_timeout_wrap",test_timeout_total_wrap},{"local_counter_saturation",test_saturation},{"valid_guard_duplicate_session_conflict",test_valid_guards},{"recovery_consumed_suffix_fresh_hello",test_recovery_suffix},{"corruption_then_sequence_gap",test_corruption_followed_by_gap},{"transport_loss_stop_failure",test_transport_loss},{"stop_failure_retains_hello_suffix",test_stop_failure_suffix},{"partial_tx_retained_across_loss",test_partial_tx_retained},{"rx_poll_does_not_service_control",test_poll_no_control_service},{"all_command_payload_lengths",test_payload_length_dispatch},{"independent_codec_oracle",test_independent_codec_oracle}};
int main(int argc,char **argv) {volatile unsigned passed=0,bad=0;for(volatile size_t i=0;i<sizeof tests/sizeof tests[0];i++){if(argc>1&&strcmp(argv[1],tests[i].name)!=0)continue;current=tests[i].name;if(!setjmp(failed)){tests[i].run();++passed;printf("PASS %s\n",current);}else ++bad;}printf("RESULT %u passed; %u failed; %" PRIu64 " checks\n",passed,bad,checks);return bad?1:0;}
