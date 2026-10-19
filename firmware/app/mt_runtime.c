#include "mt_runtime.h"
#include <stddef.h>
#if !CFG_MT_USB_BRIDGE
#error "Runtime owner requires the guarded USB bridge"
#endif

static uint32_t adc_read(void *context, uint32_t address) {
    mt_runtime *r = context;
    return r->mmio.read32(r->mmio.context, address);
}
static void adc_write(void *context, uint32_t address, uint32_t value) {
    mt_runtime *r = context;
    r->mmio.write32(r->mmio.context, address, value);
}
static uint32_t adc_time(void *context) {
    mt_runtime *r = context;
    return mt_timebase_now_us(r->clock);
}
static int stable(void *context) {
    mt_runtime *r = context;
    uint32_t key, token;
    int ok;
    if (!r || !r->initialized || r->pending || r->clock_fault ||
        !mt_timebase_healthy(r->clock)) return 0;
    key = mt_usb_bridge_lock();
    mt_usb_epoch snapshot = mt_usb_bridge_snapshot();
    token = mt_usb_bridge_actor();
    if (!token) token = snapshot.current;
    ok = token == r->epoch && snapshot.soft_serial == r->soft_serial &&
        mt_usb_epoch_app_io(&snapshot, token);
    mt_usb_bridge_unlock(key);
    return ok;
}
static uint32_t commit_enter(void *context) {
    (void)context;
    return mt_usb_bridge_lock();
}
static int commit_allowed(void *context) {
    mt_runtime *r = context;
    /* Called inside the commit guard, after the driver's fresh clock read.
     * Refresh again, never substitute the current epoch for the command actor. */
    if (!mt_usb_bridge_actor() || !mt_timebase_update(r->clock)) return 0;
    return stable(r);
}
static void commit_leave(void *context, uint32_t previous) {
    (void)context;
    mt_usb_bridge_unlock(previous);
}
static int commit_timed_allowed(void *context, uint32_t *us) {
    mt_runtime *r = context;
    uint32_t observed_us, observed_ms;
    /* The returned sample belongs to this fresh full validation, not a cached
     * healthy flag. The guard preserves the original command actor throughout.
     * Its validation time remains part of the measured CEN bracket. */
    if (!mt_usb_bridge_actor() ||
        !mt_timebase_snapshot(r->clock, &observed_us, &observed_ms) ||
        !stable(r)) return 0;
    *us = observed_us;
    return 1;
}
mt_runtime_result mt_runtime_init(mt_runtime *r, mt_timebase *clock,
    const mt_runtime_mmio *mmio, uint32_t hse_hz,
    mt_usb_lock_fn enter, mt_usb_unlock_fn leave,
    uint32_t firmware_build, unsigned require_dtr) {
    mt_runtime_mmio saved_mmio;
    mt_adc_dma_io_t adc_io;
    mt_adc_dma_start_guard guard;
    if (!r || r->initialized || !clock || !mmio || !mmio->read32 ||
        !mmio->write32 || !enter || !leave || require_dtr > 1u || tud_inited())
        return MT_RUNTIME_ARGUMENT;
    if (!mt_timebase_update(clock)) return MT_RUNTIME_TIME_FAULT;
    saved_mmio = *mmio;
    *r = (mt_runtime){0};
    r->clock = clock; r->mmio = saved_mmio;
    adc_io.read32 = adc_read; adc_io.write32 = adc_write;
    adc_io.now_us = adc_time; adc_io.user = r;
    if (mt_adc_dma_init(&r->adc, &adc_io, hse_hz) != MT_ADC_DMA_OK)
        return MT_RUNTIME_ARGUMENT;
    guard.enter = commit_enter; guard.allowed = commit_allowed;
    guard.leave = commit_leave; guard.context = r;
    guard.timed_allowed = commit_timed_allowed;
    if (mt_adc_control_init_coherent_guarded(&r->binding, &r->control, &r->adc,
            mt_timebase_now_ms, clock, firmware_build, &guard,
            mt_timebase_snapshot) != MT_CONTROL_OK ||
        mt_control_rx_init(&r->rx, &r->control) != MT_CONTROL_OK ||
        mt_usb_cdc_init(&r->usb, &r->rx, stable, r, require_dtr) != MT_CONTROL_OK)
        return MT_RUNTIME_ARGUMENT;
    mt_usb_bridge_init(enter, leave);
    r->initialized = 1;
    return MT_RUNTIME_OK;
}
static int quiescent(const mt_runtime *r) {
    /* hardware_running alone can be false after a partially failed start. */
    return !r->control.hardware_running && !r->adc.owns_peripherals &&
           r->adc.state == MT_ADC_DMA_STOPPED;
}
static int synchronize_loss(mt_runtime *r) {
    mt_usb_epoch s = mt_usb_bridge_snapshot();
    if (s.current != r->epoch || s.faults != r->faults ||
        s.exhausted != r->exhausted) {
        r->epoch = s.current; r->faults = s.faults; r->exhausted = s.exhausted;
        r->soft_serial = s.soft_serial;
        r->pending = 2; /* hard loss supersedes any soft pause */
    } else if (s.soft_serial != r->soft_serial) {
        r->soft_serial = s.soft_serial;
        if (!r->pending) r->pending = 1;
    }
    if (!r->pending) return 1;
    if (r->pending == 2 && r->epoch)
        (void)mt_usb_cdc_hard_loss(&r->usb, r->epoch);
    else {
        (void)mt_usb_cdc_soft_pause(&r->usb);
        /* CLOSED/QUARANTINED soft_pause is intentionally a no-op. */
        if (!quiescent(r)) (void)mt_control_rx_transport_lost(&r->rx);
    }
    if (!quiescent(r)) return 0;
    r->pending = 0;
    /* A stronger receipt than the generic bridge requires: this owner waits
     * for successful actual Stop/readback. Stale cleanup cannot open E+1. */
    mt_usb_bridge_cleaned(r->epoch);
    return 1;
}
static mt_runtime_result time_fault(mt_runtime *r) {
    mt_usb_epoch s = mt_usb_bridge_snapshot();
    r->clock_fault = 1; r->pending = 2;
    if (s.current) (void)mt_usb_cdc_hard_loss(&r->usb, s.current);
    else (void)mt_control_rx_transport_lost(&r->rx);
    /* Never mark USB cleanup or restart the clock on an invalid timebase. */
    return MT_RUNTIME_TIME_FAULT;
}
static mt_runtime_result step(mt_runtime *r) {
    mt_control_result result;
    uint32_t now_ms;
    if (!mt_timebase_update(r->clock) || r->clock_fault) return time_fault(r);
    if (!synchronize_loss(r)) return MT_RUNTIME_RECOVERY;
    tud_task_ext(0, false);
    if (!mt_timebase_update(r->clock)) return time_fault(r);
    if (!synchronize_loss(r)) return MT_RUNTIME_RECOVERY;
    if (!stable(r)) return MT_RUNTIME_IDLE;
    if (r->usb.state != MT_USB_OPEN) {
        if (mt_usb_cdc_open_epoch(&r->usb, r->epoch) != MT_CONTROL_OK)
            return MT_RUNTIME_IDLE;
    }
    result = mt_adc_control_service(&r->binding);
    if (result == MT_CONTROL_RECOVERY || result == MT_CONTROL_ARGUMENT) {
        r->pending = 1;
        return MT_RUNTIME_RECOVERY;
    }
    now_ms = mt_timebase_now_ms(r->clock);
    if (!mt_timebase_healthy(r->clock)) return time_fault(r);
    result = mt_usb_cdc_step(&r->usb, now_ms);
    if (!mt_timebase_update(r->clock)) return time_fault(r);
    if (!synchronize_loss(r)) return MT_RUNTIME_RECOVERY;
    if (result == MT_CONTROL_RECOVERY || result == MT_CONTROL_ARGUMENT) {
        r->pending = 1;
        return MT_RUNTIME_RECOVERY;
    }
    return stable(r) ? MT_RUNTIME_OK : MT_RUNTIME_IDLE;
}
mt_runtime_result mt_runtime_step(mt_runtime *r) {
    mt_runtime_result result;
    if (!r || !r->initialized || r->in_step || !tud_inited())
        return MT_RUNTIME_ARGUMENT;
    r->in_step = 1;
    result = step(r);
    r->in_step = 0;
    return result;
}
