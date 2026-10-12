/* ADC fault probe: test-only adaptation of frozen native-host device_bridge.c.
 * Production sources remain untouched.
 * Reuses the package's synchronous hardware fake and finite TX sink. */
#define main unused_characterization_main
#include "../snapshots/a-native/tests/wait-release/fixtures/test_wait_release.c"
#undef main

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
        } else if(!strcmp(op,"RX")) {
            uint8_t data[MT_WIRE_FRAME_MAX];size_t n;mt_wire_message m;
            CHECK(sscanf(line,"%31s %u %2047s",op,&time,hex)==3);
            n=strlen(hex)/2u;CHECK(n<=sizeof data && strlen(hex)%2u==0u);
            for(i=0;i<n;++i)data[i]=(uint8_t)((nibble(hex[2u*i])<<4)|nibble(hex[2u*i+1u]));
            CHECK(n>0 && data[n-1u]==0u);CHECK(mt_wire_decode(data,n-1u,&m)==MT_WIRE_OK);
            f.now=time;result=(int)mt_control_receive(&f.c,&m,time);
        } else if(!strcmp(op,"POLL")) {
            CHECK(sscanf(line,"%31s %u %u %u",op,&time,&cap,&count)==4);
            CHECK(cap<=64u && count<=1000u);
            for(i=0;i<count;++i)result=(int)poll_at(time,cap);
        } else if(!strcmp(op,"PARTIAL")) {
            CHECK(sscanf(line,"%31s %u",op,&time)==2);
            f.now=time;
            CHECK(mt_control_sample(&f.c,100,time)==MT_CONTROL_OK);
            CHECK(mt_control_sample(&f.c,3000,time)==MT_CONTROL_OK);
            CHECK(f.c.link.active_window && f.c.hardware_running);
            drain_at(time);
        } else if(!strcmp(op,"FAULT")) {
            CHECK(sscanf(line,"%31s %u %u",op,&time,&count)==3);
            CHECK(count==1u || count==2u);f.now=time;
            result=(int)mt_control_acquisition_fault(&f.c,(uint8_t)count,time);
            CHECK(result==(int)MT_CONTROL_OK);
            CHECK(f.c.ready && f.c.session.active && !f.c.hardware_running);
            CHECK(f.c.link.phase==MT_LINK_ACQ_ERROR);
        } else if(!strcmp(op,"ENDPREFIX")) {
            end_prefix();
        } else if(!strcmp(op,"CAPTURE")) {
            CHECK(sscanf(line,"%31s %u %u",op,&time,&count)==3);
            CHECK(count<=1u);capture(time,(int)count);
        } else {CHECK(!strcmp(op,"STATE"));}
        printf("{\"result\":%d,\"ready\":%u,\"session_active\":%u,\"phase\":%u,\"armed\":%u,\"starts\":%u,\"hw\":%u,\"generation\":%"PRIu32",\"tombstone\":%u,\"window\":%"PRIu32",\"anchor\":%"PRIu32",\"queued\":%zu,\"offset\":%zu,\"pending_states\":%u,\"dropped\":%"PRIu32",\"error_reason\":%u,\"wire\":\"",result,f.c.ready,f.c.session.active,(unsigned)f.c.link.phase,f.c.link.armed,f.starts,f.c.hardware_running,f.c.link.generation,f.c.link.have_tombstone,f.c.link.active_window,f.c.link.release_since_ms,f.c.tx.count,f.c.tx.offset,f.c.state_count,f.c.link.dropped_windows,f.c.link.error_reason);
        for(i=(unsigned)before;i<f.used;++i)printf("%02x",f.bytes[i]);
        puts("\"}");fflush(stdout);
    }
    return 0;
}
