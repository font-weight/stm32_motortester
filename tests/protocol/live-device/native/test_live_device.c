#include "mt_live_device.h"
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Synthetic adapter; the code under test remains the real endpoint/codec/queue. */
enum { HELLO=1, CAPS=2, STOP=5, START=8, HELLO_ACK=129, CAPS_REPLY=130,
       STOP_ACK=133, START_ACK=136, DATA=152, END=153, ERROR_REPLY=255 };
#define RESERVE UINT32_C(0xffffff00)
#define NONCE UINT64_C(0x1020304050607080)
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL %s:%d: %s\n", __func__, __LINE__, #x); return 0; } } while (0)
#define CHECK_EQ(x,y) CHECK((x)==(y))

typedef struct {
    mt_live_device d;
    uint64_t time;
    size_t write_cap;
    unsigned write_mode, write_count, start_count, quiesce_count, isolate_count;
    uint64_t write_advance, quiesce_advance, isolate_advance, start_advance;
    unsigned quiesce_advance_at;
    unsigned clock_count, jump_at;
    uint64_t jump_to;
    int start_result, isolate_result;
    mt_live_snapshot snapshot;
    uint8_t output[40000];
    size_t output_length;
    uint64_t last_write_begin, last_write_end;
    uint32_t start_generation;
} fixture;

typedef struct { mt_wire_message m[128]; size_t n, partial, invalid, delimiters; } decoded;
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static uint32_t r32(const uint8_t *p) { return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static uint64_t r64(const uint8_t *p) { return r32(p)|((uint64_t)r32(p+4)<<32); }
static void w16(uint8_t *p,uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void w32(uint8_t *p,uint32_t v) { unsigned i; for(i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i)); }
static void w64(uint8_t *p,uint64_t v) { unsigned i; for(i=0;i<8;i++)p[i]=(uint8_t)(v>>(8*i)); }
static uint64_t clock_cb(void *v) { fixture *f=v; ++f->clock_count; if(f->jump_at && f->clock_count==f->jump_at)f->time=f->jump_to; return f->time; }
static int start_cb(void *v,uint32_t generation) { fixture *f=v; ++f->start_count; f->start_generation=generation; f->time+=f->start_advance; return f->start_result; }
static void quiesce_cb(void *v,mt_live_snapshot *s) { fixture *f=v; ++f->quiesce_count; *s=f->snapshot; if(!f->quiesce_advance_at || f->quiesce_count==f->quiesce_advance_at)f->time+=f->quiesce_advance; }
static int isolate_cb(void *v) { fixture *f=v; ++f->isolate_count; f->time+=f->isolate_advance; return f->isolate_result; }
static size_t write_cb(void *v,const uint8_t *p,size_t n) {
    fixture *f=v; size_t accepted=n;
    ++f->write_count; f->last_write_begin=f->time;
    if(accepted>f->write_cap)accepted=f->write_cap;
    if(f->output_length+accepted>sizeof f->output)abort();
    memcpy(f->output+f->output_length,p,accepted); f->output_length+=accepted;
    f->time+=f->write_advance; f->last_write_end=f->time;
    if(f->write_mode==1u)return SIZE_MAX;
    if(f->write_mode==2u)return n+1u;
    return accepted;
}
static int init(fixture *f) {
    mt_live_io io;
    memset(f,0,sizeof *f); memset(&io,0,sizeof io);
    f->write_cap=64; f->start_result=1; f->isolate_result=1;
    f->snapshot.hardware_off=1; f->snapshot.produced_known=1;
    io.context=f; io.clock_ms=clock_cb; io.start=start_cb; io.quiesce=quiesce_cb;
    io.write=write_cb; io.isolate_tx=isolate_cb; io.transaction_bound_ms=1;
    io.firmware_build=UINT32_C(0x12345678);
    CHECK(mt_live_device_init(&f->d,&io)); CHECK_EQ(f->quiesce_count,0u);
    return 1;
}
static int encode(uint8_t type,uint64_t nonce,uint32_t seq,uint32_t req,const uint8_t *p,uint16_t n,uint8_t *frame,size_t *length) {
    mt_wire_message m; memset(&m,0,sizeof m); m.type=type; m.session=nonce; m.sequence=seq; m.request=req; m.payload_length=n;
    if(n)memcpy(m.payload,p,n);
    CHECK_EQ(mt_wire_encode_revision(&m,0,2,frame,MT_WIRE_FRAME_MAX,length),MT_WIRE_OK); return 1;
}
static int request(fixture *f,uint8_t type,uint64_t nonce,uint32_t seq,uint32_t req,const uint8_t *p,uint16_t n) {
    uint8_t frame[MT_WIRE_FRAME_MAX]; size_t length;
    CHECK(encode(type,nonce,seq,req,p,n,frame,&length));
    CHECK(mt_live_device_receive(&f->d,frame,length)); return 1;
}
static int hello(fixture *f,uint64_t nonce) { uint8_t p[8]; w64(p,nonce); return request(f,HELLO,0,1,1,p,8); }
static int stop(fixture *f,uint32_t seq,uint32_t req) { uint8_t p[4]; w32(p,f->d.generation); return request(f,STOP,f->d.nonce,seq,req,p,4); }
static int decode(const fixture *f,decoded *out) {
    size_t start=0,i; memset(out,0,sizeof *out);
    for(i=0;i<f->output_length;i++)if(!f->output[i]) {
        ++out->delimiters;
        if(i>start) {
            CHECK(out->n<sizeof out->m/sizeof out->m[0]);
            if(mt_wire_decode_revision(f->output+start,i-start,0,2,&out->m[out->n])==MT_WIRE_OK)++out->n;
            else ++out->invalid;
        }
        start=i+1;
    }
    out->partial=f->output_length-start; return 1;
}
static int pump(fixture *f) {
    unsigned i;
    for(i=0;i<2000;i++) {
        CHECK(mt_live_device_step(&f->d));
        if(!f->d.tx_length && !f->d.want_caps && !f->d.want_start && !f->d.want_error && !f->d.want_end && !f->d.want_ack && f->d.state!=MT_LIVE_HELLO_PENDING && (f->d.state!=MT_LIVE_DRAINING || !f->d.queue.used))return 1;
    }
    CHECK(0); return 0;
}
static int ready(fixture *f) {
    CHECK(init(f)); CHECK(hello(f,NONCE)); CHECK(pump(f)); CHECK_EQ(f->d.state,MT_LIVE_STOPPED);
    CHECK(request(f,CAPS,NONCE,2,2,NULL,0)); CHECK(pump(f)); CHECK_EQ(f->d.caps_ready,1u); return 1;
}
static int begin(fixture *f,int finish_ack) {
    uint8_t p[4]={1,0,0,0}; CHECK(ready(f)); CHECK(request(f,START,NONCE,3,3,p,4));
    CHECK_EQ(f->d.state,MT_LIVE_STARTING); CHECK_EQ(f->start_count,1u); CHECK_EQ(f->start_generation,3u);
    if(finish_ack) { CHECK(pump(f)); CHECK_EQ(f->d.state,MT_LIVE_STREAMING); }
    return 1;
}
static void codes(uint16_t *p,unsigned n,unsigned seed) { unsigned i; for(i=0;i<n;i++)p[i]=(uint16_t)((seed+i*37u)&4095u); }
static int offer(fixture *f,uint64_t index) { uint16_t p[128]; codes(p,128,(unsigned)index); CHECK(mt_live_device_offer(&f->d,index,p,128)); return 1; }
static const mt_wire_message *find_type(const decoded *out,uint8_t type,unsigned ordinal) {
    size_t i; for(i=0;i<out->n;i++)if(out->m[i].type==type) { if(!ordinal)return &out->m[i]; --ordinal; } return NULL;
}
static int end_check(fixture *f,uint64_t prefix,uint64_t produced,unsigned reason,unsigned flags,uint32_t stop_id,unsigned data_count,unsigned ack_count) {
    decoded o; const mt_wire_message *e,*a; size_t i; unsigned nd=0,na=0; int seen_end=0;
    CHECK(decode(f,&o)); CHECK_EQ(o.invalid,0u); CHECK_EQ(o.partial,0u);
    e=find_type(&o,END,0); CHECK(e); CHECK(!find_type(&o,END,1));
    CHECK_EQ(e->request,0u); CHECK_EQ(e->payload_length,40u); CHECK_EQ(r32(e->payload),3u); CHECK_EQ(r32(e->payload+4),1u);
    CHECK_EQ(r64(e->payload+8),prefix); CHECK_EQ(r64(e->payload+16),produced);
    CHECK_EQ(r16(e->payload+32),reason); CHECK_EQ(r16(e->payload+34),flags); CHECK_EQ(r32(e->payload+36),stop_id);
    for(i=0;i<o.n;i++) {
        if(o.m[i].type==DATA) { CHECK(!seen_end); ++nd; }
        if(o.m[i].type==END)seen_end=1;
        if(o.m[i].type==STOP_ACK) { CHECK(seen_end); ++na; }
    }
    CHECK_EQ(nd,data_count); CHECK_EQ(na,ack_count);
    if(ack_count) { a=find_type(&o,STOP_ACK,0); CHECK(a); CHECK_EQ(a->request,f->d.stop_request); CHECK_EQ(r64(a->payload+4),prefix); CHECK_EQ(r32(a->payload+12),e->sequence); CHECK_EQ(r32(a->payload+16),flags); }
    return 1;
}

static int test_handshake_and_empty_stop(void) {
    fixture f; decoded o; const mt_wire_message *h,*c; CHECK(begin(&f,1)); CHECK(decode(&f,&o)); CHECK_EQ(o.n,3u);
    h=&o.m[0]; c=&o.m[1]; CHECK_EQ(h->type,HELLO_ACK); CHECK_EQ(h->sequence,1u); CHECK_EQ(h->session,NONCE); CHECK_EQ(h->payload_length,16u); CHECK_EQ(h->payload[0],0u); CHECK_EQ(h->payload[1],2u); CHECK_EQ(h->payload[2],1u); CHECK_EQ(h->payload[3],12u); CHECK_EQ(r16(h->payload+4),512u); CHECK_EQ(r16(h->payload+6),128u); CHECK_EQ(r32(h->payload+8),UINT32_C(0x12345678)); CHECK_EQ(r32(h->payload+12),1u);
    CHECK_EQ(c->type,CAPS_REPLY); CHECK_EQ(c->sequence,2u); CHECK_EQ(c->payload_length,24u); CHECK_EQ(r32(c->payload),1u); CHECK_EQ(r16(c->payload+4),1u); CHECK_EQ(c->payload[6],0u); CHECK_EQ(c->payload[7],12u); CHECK_EQ(r32(c->payload+8),50000u); CHECK_EQ(r32(c->payload+12),1u); CHECK_EQ(r16(c->payload+16),128u); CHECK_EQ(r16(c->payload+18),0u); CHECK_EQ(r32(c->payload+20),1u);
    CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,0,0,0,0,4,0,1)); CHECK_EQ(f.d.state,MT_LIVE_NEEDS_HELLO);
    CHECK(decode(&f,&o)); CHECK_EQ(r32(find_type(&o,END,0)->payload+28),0u); return 1;
}
static int test_stop_before_start_ack(void) { fixture f; decoded o; CHECK(begin(&f,0)); CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.m[2].type,START_ACK); CHECK_EQ(o.m[3].type,END); CHECK(end_check(&f,0,0,0,0,4,0,1)); return 1; }
static int test_stop_during_start_ack(void) { fixture f; decoded o; CHECK(begin(&f,0)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK(f.d.tx_offset>0); CHECK_EQ(f.d.tx_type,START_ACK); CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.m[2].type,START_ACK); CHECK(end_check(&f,0,0,0,0,4,0,1)); return 1; }
static int test_full_and_short_tail_crc(void) {
    fixture f; decoded o; uint8_t raw[2*145]; uint16_t p[128]; unsigned i;
    CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.produced_end=145; f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=128; f.snapshot.tail[0].count=17; codes(f.snapshot.tail[0].codes,17,128);
    CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,145,145,0,0,4,2,1)); CHECK(decode(&f,&o));
    codes(p,128,0); for(i=0;i<128;i++)w16(raw+2*i,p[i]); for(i=0;i<17;i++)w16(raw+2*(i+128),f.snapshot.tail[0].codes[i]);
    CHECK_EQ(r32(find_type(&o,END,0)->payload+28),mt_wire_crc32(raw,sizeof raw)); CHECK_EQ(r32(find_type(&o,END,0)->payload+24),find_type(&o,DATA,1)->sequence); return 1;
}
static int test_queue_overrun_known(void) {
    fixture f; uint16_t p[128]; unsigned i; CHECK(begin(&f,1)); for(i=0;i<16;i++)CHECK(offer(&f,128u*i));
    f.snapshot.produced_end=2176; codes(p,128,2048); CHECK(!mt_live_device_offer(&f.d,2048,p,128)); CHECK_EQ(f.d.first_reason,1u); CHECK_EQ(f.d.state,MT_LIVE_DRAINING); CHECK(pump(&f)); CHECK(end_check(&f,2048,2176,1,2,0,16,0)); return 1;
}
static int test_queue_overrun_unknown(void) {
    fixture f; uint16_t p[128]; unsigned i; CHECK(begin(&f,1)); for(i=0;i<16;i++)CHECK(offer(&f,128u*i)); f.snapshot.produced_known=0; f.snapshot.produced_end=0; codes(p,128,0);
    CHECK(!mt_live_device_offer(&f.d,2048,p,128)); CHECK(pump(&f)); CHECK(end_check(&f,2048,0,1,3,0,16,0)); return 1;
}
static int test_snapshot_atomic_invalid_second(void) {
    fixture f; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.produced_end=384; f.snapshot.tail_count=2;
    f.snapshot.tail[0].first_index=128; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,128);
    f.snapshot.tail[1].first_index=256; f.snapshot.tail[1].count=128; codes(f.snapshot.tail[1].codes,128,256); f.snapshot.tail[1].codes[127]=4096;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.queue.next_index,128u); CHECK(pump(&f)); CHECK(end_check(&f,128,0,2,3,4,1,1)); return 1;
}
static int test_snapshot_known_gap(void) { fixture f; CHECK(begin(&f,1)); f.snapshot.produced_end=23; CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,0,23,0,2,4,0,1)); return 1; }
static int test_snapshot_unknown(void) { fixture f; CHECK(begin(&f,1)); f.snapshot.produced_known=0; CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,0,0,0,3,4,0,1)); return 1; }
static int test_first_fault_and_failed_quiescence(void) {
    fixture f; CHECK(begin(&f,1)); f.snapshot.hardware_off=0; f.snapshot.acquisition_reason=2;
    mt_live_device_fault(&f.d,1); CHECK_EQ(f.d.first_reason,1u); mt_live_device_fault(&f.d,6); CHECK_EQ(f.d.first_reason,1u);
    CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,0,0,1,7,4,0,0)); CHECK_EQ(f.d.hardware_off,0u);
    CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.hardware_off,0u); CHECK_EQ(f.d.nonce,NONCE); return 1;
}
static int test_late_stop_before_end(void) { fixture f; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,0,4,0,1)); return 1; }
static int test_late_stop_during_end(void) {
    fixture f; uint64_t deadline; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); deadline=f.d.stop_deadline;
    f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_type,END); CHECK(f.d.tx_offset); f.time=10;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.stop_deadline,deadline); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,0,0,0,1)); return 1;
}
static int test_late_stop_after_end(void) {
    fixture f; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); CHECK(pump(&f)); CHECK_EQ(f.d.stop_budget,0u); f.time=1200;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.stop_deadline,1700u); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,0,0,0,1)); return 1;
}
static int test_zero_write_freezes_sequence(void) { fixture f; unsigned i; CHECK(begin(&f,0)); f.write_cap=0; for(i=0;i<10;i++)CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_next,3u); CHECK_EQ(f.d.tx_offset,0u); f.write_cap=1; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_next,4u); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STREAMING); return 1; }
static int test_unknown_write_requires_resync(void) {
    fixture f; decoded o; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.write_cap=5; f.write_mode=1;
    CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.needs_delimiter,1u); CHECK_EQ(f.d.hardware_off,1u);
    f.write_cap=64; f.write_mode=0; CHECK(hello(&f,NONCE+1)); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK(decode(&f,&o)); CHECK_EQ(o.invalid,1u); CHECK_EQ(o.partial,0u); CHECK_EQ(o.m[o.n-1].type,HELLO_ACK); CHECK_EQ(o.m[o.n-1].session,NONCE+1); return 1;
}
static int test_partial_write_then_detach(void) { fixture f; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.write_cap=9; CHECK(mt_live_device_step(&f.d)); mt_live_device_transport_lost(&f.d); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.tx_length,0u); CHECK_EQ(f.d.needs_delimiter,1u); CHECK_EQ(f.d.hardware_off,1u); CHECK(!mt_live_device_offer(&f.d,128,(uint16_t[128]){0},128)); return 1; }
static int test_duplicate_and_old_nonce_history(void) {
    fixture f; uint8_t p[4]={1,0,0,0}; unsigned q; CHECK(begin(&f,0)); q=f.quiesce_count;
    CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.start_count,1u); CHECK_EQ(f.quiesce_count,q);
    CHECK(request(&f,CAPS,NONCE-1,99,99,NULL,0)); CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.start_count,1u); CHECK_EQ(f.d.state,MT_LIVE_STARTING); CHECK(pump(&f)); return 1;
}
static int test_reordered_command_fatal(void) { fixture f; CHECK(begin(&f,1)); CHECK(request(&f,CAPS,NONCE,2,2,NULL,0)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.hardware_off,1u); return 1; }
static int test_conflicting_duplicate_fatal(void) { fixture f; uint8_t p[4]={1,0,1,0}; CHECK(begin(&f,1)); CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1; }
static int test_unknown_active_old_nonce_fatal(void) { fixture f; CHECK(begin(&f,1)); CHECK(request(&f,77,NONCE-1,42,42,NULL,0)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1; }
static int test_wrong_direction_old_nonce_fatal(void) { fixture f; CHECK(ready(&f)); CHECK(request(&f,DATA,NONCE-1,42,42,NULL,0)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1; }
static int test_stop_duplicate_no_new_ack(void) { fixture f; size_t length; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); CHECK(stop(&f,4,4)); CHECK(pump(&f)); length=f.output_length; CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK_EQ(f.output_length,length); CHECK(end_check(&f,0,0,0,0,4,0,1)); return 1; }
static int test_second_distinct_stop_fatal(void) { fixture f; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); CHECK(stop(&f,5,5)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1; }
static int test_start_failure_reserved_error_only(void) {
    fixture f; uint8_t p[4]={1,0,0,0}; decoded o; CHECK(ready(&f)); f.start_result=0; CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.d.state,MT_LIVE_NEEDS_HELLO); CHECK(stop(&f,4,4)); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.n,3u); CHECK_EQ(o.m[2].type,ERROR_REPLY); CHECK_EQ(o.m[2].payload[1],4u); CHECK_EQ(r32(o.m[2].payload+4),3u); CHECK(!find_type(&o,START_ACK,0)); return 1;
}
static int test_fresh_hello_barrier_and_nonce_retry(void) {
    fixture f; unsigned q; CHECK(init(&f)); f.snapshot.hardware_off=0; CHECK(hello(&f,NONCE)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.nonce,0u); q=f.quiesce_count;
    f.snapshot.hardware_off=1; CHECK(hello(&f,NONCE)); CHECK_EQ(f.quiesce_count,q); CHECK_EQ(f.d.state,MT_LIVE_FAULT);
    CHECK(hello(&f,NONCE+1)); CHECK(pump(&f)); CHECK_EQ(f.d.nonce,NONCE+1); return 1;
}
static int test_hello_isolation_failure(void) { fixture f; CHECK(init(&f)); f.isolate_result=0; CHECK(hello(&f,NONCE)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,0u); CHECK_EQ(f.d.nonce,0u); return 1; }
static int test_hello_deadline_boundaries(void) {
    unsigned delta; for(delta=499;delta<=501;delta++) { fixture f; CHECK(init(&f)); f.quiesce_advance=delta; CHECK(hello(&f,NONCE)); CHECK_EQ(f.d.hello_deadline,500u);
        (void)mt_live_device_step(&f.d); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,0u); CHECK_EQ(f.d.nonce,0u); }
    { fixture f; CHECK(init(&f)); f.quiesce_advance=498; CHECK(hello(&f,NONCE)); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); }
    return 1;
}
static int test_stop_deadline_boundaries(void) {
    unsigned delta; for(delta=499;delta<=501;delta++) { fixture f; size_t before; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); before=f.output_length; f.time=delta;
        CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.stop_deadline,500u); }
    { fixture f; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); f.time=498; CHECK(pump(&f)); CHECK(end_check(&f,0,0,0,0,4,0,1)); }
    return 1;
}
static int test_barrier_time_included_in_shutdown(void) { fixture f; size_t before; CHECK(begin(&f,1)); before=f.output_length; f.quiesce_advance=500; CHECK(stop(&f,4,4)); CHECK_EQ(f.d.stop_deadline,500u); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,before); return 1; }
static int test_whole_transaction_guard(void) {
    unsigned now; for(now=492;now<=494;now++) { fixture f; size_t before; CHECK(begin(&f,1)); f.d.io.transaction_bound_ms=7; CHECK(stop(&f,4,4)); before=f.output_length; f.time=now;
        if(now==492) { CHECK(pump(&f)); CHECK(end_check(&f,0,0,0,0,4,0,1)); }
        else { CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.state,MT_LIVE_FAULT); } }
    return 1;
}
static int test_inherited_hello_deadline(void) {
    fixture f; size_t before; CHECK(init(&f)); CHECK(hello(&f,NONCE)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK(f.d.tx_offset); f.time=400; CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.hello_deadline,900u); CHECK_EQ(f.d.inherited_deadline,500u); CHECK_EQ(f.d.inherited_budget,1u);
    before=f.output_length; f.time=499; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.nonce,0u);
    f.write_cap=64; f.time=501; CHECK(hello(&f,NONCE+2)); CHECK(pump(&f)); CHECK_EQ(f.d.nonce,NONCE+2); return 1;
}
static int test_inherited_stop_deadline(void) {
    fixture f; size_t before; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_type,END); f.time=400; CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.inherited_deadline,500u); CHECK_EQ(f.d.hello_deadline,900u);
    before=f.output_length; f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_replacement_hello_finishes_old_frame(void) {
    fixture f; decoded o; CHECK(init(&f)); CHECK(hello(&f,NONCE)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); f.time=20; CHECK(hello(&f,NONCE+1)); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.n,2u); CHECK_EQ(o.invalid,0u); CHECK_EQ(o.m[0].session,NONCE); CHECK_EQ(o.m[1].session,NONCE+1); CHECK_EQ(f.d.nonce,NONCE+1); return 1;
}
static int test_counter_reserve_tx(void) {
    fixture f; decoded o; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.produced_end=128;
    /* White-box setup targets the otherwise billions-of-frames counter boundary. */
    f.d.tx_next=RESERVE; CHECK(pump(&f)); CHECK(end_check(&f,128,128,4,0,0,1,0)); CHECK(decode(&f,&o)); CHECK_EQ(find_type(&o,DATA,0)->sequence,RESERVE); CHECK_EQ(find_type(&o,END,0)->sequence,RESERVE+1u); return 1;
}
static int test_counter_reserve_request(void) { fixture f; CHECK(begin(&f,1)); CHECK(request(&f,CAPS,NONCE,4,RESERVE,NULL,0)); CHECK_EQ(f.d.first_reason,4u); CHECK_EQ(f.d.state,MT_LIVE_DRAINING); CHECK(pump(&f)); CHECK(end_check(&f,0,0,4,0,0,0,0)); return 1; }
static int test_maximum_stop_request_and_sequence(void) { fixture f; CHECK(begin(&f,1)); f.d.rx_next=UINT32_MAX; CHECK(stop(&f,UINT32_MAX,UINT32_MAX)); CHECK_EQ(f.d.rx_exhausted,1u); CHECK_EQ(f.d.last_request,UINT32_MAX); CHECK(pump(&f)); CHECK(end_check(&f,0,0,0,0,UINT32_MAX,0,1)); return 1; }
static int test_clock_regression_and_overflow(void) {
    fixture f; CHECK(begin(&f,1)); f.time=10; CHECK(mt_live_device_step(&f.d)); f.time=9; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.first_reason,3u); CHECK_EQ(f.d.hardware_off,1u);
    CHECK(init(&f)); f.time=(uint64_t)INT64_MAX-499u; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.clock_failed,1u); return 1;
}
static int test_late_known_acceptance_resync(void) {
    fixture f; decoded o; CHECK(init(&f)); CHECK(hello(&f,NONCE)); f.write_cap=7; f.write_advance=500;
    CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,7u);
    /* Deliberately broken adapter bound: accepted bytes must still be tracked so recovery cannot splice frames. */
    CHECK_EQ(f.d.needs_delimiter,1u);
    f.write_cap=64; f.write_advance=0; CHECK(hello(&f,NONCE+1)); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.invalid,1u); CHECK_EQ(o.n,1u); CHECK_EQ(o.m[0].type,HELLO_ACK); return 1;
}
static int test_postwrite_clock_failure_resync(void) {
    fixture f; CHECK(init(&f)); f.time=10; CHECK(hello(&f,NONCE)); f.write_cap=7;
    f.jump_at=f.clock_count+4; f.jump_to=9; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.output_length,7u); CHECK_EQ(f.d.needs_delimiter,1u); return 1;
}
static int test_malformed_commands_do_not_execute(void) {
    fixture f; uint8_t frame[MT_WIRE_FRAME_MAX],p[4]={1,0,0,0}; size_t length; CHECK(ready(&f)); CHECK(encode(START,NONCE,3,3,p,4,frame,&length)); frame[length-3]^=2;
    CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.start_count,0u); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK_EQ(f.d.rx_next,3u); return 1;
}


static int test_equivalent_cobs_is_not_duplicate(void) {
    fixture f; uint8_t p[250],frame[MT_WIRE_FRAME_MAX],alt[MT_WIRE_FRAME_MAX]; size_t length; mt_wire_message a,b; unsigned seed;
    CHECK(ready(&f));
    /* A 254-byte nonzero suffix permits equivalent COBS ending FF/data/01 or FF/data. */
    for(seed=1;seed<255;seed++) { memset(p,(int)seed,sizeof p); CHECK(encode(77,NONCE,3,3,p,250,frame,&length)); if(length>=2 && frame[length-2]==1 && length>256) { memcpy(alt,frame,length); alt[length-2]=0; if(mt_wire_decode_revision(alt,length-2,0,2,&b)==MT_WIRE_OK)break; } }
    CHECK(seed<255); CHECK_EQ(mt_wire_decode_revision(frame,length-1,0,2,&a),MT_WIRE_OK); CHECK_EQ(a.type,b.type); CHECK_EQ(a.payload_length,b.payload_length); CHECK_EQ(memcmp(a.payload,b.payload,a.payload_length),0);
    CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED);
    CHECK(mt_live_device_receive(&f.d,alt,length-1)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_snapshot_invalid_matrix(void) {
    unsigned which; for(which=0;which<10;which++) { fixture f; CHECK(begin(&f,1)); f.snapshot.produced_end=17; f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=0; f.snapshot.tail[0].count=17; codes(f.snapshot.tail[0].codes,17,0);
        switch(which) {
            case 0:f.snapshot.hardware_off=2;break;
            case 1:f.snapshot.produced_known=2;break;
            case 2:f.snapshot.tail_count=3;break;
            case 3:f.snapshot.acquisition_reason=7;break;
            case 4:f.snapshot.tail[0].count=0;break;
            case 5:f.snapshot.tail[0].count=129;break;
            case 6:f.snapshot.tail[0].first_index=1;break;
            case 7:f.snapshot.produced_end=16;break;
            case 8:f.snapshot.produced_known=0;break;
            case 9:f.snapshot.tail_count=2;f.snapshot.tail[1].first_index=17;f.snapshot.tail[1].count=1;f.snapshot.produced_end=18;break;
            default:abort();
        }
        CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.used,0u); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,which==0?7u:3u,4,0,which==0?0u:1u));
    } return 1;
}
static int test_full_snapshot_overflows_queue_without_rewriting_prefix(void) {
    fixture f; unsigned i; CHECK(begin(&f,1)); for(i=0;i<16;i++)CHECK(offer(&f,128u*i));
    f.snapshot.produced_end=2176; f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=2048; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,2048);
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.first_reason,1u); CHECK_EQ(f.d.queue.next_index,2048u); CHECK(pump(&f)); CHECK(end_check(&f,2048,2176,1,2,4,16,1)); return 1;
}
static int test_invalid_offered_code_and_index(void) {
    unsigned which; for(which=0;which<4;which++) { fixture f; uint16_t p[128]; CHECK(begin(&f,1)); CHECK(offer(&f,0)); codes(p,128,128); f.snapshot.produced_known=0;
        if(which==0)p[127]=4096;
        CHECK(!mt_live_device_offer(&f.d,which==1?129u:128u,which==2?NULL:p,which==3?127u:128u));
        CHECK_EQ(f.d.first_reason,2u); CHECK(pump(&f)); CHECK(end_check(&f,128,0,2,3,0,1,0));
    } return 1;
}
static int test_unstarted_end_refreezes_for_stop(void) {
    fixture f; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); f.write_cap=0; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_type,END); CHECK_EQ(f.d.tx_offset,0u); CHECK_EQ(f.d.end_started,0u);
    CHECK(stop(&f,4,4)); f.write_cap=64; CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,0,4,0,1)); return 1;
}
static int test_late_stop_timer_expires_before_ack(void) {
    unsigned delta; for(delta=499;delta<=501;delta++) { fixture f; size_t before; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); CHECK(pump(&f)); f.time=1000; CHECK(stop(&f,4,4)); CHECK_EQ(f.d.stop_deadline,1500u); before=f.output_length; f.time=1000u+delta; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.end_complete,1u); CHECK_EQ(f.d.first_reason,2u); }
    return 1;
}
static int test_zero_trickle_progress_never_extends_deadline(void) {
    fixture f; unsigned i; uint64_t deadline; CHECK(begin(&f,1)); CHECK(stop(&f,4,4)); deadline=f.d.stop_deadline; f.write_cap=1;
    for(i=0;i<5;i++) { f.time=i*100u; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.stop_deadline,deadline); }
    f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_offset,0u); CHECK_EQ(f.d.needs_delimiter,1u); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_hello_supersedes_not_started_output_without_gap(void) {
    fixture f; decoded o; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.write_cap=0; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_next,4u); CHECK_EQ(f.d.tx_offset,0u); CHECK(hello(&f,NONCE+1)); f.write_cap=64; CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.n,4u); CHECK_EQ(o.m[3].type,HELLO_ACK); CHECK_EQ(o.m[3].sequence,1u); CHECK_EQ(o.m[3].session,NONCE+1); CHECK_EQ(f.d.queue.used,0u); return 1;
}
static int test_tx_maximum_never_wraps(void) {
    fixture f; decoded o; CHECK(begin(&f,1)); f.d.tx_next=UINT32_MAX; CHECK(stop(&f,4,4));
    CHECK(mt_live_device_step(&f.d)); CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.end_complete,1u); CHECK_EQ(f.d.tx_exhausted,1u); CHECK_EQ(f.d.tx_next,UINT32_MAX); CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK(decode(&f,&o)); CHECK_EQ(find_type(&o,END,0)->sequence,UINT32_MAX); CHECK(!find_type(&o,STOP_ACK,0)); return 1;
}
static int test_start_refused_without_tx_reserve(void) {
    fixture f; uint8_t p[4]={1,0,0,0}; CHECK(ready(&f)); f.d.tx_next=RESERVE; CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.start_count,0u); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_detach_failed_barrier_preserves_uncertainty(void) {
    fixture f; unsigned q; CHECK(begin(&f,1)); f.snapshot.hardware_off=0; q=f.quiesce_count; mt_live_device_transport_lost(&f.d); CHECK_EQ(f.quiesce_count,q+1); CHECK_EQ(f.d.hardware_off,0u); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.needs_delimiter,1u); CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_oversized_write_result_is_unknown(void) {
    fixture f; CHECK(begin(&f,0)); f.write_mode=2; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.needs_delimiter,1u); CHECK_EQ(f.d.last_write_known,0u); return 1;
}
static int test_postcommit_deadline_failure_cannot_publish_clean_state(void) {
    fixture f; CHECK(init(&f)); CHECK(hello(&f,NONCE)); f.jump_at=f.clock_count+5; f.jump_to=500;
    CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.local_error,6u); CHECK_EQ(f.d.hardware_off,1u); return 1;
}


static int test_failed_start_cleanup_deadline(void) {
    unsigned delay; for(delay=498;delay<=501;delay++) { fixture f; uint8_t p[4]={1,0,0,0}; decoded o; CHECK(ready(&f)); f.start_result=0; f.quiesce_advance_at=f.quiesce_count+2; f.quiesce_advance=delay;
        CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.d.stop_deadline,500u);
        if(delay==498) { CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_NEEDS_HELLO); CHECK_EQ(f.d.stop_budget,0u); CHECK(decode(&f,&o)); CHECK_EQ(o.n,3u); CHECK_EQ(o.m[2].type,ERROR_REPLY); CHECK_EQ(o.m[2].payload[1],4u); }
        else { (void)mt_live_device_step(&f.d); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK(decode(&f,&o)); CHECK_EQ(o.n,2u); CHECK_EQ(f.d.want_error,0u); }
    } return 1;
}
static int test_failed_start_error_inherits_deadline_into_hello(void) {
    fixture f; uint8_t p[4]={1,0,0,0}; size_t before; CHECK(ready(&f)); f.start_result=0; CHECK(request(&f,START,NONCE,3,3,p,4)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.tx_type,ERROR_REPLY); f.time=400; CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.inherited_deadline,500u); before=f.output_length; f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.state,MT_LIVE_FAULT); return 1;
}
static int test_rx_fragmentation_and_discard_recovery(void) {
    size_t split; for(split=1;split<38;split++) { fixture f; uint8_t p[8],frame[MT_WIRE_FRAME_MAX]; size_t length; CHECK(init(&f)); w64(p,NONCE); CHECK(encode(HELLO,0,1,1,p,8,frame,&length)); CHECK(split<length); CHECK(mt_live_device_receive(&f.d,frame,split)); CHECK_EQ(f.d.state,MT_LIVE_NO_SESSION); CHECK_EQ(f.quiesce_count,0u); CHECK(mt_live_device_receive(&f.d,frame+split,length-split)); CHECK(pump(&f)); CHECK_EQ(f.d.nonce,NONCE); }
    { fixture f; uint8_t bad[MT_WIRE_FRAME_MAX]; memset(bad,1,sizeof bad); CHECK(init(&f)); CHECK(mt_live_device_receive(&f.d,bad,sizeof bad)); CHECK_EQ(f.d.rx_discarding,1u); CHECK_EQ(f.d.state,MT_LIVE_NO_SESSION); CHECK(mt_live_device_receive(&f.d,(uint8_t[1]){0},1)); CHECK(hello(&f,NONCE)); CHECK(pump(&f)); CHECK_EQ(f.d.nonce,NONCE); }
    return 1;
}
static int test_u64_sample_end_boundaries(void) {
    fixture f; uint16_t p[128]; CHECK(begin(&f,1)); codes(p,128,0);
    /* White-box counter injection isolates checked arithmetic; it does not assert a valid zero-origin wire recording. */
    mt_sample_queue_init(&f.d.queue,UINT64_MAX-128u); CHECK(mt_live_device_offer(&f.d,UINT64_MAX-128u,p,128)); CHECK_EQ(f.d.queue.next_index,UINT64_MAX); f.snapshot.produced_known=0;
    CHECK(!mt_live_device_offer(&f.d,UINT64_MAX,p,128)); CHECK_EQ(f.d.queue.next_index,UINT64_MAX); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.state,MT_LIVE_DRAINING); return 1;
}
static int test_block_arrival_after_shutdown_refused(void) {
    fixture f; uint16_t p[128]; size_t used; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.produced_end=128; CHECK(stop(&f,4,4)); used=f.d.queue.used; codes(p,128,128); CHECK(!mt_live_device_offer(&f.d,128,p,128)); CHECK_EQ(f.d.queue.used,used); CHECK_EQ(f.d.first_reason,0u); CHECK(pump(&f)); CHECK(end_check(&f,128,128,0,0,4,1,1)); return 1;
}
static int test_invalid_start_payload_is_not_admitted(void) {
    fixture f; uint8_t p[4]={1,0,1,0}; decoded o; CHECK(ready(&f)); CHECK(request(&f,START,NONCE,3,3,p,4)); CHECK_EQ(f.start_count,0u); CHECK_EQ(f.d.generation,0u); CHECK(pump(&f)); CHECK(decode(&f,&o)); CHECK_EQ(o.m[2].type,ERROR_REPLY); CHECK_EQ(o.m[2].payload[1],1u); CHECK_EQ(r32(o.m[2].payload+4),0u); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); return 1;
}
static int test_init_invalid_callback_contract(void) {
    fixture f; mt_live_device d; mt_live_io io; CHECK(init(&f)); io=f.d.io; io.transaction_bound_ms=0; CHECK(!mt_live_device_init(&d,&io)); io.transaction_bound_ms=500; CHECK(!mt_live_device_init(&d,&io)); io=f.d.io; io.isolate_tx=NULL; CHECK(!mt_live_device_init(&d,&io)); io=f.d.io; io.quiesce=NULL; CHECK(!mt_live_device_init(&d,&io)); CHECK(!mt_live_device_init(NULL,&f.d.io)); CHECK(!mt_live_device_init(&d,NULL)); return 1;
}


static int failed_recovery_commit_preserves_old_evidence(unsigned which) {
    {
        fixture f; mt_sample_queue old_queue; unsigned old_local; uint16_t old_reason; uint32_t old_crc,old_last_data;
        CHECK(begin(&f,1)); CHECK(offer(&f,0)); CHECK(pump(&f)); CHECK_EQ(f.d.prefix_end,128u);
        CHECK(offer(&f,128)); f.snapshot.produced_end=256; mt_live_device_fault(&f.d,2); f.time=500; CHECK(!mt_live_device_step(&f.d));
        old_queue=f.d.queue; old_local=f.d.local_error; old_reason=f.d.first_reason; old_crc=f.d.prefix_crc_state; old_last_data=f.d.last_data_sequence;
        CHECK_EQ(old_local,4u); CHECK_EQ(old_reason,2u); CHECK(hello(&f,NONCE+1));
        /* Deliberate qualified-bound violation at the final completion observation. */
        f.jump_at=f.clock_count+5; f.jump_to=which?499u:1000u; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT);
        if(f.d.local_error!=old_local || f.d.first_reason!=old_reason || f.d.prefix_end!=128u || f.d.nonce!=NONCE)
            fprintf(stderr,"failed recovery final observation: variant=%u old_local=%u new_local=%u old_reason=%u new_reason=%u prefix=%" PRIu64 " nonce=%" PRIu64 "\n",which,old_local,f.d.local_error,(unsigned)old_reason,(unsigned)f.d.first_reason,f.d.prefix_end,f.d.nonce);
        CHECK_EQ(f.d.local_error,old_local); CHECK_EQ(f.d.first_reason,old_reason); CHECK_EQ(f.d.prefix_end,128u); CHECK_EQ(f.d.prefix_crc_state,old_crc); CHECK_EQ(f.d.last_data_sequence,old_last_data); CHECK_EQ(f.d.nonce,NONCE); CHECK_EQ(memcmp(&f.d.queue,&old_queue,sizeof old_queue),0);
    } return 1;
}

static int test_failed_recovery_late_commit_preserves_old_evidence(void) { return failed_recovery_commit_preserves_old_evidence(0); }
static int test_failed_recovery_clock_regression_preserves_old_evidence(void) { return failed_recovery_commit_preserves_old_evidence(1); }


static int test_successful_recovery_commits_fresh_state_once(void) {
    fixture f; decoded o; CHECK(begin(&f,1)); CHECK(offer(&f,0)); CHECK(pump(&f)); CHECK(offer(&f,128)); f.snapshot.produced_end=256;
    mt_live_device_fault(&f.d,2); f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.prefix_end,128u); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.first_reason,2u);
    CHECK(hello(&f,NONCE+1)); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK_EQ(f.d.nonce,NONCE+1); CHECK_EQ(f.d.first_reason,0u); CHECK_EQ(f.d.local_error,0u); CHECK_EQ(f.d.prefix_end,0u); CHECK_EQ(f.d.prefix_crc_state,UINT32_MAX); CHECK_EQ(f.d.queue.used,0u); CHECK_EQ(f.d.queue.next_index,0u); CHECK_EQ(f.d.caps_ready,0u); CHECK_EQ(f.d.generation,0u); CHECK_EQ(f.d.tx_next,2u); CHECK_EQ(f.d.rx_next,2u);
    CHECK(decode(&f,&o)); CHECK_EQ(o.invalid,0u); CHECK_EQ(o.partial,0u); CHECK_EQ(o.m[o.n-1].type,HELLO_ACK); CHECK_EQ(o.m[o.n-1].session,NONCE+1); return 1;
}


static int test_request_id_must_increase_with_correct_sequence(void) {
    uint32_t req; for(req=0;req<=3;req++) { fixture f; size_t before; CHECK(begin(&f,1)); before=f.output_length; CHECK(stop(&f,4,req)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.hardware_off,1u); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.want_ack,0u); }
    return 1;
}
static int test_sealed_queue_never_appends_valid_postgap_tail(void) {
    fixture f; uint16_t p[128]; unsigned i; CHECK(begin(&f,1)); for(i=0;i<16;i++)CHECK(offer(&f,128u*i));
    f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=2176; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,2176); f.snapshot.produced_end=2304; codes(p,128,2048);
    CHECK(!mt_live_device_offer(&f.d,2048,p,128)); CHECK_EQ(f.d.queue.sealed,1u); CHECK_EQ(f.d.queue.used,16u); CHECK_EQ(f.d.queue.next_index,2048u); CHECK_EQ(f.d.queue.first_fault,MT_SAMPLE_QUEUE_FULL); CHECK(pump(&f)); CHECK(end_check(&f,2048,2304,1,2,0,16,0)); return 1;
}
static int test_snapshot_tail_overlap_and_u64_overflow(void) {
    fixture f; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=127; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,127); f.snapshot.produced_end=255;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.queue.next_index,128u); CHECK(pump(&f)); CHECK(end_check(&f,128,0,2,3,4,1,1));
    CHECK(begin(&f,1)); mt_sample_queue_init(&f.d.queue,UINT64_MAX-10u); f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=UINT64_MAX-10u; f.snapshot.tail[0].count=17; codes(f.snapshot.tail[0].codes,17,0); f.snapshot.produced_known=0;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.used,0u); CHECK_EQ(f.d.queue.next_index,UINT64_MAX-10u); CHECK_EQ(f.d.first_reason,2u); CHECK_EQ(f.d.produced_known,0u); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,3,4,0,1)); return 1;
}
static int test_snapshot_two_full_valid_blocks(void) {
    fixture f; unsigned i; CHECK(begin(&f,1)); f.snapshot.produced_end=256; f.snapshot.tail_count=2;
    for(i=0;i<2;i++) { f.snapshot.tail[i].first_index=128u*i; f.snapshot.tail[i].count=128; codes(f.snapshot.tail[i].codes,128,128u*i); }
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.used,2u); CHECK_EQ(f.d.queue.next_index,256u); CHECK(pump(&f)); CHECK(end_check(&f,256,256,0,0,4,2,1)); return 1;
}
static int test_second_start_active_recovers_without_error(void) {
    int finish; for(finish=0;finish<=1;finish++) { fixture f; uint8_t p[4]={1,0,0,0}; decoded o; size_t before; CHECK(begin(&f,finish)); before=f.output_length; CHECK(request(&f,START,NONCE,4,4,p,4)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.start_count,1u); CHECK_EQ(f.d.want_error,0u); CHECK_EQ(f.d.hardware_off,1u); CHECK_EQ(f.output_length,before); CHECK(decode(&f,&o)); CHECK(!find_type(&o,ERROR_REPLY,0)); }
    return 1;
}
static int test_second_start_terminal_recovers_without_error(void) {
    fixture f; uint8_t p[4]={1,0,0,0}; decoded o; size_t before; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_TERMINAL); before=f.output_length; CHECK(request(&f,START,NONCE,4,4,p,4)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.start_count,1u); CHECK_EQ(f.d.want_error,0u); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.first_reason,2u); CHECK(decode(&f,&o)); CHECK(!find_type(&o,ERROR_REPLY,0)); return 1;
}


static int test_future_sequence_with_increasing_request_is_fatal(void) {
    fixture f; size_t before; CHECK(begin(&f,1)); before=f.output_length;
    CHECK(stop(&f,5,4)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_PROTOCOL); CHECK_EQ(f.d.rx_next,4u); CHECK_EQ(f.d.last_request,3u); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.want_ack,0u); CHECK_EQ(f.d.hardware_off,1u); return 1;
}
static int test_later_fault_never_renews_shutdown_budget(void) {
    fixture f; unsigned q; CHECK(begin(&f,1)); mt_live_device_fault(&f.d,2); CHECK_EQ(f.d.stop_deadline,500u); q=f.quiesce_count;
    f.time=100; mt_live_device_fault(&f.d,1); CHECK_EQ(f.d.stop_deadline,500u); CHECK_EQ(f.d.first_reason,2u); CHECK_EQ(f.quiesce_count,q);
    f.time=400; mt_live_device_fault(&f.d,6); CHECK_EQ(f.d.stop_deadline,500u); CHECK_EQ(f.d.first_reason,2u); CHECK_EQ(f.quiesce_count,q);
    f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.first_reason,2u); return 1;
}
static int test_overflow_snapshot_invalidates_known_produced_end(void) {
    fixture f; CHECK(begin(&f,1)); mt_sample_queue_init(&f.d.queue,UINT64_MAX-10u); f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=UINT64_MAX-10u; f.snapshot.tail[0].count=17; codes(f.snapshot.tail[0].codes,17,0); f.snapshot.produced_known=1; f.snapshot.produced_end=UINT64_MAX;
    CHECK(stop(&f,4,4)); CHECK_EQ(f.d.queue.next_index,UINT64_MAX-10u); CHECK_EQ(f.d.queue.used,0u); CHECK_EQ(f.d.produced_known,0u); CHECK_EQ(f.d.produced_end,0u); CHECK_EQ(f.d.first_reason,2u); CHECK(pump(&f)); CHECK(end_check(&f,0,0,2,3,4,0,1)); return 1;
}
static int test_nonfull_fault_sealed_queue_never_reopens(void) {
    fixture f; uint16_t p[128]; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=128; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,128); f.snapshot.produced_end=256; codes(p,128,128); p[127]=4096;
    CHECK(!mt_live_device_offer(&f.d,128,p,128)); CHECK_EQ(f.d.queue.first_fault,MT_SAMPLE_QUEUE_CODE); CHECK_EQ(f.d.queue.sealed,1u); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.queue.next_index,128u); CHECK(pump(&f)); CHECK(end_check(&f,128,256,2,2,0,1,0)); return 1;
}
static int revision_frame(fixture *f,unsigned active,uint8_t major,uint8_t minor,uint8_t *frame,size_t *length) {
    mt_wire_message m; memset(&m,0,sizeof m); m.type=active?STOP:CAPS; m.session=f->d.nonce; m.sequence=f->d.rx_next; m.request=f->d.last_request+1;
    if(active) { m.payload_length=4; w32(m.payload,f->d.generation); }
    CHECK_EQ(mt_wire_encode_revision(&m,major,minor,frame,MT_WIRE_FRAME_MAX,length),MT_WIRE_OK); return 1;
}
static int test_valid_crc_incompatible_revision_idle_and_active(void) {
    unsigned active,revision; for(active=0;active<2;active++)for(revision=0;revision<2;revision++) {
        fixture f; uint8_t frame[MT_WIRE_FRAME_MAX],previous[MT_WIRE_FRAME_MAX]; size_t length,previous_length,before; uint32_t next,request_id,generation; unsigned had_previous,starts; mt_wire_message m;
        if(active)CHECK(begin(&f,1)); else CHECK(ready(&f));
        before=f.output_length; next=f.d.rx_next; request_id=f.d.last_request; generation=f.d.generation; starts=f.start_count; previous_length=f.d.previous_length; had_previous=f.d.have_previous; memcpy(previous,f.d.previous,previous_length);
        CHECK(revision_frame(&f,active,revision?1u:0u,revision?2u:1u,frame,&length)); CHECK_EQ(mt_wire_decode_revision(frame,length-1,0,2,&m),MT_WIRE_VERSION); CHECK(mt_live_device_receive(&f.d,frame,length));
        CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_INCOMPATIBLE); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.nonce,NONCE); CHECK_EQ(f.d.rx_next,next); CHECK_EQ(f.d.last_request,request_id); CHECK_EQ(f.d.generation,generation); CHECK_EQ(f.start_count,starts); CHECK_EQ(f.output_length,before); CHECK_EQ(f.d.previous_length,previous_length); CHECK_EQ(f.d.have_previous,had_previous); CHECK_EQ(memcmp(previous,f.d.previous,previous_length),0); CHECK_EQ(f.d.want_error,0u); CHECK_EQ(f.d.hardware_off,1u);
    } return 1;
}
static int test_crc_bad_revision_bytes_are_discarded_without_incompatibility(void) {
    unsigned active,revision; for(active=0;active<2;active++)for(revision=0;revision<2;revision++) {
        fixture f; uint8_t frame[MT_WIRE_FRAME_MAX],bad[MT_WIRE_FRAME_MAX],previous[MT_WIRE_FRAME_MAX]; size_t length,i,before,previous_length; uint32_t next,request_id; mt_live_state state; unsigned q; mt_wire_message m;
        if(active)CHECK(begin(&f,1)); else CHECK(ready(&f));
        before=f.output_length; next=f.d.rx_next; request_id=f.d.last_request; state=f.d.state; q=f.quiesce_count; previous_length=f.d.previous_length; memcpy(previous,f.d.previous,previous_length);
        CHECK(revision_frame(&f,active,revision?1u:0u,revision?2u:1u,frame,&length));
        /* In these canonical frames index 2 is minor for major 0; index 1 is nonzero major. */
        i=revision?1u:2u; CHECK_EQ(frame[i],1u); memcpy(bad,frame,length); bad[i]^=16u;
        CHECK(i<length-1); CHECK_EQ(mt_wire_decode_revision(bad,length-1,0,2,&m),MT_WIRE_CRC); CHECK(mt_live_device_receive(&f.d,bad,length));
        CHECK_EQ(f.d.state,state); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_NONE); CHECK_EQ(f.d.incompatible_seen,0u); CHECK_EQ(f.d.nonce,NONCE); CHECK_EQ(f.d.rx_next,next); CHECK_EQ(f.d.last_request,request_id); CHECK_EQ(f.output_length,before); CHECK_EQ(f.quiesce_count,q); CHECK_EQ(f.d.previous_length,previous_length); CHECK_EQ(memcmp(previous,f.d.previous,previous_length),0);
    } return 1;
}
static int test_incompatible_hello_never_falls_back_or_arms(void) {
    fixture f; mt_wire_message m; uint8_t frame[MT_WIRE_FRAME_MAX]; size_t length; CHECK(init(&f)); memset(&m,0,sizeof m); m.type=HELLO; m.sequence=1; m.request=1; m.payload_length=8; w64(m.payload,NONCE);
    CHECK_EQ(mt_wire_encode_revision(&m,0,1,frame,sizeof frame,&length),MT_WIRE_OK); CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_INCOMPATIBLE); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.nonce,0u); CHECK_EQ(f.d.attempted_nonce,0u); CHECK_EQ(f.quiesce_count,0u); CHECK_EQ(f.start_count,0u); CHECK_EQ(f.output_length,0u);
    CHECK(hello(&f,NONCE)); CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK_EQ(f.d.incompatible_seen,0u); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_NONE); CHECK_EQ(f.start_count,0u); return 1;
}
static int test_incompatibility_preserves_prior_cause_until_timely_recovery(void) {
    fixture f; uint8_t frame[MT_WIRE_FRAME_MAX]; size_t length; CHECK(begin(&f,1)); CHECK(offer(&f,0)); CHECK(pump(&f)); f.snapshot.produced_end=128; mt_live_device_fault(&f.d,2); f.time=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_DEADLINE);
    CHECK(revision_frame(&f,1,0,1,frame,&length)); CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_DEADLINE); CHECK_EQ(f.d.first_reason,2u); CHECK_EQ(f.d.prefix_end,128u); CHECK_EQ(f.d.incompatible_seen,1u);
    f.snapshot.hardware_off=0; CHECK(hello(&f,NONCE+1)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.first_reason,2u); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_DEADLINE);
    f.snapshot.hardware_off=1; CHECK(hello(&f,NONCE+2)); f.write_cap=7; CHECK(mt_live_device_step(&f.d)); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.nonce,NONCE); f.write_cap=64; CHECK(pump(&f)); CHECK_EQ(f.d.state,MT_LIVE_STOPPED); CHECK_EQ(f.d.incompatible_seen,0u); CHECK_EQ(f.d.first_reason,0u); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_NONE); CHECK_EQ(f.d.nonce,NONCE+2); return 1;
}
static int test_failed_hello_commit_retains_incompatible_flag(void) {
    fixture f; uint8_t frame[MT_WIRE_FRAME_MAX]; size_t length; CHECK(ready(&f)); CHECK(revision_frame(&f,0,1,2,frame,&length)); CHECK(mt_live_device_receive(&f.d,frame,length)); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_INCOMPATIBLE);
    CHECK(hello(&f,NONCE+1)); f.jump_at=f.clock_count+5; f.jump_to=500; CHECK(!mt_live_device_step(&f.d)); CHECK_EQ(f.d.state,MT_LIVE_FAULT); CHECK_EQ(f.d.incompatible_seen,1u); CHECK_EQ(f.d.local_error,MT_LIVE_ERR_INCOMPATIBLE); CHECK_EQ(f.d.nonce,NONCE); return 1;
}


static int test_malformed_count_seals_before_contiguous_snapshot_tail(void) {
    fixture f; uint16_t p[128]; CHECK(begin(&f,1)); CHECK(offer(&f,0)); f.snapshot.tail_count=1; f.snapshot.tail[0].first_index=128; f.snapshot.tail[0].count=128; codes(f.snapshot.tail[0].codes,128,128); f.snapshot.produced_end=256; codes(p,128,128);
    CHECK(!mt_live_device_offer(&f.d,128,p,127)); CHECK_EQ(f.d.queue.first_fault,MT_SAMPLE_QUEUE_COUNT); CHECK_EQ(f.d.queue.sealed,1u); CHECK_EQ(f.d.queue.used,1u); CHECK_EQ(f.d.queue.next_index,128u); CHECK_EQ(f.d.first_reason,2u); CHECK(pump(&f)); CHECK(end_check(&f,128,256,2,2,0,1,0)); return 1;
}

typedef int (*test_fn)(void);
typedef struct { const char *name; test_fn fn; } test_case;
#define TEST(x) {#x,x}
int main(int argc,char **argv) {
    static const test_case tests[]={
        TEST(test_handshake_and_empty_stop),TEST(test_stop_before_start_ack),TEST(test_stop_during_start_ack),TEST(test_full_and_short_tail_crc),
        TEST(test_queue_overrun_known),TEST(test_queue_overrun_unknown),TEST(test_snapshot_atomic_invalid_second),TEST(test_snapshot_known_gap),TEST(test_snapshot_unknown),TEST(test_first_fault_and_failed_quiescence),
        TEST(test_late_stop_before_end),TEST(test_late_stop_during_end),TEST(test_late_stop_after_end),TEST(test_zero_write_freezes_sequence),TEST(test_unknown_write_requires_resync),TEST(test_partial_write_then_detach),
        TEST(test_duplicate_and_old_nonce_history),TEST(test_reordered_command_fatal),TEST(test_conflicting_duplicate_fatal),TEST(test_unknown_active_old_nonce_fatal),TEST(test_wrong_direction_old_nonce_fatal),TEST(test_stop_duplicate_no_new_ack),TEST(test_second_distinct_stop_fatal),
        TEST(test_start_failure_reserved_error_only),TEST(test_fresh_hello_barrier_and_nonce_retry),TEST(test_hello_isolation_failure),TEST(test_hello_deadline_boundaries),TEST(test_stop_deadline_boundaries),TEST(test_barrier_time_included_in_shutdown),TEST(test_whole_transaction_guard),
        TEST(test_inherited_hello_deadline),TEST(test_inherited_stop_deadline),TEST(test_replacement_hello_finishes_old_frame),TEST(test_counter_reserve_tx),TEST(test_counter_reserve_request),TEST(test_maximum_stop_request_and_sequence),TEST(test_clock_regression_and_overflow),
        TEST(test_late_known_acceptance_resync),TEST(test_postwrite_clock_failure_resync),TEST(test_malformed_commands_do_not_execute),
        TEST(test_equivalent_cobs_is_not_duplicate),TEST(test_snapshot_invalid_matrix),TEST(test_full_snapshot_overflows_queue_without_rewriting_prefix),TEST(test_invalid_offered_code_and_index),TEST(test_unstarted_end_refreezes_for_stop),TEST(test_late_stop_timer_expires_before_ack),TEST(test_zero_trickle_progress_never_extends_deadline),TEST(test_hello_supersedes_not_started_output_without_gap),TEST(test_tx_maximum_never_wraps),TEST(test_start_refused_without_tx_reserve),TEST(test_detach_failed_barrier_preserves_uncertainty),TEST(test_oversized_write_result_is_unknown),TEST(test_postcommit_deadline_failure_cannot_publish_clean_state),TEST(test_failed_start_cleanup_deadline),TEST(test_failed_start_error_inherits_deadline_into_hello),TEST(test_rx_fragmentation_and_discard_recovery),TEST(test_u64_sample_end_boundaries),TEST(test_block_arrival_after_shutdown_refused),TEST(test_invalid_start_payload_is_not_admitted),TEST(test_init_invalid_callback_contract),TEST(test_failed_recovery_late_commit_preserves_old_evidence),TEST(test_failed_recovery_clock_regression_preserves_old_evidence),TEST(test_successful_recovery_commits_fresh_state_once),TEST(test_request_id_must_increase_with_correct_sequence),TEST(test_sealed_queue_never_appends_valid_postgap_tail),TEST(test_snapshot_tail_overlap_and_u64_overflow),TEST(test_snapshot_two_full_valid_blocks),TEST(test_second_start_active_recovers_without_error),TEST(test_second_start_terminal_recovers_without_error),TEST(test_future_sequence_with_increasing_request_is_fatal),TEST(test_later_fault_never_renews_shutdown_budget),TEST(test_overflow_snapshot_invalidates_known_produced_end),TEST(test_nonfull_fault_sealed_queue_never_reopens),TEST(test_valid_crc_incompatible_revision_idle_and_active),TEST(test_crc_bad_revision_bytes_are_discarded_without_incompatibility),TEST(test_incompatible_hello_never_falls_back_or_arms),TEST(test_incompatibility_preserves_prior_cause_until_timely_recovery),TEST(test_failed_hello_commit_retains_incompatible_flag),TEST(test_malformed_count_seals_before_contiguous_snapshot_tail)
    };
    size_t i; unsigned ran=0,failed=0;
    for(i=0;i<sizeof tests/sizeof tests[0];i++)if(argc==1 || strstr(tests[i].name,argv[1])) { int ok=tests[i].fn(); printf("%s %s\n",ok?"PASS":"FAIL",tests[i].name); ++ran; if(!ok)++failed; }
    printf("RESULT %u tests, %u failed. Synthetic native checks only.\n",ran,failed); return failed?1:0;
}
