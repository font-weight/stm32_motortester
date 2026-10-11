/* External oracle for the unaccepted WAIT_RELEASE proposal, 2026-10-11.
 * Helpers are compiled directly from the immutable characterization, including
 * all original assertions under its renamed main. They are NOT rewritten.
 * See PROVENANCE.md for exact source identities and semantic delta. */
#define main r8_characterization_main
#include "fixtures/test_wait_release.c"
#undef main

static mt_wire_message decoded[256];
static size_t decoded_count;
static uint32_t u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void decode_all(int complete) {
    size_t i, first=0;
    uint32_t sequence=0;
    uint64_t session=0;
    decoded_count=0;
    for(i=0;i<f.used;++i) if(f.bytes[i]==0u) {
        mt_wire_message *m=&decoded[decoded_count++];
        CHECK(decoded_count<=256u);
        CHECK(mt_wire_decode(f.bytes+first,i-first,m)==MT_WIRE_OK);
        if(m->session!=session) {CHECK(m->type==129u && m->sequence==1u);session=m->session;sequence=0;}
        CHECK(m->sequence==sequence+1u);sequence=m->sequence;
        first=i+1u;
    }
    if(complete)CHECK(first==f.used);
}
static size_t locate(uint8_t type,uint32_t request,int state_value,int reason) {
    size_t i;
    decode_all(1);
    for(i=0;i<decoded_count;++i) if(decoded[i].type==type && decoded[i].request==request &&
      (state_value<0 || decoded[i].payload[0]==(unsigned)state_value) &&
      (reason<0 || decoded[i].payload[1]==(unsigned)reason))return i;
    fprintf(stderr,"missing frame type=%u request=%"PRIu32" state=%d reason=%d\n",type,request,state_value,reason);exit(1);
}
static void ended_once(void) {CHECK(frames(146,-1,-1)==1u && frames(147,-1,-1)==0u);}
static void retained_error(int held) {
    CHECK(f.c.ready && f.c.session.active && f.c.session.nonce==f.nonce);
    CHECK(!f.c.link.armed && !f.c.hardware_running && !f.running && f.starts==1u);
    CHECK(f.c.link.have_tombstone && f.c.link.tomb_generation==3u && f.c.link.tomb_window==1u);
    CHECK(f.c.link.active_window==(held?1u:0u));
    CHECK(f.c.link.phase!=MT_LINK_IDLE && f.c.link.phase!=MT_LINK_WAIT_RELEASE && f.c.link.phase!=MT_LINK_FAILED);
    ended_once();
}
static mt_wire_message config_command(void) {
    mt_wire_message m=command(3,16);put16(m.payload,1);m.payload[2]=1;put16(m.payload+6,2048);return m;
}
static void boundary(uint32_t anchor) {
    size_t bytes;unsigned stops;
    setup(anchor);capture(anchor,1);bytes=f.used;stops=f.stops;
    CHECK(poll_at(anchor+4999u,64)==MT_CONTROL_OK);CHECK(f.used==bytes && f.c.link.phase==MT_LINK_WAIT_RELEASE && f.c.link.armed);
    trace("before-4999",MT_CONTROL_OK);
    { mt_control_result r=poll_at(anchor+5000u,64);trace("at-5000",r);CHECK(r==MT_CONTROL_OK); }
    retained_error(1);CHECK(f.stops==stops);drain_at(anchor+5000u);
    CHECK(frames(134,5,3)==1u);CHECK(f.c.link.release_since_ms==anchor);
    CHECK(u32(decoded[locate(134,0,5,3)].payload+4)==3u);
    CHECK(u32(decoded[locate(134,0,5,3)].payload+8)==1u);
    bytes=f.used;CHECK(poll_at(anchor+5001u,64)==MT_CONTROL_OK);CHECK(f.used==bytes);
    CHECK(mt_control_sample(&f.c,2500,anchor+5001u)==MT_CONTROL_IGNORED);
    trace("error-status-drained",MT_CONTROL_OK);
}
static void normal_boundary(void){boundary(100);}
static void wrap_boundary(void){boundary(UINT32_MAX-2499u);}
static void early_release(void) {
    mt_wire_message m;setup(100);capture(100,1);m=release();
    CHECK(send_at(&m,5099)==MT_CONTROL_OK && f.starts==2u && f.c.hardware_running);drain_at(5099);
    CHECK(frames(135,-1,-1)==1u && frames(134,5,3)==0u);
    m=release();CHECK(send_at(&m,5100)==MT_CONTROL_OK);drain_at(5100);CHECK(f.starts==2u && frames(135,-1,-1)==2u);ended_once();trace("early-release-replay",MT_CONTROL_OK);
}
static void late_release(uint32_t time,int poll_first) {
    mt_wire_message m;uint32_t request;
    setup(100);capture(100,1);
    if(poll_first) {CHECK(poll_at(5100,64)==MT_CONTROL_OK);drain_at(5100);}
    m=release();request=m.request;
    {mt_control_result r=send_at(&m,time);trace("late-release-command",r);CHECK(r==MT_CONTROL_OK);}
    retained_error(0);drain_at(time);CHECK(frames(135,-1,-1)==1u && frames(134,5,3)==1u);
    CHECK(locate(134,0,5,3)<locate(135,request,-1,-1));
    m=release();CHECK(send_at(&m,time+1u)==MT_CONTROL_OK);drain_at(time+1u);retained_error(0);CHECK(frames(135,-1,-1)==2u);
    m=command(6,0);request=m.request;CHECK(send_at(&m,time+1u)==MT_CONTROL_OK);drain_at(time+1u);(void)locate(134,request,5,3);
    trace("late-and-tombstone-ack-no-rearm",MT_CONTROL_OK);
}
static void release_exact(void){late_release(5100,0);}
static void release_late(void){late_release(5101,1);}
static void bad_release(void) {
    mt_wire_message m;setup(100);capture(100,1);m=release();m.payload[8]^=1;
    CHECK(send_at(&m,5100)==MT_CONTROL_OK);drain_at(5100);retained_error(1);
    CHECK(frames(135,-1,-1)==0u && frames(255,7,7)==1u && frames(134,5,3)==1u);
    CHECK(locate(134,0,5,3)<locate(255,m.request,7,7));trace("bad-release-retains-error",MT_CONTROL_OK);
}
static void commands(void) {
    mt_wire_message m;uint32_t status_request,config_id,generation;
    setup(100);capture(100,1);config_id=f.c.link.config_id;generation=f.c.link.generation;
    m=command(6,0);CHECK(send_at(&m,5099)==MT_CONTROL_OK);drain_at(5099);(void)locate(134,m.request,6,0);
    m=command(6,0);status_request=m.request;
    {mt_control_result r=send_at(&m,5100);trace("get-status-at-expiry",r);CHECK(r==MT_CONTROL_OK);}
    drain_at(5100);retained_error(1);CHECK(locate(134,0,5,3)<locate(134,status_request,5,3));
    m=config_command();CHECK(send_at(&m,5101)==MT_CONTROL_OK);drain_at(5101);(void)locate(255,m.request,3,2);
    m=command(4,4);put32(m.payload,config_id);CHECK(send_at(&m,5102)==MT_CONTROL_OK);drain_at(5102);(void)locate(255,m.request,4,2);
    m=release();CHECK(send_at(&m,5103)==MT_CONTROL_OK);drain_at(5103);retained_error(0);
    m=config_command();CHECK(send_at(&m,5104)==MT_CONTROL_OK);drain_at(5104);(void)locate(255,m.request,3,2);
    m=command(4,4);put32(m.payload,config_id);CHECK(send_at(&m,5105)==MT_CONTROL_OK);drain_at(5105);(void)locate(255,m.request,4,2);
    CHECK(f.c.link.config_id==config_id && f.c.link.generation==generation);trace("config-arm-busy-even-after-release",MT_CONTROL_OK);
}
static void delayed_end(void) {
    mt_wire_message m;setup(100);capture(100,0);end_prefix();
    CHECK(poll_at(1099,0)==MT_CONTROL_OK);m=release();CHECK(send_at(&m,1099)==MT_CONTROL_OK);
    CHECK(f.c.link.phase==MT_LINK_END_QUEUED && f.starts==1u);drain_at(1099);
    CHECK(f.c.link.release_since_ms==1099u && frames(255,7,2)==1u);
    CHECK(poll_at(5100,64)==MT_CONTROL_OK && f.c.link.phase==MT_LINK_WAIT_RELEASE);
    CHECK(poll_at(6098,64)==MT_CONTROL_OK && f.c.link.phase==MT_LINK_WAIT_RELEASE);
    {mt_control_result r=poll_at(6099,64);trace("delayed-end-final-plus5000",r);CHECK(r==MT_CONTROL_OK);}
    drain_at(6099);retained_error(1);CHECK(frames(134,5,3)==1u);trace("delayed-end-timeout-status",MT_CONTROL_OK);
}
static void end_stall(void) {
    setup(100);capture(100,0);end_prefix();CHECK(poll_at(1099,0)==MT_CONTROL_OK);
    CHECK(poll_at(1100,0)==MT_CONTROL_RECOVERY && f.c.tx.fault==MT_TX_TIMEOUT);
    CHECK(!f.c.ready && !f.c.session.active && !f.c.hardware_running && f.starts==1u);
    CHECK(f.c.link.release_since_ms==0u && frames(146,-1,-1)==0u && frames(134,5,3)==0u);trace("end-stall1000-before-hold",MT_CONTROL_RECOVERY);
}
static void stop_variant(int stage) {
    mt_wire_message m;uint32_t request;unsigned expected_timeout=stage==3?1u:0u;
    setup(100);capture(100,1);
    if(stage)CHECK(poll_at(5100,64)==MT_CONTROL_OK);
    if(stage==2)CHECK(poll_at(5100,0)==MT_CONTROL_OK);
    if(stage==3)CHECK(poll_at(5100,1)==MT_CONTROL_OK && f.c.tx.offset==1u);
    m=stop_command();request=m.request;CHECK(send_at(&m,5100)==MT_CONTROL_OK);drain_at(5100);
    CHECK(f.c.ready && f.c.link.phase==MT_LINK_IDLE && !f.c.link.armed && !f.c.hardware_running);
    ended_once();CHECK(frames(133,-1,-1)==1u && frames(134,5,3)==expected_timeout);
    CHECK(locate(133,request,-1,-1)<locate(134,0,0,0));
    if(stage==3)CHECK(locate(134,0,5,3)<locate(133,request,-1,-1));
    m=release();CHECK(send_at(&m,5101)==MT_CONTROL_OK);drain_at(5101);CHECK(f.starts==1u && frames(135,-1,-1)==1u);
    m=command(4,4);put32(m.payload,1);CHECK(send_at(&m,5102)==MT_CONTROL_OK);drain_at(5102);CHECK(f.starts==2u && f.c.hardware_running);
    trace("stop-supersedes-then-new-arm",MT_CONTROL_OK);
}
static void stop_exact(void){stop_variant(0);}static void stop_pending(void){stop_variant(1);}static void stop_queued(void){stop_variant(2);}static void stop_partial(void){stop_variant(3);}
static void hello_variant(int stage) {
    size_t ack_at;setup(100);capture(100,1);
    if(stage)CHECK(poll_at(5100,64)==MT_CONTROL_OK);
    if(stage==2)CHECK(poll_at(5100,0)==MT_CONTROL_OK);
    if(stage==3)CHECK(poll_at(5100,1)==MT_CONTROL_OK && f.c.tx.offset==1u);
    CHECK(hello(12,5100)==MT_CONTROL_OK);f.nonce=12;f.next=2;drain_at(5100);
    CHECK(f.c.ready && f.c.session.nonce==12u && f.c.link.phase==MT_LINK_IDLE && !f.c.link.have_tombstone);
    CHECK(frames(134,5,3)==(stage==3?1u:0u) && frames(129,-1,-1)==2u);ended_once();decode_all(1);
    ack_at=decoded_count-1u;CHECK(decoded[ack_at].type==129u && decoded[ack_at].session==12u && decoded[ack_at].sequence==1u);
    if(stage==3)CHECK(decoded[ack_at-1u].type==134u && decoded[ack_at-1u].session==11u && decoded[ack_at-1u].payload[1]==3u);
    CHECK(f.starts==1u);trace("hello-supersession",MT_CONTROL_OK);
}
static void hello_exact(void){hello_variant(0);}static void hello_pending(void){hello_variant(1);}static void hello_queued(void){hello_variant(2);}static void hello_partial(void){hello_variant(3);}
static void older_partial(void) {
    mt_wire_message m;uint32_t request;size_t bytes;
    setup(100);capture(100,1);m=command(6,0);request=m.request;CHECK(send_at(&m,5099)==MT_CONTROL_OK);
    CHECK(poll_at(5099,1)==MT_CONTROL_OK && f.c.tx.offset==1u);bytes=f.used;
    {mt_control_result r=poll_at(5100,0);trace("partial-old-reply-at-expiry",r);CHECK(r==MT_CONTROL_OK);}
    CHECK(f.used==bytes && f.c.tx.offset==1u);retained_error(1);drain_at(5100);
    CHECK(locate(134,request,6,0)<locate(134,0,5,3));CHECK(frames(134,5,3)==1u);trace("older-partial-complete-before-error",MT_CONTROL_OK);
}
static void older_replies(void) {
    mt_wire_message m;uint32_t requests[3];unsigned i;
    setup(100);capture(100,1);
    for(i=0;i<3u;++i){m=command(2,0);requests[i]=m.request;CHECK(send_at(&m,5099)==MT_CONTROL_OK);}
    CHECK(f.c.tx.count==3u);
    {mt_control_result r=poll_at(5100,0);trace("three-older-replies-at-expiry",r);CHECK(r==MT_CONTROL_OK);}
    CHECK(f.c.state_count==1u);drain_at(5100);retained_error(1);
    for(i=0;i<3u;++i)CHECK(locate(130,requests[i],-1,-1)<locate(134,0,5,3));
    CHECK(frames(134,5,3)==1u);trace("backpressure-retains-replies-and-event",MT_CONTROL_OK);
}
static void queue_fallback(void) {
    mt_wire_message m;unsigned i;size_t bytes;
    setup(100);capture(100,1);for(i=0;i<3u;++i){m=command(2,0);CHECK(send_at(&m,5099)==MT_CONTROL_OK);}
    bytes=f.used;m=release();CHECK(send_at(&m,5100)==MT_CONTROL_RECOVERY);
    CHECK(!f.c.ready && !f.c.session.active && f.starts==1u && !f.c.link.armed && f.c.link.active_window==1u);
    CHECK(poll_at(5100,64)==MT_CONTROL_IGNORED && f.used==bytes);CHECK(frames(135,-1,-1)==0u);trace("queue-full-bounded-recovery",MT_CONTROL_RECOVERY);
}
static void report_stall(uint32_t anchor) {
    size_t bytes;setup(anchor);capture(anchor,1);CHECK(poll_at(anchor+5000u,64)==MT_CONTROL_OK);
    CHECK(poll_at(anchor+5000u,0)==MT_CONTROL_OK);bytes=f.used;CHECK(f.c.tx.count==1u);
    CHECK(poll_at(anchor+5999u,0)==MT_CONTROL_OK && f.c.ready && f.used==bytes);
    CHECK(poll_at(anchor+6000u,0)==MT_CONTROL_RECOVERY && f.c.tx.fault==MT_TX_TIMEOUT);
    CHECK(!f.c.ready && !f.c.session.active && f.starts==1u && frames(134,5,3)==0u);ended_once();trace("timeout-report-stall-bounded-fallback",MT_CONTROL_RECOVERY);
    CHECK(hello(12,anchor+6001u)==MT_CONTROL_OK);drain_at(anchor+6001u);CHECK(frames(129,-1,-1)==2u && frames(134,5,3)==0u);
}
static void status_stall(void){report_stall(100);}static void status_stall_wrap(void){report_stall(UINT32_MAX-5499u);}
static int fail_write(void *v,const uint8_t *p,size_t n){(void)v;(void)p;(void)n;return -1;}
static void status_io(void) {
    setup(100);capture(100,1);CHECK(poll_at(5100,64)==MT_CONTROL_OK);
    CHECK(mt_control_poll(&f.c,5100,fail_write,&f)==MT_CONTROL_RECOVERY && f.c.tx.fault==MT_TX_IO);
    CHECK(!f.c.ready && !f.c.session.active && frames(134,5,3)==0u && f.starts==1u);ended_once();trace("timeout-report-io-fallback",MT_CONTROL_RECOVERY);
}
static void status_frame_timeout(void) {
    size_t bytes;uint32_t times[]={5100,6000,6900,7800,8099};unsigned i;
    setup(100);capture(100,1);CHECK(poll_at(5100,64)==MT_CONTROL_OK);
    for(i=0;i<5u;++i)CHECK(poll_at(times[i],1)==MT_CONTROL_OK && f.c.ready);
    bytes=f.used;CHECK(f.c.tx.offset==5u);
    CHECK(poll_at(8100,1)==MT_CONTROL_RECOVERY && f.c.tx.fault==MT_TX_TIMEOUT && f.used==bytes);
    CHECK(frames(134,5,3)==0u && !f.c.ready && f.starts==1u);ended_once();trace("report-frame3000-despite-progress",MT_CONTROL_RECOVERY);
    CHECK(hello(12,8101)==MT_CONTROL_OK);drain_at(8101);CHECK(frames(134,5,3)==1u && frames(129,-1,-1)==2u);decode_all(1);
    CHECK(decoded[decoded_count-1u].session==12u && decoded[decoded_count-2u].session==11u);trace("new-hello-completes-old-partial-error",MT_CONTROL_OK);
}
static void stale_stop_order(void) {
    mt_wire_message m;setup(100);capture(100,1);m=stop_command();put32(m.payload,2);
    CHECK(send_at(&m,5100)==MT_CONTROL_OK);drain_at(5100);retained_error(1);trace("stale-stop-at-expiry-order",MT_CONTROL_OK);
    CHECK(locate(134,0,5,3)<locate(255,m.request,5,4));
}
static const struct {const char *name;void(*run)(void);} cases[]={
 {"boundary",normal_boundary},{"wrap",wrap_boundary},{"early_release",early_release},{"release_exact",release_exact},{"release_late",release_late},{"bad_release",bad_release},{"commands",commands},{"delayed_end",delayed_end},{"end_stall",end_stall},
 {"stop_exact",stop_exact},{"stop_pending",stop_pending},{"stop_queued",stop_queued},{"stop_partial",stop_partial},{"hello_exact",hello_exact},{"hello_pending",hello_pending},{"hello_queued",hello_queued},{"hello_partial",hello_partial},
 {"older_partial",older_partial},{"older_replies",older_replies},{"queue_fallback",queue_fallback},{"status_stall",status_stall},{"status_stall_wrap",status_stall_wrap},{"status_io",status_io},{"status_frame_timeout",status_frame_timeout},{"stale_stop_order",stale_stop_order}
};
int main(int argc,char **argv) {
    size_t i;if(argc==2 && strcmp(argv[1],"--original")==0)return r8_characterization_main();if(argc==2 && strcmp(argv[1],"--list")==0){for(i=0;i<sizeof cases/sizeof cases[0];++i)puts(cases[i].name);return 0;}
    CHECK(argc==2);for(i=0;i<sizeof cases/sizeof cases[0];++i)if(strcmp(argv[1],cases[i].name)==0){cases[i].run();printf("PASS %s\n",cases[i].name);return 0;}
    fprintf(stderr,"Unknown case %s\n",argv[1]);return 2;
}
