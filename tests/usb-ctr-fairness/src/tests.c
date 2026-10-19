/* Executes extracted implementation bodies. Environment and event queue are models. */
static uint8_t rx[3][128],tx[3][128];
static unsigned cases;
static void irq(void) { istr_reads=0; dcd_int_handler(0); }
static void initialize(void) {
    memset(&registers,0,sizeof(registers));memset(&btable,0,sizeof(btable));memset(pma,0,sizeof(pma));
    memset(xfer_status,0,sizeof(xfer_status));memset(ep_alloc_status,0,sizeof(ep_alloc_status));
    memset(rx,0,sizeof(rx));memset(tx,0x61,sizeof(tx));
    event_count=bus_reset_events=setup_events=signal_events=sof_events=overrun_calls=0;
    pre_open_observations=dirty_pre_open=enabled_write_seen=0;
    fifo_reads=fifo_writes=pma_reads=pma_writes=0;
    istr_flags=0;istr_reads=0;reset_scope=0;ep_buf_ptr=0;remoteWakeCountdown=0;
    inject_istr_after_read=0;inject_ctr_bits=0;
    registers.CNTR=U_CNTR_CTRM|U_CNTR_RESETM|U_CNTR_PMAOVRM|U_CNTR_SUSPM|U_CNTR_WKUPM|U_CNTR_SOFM|U_CNTR_ESOFM;
    handle_bus_reset(0);
    for(unsigned ep=1;ep<=2;ep++) for(unsigned dir=0;dir<2;dir++) {
        tusb_desc_endpoint_t desc={(uint8_t)(ep|(dir?0x80:0)),{TUSB_XFER_BULK},64};
        CHECK(dcd_edpt_open(0,&desc));
        CHECK(xfer_status[ep][dir].ep_idx==ep);
    }
}
static void arm_pair(unsigned ep,unsigned total) {
    CHECK(dcd_edpt_xfer(0,(uint8_t)ep,rx[ep],(uint16_t)total,false));
    CHECK(dcd_edpt_xfer(0,(uint8_t)(ep|0x80),tx[ep],(uint16_t)total,false));
}
static void arrival(unsigned ep,bool in,bool out,unsigned n) {
    if(out) {
        for(unsigned i=0;i<n;i++) ((uint8_t*)PMA_BUF_AT(btable.ep16[ep][BTABLE_BUF_RX].addr))[i]=(uint8_t)(ep*32+i);
        btable_set_count(ep,BTABLE_BUF_RX,(uint16_t)n);
        registers.ep[ep].reg=(uint16_t)((registers.ep[ep].reg&~U_EPRX_STAT)|0x2000u|U_EP_CTR_RX);
    }
    if(in) registers.ep[ep].reg=(uint16_t)((registers.ep[ep].reg&~U_EPTX_STAT)|0x20u|U_EP_CTR_TX);
}
static void check_pair(unsigned index,unsigned ep,unsigned n) {
    CHECK(events[index].ep==ep&&events[index].length==n);
    CHECK(events[index+1].ep==(ep|0x80)&&events[index+1].length==n);
}
static void test_two_endpoints(void) {
    test_context="two endpoint pairs, no implicit grant"; initialize();arm_pair(1,8);arm_pair(2,8);
    arrival(1,true,true,8);arrival(2,true,true,8);
    uint16_t pending=registers.ep[2].reg; uint16_t mask=registers.CNTR;
    irq();
#if CFG_MT_USB_CTR_FAIRNESS
    CHECK(event_count==2);check_pair(0,1,8);CHECK(mt_usb_fsdev_ctr_deferred());
    CHECK(registers.CNTR==(mask&~U_CNTR_CTRM));CHECK(registers.ep[2].reg==pending);
    CHECK(pma_reads==1);
    for(unsigned repeat=0;repeat<12;repeat++) irq();
    CHECK(event_count==2&&registers.ep[2].reg==pending&&pma_reads==1);
    CHECK(mt_usb_fsdev_ctr_rearm());CHECK(!mt_usb_fsdev_ctr_deferred());CHECK(registers.CNTR==mask);
    CHECK(registers.ep[2].reg==pending);irq();CHECK(event_count==4);check_pair(2,2,8);
    CHECK(mt_usb_fsdev_ctr_deferred());CHECK(!(registers.CNTR&U_CNTR_CTRM));
    CHECK(!(model_istr_read()&U_ISTR_CTR));
    CHECK(mt_usb_fsdev_ctr_rearm());irq();CHECK(!mt_usb_fsdev_ctr_deferred());
#else
    (void)pending;CHECK(event_count==4);CHECK(registers.CNTR==mask);check_pair(0,1,8);check_pair(2,2,8);
#endif
    for(unsigned ep=1;ep<=2;ep++)for(unsigned i=0;i<8;i++)CHECK(rx[ep][i]==ep*32+i);
    cases++;
}
#if CFG_MT_USB_CTR_FAIRNESS
static void establish_deferred(void) { initialize();arm_pair(1,8);arm_pair(2,8);arrival(1,true,true,8);irq();CHECK(mt_usb_fsdev_ctr_deferred()); }
static void test_lifecycle(void) {
    test_context="lifecycle services while CTR deferred";establish_deferred();arrival(2,true,true,8);
    const uint16_t pending=registers.ep[2].reg;
    istr_flags=U_ISTR_SOF|U_ISTR_SUSP|U_ISTR_ESOF;remoteWakeCountdown=1;registers.CNTR|=U_CNTR_RESUME;
    irq();CHECK(event_count==2&&sof_events==1&&signal_events==1);CHECK(registers.ep[2].reg==pending);
    CHECK((registers.CNTR&(U_CNTR_FSUSP|U_CNTR_LPMODE))==(U_CNTR_FSUSP|U_CNTR_LPMODE));
    CHECK(!(registers.CNTR&U_CNTR_RESUME)&&remoteWakeCountdown==0&&istr_flags==0);
    CHECK(!mt_usb_fsdev_ctr_rearm());CHECK(mt_usb_fsdev_ctr_deferred());
    istr_flags=U_ISTR_WKUP;irq();CHECK(signal_events==2&&event_count==2&&istr_flags==0);
    CHECK(!(registers.CNTR&(U_CNTR_FSUSP|U_CNTR_LPMODE|U_CNTR_CTRM)));
    CHECK(mt_usb_fsdev_ctr_rearm());irq();CHECK(event_count==4);check_pair(2,2,8);cases++;
}
static void test_overrun_and_reset(void) {
    test_context="PMA overrun and real reset while deferred";establish_deferred();arrival(2,true,true,8);
    uint16_t pending=registers.ep[2].reg;istr_flags=U_ISTR_PMAOVR;
    CHECK(!mt_usb_fsdev_ctr_rearm());irq();CHECK(overrun_calls==1&&event_count==2);
    CHECK(registers.ep[2].reg==pending&&istr_flags==0&&mt_usb_fsdev_ctr_deferred());
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++)registers.ep[i].reg&=(U_EP_CTR_RX|U_EP_CTR_TX);
    istr_flags=U_ISTR_RESET|U_ISTR_PMAOVR;CHECK(!mt_usb_fsdev_ctr_rearm());irq();
    CHECK(bus_reset_events==1&&event_count==2&&overrun_calls==1);CHECK(istr_flags==0);
    CHECK(!mt_usb_fsdev_ctr_deferred()&&(registers.CNTR&U_CNTR_CTRM));
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++)CHECK(!(registers.ep[i].reg&(U_EP_CTR_RX|U_EP_CTR_TX)));
    for(unsigned ep=1;ep<=2;ep++)for(unsigned d=0;d<2;d++)CHECK(model_xfer_zero(&xfer_status[ep][d]));
    irq();CHECK(event_count==2);CHECK(registers.DADDR==U_DADDR_EF);cases++;
}
static void test_rearm_refusals(void) {
    const uint16_t flags[]={U_ISTR_RESET,U_ISTR_PMAOVR,U_ISTR_SUSP,U_ISTR_RESET|U_ISTR_PMAOVR|U_ISTR_SUSP};
    const uint16_t controls[]={U_CNTR_FRES,U_CNTR_PDWN,U_CNTR_FSUSP,U_CNTR_FRES|U_CNTR_PDWN|U_CNTR_FSUSP};
    for(unsigned i=0;i<4;i++) {
        test_context="pending lifecycle rearm refusal";establish_deferred();arrival(2,true,true,8);
        istr_flags=flags[i];uint16_t cntr=registers.CNTR,ep=registers.ep[2].reg;
        CHECK(!mt_usb_fsdev_ctr_rearm());CHECK(registers.CNTR==cntr&&registers.ep[2].reg==ep&&istr_flags==flags[i]);CHECK(mt_usb_fsdev_ctr_deferred());cases++;
        test_context="off/reset/suspend control refusal";establish_deferred();registers.CNTR|=controls[i];cntr=registers.CNTR;
        CHECK(!mt_usb_fsdev_ctr_rearm());CHECK(registers.CNTR==cntr&&mt_usb_fsdev_ctr_deferred());cases++;
    }
    test_context="function-disabled refusal";establish_deferred();registers.DADDR=7;
    CHECK(!mt_usb_fsdev_ctr_rearm());CHECK(mt_usb_fsdev_ctr_deferred());CHECK(!(registers.CNTR&U_CNTR_CTRM));cases++;
}
static void test_rearm_windows(void) {
    test_context="CTR arrives immediately after rearm flag snapshot";establish_deferred();
    inject_ctr_slot=2;inject_ctr_bits=U_EP_CTR_TX;CHECK(mt_usb_fsdev_ctr_rearm());
    CHECK(registers.ep[2].reg&U_EP_CTR_TX);irq();CHECK(event_count==3&&events[2].ep==0x82);cases++;
    const uint16_t flags[]={U_ISTR_RESET,U_ISTR_PMAOVR,U_ISTR_SUSP};
    for(unsigned i=0;i<3;i++) {
        test_context="lifecycle arrival after sampled rearm read stays pending";establish_deferred();
        inject_istr_after_read=flags[i];CHECK(mt_usb_fsdev_ctr_rearm());CHECK(istr_flags==flags[i]);
        CHECK(!mt_usb_fsdev_ctr_rearm());irq();CHECK(istr_flags==0&&event_count==2);
        if(i==0)CHECK(bus_reset_events==1);
        if(i==1)CHECK(overrun_calls==1);
        if(i==2) CHECK(signal_events==1&&(registers.CNTR&U_CNTR_FSUSP));
        cases++;
    }
}
static void test_setup_latest_and_fifo(void) {
    test_context="latest legal EP0 SETUP retained during defer";establish_deferred();
    uint8_t request[8]={0x80,6,0,1,0,0,18,0};
    memcpy(PMA_BUF_AT(btable.ep16[0][BTABLE_BUF_RX].addr),request,8);btable_set_count(0,BTABLE_BUF_RX,8);
    registers.ep[0].reg|=U_EP_SETUP|U_EP_CTR_RX;irq();CHECK(setup_events==0);
    request[6]=64;memcpy(PMA_BUF_AT(btable.ep16[0][BTABLE_BUF_RX].addr),request,8);
    CHECK(mt_usb_fsdev_ctr_rearm());irq();CHECK(setup_events==1&&memcmp(last_setup,request,8)==0);CHECK(!(registers.ep[0].reg&U_EP_CTR_RX));cases++;
    test_context="FIFO transfer and multipacket state retained";initialize();tu_fifo_t fifo={{0},0};
    CHECK(dcd_edpt_xfer_fifo(0,1,&fifo,8,false));arrival(1,false,true,8);irq();CHECK(fifo.count==8&&fifo_reads==1);
    CHECK(dcd_edpt_xfer_fifo(0,0x81,&fifo,8,false));CHECK(fifo.count==0&&fifo_writes==1);arrival(1,true,false,0);
    irq();CHECK(event_count==1);CHECK(mt_usb_fsdev_ctr_rearm());irq();CHECK(event_count==2&&events[1].ep==0x81);cases++;
    initialize();arm_pair(1,128);arrival(1,true,true,64);irq();CHECK(event_count==0&&mt_usb_fsdev_ctr_deferred());
    CHECK(xfer_status[1][0].queued_len==64&&xfer_status[1][1].queued_len==128);
    CHECK((registers.ep[1].reg&U_EPRX_STAT)==0x3000&&(registers.ep[1].reg&U_EPTX_STAT)==0x30);
    arrival(1,true,true,64);irq();CHECK(event_count==0);CHECK(mt_usb_fsdev_ctr_rearm());irq();
    CHECK(event_count==2);check_pair(0,1,128);cases++;
}
static void test_producer_burst(void) {
    test_context="one endpoint quantum can produce two events plus lifecycle";initialize();arm_pair(1,8);arrival(1,true,true,8);
    istr_flags=U_ISTR_WKUP;irq();CHECK(signal_events==1&&event_count==2&&mt_usb_fsdev_ctr_deferred());cases++;
    test_context="coexisting sampled lifecycle flags add five modeled enqueue calls";initialize();arm_pair(1,8);arrival(1,true,true,8);
    istr_flags=U_ISTR_SOF|U_ISTR_WKUP|U_ISTR_SUSP;irq();
    CHECK(sof_events==1&&signal_events==2&&event_count==2&&mt_usb_fsdev_ctr_deferred());
    CHECK(registers.CNTR&U_CNTR_FSUSP);CHECK(!mt_usb_fsdev_ctr_rearm());cases++;
    /* No actual TinyUSB queue is substituted here. Counts establish admission demand only. */
}
static void test_direction_matrix(void) {
    for(unsigned one=0;one<4;one++)for(unsigned two=0;two<4;two++) {
        test_context="all two-endpoint RX/TX pending combinations";initialize();arm_pair(1,8);arm_pair(2,8);
        arrival(1,(one&2)!=0,(one&1)!=0,8);arrival(2,(two&2)!=0,(two&1)!=0,8);
        const unsigned first=one?one:two;const unsigned first_count=((first&1)!=0)+((first&2)!=0);
        const uint16_t second_reg=registers.ep[2].reg;irq();CHECK(event_count==first_count);
        if(one) CHECK(registers.ep[2].reg==second_reg);
        unsigned completed=event_count;for(unsigned repeat=0;repeat<3;repeat++)irq();CHECK(event_count==completed);
        if(one&&two) { CHECK(mt_usb_fsdev_ctr_rearm());irq(); }
        CHECK(event_count==(unsigned)(((one&1)!=0)+((one&2)!=0)+((two&1)!=0)+((two&2)!=0)));
        CHECK(!(model_istr_read()&U_ISTR_CTR));cases++;
    }
}
#endif
static void test_iso_profile(void) {
    test_context="ISO API baseline contrast and opt-in fail-closed profile";initialize();handle_bus_reset(0);
    tusb_desc_endpoint_t iso={0x81,{TUSB_XFER_ISOCHRONOUS},64};
#if CFG_MT_USB_CTR_FAIRNESS && EXPECT_RESTRICTED
    model_regs_t prior_regs=registers;model_btable_t prior_btable=btable;
    xfer_ctl_t prior_xfer[MODEL_ENDPOINTS][2];ep_alloc_t prior_alloc[FSDEV_EP_COUNT];fsdev_pma_buf_t prior_pma[256];
    memcpy(prior_xfer,xfer_status,sizeof(prior_xfer));memcpy(prior_alloc,ep_alloc_status,sizeof(prior_alloc));memcpy(prior_pma,pma,sizeof(prior_pma));
    uint16_t prior_ptr=ep_buf_ptr;
    CHECK(!dcd_edpt_iso_alloc(0,0x81,64));CHECK(!dcd_edpt_iso_activate(0,&iso));
    CHECK(!dcd_edpt_iso_alloc(255,255,65535));CHECK(!dcd_edpt_iso_activate(255,NULL));
    CHECK(!dcd_edpt_open(0,NULL));
    for(unsigned type=0;type<256;type++) if(type!=TUSB_XFER_BULK&&type!=TUSB_XFER_INTERRUPT) {
        iso.bmAttributes.xfer=(uint8_t)type;CHECK(!dcd_edpt_open(0,&iso));
    }
    CHECK(memcmp(&registers,&prior_regs,sizeof(registers))==0&&memcmp(&btable,&prior_btable,sizeof(btable))==0);
    CHECK(memcmp(xfer_status,prior_xfer,sizeof(prior_xfer))==0&&memcmp(ep_alloc_status,prior_alloc,sizeof(prior_alloc))==0);
    CHECK(memcmp(pma,prior_pma,sizeof(prior_pma))==0&&ep_buf_ptr==prior_ptr);
    iso.bmAttributes.xfer=TUSB_XFER_INTERRUPT;CHECK(dcd_edpt_open(0,&iso));
    CHECK((registers.ep[1].reg&U_EP_T_FIELD)==U_EP_INTERRUPT);
#else
    CHECK(dcd_edpt_iso_alloc(0,0x81,64));CHECK(dcd_edpt_iso_activate(0,&iso));
    CHECK((registers.ep[xfer_status[1][1].ep_idx].reg&U_EP_T_FIELD)==U_EP_ISOCHRONOUS);
#endif
    cases++;
}

int main(void) {
    test_two_endpoints();test_iso_profile();
#if CFG_MT_USB_CTR_FAIRNESS
    test_lifecycle();test_overrun_and_reset();test_rearm_refusals();test_rearm_windows();test_setup_latest_and_fifo();test_producer_burst();test_direction_matrix();
#endif
    printf("PASS fairness=%d cases=%u assertions=%lu; actual extracted DCD, synthetic registers/PMA/event sink\n",CFG_MT_USB_CTR_FAIRNESS,cases,checks);
    return 0;
}
