#include "mt_live_runtime.h"
#include "../platform/mt_usb_ctr.h"
#include "tusb.h"
#include <stddef.h>
#if !CFG_MT_USB_BRIDGE || !CFG_MT_USB_CTR_FAIRNESS
#error "Stream runtime requires guarded USB and opt-in CTR fairness"
#endif
#if CFG_TUD_CDC != 1 || CFG_TUD_CDC_TX_OVERWRITABLE_IF_NOT_CONNECTED != 0 || \
    CFG_TUD_CDC_RX_PERSISTENT != 0 || CFG_TUD_CDC_TX_PERSISTENT != 0
#error "Stream runtime requires one non-overwritable, nonpersistent CDC interface"
#endif
static uint32_t adc_read(void *context, uint32_t address) {
    mt_live_runtime *r=context; return r->mmio.read32(r->mmio.context,address);
}
static void adc_write(void *context, uint32_t address, uint32_t value) {
    mt_live_runtime *r=context; r->mmio.write32(r->mmio.context,address,value);
}
static uint32_t adc_time(void *context) {
    mt_live_runtime *r=context; return mt_timebase_now_us(r->clock);
}
static uint64_t live_time(void *context) {
    mt_live_runtime *r=context; return mt_live_clock_now(&r->clock64);
}
static int stable(mt_live_runtime *r) {
    uint32_t key,token; int ok;
    if(!r || !r->initialized || r->pending || r->clock_fault || r->clock64.failed ||
       !mt_timebase_healthy(r->clock))return 0;
    key=mt_usb_bridge_lock();
    mt_usb_epoch s=mt_usb_bridge_snapshot(); token=mt_usb_bridge_actor();
    if(!token)token=s.current;
    ok=token==r->epoch && s.soft_serial==r->soft_serial && mt_usb_epoch_app_io(&s,token);
    mt_usb_bridge_unlock(key); return ok;
}
static int usable(mt_live_runtime *r) {
    return r->open && stable(r) && tud_cdc_n_ready(0) &&
        (!r->require_dtr || tud_cdc_n_connected(0));
}
static uint32_t commit_enter(void *context) { (void)context; return mt_usb_bridge_lock(); }
static void commit_leave(void *context,uint32_t key) { (void)context; mt_usb_bridge_unlock(key); }
static int commit_allowed(void *context) {
    mt_live_runtime *r=context;
    return mt_usb_bridge_actor() && mt_timebase_update(r->clock) && usable(r);
}
static int commit_timed(void *context,uint32_t *us) {
    mt_live_runtime *r=context; uint32_t observed_us,observed_ms;
    if(!mt_usb_bridge_actor() || !mt_timebase_snapshot(r->clock,&observed_us,&observed_ms) ||
       !usable(r))return 0;
    *us=observed_us; return 1;
}
static int live_start(void *context,uint32_t generation) {
    mt_live_runtime *r=context;
    return usable(r) && mt_usb_bridge_actor() && mt_live_adc_start(&r->acquisition,generation);
}
static void live_stop(void *context,mt_live_snapshot *snapshot) {
    mt_live_runtime *r=context; mt_live_adc_quiesce(&r->acquisition,snapshot);
}
static size_t live_write(void *context,const uint8_t *bytes,size_t length) {
    mt_live_runtime *r=context; mt_usb_scope scope; size_t accepted;
    if(!r || !r->initialized || !r->in_step || r->writer_active ||
       (!bytes && length) || length>64u)return SIZE_MAX;
    if(!length || !mt_usb_bridge_app_enter(&scope))return 0;
    if(!usable(r)) { mt_usb_bridge_leave(&scope); return 0; }
    r->writer_active=1;
    accepted=tud_cdc_n_write(0,bytes,(uint32_t)length);
    r->writer_active=0;
    mt_usb_bridge_leave(&scope);
    /* An epoch loss after the copy never erases a known positive acceptance. */
    return accepted;
}
static int live_isolate(void *context) {
    mt_live_runtime *r=context;
    /* Sole synchronous foreground writer; accepted FIFO bytes remain ordered.
     * No clearing here: hard-loss quarantine is a separate lifecycle action. */
    return r && r->initialized && r->in_step && !r->writer_active && usable(r);
}
static int prepare_adc(mt_live_runtime *r) {
    const uint32_t ahb=0x40021014u,apb2=0x40021018u,apb1=0x4002101cu;
    adc_write(r,ahb,adc_read(r,ahb)|1u);
    adc_write(r,apb2,adc_read(r,apb2)|(1u<<2)|(1u<<9));
    adc_write(r,apb1,adc_read(r,apb1)|(1u<<1));
    return (adc_read(r,ahb)&1u) &&
        (adc_read(r,apb2)&((1u<<2)|(1u<<9)))==((1u<<2)|(1u<<9)) &&
        (adc_read(r,apb1)&(1u<<1)) && !(adc_read(r,0x4002100cu)&(1u<<9)) &&
        !(adc_read(r,0x40021010u)&(1u<<1)) &&
        !(adc_read(r,0x40000400u)&1u) && adc_read(r,0x40012408u)==0u &&
        !(adc_read(r,0x40020008u)&1u);
}
mt_live_runtime_result mt_live_runtime_init(mt_live_runtime *r,mt_timebase *clock,
    const mt_live_runtime_mmio *mmio,uint32_t hse_hz,mt_usb_lock_fn enter,
    mt_usb_unlock_fn leave,uint32_t build,unsigned require_dtr,uint32_t bound_ms) {
    mt_live_runtime_mmio saved; mt_adc_dma_io_t adc_io; mt_adc_dma_start_guard guard;
    mt_live_io io;
    if(!r || r->initialized || !clock || !mmio || !mmio->read32 || !mmio->write32 ||
       !enter || !leave || require_dtr>1u || !bound_ms || bound_ms>=500u || tud_inited())
        return MT_LIVE_RUNTIME_ARGUMENT;
    if(!mt_timebase_update(clock))return MT_LIVE_RUNTIME_TIME_FAULT;
    saved=*mmio; *r=(mt_live_runtime){0}; r->clock=clock; r->mmio=saved;
    r->require_dtr=require_dtr;
    if(!prepare_adc(r))return MT_LIVE_RUNTIME_ADC_UNSAFE;
    adc_io=(mt_adc_dma_io_t){adc_read,adc_write,adc_time,r};
    if(mt_adc_dma_init(&r->adc,&adc_io,hse_hz)!=MT_ADC_DMA_OK ||
       !mt_live_clock_init(&r->clock64,clock))return MT_LIVE_RUNTIME_ARGUMENT;
    guard=(mt_adc_dma_start_guard){.enter=commit_enter,.allowed=commit_allowed,
        .leave=commit_leave,.context=r,.timed_allowed=commit_timed};
    if(!mt_live_adc_init(&r->acquisition,&r->adc,clock,&r->device,&guard))
        return MT_LIVE_RUNTIME_ARGUMENT;
    io=(mt_live_io){.context=r,.clock_ms=live_time,.start=live_start,.quiesce=live_stop,
        .write=live_write,.isolate_tx=live_isolate,.transaction_bound_ms=bound_ms,
        .firmware_build=build};
    if(!mt_live_device_init(&r->device,&io))return MT_LIVE_RUNTIME_ARGUMENT;
    mt_usb_bridge_init(enter,leave); r->initialized=1; return MT_LIVE_RUNTIME_OK;
}
static int quiescent(const mt_live_runtime *r) {
    return r->device.hardware_off && !r->adc.owns_peripherals &&
        r->adc.state==MT_ADC_DMA_STOPPED;
}
static mt_live_runtime_result time_fault(mt_live_runtime *r);
static mt_live_runtime_result loss_result(const mt_live_runtime *r) {
    return r->clock_fault ? MT_LIVE_RUNTIME_TIME_FAULT : MT_LIVE_RUNTIME_RECOVERY;
}
static int synchronize_loss(mt_live_runtime *r) {
    mt_usb_epoch s=mt_usb_bridge_snapshot();
    if(s.current!=r->epoch || s.faults!=r->faults || s.exhausted!=r->exhausted) {
        r->epoch=s.current; r->faults=s.faults; r->exhausted=s.exhausted;
        r->soft_serial=s.soft_serial; r->pending=2;
    } else if(s.soft_serial!=r->soft_serial) {
        r->soft_serial=s.soft_serial; if(!r->pending)r->pending=1;
    }
    if(!r->pending)return 1;
    r->open=0; r->carry_offset=r->carry_used=0;
    r->rx_flush_pending=(r->pending==1u);
    mt_live_device_transport_lost(&r->device);
    if(r->pending==2u)(void)tud_cdc_n_write_clear(0);
    /* Stop callbacks can themselves discover a TIME failure. Never publish a
     * cleanup receipt or dispatch queued RESET using an earlier healthy read. */
    if(live_time(r)==UINT64_MAX || r->clock_fault) { (void)time_fault(r); return 0; }
    if(!quiescent(r))return 0;
    r->pending=0;
    /* Receipt only after actual off, not merely a software stop request. */
    mt_usb_bridge_cleaned(r->epoch); return 1;
}
static mt_live_runtime_result time_fault(mt_live_runtime *r) {
    r->clock_fault=1; r->open=0; r->pending=2;
    r->carry_offset=r->carry_used=0;
    mt_adc_dma_invalidate_stream_history(&r->adc,MT_ADC_DMA_FAULT_CLOCK);
    mt_live_device_transport_lost(&r->device);
    (void)tud_cdc_n_write_clear(0);
    return MT_LIVE_RUNTIME_TIME_FAULT;
}
static mt_live_runtime_result step(mt_live_runtime *r) {
    mt_usb_scope scope; uint32_t key; int serviced,tx_ok;
    if(live_time(r)==UINT64_MAX || r->clock_fault)return time_fault(r);
    if(!synchronize_loss(r))return loss_result(r);
    tud_task_ext(0,false);
    if(live_time(r)==UINT64_MAX)return time_fault(r);
    if(!synchronize_loss(r))return loss_result(r);
    if(!stable(r))return MT_LIVE_RUNTIME_IDLE;
    if(!tud_cdc_n_ready(0) || (r->require_dtr && !tud_cdc_n_connected(0))) {
        if(r->open) { r->pending=1; if(!synchronize_loss(r))return loss_result(r); }
        return MT_LIVE_RUNTIME_IDLE;
    }
    if(!mt_usb_bridge_app_enter(&scope))return MT_LIVE_RUNTIME_IDLE;
    if(!stable(r)) { mt_usb_bridge_leave(&scope); return MT_LIVE_RUNTIME_IDLE; }
    if(!r->open) {
        if(r->rx_flush_pending) {
            tud_cdc_n_read_flush(0);
            if(!stable(r)) { mt_usb_bridge_leave(&scope); return MT_LIVE_RUNTIME_RECOVERY; }
            r->rx_flush_pending=0;
        }
        r->open=1;
    }
    serviced=mt_live_adc_service(&r->acquisition);
    if(live_time(r)==UINT64_MAX) { mt_usb_bridge_leave(&scope); return time_fault(r); }
    if(!stable(r)) { mt_usb_bridge_leave(&scope); (void)synchronize_loss(r); return loss_result(r); }
    if(serviced==-2) { mt_usb_bridge_leave(&scope); return MT_LIVE_RUNTIME_ARGUMENT; }
    if(r->carry_offset==r->carry_used) {
        r->carry_offset=0; r->carry_used=tud_cdc_n_read(0,r->carry,sizeof r->carry);
    }
    while(r->carry_offset<r->carry_used) {
        if(!usable(r))break;
        if(!mt_live_device_receive(&r->device,r->carry+r->carry_offset,1u))break;
        ++r->carry_offset;
    }
    mt_usb_bridge_leave(&scope);
    if(live_time(r)==UINT64_MAX)return time_fault(r);
    if(!synchronize_loss(r))return loss_result(r);
    if(!usable(r))return MT_LIVE_RUNTIME_IDLE;
    /* Preserve epoch and forbid USB ISR preemption through the core's final
     * post-write observation and state publication. Bound still needs proof. */
    key=mt_usb_bridge_lock(); tx_ok=mt_live_device_step(&r->device); mt_usb_bridge_unlock(key);
    if(live_time(r)==UINT64_MAX)return time_fault(r);
    if(!synchronize_loss(r))return loss_result(r);
    if(mt_usb_bridge_app_enter(&scope)) {
        if(usable(r))(void)tud_cdc_n_write_flush(0);
        mt_usb_bridge_leave(&scope);
    }
    if(live_time(r)==UINT64_MAX)return time_fault(r);
    if(!synchronize_loss(r))return loss_result(r);
    return tx_ok ? MT_LIVE_RUNTIME_OK : MT_LIVE_RUNTIME_RECOVERY;
}
mt_live_runtime_result mt_live_runtime_step(mt_live_runtime *r) {
    mt_live_runtime_result result; uint32_t key;
    if(!r || !r->initialized || r->in_step || !tud_inited())return MT_LIVE_RUNTIME_ARGUMENT;
    r->in_step=1; result=step(r);
    if(!r->clock_fault && !r->pending && !r->faults && !r->exhausted &&
       mt_timebase_healthy(r->clock)) {
        key=mt_usb_bridge_lock();
        mt_usb_epoch s=mt_usb_bridge_snapshot();
        /* Do not grant using a stale pre-lock lifecycle observation. PMAOVR
         * can poison an epoch without leaving an event in the task queue. */
        if(s.current==r->epoch && s.soft_serial==r->soft_serial &&
           s.cleaned==s.current && !s.pending_soft && !s.faults && !s.exhausted &&
           !tud_task_event_ready())(void)mt_usb_fsdev_ctr_rearm();
        mt_usb_bridge_unlock(key);
    }
    r->in_step=0; return result;
}
