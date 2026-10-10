/* Independent API-contract checks, complementing the separate sequence model.
 * No IRQ, queue, TinyUSB, hardware or concurrent execution is modeled. */
#include "firmware/transport/mt_usb_epoch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned checks;
#define CK(x) do { ++checks; if (!(x)) { fprintf(stderr,"FAIL:%d %s\n",__LINE__,#x); exit(1); } } while (0)
#define UNCHANGED(expr, expected) do { mt_usb_epoch before=s; CK((expr)==(expected)); CK(memcmp(&before,&s,sizeof s)==0); } while (0)
static void nulls(void) {
    mt_usb_epoch_init(NULL);
    CK(mt_usb_epoch_reset(NULL)==0u); CK(mt_usb_epoch_disconnect(NULL)==0u);
    CK(!mt_usb_epoch_enqueued(NULL,1,1,1)); CK(!mt_usb_epoch_cleaned(NULL,1));
    CK(!mt_usb_epoch_dispatch(NULL,1,1)); CK(!mt_usb_epoch_reset_done(NULL,1));
    CK(!mt_usb_epoch_configured(NULL,1)); CK(!mt_usb_epoch_core_io(NULL,1));
    CK(!mt_usb_epoch_app_io(NULL,1)); CK(!mt_usb_epoch_fail(NULL,1,1));
    CK(!mt_usb_epoch_suspend(NULL,1)); CK(!mt_usb_epoch_resume(NULL,1,1));
}
static mt_usb_epoch state_at(unsigned stage) {
    mt_usb_epoch s;
    uint32_t t;
    mt_usb_epoch_init(&s);
    if (!stage) return s;
    t=mt_usb_epoch_reset(&s); CK(t==1u);
    if (stage==1u) return s;
    CK(mt_usb_epoch_enqueued(&s,t,1,1));
    if (stage==2u) return s;
    CK(mt_usb_epoch_cleaned(&s,t));
    if (stage==3u) return s;
    CK(mt_usb_epoch_reset_done(&s,t));
    if (stage==4u) return s;
    CK(mt_usb_epoch_configured(&s,t));
    if (stage==5u) return s;
    CK(mt_usb_epoch_suspend(&s,t)==1u);
    if (stage==6u) return s;
    CK(mt_usb_epoch_fail(&s,t,MT_USB_EPOCH_QUEUE_LOSS)==0);
    CK(mt_usb_epoch_enqueued(&s,t,0,0)==0);
    if (stage==7u) return s;
    CK(mt_usb_epoch_disconnect(&s)==2u);
    return s;
}
static void stale_and_arguments(void) {
    unsigned stage;
    for (stage=0;stage<9u;++stage) {
        mt_usb_epoch s=state_at(stage);
        const uint32_t tokens[]={0,3,UINT32_MAX};
        unsigned i;
        for (i=0;i<sizeof tokens/sizeof tokens[0];++i) {
            uint32_t t=tokens[i];
            UNCHANGED(mt_usb_epoch_enqueued(&s,t,0,0),0);
            UNCHANGED(mt_usb_epoch_enqueued(&s,t,1,1),0);
            UNCHANGED(mt_usb_epoch_cleaned(&s,t),0);
            UNCHANGED(mt_usb_epoch_reset_done(&s,t),0);
            UNCHANGED(mt_usb_epoch_configured(&s,t),0);
            UNCHANGED(mt_usb_epoch_fail(&s,t,MT_USB_EPOCH_SUBMIT_FAILED),0);
            UNCHANGED(mt_usb_epoch_fail(&s,t,MT_USB_EPOCH_COMPLETION_FAILED),0);
            UNCHANGED(mt_usb_epoch_fail(&s,t,MT_USB_EPOCH_DECONFIGURED),0);
            UNCHANGED(mt_usb_epoch_suspend(&s,t),0);
            UNCHANGED(mt_usb_epoch_resume(&s,t,1),0);
            UNCHANGED(mt_usb_epoch_dispatch(&s,t,0),0);
            UNCHANGED(mt_usb_epoch_dispatch(&s,t,1),0);
            UNCHANGED(mt_usb_epoch_core_io(&s,t),0);
            UNCHANGED(mt_usb_epoch_app_io(&s,t),0);
        }
        /* Invalid flags/reasons must not record even a queue-loss diagnostic. */
        UNCHANGED(mt_usb_epoch_enqueued(&s,s.current,2,0),0);
        UNCHANGED(mt_usb_epoch_enqueued(&s,s.current,0,2),0);
        UNCHANGED(mt_usb_epoch_enqueued(&s,s.current,UINT32_MAX,1),0);
        UNCHANGED(mt_usb_epoch_dispatch(&s,s.current,2),0);
        UNCHANGED(mt_usb_epoch_fail(&s,s.current,0),0);
        UNCHANGED(mt_usb_epoch_fail(&s,s.current,3),0);
        UNCHANGED(mt_usb_epoch_fail(&s,s.current,UINT32_MAX),0);
        UNCHANGED(mt_usb_epoch_resume(&s,s.current,0),0);
        UNCHANGED(mt_usb_epoch_resume(&s,s.current,UINT32_MAX),0);
    }
}
static void observations_and_acknowledgements(void) {
    mt_usb_epoch s=state_at(5);
    uint32_t t=s.current, first,second;
    unsigned i;
    for (i=0;i<100u;++i) {
        UNCHANGED(mt_usb_epoch_core_io(&s,t),1);
        UNCHANGED(mt_usb_epoch_app_io(&s,t),1);
        UNCHANGED(mt_usb_epoch_dispatch(&s,t,0),1);
        UNCHANGED(mt_usb_epoch_dispatch(&s,t,1),0);
    }
    UNCHANGED(mt_usb_epoch_reset_done(&s,t),0);
    CK(mt_usb_epoch_enqueued(&s,t,1,1));
    UNCHANGED(mt_usb_epoch_dispatch(&s,t,1),0);
    first=mt_usb_epoch_suspend(&s,t); second=mt_usb_epoch_suspend(&s,t);
    CK(first==1u && second==2u);
    UNCHANGED(mt_usb_epoch_resume(&s,t,first),0);
    CK(mt_usb_epoch_resume(&s,t,second));
    UNCHANGED(mt_usb_epoch_resume(&s,t,second),0);
    CK(mt_usb_epoch_app_io(&s,t));
    CK(mt_usb_epoch_suspend(&s,t)==3u);
    CK(mt_usb_epoch_reset(&s)==2u);
    UNCHANGED(mt_usb_epoch_resume(&s,t,3),0);
    CK(mt_usb_epoch_enqueued(&s,2,1,1));CK(mt_usb_epoch_cleaned(&s,2));
    CK(mt_usb_epoch_suspend(&s,2)==1u);
    CK(mt_usb_epoch_dispatch(&s,2,1));CK(mt_usb_epoch_reset_done(&s,2));
    CK(!mt_usb_epoch_core_io(&s,2));CK(!mt_usb_epoch_configured(&s,2));
    CK(mt_usb_epoch_resume(&s,2,1));CK(mt_usb_epoch_core_io(&s,2));
    CK(!mt_usb_epoch_app_io(&s,2));CK(mt_usb_epoch_configured(&s,2));
    CK(mt_usb_epoch_app_io(&s,2));
}
static void saturation_fixtures(void) {
    mt_usb_epoch s=state_at(5);
    /* Explicit seeded boundary fixtures, not billions of physical events. */
    s.queue_losses=UINT32_MAX-1u;s.io_failures=UINT32_MAX-1u;
    CK(!mt_usb_epoch_enqueued(&s,1,0,0));CK(s.queue_losses==UINT32_MAX);
    CK(!mt_usb_epoch_enqueued(&s,1,0,0));CK(s.queue_losses==UINT32_MAX);
    CK(mt_usb_epoch_fail(&s,1,MT_USB_EPOCH_COMPLETION_FAILED));
    CK(mt_usb_epoch_fail(&s,1,MT_USB_EPOCH_SUBMIT_FAILED));
    CK(s.io_failures==UINT32_MAX);
    CK(mt_usb_epoch_reset(&s)==2u);
    CK(s.queue_losses==UINT32_MAX && s.io_failures==UINT32_MAX);
    s.soft_serial=UINT32_MAX-1u;
    CK(mt_usb_epoch_suspend(&s,2)==UINT32_MAX);
    CK(mt_usb_epoch_resume(&s,2,UINT32_MAX));
    CK(!mt_usb_epoch_suspend(&s,2));CK(s.faults==MT_USB_EPOCH_SOFT_EXHAUSTED);
    CK(mt_usb_epoch_reset(&s)==3u && !s.faults);
    s.current=UINT32_MAX-1u;
    CK(mt_usb_epoch_reset(&s)==UINT32_MAX);
    CK(mt_usb_epoch_enqueued(&s,UINT32_MAX,1,1));
    CK(mt_usb_epoch_cleaned(&s,UINT32_MAX));
    CK(mt_usb_epoch_reset_done(&s,UINT32_MAX));
    CK(mt_usb_epoch_configured(&s,UINT32_MAX));
    CK(mt_usb_epoch_app_io(&s,UINT32_MAX));
    CK(!mt_usb_epoch_reset(&s)); CK(s.exhausted && (s.faults&MT_USB_EPOCH_EXHAUSTED));
    UNCHANGED(mt_usb_epoch_disconnect(&s),0);
    UNCHANGED(mt_usb_epoch_reset(&s),0);
    UNCHANGED(mt_usb_epoch_cleaned(&s,UINT32_MAX),0);
    UNCHANGED(mt_usb_epoch_configured(&s,UINT32_MAX),0);
    UNCHANGED(mt_usb_epoch_enqueued(&s,UINT32_MAX,0,0),0);
    CK(!mt_usb_epoch_app_io(&s,UINT32_MAX));
}
int main(void) {
    nulls();stale_and_arguments();observations_and_acknowledgements();saturation_fixtures();
    printf("PASS: four supplemental API groups, %u checks.\n",checks);return 0;
}
