/* Software-only recorder fixture. Explicit RX chunks, TX polls and samples. */
#define main unused_characterization_main
#include "tests/native-host-reproducer/snapshots/a-native/tests/wait-release/fixtures/test_wait_release.c"
#undef main
#include "firmware/app/mt_control_rx.h"
static mt_control_rx rx;

static unsigned nibble(char c) {
    if(c>='0' && c<='9')return (unsigned)(c-'0');
    if(c>='a' && c<='f')return (unsigned)(c-'a')+10u;
    CHECK(0);return 0;
}
int main(void) {
    char line[4096], op[32], hex[2048];
    while(fgets(line,sizeof line,stdin)) {
        unsigned time=0,cap=0,count=0,i;size_t before=f.used;int result=0;
        CHECK(sscanf(line,"%31s",op)==1);
        if(!strcmp(op,"INIT")) {
            mt_control_io io={profile,start,stop,&f,0};
            memset(&f,0,sizeof f);f.cap=64;before=0;
            result=(int)mt_control_init(&f.c,&io);
            CHECK(mt_control_rx_init(&rx,&f.c)==MT_CONTROL_OK);
        } else if(!strcmp(op,"RX")) {
            uint8_t data[MT_WIRE_FRAME_MAX];size_t n,consumed;
            CHECK(sscanf(line,"%31s %u %2047s",op,&time,hex)==3);
            n=strlen(hex)/2u;CHECK(n<=64u && strlen(hex)%2u==0u);
            for(i=0;i<n;++i)data[i]=(uint8_t)((nibble(hex[2u*i])<<4)|nibble(hex[2u*i+1u]));
            f.now=time;result=(int)mt_control_rx_feed(&rx,data,n,time,&consumed);
            CHECK(consumed==n);
        } else if(!strcmp(op,"POLL")) {
            CHECK(sscanf(line,"%31s %u %u %u",op,&time,&cap,&count)==4);
            CHECK(cap<=64u && count<=1000u);
            CHECK(mt_control_rx_poll(&rx,time)==MT_CONTROL_OK);
            for(i=0;i<count;++i)result=(int)poll_at(time,cap);
        } else if(!strcmp(op,"SAMPLES")) {
            unsigned mode, number;
            CHECK(sscanf(line,"%31s %u %u %u %u",op,&time,&mode,&number,&count)==5);
            CHECK(mode<=2u && number>0u && number<=100u && count<=2048u);
            f.now=time;
            if(mode!=0u) CHECK(mt_control_sample(&f.c,100u,time)==MT_CONTROL_OK);
            for(i=0u;i<count;++i) {
                uint16_t code=(uint16_t)((73u*i+19u*number)&4095u);
                if(mode!=0u && i==0u) code=3000u;
                if(i==511u)code=0u;
                if(i==1023u)code=4095u;
                CHECK(mt_control_sample(&f.c,code,time)==MT_CONTROL_OK);
            }
        } else {CHECK(!strcmp(op,"STATE"));}
        printf("{\"result\":%d,\"ready\":%u,\"session_active\":%u,\"phase\":%u,\"armed\":%u,\"starts\":%u,\"hw\":%u,\"generation\":%"PRIu32",\"tombstone\":%u,\"window\":%"PRIu32",\"anchor\":%"PRIu32",\"queued\":%zu,\"offset\":%zu,\"pending_states\":%u,\"dropped\":%"PRIu32",\"error_reason\":%u,\"head_type\":%u,\"tx_length\":%zu,\"wire\":\"",result,f.c.ready,f.c.session.active,(unsigned)f.c.link.phase,f.c.link.armed,f.starts,f.c.hardware_running,f.c.link.generation,f.c.link.have_tombstone,f.c.link.active_window,f.c.link.release_since_ms,f.c.tx.count,f.c.tx.offset,f.c.state_count,f.c.link.dropped_windows,f.c.link.error_reason,f.c.tx.count ? (unsigned)f.c.tx.slots[0].message.type : 0u,f.c.tx.length);
        for(i=(unsigned)before;i<f.used;++i)printf("%02x",f.bytes[i]);
        puts("\"}");fflush(stdout);
        /* Test sink is drained by this response; never retain 20 windows in C. */
        f.used=0;
    }
    return 0;
}
