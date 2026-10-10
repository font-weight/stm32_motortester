#define main supporting_harness_main
#include "test_control_independent.c"
#undef main
int main(void) {
 current="old_tombstone_during_new_frozen_window";
 if(setjmp(failed)) return 1;
 boot(); configure(1,0,0); arm(); drain(); capture(0);
 mt_wire_message old=release_message();
 CHECK(receive(&old)==MT_CONTROL_OK); drain();
 sample(1000); sample(3000);
 for(unsigned i=1;i<2048u;++i) sample((uint16_t)((i*13u)&4095u));
 CHECK(f.c.link.phase==MT_LINK_BEGIN && f.c.link.armed && !f.fake_running);
 mt_wire_message replay=command(7,12); memcpy(replay.payload,old.payload,12);
 unsigned before=f.start_calls;
 CHECK(receive(&replay)==MT_CONTROL_OK);
 printf("before starts=%u; after starts=%u; phase=%u; hardware_running=%u; active_window=%u; replay_window=%u\n",before,f.start_calls,(unsigned)f.c.link.phase,f.c.hardware_running,f.c.link.active_window,u32(replay.payload+4));
 CHECK(f.start_calls==before); CHECK(!f.fake_running && !f.c.hardware_running);
 return 0;
}
