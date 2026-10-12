#define main supporting_harness_main
#include "test_control_independent.c"
#undef main
int main(void) {
 current="stale_stop_with_pending_statuses";
 if(setjmp(failed)) return 1;
 boot(); configure(1,0,512); arm();
 for(unsigned i=0;i<512u;++i) sample(1000);
 sample(3000);
 CHECK(f.c.state_count==3u && f.c.tx.count==1u);
 unsigned before=f.stop_calls;
 mt_wire_message m=command(5,4); p32(m.payload,f.c.link.generation-1u);
 mt_control_result result=receive(&m);
 printf("result=%d; stops %u->%u; ready=%u; hardware=%u; pending_before=3; tx_before=1\n",(int)result,before,f.stop_calls,f.c.ready,f.c.hardware_running);
 CHECK(f.stop_calls==before); CHECK(f.c.ready && f.c.hardware_running);
 CHECK(result==MT_CONTROL_OK);
 return 0;
}
