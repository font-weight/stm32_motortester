/* Supplemental negative/control trace with buffer-only EP0 state: ff is NULL,
 * EP0 uses physical slot 0, and the stale completion is physical slot 7 / EA2.
 * The supporting register model remains shared and explicitly synthetic. */
#define main supporting_original_main
#include "translation.c"
#undef main
int main(void) {
    for (unsigned dir=0; dir<2; ++dir) {
        test_context="buffer-only EP0 retained physical-slot7 completion";
        fresh_model();
        for(unsigned i=0;i<FSDEV_EP_COUNT;++i)
            ep_alloc_status[i]=(ep_alloc_t){0xff,0xff,{false,false}};
        ep_alloc_status[0]=(ep_alloc_t){0,TUSB_XFER_CONTROL,{true,true}};
        ep_alloc_status[7]=(ep_alloc_t){2,TUSB_XFER_BULK,{true,true}};
        for(unsigned d=0;d<2;++d)
            xfer_status[0][d]=(xfer_ctl_t){old_buffers[0][d],NULL,8,(uint16_t)(d?8:0),64,0,false};
        xfer_status[2][dir]=(xfer_ctl_t){old_buffers[2][dir],NULL,8,(uint16_t)(dir?8:0),64,7,false};
        btable.ep16[7][BTABLE_BUF_RX].addr=256;
        btable.ep16[7][BTABLE_BUF_RX].count=8;
        memset(&pma[128],0x39,8);
        registers.DADDR=U_DADDR_EF|35u;
        registers.ep[7].reg=(uint16_t)(2u|(dir?U_EP_CTR_TX:U_EP_CTR_RX));
        bus_reset_endpoint_registers();
        reset_scope=1;dcd_int_handler(0);reset_scope=0;
        CHECK(bus_reset_events==1 && event_count==0);
        CHECK(registers.DADDR==U_DADDR_EF);
        istr_reads=0;dcd_int_handler(0);
        if(EXPECT_FIXED) {
            CHECK(event_count==0 && pma_reads==0 && pma_writes==0);
            CHECK(old_buffers[0][0][0]==0x5a);
        } else {
            CHECK(event_count==1 && events[0].ep==(dir?0x80u:0u) && events[0].length==8);
            CHECK(fifo_reads==0 && fifo_writes==0);
            if(!dir) CHECK(pma_reads==1 && old_buffers[0][0][0]==0x39);
        }
        printf("PASS legal buffer-only EP0: fixed=%u direction=%s events=%u PMA_reads=%u\n",
            EXPECT_FIXED,dir?"TX":"RX",event_count,pma_reads);
    }
    return 0;
}
