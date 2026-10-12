/* Supplemental Critic-A assertions reuse the disclosed supporting real-core
 * fixture/fake DCD. They are not a second independent peripheral model. */
#define main supporting_main
#include "test_integration.c"
#undef main

int main(void) {
    /* A held older SETUP is already removed from the counter. A newer
     * accepted SETUP still supersedes it once producer-side resume arrives. */
    fixture(true);
    setup(0,TUSB_REQ_SET_CONFIGURATION,0,0,0,false);
    fake_signal(DCD_EVENT_SUSPEND,false); step();
    assert(mt_usb_bridge_snapshot().pending_soft && tud_task_event_ready());
    setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,false);
    fake_signal(DCD_EVENT_RESUME,false); fake_pump();
    assert(!mt_usb_bridge_snapshot().faults);
    assert(mt_usb_bridge_snapshot().configured == 1 && fake_ep[0x80].pending);
    end("critic_held_setup_superseded_after_resume");

    const unsigned rejected[] = {1,255,256,257,511,512,513,1025};
    for (unsigned n=0;n<sizeof(rejected)/sizeof(rejected[0]);++n) {
        fixture(true);
        for(unsigned k=0;k<16+rejected[n];++k)
            setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,false);
        assert(mt_usb_bridge_snapshot().queue_losses == rejected[n]);
        assert(mt_usb_bridge_snapshot().faults == MT_USB_EPOCH_QUEUE_LOSS);
        fake_pump(); assert(!tud_task_event_ready());
        fake_bus_reset(true); setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
        assert(mt_usb_bridge_snapshot().configured == 2);
        assert(!mt_usb_bridge_snapshot().faults && fake_mounts == 2);
        assert(mt_usb_bridge_snapshot().queue_losses == rejected[n]);
        char name[96]; (void)snprintf(name,sizeof(name),"critic_%u_rejected_setup_no_counter_wrap",rejected[n]);
        end(name);
    }

    /* Failure accounting happens inside the old operation's guard. Delivery
     * of the pending reset afterward must preserve the diagnostic count but
     * must not carry the old sticky fault into the fresh epoch. */
    fixture(true); fake_reject_next_in=true; fake_on_in_submit=reset_irq;
    assert(write_bytes(64)==64);
    mt_usb_epoch s=mt_usb_bridge_snapshot();
    assert(s.current==2 && s.io_failures==1 && !s.faults && !s.configured);
    assert(fake_host_size==0 && fake_record_count==1 && !fake_records[0].accepted);
    assert(write_bytes(64)==0); fake_pump();
    setup(0,TUSB_REQ_SET_CONFIGURATION,1,0,0,true);
    assert(mt_usb_bridge_snapshot().configured==2 && mt_usb_bridge_snapshot().io_failures==1);
    end("critic_failed_old_submit_plus_pending_reset");
    return 0;
}
