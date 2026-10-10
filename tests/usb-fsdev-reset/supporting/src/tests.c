static uint8_t old_buffers[MODEL_ENDPOINTS][2][256];
static tu_fifo_t old_fifos[MODEL_ENDPOINTS][2];
static unsigned reset_cases;
static unsigned fresh_cases;
static unsigned retained_cases;

static void model_open_checked(uint8_t rhport) {
    bool dirty=false;
    pre_open_observations++;
    for(unsigned i=0;i<MODEL_ENDPOINTS;i++) for(unsigned d=0;d<2;d++)
        dirty=dirty||!model_xfer_zero(&xfer_status[i][d]);
    if(dirty) dirty_pre_open++;
    if(EXPECT_FIXED) {
        CHECK(!dirty);
        CHECK(registers.DADDR==0);
        for(unsigned i=0;i<FSDEV_EP_COUNT;i++) {
            CHECK((registers.ep[i].reg&(U_EP_CTR_RX|U_EP_CTR_TX))==0);
            CHECK(ep_alloc_status[i].ep_num==0xff && ep_alloc_status[i].ep_type==0xff);
            CHECK(!ep_alloc_status[i].allocated[0]&&!ep_alloc_status[i].allocated[1]);
        }
        CHECK(ep_buf_ptr==64);
    }
    edpt0_open(rhport);
}
static void fresh_model(void) {
    memset(&registers,0,sizeof(registers));memset(&btable,0,sizeof(btable));memset(pma,0,sizeof(pma));
    memset(xfer_status,0,sizeof(xfer_status));memset(ep_alloc_status,0,sizeof(ep_alloc_status));
    memset(old_buffers,0x5a,sizeof(old_buffers));memset(old_fifos,0,sizeof(old_fifos));
    event_count=bus_reset_events=setup_events=signal_events=sof_events=0;
    pre_open_observations=dirty_pre_open=enabled_write_seen=0;
    fifo_reads=fifo_writes=pma_reads=pma_writes=0;
    istr_flags=0;istr_reads=0;reset_scope=0;ep_buf_ptr=500;
    memset(write_seen,0,sizeof(write_seen));
}
static void seed_ownership(void) {
    for(unsigned i=0;i<MODEL_ENDPOINTS;i++) for(unsigned d=0;d<2;d++) {
        old_fifos[i][d].count=8;
        memset(old_fifos[i][d].data,0xa6,8);
        xfer_status[i][d]=(xfer_ctl_t){old_buffers[i][d],&old_fifos[i][d],8,(uint16_t)(d?8:0),64,(uint8_t)((i+3)%8),true};
    }
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++) {
        ep_alloc_status[i]=(ep_alloc_t){(uint8_t)(i%MODEL_ENDPOINTS),TUSB_XFER_BULK,{true,true}};
        btable.ep16[i][BTABLE_BUF_RX].addr=256;
        btable.ep16[i][BTABLE_BUF_RX].count=8;
        btable.ep16[i][BTABLE_BUF_TX].addr=320;
        btable.ep16[i][BTABLE_BUF_TX].count=8;
    }
    memset(&pma[128],0x39,8);
    registers.DADDR=U_DADDR_EF|35u;
}
static void bus_reset_endpoint_registers(void) {
    /* RM0008 23.5.2: bus/FRES reset clears every EPnR bit except both CTR bits.
     * DADDR is intentionally left enabled here as a conservative entry-state
     * challenge; assertions concern handle_bus_reset's own disable sequence. */
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++) registers.ep[i].reg&=(U_EP_CTR_RX|U_EP_CTR_TX);
    istr_flags|=U_ISTR_RESET;
}
static void run_reset_entry(bool interrupt_entry) {
    reset_scope=1;istr_reads=0;
    if(interrupt_entry) dcd_int_handler(0); else handle_bus_reset(0);
    reset_scope=0;reset_cases++;
    if(interrupt_entry) {CHECK(bus_reset_events>0);CHECK((model_istr_read()&U_ISTR_RESET)==0);}
    CHECK(pre_open_observations>0);
    CHECK(registers.DADDR==U_DADDR_EF);
    CHECK(registers.ep[0].reg==(U_EP_CONTROL|0x2020u));
    CHECK(ep_buf_ptr==192);
    CHECK(btable.ep16[0][BTABLE_BUF_RX].addr==64 && btable.ep16[0][BTABLE_BUF_TX].addr==128);
    CHECK(btable.ep16[0][BTABLE_BUF_RX].count==0x1000u);
    CHECK(ep_alloc_status[0].ep_num==0&&ep_alloc_status[0].ep_type==TUSB_XFER_CONTROL);
    CHECK(ep_alloc_status[0].allocated[0]&&ep_alloc_status[0].allocated[1]);
    for(unsigned i=1;i<FSDEV_EP_COUNT;i++) {
        CHECK(ep_alloc_status[i].ep_num==0xff&&ep_alloc_status[i].ep_type==0xff);
        CHECK(!ep_alloc_status[i].allocated[0]&&!ep_alloc_status[i].allocated[1]);
    }
    for(unsigned d=0;d<2;d++) CHECK(xfer_status[0][d].max_packet_size==64&&xfer_status[0][d].ep_idx==0);
    if(EXPECT_FIXED) {
        CHECK(dirty_pre_open==0&&enabled_write_seen==0);
        for(unsigned i=0;i<FSDEV_EP_COUNT;i++) {CHECK(write_seen[i]>0);CHECK((registers.ep[i].reg&(U_EP_CTR_RX|U_EP_CTR_TX))==0);}
        for(unsigned i=0;i<MODEL_ENDPOINTS;i++) for(unsigned d=0;d<2;d++) {
            xfer_ctl_t got=xfer_status[i][d];
            if(i==0) got.max_packet_size=0;
            CHECK(model_xfer_zero(&got));
        }
    } else {
        CHECK(dirty_pre_open>0);
        for(unsigned i=0;i<MODEL_ENDPOINTS;i++) for(unsigned d=0;d<2;d++) {
            xfer_ctl_t *got=&xfer_status[i][d];
            CHECK(got->buffer==old_buffers[i][d]&&got->ff==&old_fifos[i][d]);
            CHECK(got->total_len==8&&got->queued_len==(d?8:0)&&got->iso_in_sending);
            if(i) CHECK(got->max_packet_size==64&&got->ep_idx==(i+3)%8);
        }
    }
}
static void run_reset(void) {run_reset_entry(true);}
static void test_direct_reset_entry(void) {
    if(!EXPECT_FIXED) return;
    test_context="direct reset helper entry, cold and stale ownership";
    fresh_model();run_reset_entry(false);CHECK(event_count==0&&bus_reset_events==0);
    fresh_model();seed_ownership();
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++) registers.ep[i].reg=U_EP_CTR_RX|U_EP_CTR_TX;
    run_reset_entry(false);CHECK(event_count==0&&bus_reset_events==0);
}
static void test_register_semantics(void) {
    test_context="model register semantics";fresh_model();
    registers.ep[7].reg=U_EP_CTR_RX|U_EP_CTR_TX|U_EP_SETUP|U_EP_DTOG_RX|U_EPRX_STAT|U_EP_DTOG_TX|U_EPTX_STAT|U_EP_CONTROL|7u;
    ep_write(7,U_EP_CTR_RX|U_EP_CTR_TX|U_EP_DTOG_RX|0x1010u|U_EP_KIND|2u,false);
    CHECK(registers.ep[7].reg==(U_EP_CTR_RX|U_EP_CTR_TX|U_EP_SETUP|0x2000u|U_EP_DTOG_TX|0x20u|U_EP_KIND|2u));
    ep_write_clear_ctr(7,TUSB_DIR_OUT);CHECK(!(registers.ep[7].reg&U_EP_CTR_RX));CHECK(registers.ep[7].reg&U_EP_CTR_TX);
    ep_write_clear_ctr(7,TUSB_DIR_IN);CHECK(!(registers.ep[7].reg&(U_EP_CTR_RX|U_EP_CTR_TX)));
    registers.ep[7].reg=U_EP_CTR_RX|U_EP_CTR_TX|0x7070u;
    ep_write(7,0,false);CHECK(registers.ep[7].reg==0x7070u); /* zero does not disable VALID STAT */
    bus_reset_endpoint_registers();CHECK(registers.ep[7].reg==0);
    registers.ep[6].reg=U_EP_CTR_TX;istr_flags=U_ISTR_RESET;
    model_istr_write(0);CHECK((model_istr_read()&U_ISTR_CTR)!=0);CHECK((model_istr_read()&U_ISTR_EP_ID)==6);
    ep_write(6,0,false);CHECK(model_istr_read()==0);
}
static void test_patterns(void) {
    const uint16_t patterns[]={0,U_EP_CTR_RX,U_EP_CTR_TX,U_EP_CTR_RX|U_EP_CTR_TX};
    for(unsigned slot=0;slot<=FSDEV_EP_COUNT;slot++) for(unsigned k=0;k<4;k++) {
        test_context="single-slot/all-slot reset pattern";fresh_model();seed_ownership();
        for(unsigned i=0;i<FSDEV_EP_COUNT;i++) registers.ep[i].reg=(uint16_t)(U_EP_CONTROL|U_EP_DTOG_RX|0x3030u|i|((i==slot||slot==FSDEV_EP_COUNT)?patterns[k]:0));
        bus_reset_endpoint_registers();run_reset();CHECK(event_count==0);
        if(!EXPECT_FIXED) {
            for(unsigned i=1;i<FSDEV_EP_COUNT;i++) CHECK(registers.ep[i].reg==((i==slot||slot==FSDEV_EP_COUNT)?patterns[k]:0));
            if(k&&slot) retained_cases++;
        }
        if(EXPECT_FIXED) {istr_reads=0;dcd_int_handler(0);CHECK(event_count==0&&pma_reads==0&&pma_writes==0);}
        /* A second physical reset must not depend on stale allocation or PMA growth. */
        if(EXPECT_FIXED) {bus_reset_endpoint_registers();run_reset();CHECK(event_count==0);}
    }
}
static void test_original_misrouting(void) {
    if(EXPECT_FIXED) return;
    for(unsigned dir=0;dir<2;dir++) {
        test_context="original retained CTR misroutes to EP0";fresh_model();seed_ownership();
        registers.ep[7].reg=(uint16_t)(2u|(dir?U_EP_CTR_TX:U_EP_CTR_RX));
        bus_reset_endpoint_registers();run_reset();
        CHECK(registers.ep[7].reg==(dir?U_EP_CTR_TX:U_EP_CTR_RX));
        istr_reads=0;dcd_int_handler(0);
        CHECK(event_count==1&&events[0].ep==(dir?0x80u:0)&&events[0].length==8);
        if(!dir) {CHECK(fifo_reads==1&&old_fifos[0][0].count==16);CHECK(old_fifos[0][0].data[8]==0x39);}
    }
}
static void complete_tx(unsigned slot) {
    CHECK(registers.DADDR&U_DADDR_EF);
    CHECK((registers.ep[slot].reg&U_EPTX_STAT)==0x30);
    registers.ep[slot].reg=(uint16_t)((registers.ep[slot].reg&~U_EPTX_STAT)|0x20u|U_EP_CTR_TX);
    istr_reads=0;dcd_int_handler(0);
}
static void complete_rx(unsigned slot,const uint8_t *payload,unsigned n,bool setup) {
    CHECK(registers.DADDR&U_DADDR_EF);
    CHECK(setup||(registers.ep[slot].reg&U_EPRX_STAT)==0x3000);
    CHECK(n<=64);
    memcpy(PMA_BUF_AT(btable.ep16[slot][BTABLE_BUF_RX].addr),payload,n);
    btable_set_count(slot,BTABLE_BUF_RX,(uint16_t)n);
    registers.ep[slot].reg=(uint16_t)((registers.ep[slot].reg&~(U_EPRX_STAT|U_EP_SETUP))|0x2000u|U_EP_CTR_RX|(setup?U_EP_SETUP:0));
    istr_reads=0;dcd_int_handler(0);
}
static void test_fresh_transfers(void) {
    if(!EXPECT_FIXED) return;
    const uint8_t payload[8]={0,1,2,3,4,5,6,7};
    for(unsigned repeat=0;repeat<4;repeat++) {
        test_context="reset/reconfigure buffered and FIFO transfers";
        if(repeat==0) {fresh_model();seed_ownership();}
        for(unsigned i=0;i<FSDEV_EP_COUNT;i++) registers.ep[i].reg|=U_EP_CTR_RX|U_EP_CTR_TX;
        bus_reset_endpoint_registers();run_reset();
        unsigned before=event_count;
        complete_rx(0,payload,8,true);CHECK(setup_events==repeat+1&&memcmp(last_setup,payload,8)==0);
        uint8_t ep0buf[8];memcpy(ep0buf,payload,8);
        CHECK(dcd_edpt_xfer(0,0x80,ep0buf,8,false));complete_tx(0);
        CHECK(event_count==before+1&&events[before].ep==0x80&&events[before].length==8);
        CHECK(dcd_edpt_xfer(0,0,ep0buf,8,false));complete_rx(0,payload,8,false);
        CHECK(event_count==before+2&&events[before+1].ep==0&&events[before+1].length==8);
        tusb_desc_endpoint_t out={0x01,{TUSB_XFER_BULK},64},in={0x81,{TUSB_XFER_BULK},64},irq={0x82,{TUSB_XFER_INTERRUPT},8};
        CHECK(dcd_edpt_open(0,&out)&&dcd_edpt_open(0,&in)&&dcd_edpt_open(0,&irq));
        CHECK(xfer_status[1][0].ep_idx==1&&xfer_status[1][1].ep_idx==1&&xfer_status[2][1].ep_idx==2);
        CHECK(ep_buf_ptr==328);CHECK(registers.ep[1].reg==(1u|0x2020u));
        CHECK(registers.ep[2].reg==(2u|U_EP_INTERRUPT|0x20u));
        uint8_t buffer[8]={0};tu_fifo_t fifo={{0},0};
        if(repeat&1u) CHECK(dcd_edpt_xfer_fifo(0,0x01,&fifo,8,false));
        else CHECK(dcd_edpt_xfer(0,0x01,buffer,8,false));
        complete_rx(1,payload,8,false);
        CHECK(event_count==before+3&&events[before+2].ep==1&&events[before+2].length==8);
        if(repeat&1u) {CHECK(fifo.count==8&&memcmp(fifo.data,payload,8)==0);CHECK(dcd_edpt_xfer_fifo(0,0x81,&fifo,8,false));CHECK(fifo.count==0);}
        else {CHECK(memcmp(buffer,payload,8)==0);CHECK(dcd_edpt_xfer(0,0x81,buffer,8,false));}
        CHECK(memcmp(PMA_BUF_AT(btable.ep16[1][BTABLE_BUF_TX].addr),payload,8)==0);
        complete_tx(1);CHECK(event_count==before+4&&events[before+3].ep==0x81&&events[before+3].length==8);
        CHECK(!(model_istr_read()&U_ISTR_CTR));fresh_cases++;
    }
}
int main(void) {
    test_register_semantics();test_direct_reset_entry();test_patterns();test_original_misrouting();test_fresh_transfers();
    printf("PASS mode=%s logical_endpoints=%u physical_slots=%u reset_cases=%u fresh_cycles=%u retained_negative_cases=%u assertions=%lu\n",
        EXPECT_FIXED?"candidate":"original-negative",MODEL_ENDPOINTS,FSDEV_EP_COUNT,reset_cases,fresh_cases,retained_cases,checks);
    return 0;
}
