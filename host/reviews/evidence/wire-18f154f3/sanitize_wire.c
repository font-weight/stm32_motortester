#include "mt_wire.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
static uint32_t state=20261010;
static uint32_t rnd(void) { state^=state<<13;state^=state>>17;state^=state<<5;return state; }
int main(void) {
    uint8_t frame[MT_WIRE_FRAME_MAX];
    mt_wire_message m, result, before;
    size_t i,j,n,w;
    for(i=0;i<200000;i++) {
        n=rnd()%(MT_WIRE_FRAME_MAX+1u);
        for(j=0;j<n;j++)frame[j]=(uint8_t)rnd();
        memset(&result,0xa5,sizeof result);memcpy(&before,&result,sizeof result);
        mt_wire_result rc=mt_wire_decode(frame,n,&result);
        if(rc!=MT_WIRE_OK)assert(memcmp(&result,&before,sizeof result)==0);
        memset(&m,0,sizeof m);m.type=(uint8_t)rnd();m.session=((uint64_t)rnd()<<32)|rnd();m.sequence=rnd();m.request=rnd();
        m.payload_length=(uint16_t)(rnd()%513u);
        for(j=0;j<m.payload_length;j++)m.payload[j]=(uint8_t)rnd();
        assert(mt_wire_encode(&m,frame,sizeof frame,&w)==MT_WIRE_OK);
        assert(mt_wire_decode(frame,w-1,&result)==MT_WIRE_OK);
        assert(result.type==m.type && result.session==m.session && result.sequence==m.sequence && result.request==m.request && result.payload_length==m.payload_length);
        assert(memcmp(result.payload,m.payload,m.payload_length)==0);
    }
    return 0;
}
