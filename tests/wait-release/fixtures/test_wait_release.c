/* Read-only characterization of frozen R8 production sources. Synchronous
 * hardware fakes and ordered transport acceptance, never physical USB proof. */
#include "firmware/app/mt_control.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned scenarios;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d: %s\n", __func__, __LINE__, #x); exit(1); } } while (0)
typedef struct {
    mt_control c;
    uint64_t nonce;
    uint32_t next, now;
    unsigned starts, stops, writes, running;
    size_t cap, used;
    uint8_t bytes[16384];
} fixture;
static fixture f;
static int profile(void *v) { (void)v; return 1; }
static int start(void *v, uint32_t now) { fixture *x=v; (void)now; ++x->starts; x->running=1; return 1; }
static int stop(void *v) { fixture *x=v; ++x->stops; x->running=0; return 1; }
static void put16(uint8_t *p, uint16_t n) { p[0]=(uint8_t)n; p[1]=(uint8_t)(n>>8); }
static void put32(uint8_t *p, uint32_t n) { unsigned i; for(i=0;i<4u;++i) p[i]=(uint8_t)(n>>(8u*i)); }
static int writer(void *v, const uint8_t *p, size_t n) {
    fixture *x=v; ++x->writes; CHECK(n<=64u); if(n>x->cap)n=x->cap;
    CHECK(x->used+n<=sizeof x->bytes); memcpy(x->bytes+x->used,p,n); x->used+=n; return (int)n;
}
static mt_control_result poll_at(uint32_t now, size_t cap) { f.now=now; f.cap=cap; return mt_control_poll(&f.c,now,writer,&f); }
static mt_wire_message command(uint8_t type, uint16_t length) {
    mt_wire_message m={0}; m.type=type; m.payload_length=length; m.session=f.nonce;
    m.sequence=f.next; m.request=f.next++; return m;
}
static mt_control_result send_at(const mt_wire_message *m,uint32_t now) {
    uint8_t bytes[MT_WIRE_FRAME_MAX]; size_t n; mt_wire_message decoded; f.now=now;
    CHECK(mt_wire_encode(m,bytes,sizeof bytes,&n)==MT_WIRE_OK);
    CHECK(mt_wire_decode(bytes,n-1u,&decoded)==MT_WIRE_OK);
    return mt_control_receive(&f.c,&decoded,now);
}
static void drain_at(uint32_t now) {
    unsigned budget=10000;
    while(f.c.tx.count || f.c.state_count ||
          (f.c.link.phase>=MT_LINK_BEGIN && f.c.link.phase<=MT_LINK_END)) {
        CHECK(budget--); CHECK(poll_at(now,64)==MT_CONTROL_OK);
    }
}
static unsigned frames(uint8_t type,int state,int reason) {
    size_t i,first=0; unsigned count=0;
    for(i=0;i<f.used;++i) if(f.bytes[i]==0u) {
        mt_wire_message m; CHECK(mt_wire_decode(f.bytes+first,i-first,&m)==MT_WIRE_OK);
        if(m.type==type && (state<0 || m.payload[0]==(unsigned)state) &&
           (reason<0 || m.payload[1]==(unsigned)reason)) ++count;
        first=i+1u;
    }
    return count;
}
static void trace(const char *label,mt_control_result result) {
    static unsigned trace_id;
    char path[256]; FILE *file;
    CHECK(snprintf(path,sizeof path,"build/%02u-%s.bin",++trace_id,label)>0);
    file=fopen(path,"wb");CHECK(file!=NULL);
    CHECK(fwrite(f.bytes,1,f.used,file)==f.used);CHECK(fclose(file)==0);
    printf("%s now=%"PRIu32" result=%u ready=%u session_active=%u phase=%u armed=%u hw=%u starts=%u stops=%u writes=%u bytes=%zu queued=%zu offset=%zu pending_states=%u anchor=%"PRIu32" tomb=%u end=%u abort=%u release_ack=%u stop_ack=%u timeout_status=%u\n",
        label,f.now,(unsigned)result,f.c.ready,f.c.session.active,(unsigned)f.c.link.phase,
        f.c.link.armed,f.c.hardware_running,f.starts,f.stops,f.writes,f.used,
        f.c.tx.count,f.c.tx.offset,f.c.state_count,f.c.link.release_since_ms,
        f.c.link.have_tombstone,frames(146,-1,-1),frames(147,-1,-1),
        frames(135,-1,-1),frames(133,-1,-1),frames(134,5,3));
    printf("  accepted-wire=%s\n",path);
}
static mt_control_result hello(uint64_t nonce,uint32_t now) {
    mt_wire_message m={0}; unsigned i;
    m.type=1;m.sequence=1;m.request=1;m.payload_length=8;
    for(i=0;i<8u;++i)m.payload[i]=(uint8_t)(nonce>>(8u*i));
    return send_at(&m,now);
}
static void setup(uint32_t now) {
    mt_control_io io={profile,start,stop,&f,0}; mt_wire_message m;
    memset(&f,0,sizeof f); f.now=now; f.cap=64; f.nonce=11;f.next=2;
    CHECK(mt_control_init(&f.c,&io)==MT_CONTROL_OK);CHECK(hello(f.nonce,now)==MT_CONTROL_OK);drain_at(now);
    m=command(3,16);put16(m.payload,1);m.payload[2]=1;put16(m.payload+6,2048);
    CHECK(send_at(&m,now)==MT_CONTROL_OK);drain_at(now);
    m=command(4,4);put32(m.payload,1);CHECK(send_at(&m,now)==MT_CONTROL_OK);drain_at(now);
    CHECK(f.c.hardware_running && f.starts==1u);
}
static void capture(uint32_t now,int finish) {
    unsigned i;
    CHECK(mt_control_sample(&f.c,100,now)==MT_CONTROL_OK);
    CHECK(mt_control_sample(&f.c,3000,now)==MT_CONTROL_OK);
    for(i=1;i<2048u;++i)CHECK(mt_control_sample(&f.c,2500,now)==MT_CONTROL_OK);
    CHECK(!f.c.hardware_running && !f.running);
    if(finish) {drain_at(now);CHECK(f.c.link.phase==MT_LINK_WAIT_RELEASE);CHECK(f.c.link.release_since_ms==now);}
}
static mt_wire_message release(void) {
    mt_wire_message m=command(7,12);put32(m.payload,f.c.link.generation);
    put32(m.payload+4,f.c.link.tomb_window);put32(m.payload+8,f.c.link.tomb_crc);return m;
}
static mt_wire_message stop_command(void) {mt_wire_message m=command(5,4);put32(m.payload,f.c.link.generation);return m;}
static void timeout_boundary(uint32_t anchor,const char *label) {
    mt_control_result r;mt_wire_message m;size_t bytes;unsigned stops,starts;
    setup(anchor);capture(anchor,1);bytes=f.used;stops=f.stops;starts=f.starts;
    r=poll_at(anchor+4999u,64);CHECK(r==MT_CONTROL_OK && f.used==bytes && f.c.ready);trace(label,r);
    r=poll_at(anchor+5000u,64);CHECK(r==MT_CONTROL_RECOVERY && f.used==bytes);
    CHECK(!f.c.ready && !f.c.session.active && !f.c.link.armed && !f.c.hardware_running);
    CHECK(f.stops==stops+1u && f.starts==starts && f.c.link.have_tombstone);trace("expiry",r);
    m=release();r=send_at(&m,anchor+5001u);CHECK(r==MT_CONTROL_IGNORED);CHECK(f.starts==starts);
    CHECK(poll_at(anchor+5001u,64)==MT_CONTROL_IGNORED && f.used==bytes);trace("late-release-after-expiry",r);
    m=stop_command();CHECK(send_at(&m,anchor+5001u)==MT_CONTROL_IGNORED);
    CHECK(hello(f.nonce,anchor+5001u)==MT_CONTROL_IGNORED);
    CHECK(hello(12,anchor+5001u)==MT_CONTROL_OK);drain_at(anchor+5001u);
    CHECK(f.c.ready && !f.c.link.have_tombstone && frames(129,-1,-1)==2u);
    CHECK(frames(134,5,3)==0u && frames(147,-1,-1)==0u);trace("fresh-hello-recovery",MT_CONTROL_OK);++scenarios;
}
static void release_boundary(void) {
    mt_wire_message m;mt_control_result r;unsigned starts;size_t bytes;
    setup(100);capture(100,1);starts=f.starts;m=release();
    r=send_at(&m,5099);CHECK(r==MT_CONTROL_OK && f.starts==starts+1u && f.c.hardware_running);
    CHECK(frames(135,-1,-1)==0u);trace("release-4999-rearms-before-ack-output",r);drain_at(5099);
    CHECK(frames(135,-1,-1)==1u);m=release();CHECK(send_at(&m,5100)==MT_CONTROL_OK);drain_at(5100);
    CHECK(f.starts==starts+1u && frames(135,-1,-1)==2u);++scenarios;
    setup(100);capture(100,1);bytes=f.used;m=release();r=send_at(&m,5100);
    CHECK(r==MT_CONTROL_RECOVERY && !f.c.ready && f.used==bytes && f.starts==1u);
    CHECK(frames(135,-1,-1)==0u);trace("release-exact-deadline-before-poll",r);++scenarios;
}
static void stop_and_hello_boundary(void) {
    mt_wire_message m;mt_control_result r;unsigned starts;
    setup(100);capture(100,1);m=stop_command();r=send_at(&m,5100);CHECK(r==MT_CONTROL_OK);drain_at(5100);
    CHECK(f.c.ready && f.c.link.phase==MT_LINK_IDLE && frames(133,-1,-1)==1u && frames(147,-1,-1)==0u);
    CHECK(frames(134,5,3)==0u);trace("stop-exact-deadline-before-poll",r);
    starts=f.starts;m=release();CHECK(send_at(&m,5101)==MT_CONTROL_OK);drain_at(5101);
    CHECK(f.starts==starts && !f.c.link.armed && frames(135,-1,-1)==1u);trace("late-release-after-stop",MT_CONTROL_OK);++scenarios;
    setup(100);capture(100,1);r=hello(12,5100);CHECK(r==MT_CONTROL_OK);drain_at(5100);
    CHECK(f.c.ready && !f.c.link.have_tombstone && frames(134,5,3)==0u);trace("fresh-hello-exact-deadline-before-poll",r);++scenarios;
}
static void end_prefix(void) {
    unsigned budget=1000;
    while(!(f.c.tx.count && f.c.tx.slots[0].message.type==146u)) {
        CHECK(budget--);CHECK(poll_at(100,64)==MT_CONTROL_OK);
    }
    CHECK(f.c.tx.offset==0u);CHECK(poll_at(100,1)==MT_CONTROL_OK);
    CHECK(f.c.tx.offset==1u && f.c.link.have_tombstone && f.c.link.phase==MT_LINK_END_QUEUED);
    CHECK(f.c.link.release_since_ms==0u);
}
static void backpressured_end(void) {
    mt_control_result r;mt_wire_message m;size_t bytes;
    setup(100);capture(100,0);end_prefix();bytes=f.used;
    r=poll_at(1099,0);CHECK(r==MT_CONTROL_OK && f.used==bytes && f.c.ready);trace("end-prefix-blocked-999ms",r);
    m=release();CHECK(send_at(&m,1099)==MT_CONTROL_OK); /* command ERROR/BUSY queued */
    CHECK(f.c.link.phase==MT_LINK_END_QUEUED && f.starts==1u);
    drain_at(1099);CHECK(f.c.link.release_since_ms==1099u && frames(255,7,2)==1u);
    CHECK(poll_at(5100,64)==MT_CONTROL_OK && f.c.ready);trace("first-end-byte-plus5000-still-waits",MT_CONTROL_OK);
    CHECK(poll_at(6098,64)==MT_CONTROL_OK);r=poll_at(6099,64);CHECK(r==MT_CONTROL_RECOVERY);trace("final-end-byte-plus5000-expires",r);++scenarios;
    setup(100);capture(100,0);end_prefix();CHECK(poll_at(1099,0)==MT_CONTROL_OK);
    r=poll_at(1100,0);CHECK(r==MT_CONTROL_RECOVERY && f.c.tx.fault==MT_TX_TIMEOUT);
    CHECK(f.c.link.release_since_ms==0u && frames(146,-1,-1)==0u && frames(134,5,3)==0u);
    trace("partial-end-stall1000-is-separate-tx-timeout",r);++scenarios;
}
static void partial_reply_and_enqueue_recover(void) {
    mt_wire_message m;size_t bytes;mt_control_result r;
    setup(100);capture(100,1);m=command(6,0);CHECK(send_at(&m,5099)==MT_CONTROL_OK);
    CHECK(poll_at(5099,1)==MT_CONTROL_OK && f.c.tx.offset==1u);bytes=f.used;
    r=poll_at(5100,64);CHECK(r==MT_CONTROL_RECOVERY && f.used==bytes && f.c.tx.offset==1u);
    CHECK(hello(12,5101)==MT_CONTROL_OK);drain_at(5101);CHECK(f.c.tx.offset==0u);
    CHECK(frames(134,6,0)==2u && frames(129,-1,-1)==2u && frames(134,5,3)==0u);
    trace("fresh-hello-completes-old-partial-status-before-ack",MT_CONTROL_OK);++scenarios;
    /* Mechanism probe only: not a proposed or applied production patch. */
    setup(100);capture(100,1);memset(&m,0,sizeof m);m.type=134;m.payload_length=24;m.payload[0]=5;m.payload[1]=3;
    put32(m.payload+4,f.c.link.generation);put32(m.payload+8,f.c.link.active_window);
    CHECK(mt_tx_enqueue(&f.c.tx,&m,5100)==MT_TX_OK);bytes=f.used;CHECK(mt_control_recover(&f.c)==MT_CONTROL_RECOVERY);
    r=poll_at(5100,64);CHECK(r==MT_CONTROL_IGNORED && f.used==bytes && f.c.tx.count==1u);
    trace("mechanism-queued-timeout-status-stranded-by-recover",r);
    CHECK(hello(12,5101)==MT_CONTROL_OK);drain_at(5101);
    CHECK(frames(134,5,3)==0u && f.c.tx.count==0u);trace("mechanism-fresh-hello-discards-unstarted-status",MT_CONTROL_OK);++scenarios;
}
int main(void) {
    timeout_boundary(100,"normal-4999");timeout_boundary(UINT32_MAX-2499u,"wrap-4999");
    release_boundary();stop_and_hello_boundary();backpressured_end();partial_reply_and_enqueue_recover();
    printf("PASS: %u bounded characterization scenarios; no production source edits.\n",scenarios);return 0;
}
