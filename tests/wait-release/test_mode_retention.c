/* Additional read-only mode/storage observations. Baseline helpers unchanged. */
#define main r8_characterization_main
#include "fixtures/test_wait_release.c"
#undef main
static void mode_case(uint8_t mode) {
    mt_control_io io={profile,start,stop,&f,0};mt_wire_message m;
    uint16_t saved[2048];mt_acq_window_t window;mt_acq_result_t readable;
    memset(&f,0,sizeof f);f.now=100;f.cap=64;f.nonce=11;f.next=2;
    CHECK(mt_control_init(&f.c,&io)==MT_CONTROL_OK);CHECK(hello(f.nonce,100)==MT_CONTROL_OK);drain_at(100);
    m=command(3,16);put16(m.payload,1);m.payload[2]=mode;put16(m.payload+6,2048);
    if(mode==0u)put32(m.payload+12,100);
    CHECK(send_at(&m,100)==MT_CONTROL_OK);drain_at(100);
    m=command(4,4);put32(m.payload,1);CHECK(send_at(&m,100)==MT_CONTROL_OK);drain_at(100);capture(100,1);
    CHECK(mt_acq_window(&f.c.link.acquisition,&window)==MT_ACQ_OK && window.sample_count==2048u);
    memcpy(saved,f.c.link.acquisition.samples,sizeof saved);
    CHECK(poll_at(5100,64)==MT_CONTROL_OK);drain_at(5100);
    CHECK(memcmp(saved,f.c.link.acquisition.samples,sizeof saved)==0);
    CHECK(f.c.link.active_window==1u && f.c.link.tomb_window==1u && f.c.ready && f.c.session.active);
    CHECK(frames(134,5,3)==1u && f.starts==1u && !f.running && !f.c.link.armed);
    readable=mt_acq_window(&f.c.link.acquisition,&window);
    printf("mode=%u raw2048_preserved=1 held_id=%"PRIu32" tomb_id=%"PRIu32" acquisition_state=%u retained_count=%u mt_acq_window_result=%d\n",mode,f.c.link.active_window,f.c.link.tomb_window,(unsigned)f.c.link.acquisition.state,f.c.link.acquisition.retained_count,readable);
    m=release();CHECK(send_at(&m,5101)==MT_CONTROL_OK);drain_at(5101);
    m=release();CHECK(send_at(&m,5102)==MT_CONTROL_OK);drain_at(5102);
    CHECK(f.c.link.active_window==0u && f.c.link.tomb_window==1u && f.starts==1u && !f.running && !f.c.link.armed);
    CHECK(frames(135,-1,-1)==2u && frames(146,-1,-1)==1u && frames(147,-1,-1)==0u);
    CHECK(memcmp(saved,f.c.link.acquisition.samples,sizeof saved)==0);
    CHECK(mt_control_sample(&f.c,4000,5103)==MT_CONTROL_IGNORED);
    CHECK(memcmp(saved,f.c.link.acquisition.samples,sizeof saved)==0);
    trace(mode==0u?"auto-expiry-late-ack-no-rearm":mode==1u?"normal-expiry-late-ack-no-rearm":"single-expiry-late-ack-no-rearm",MT_CONTROL_OK);
}
int main(void){mode_case(0);mode_case(1);mode_case(2);puts("PASS: 3 mode/storage cases; no physical hardware claim.");return 0;}
