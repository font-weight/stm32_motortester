/* New record-now cases reuse helpers, not historical verdicts. */
#define main existing_link_main
#include "../app/test_capture_link.c"
#undef main
static void immediate_setup(mt_capture_link *l, mt_tx *t, sink *s) {
    setup(l,t,s,MT_ACQ_SINGLE,0);
    assert(mt_capture_link_stop(l,7,2,0)==MT_LINK_OK); drain(l,s,0); s->used=0;
    assert(mt_capture_link_record_now(l,5,8,0)==MT_LINK_OK);
}
static void fill(mt_capture_link *l) {
    for(unsigned i=0;i<2048u;++i)
        assert(mt_capture_link_sample(l,(uint16_t)(100u+i),0)==
               (i==0u?MT_LINK_TRIGGERED:i==2047u?MT_LINK_WINDOW_READY:MT_LINK_OK));
}
static void commitment(unsigned committed) {
    mt_capture_link l; mt_tx t; sink s; mt_wire_message frames[20];
    immediate_setup(&l,&t,&s); fill(&l);
    s.step=1;
    unsigned budget=20000;
    while (!(t.count && t.slots[0].message.type==146u && t.offset==0u)) {
        assert(budget--);
        assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_OK);
    }
    s.step=0; assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_TX_BLOCKED);
    assert(t.length && !t.offset && !l.have_tombstone);
    if(committed) {
        s.step=1; assert(mt_capture_link_poll(&l,0,output,&s)==MT_LINK_OK);
        assert(t.offset==1u && l.have_tombstone);
    }
    assert(mt_capture_link_stop(&l,8,3,0)==MT_LINK_OK);
    s.step=7; drain(&l,&s,0);
    size_t n=decode(&s,frames,20); unsigned ends=0,aborts=0,acks=0;
    for(size_t i=0;i<n;++i) {
        if(frames[i].type==146u){assert(!acks);++ends;}
        if(frames[i].type==147u){assert(!acks);++aborts;}
        if(frames[i].type==133u)++acks;
    }
    assert(acks==1u && ends==committed && aborts==1u-committed);
    assert(l.dropped_windows==1u-committed && !l.armed);
}
static void expiry(void) {
    mt_capture_link l; mt_tx t; sink s;
    immediate_setup(&l,&t,&s); fill(&l); drain(&l,&s,0); s.used=0;
    uint32_t crc=l.window_crc;
    assert(mt_capture_link_record_now(&l,5,9,0)==MT_LINK_BUSY);
    assert(mt_capture_link_poll(&l,4999,output,&s)==MT_LINK_OK && l.phase==MT_LINK_WAIT_RELEASE);
    assert(mt_capture_link_poll(&l,5000,output,&s)==MT_LINK_OK && l.phase==MT_LINK_RELEASE_EXPIRED);
    assert(mt_capture_link_release(&l,8,1,crc,3,5001)==MT_LINK_OK); drain(&l,&s,5001);
    assert(!l.armed && l.phase==MT_LINK_RELEASE_EXPIRED && !l.dropped_windows);
    assert(mt_capture_link_record_now(&l,5,9,5001)==MT_LINK_BUSY);
    assert(mt_capture_link_release(&l,8,1,crc,4,5002)==MT_LINK_OK); drain(&l,&s,5002);
    assert(!l.armed && l.phase==MT_LINK_RELEASE_EXPIRED);
}
int main(void) {
    assert(existing_link_main()==0);
    commitment(0); commitment(1); expiry();
    puts("record-now link: END byte commitment and late release passed"); return 0;
}
