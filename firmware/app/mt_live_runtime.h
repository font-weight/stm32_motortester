#ifndef MT_LIVE_RUNTIME_H
#define MT_LIVE_RUNTIME_H
#include "mt_live_adc.h"
#include "../platform/mt_live_clock.h"
#include "../transport/mt_usb_bridge.h"

typedef struct {
    uint32_t (*read32)(void *context, uint32_t address);
    void (*write32)(void *context, uint32_t address, uint32_t value);
    void *context;
} mt_live_runtime_mmio;
typedef enum {
    MT_LIVE_RUNTIME_OK, MT_LIVE_RUNTIME_IDLE, MT_LIVE_RUNTIME_RECOVERY,
    MT_LIVE_RUNTIME_TIME_FAULT, MT_LIVE_RUNTIME_ARGUMENT, MT_LIVE_RUNTIME_ADC_UNSAFE
} mt_live_runtime_result;
/* Stream-only owner; NEVER allocate/run a legacy runtime beside this owner.
 * Zeroed static DMA-accessible storage. No reinitialization while live.
 * TIME/MMIO/guard contexts outlive this object and do not overlap it.
 * No other application writer or CDC completion callback appends TX bytes.
 */
typedef struct {
    mt_adc_dma_t adc;
    mt_live_device device;
    mt_live_adc acquisition;
    mt_live_clock clock64;
    mt_timebase *clock;
    mt_live_runtime_mmio mmio;
    uint32_t epoch, soft_serial, faults;
    unsigned exhausted, pending, initialized, in_step, clock_fault;
    unsigned require_dtr, open, rx_flush_pending, writer_active;
    uint8_t carry[64];
    uint32_t carry_used, carry_offset;
} mt_live_runtime;
/* Before tusb_init, after healthy fixed-clock startup, while interrupts masked.
 * Requires exclusive ADC1/TIM3/DMA1ch1 ownership reservation. Enables their
 * clock gates preserving other bits, then rejects any active/reset peripheral
 * without taking it over. Does not configure USB descriptors/NVIC or start ADC.
 * The supplied full TX transaction bound is a qualification input, NOT derived
 * by this module. Native register/fake-DCD tests do not qualify that bound. */
mt_live_runtime_result mt_live_runtime_init(mt_live_runtime *runtime,
    mt_timebase *clock, const mt_live_runtime_mmio *mmio, uint32_t hse_hz,
    mt_usb_lock_fn enter, mt_usb_unlock_fn leave, uint32_t firmware_build,
    unsigned require_dtr, uint32_t qualified_transaction_bound_ms);
/* One bounded task opportunity, ADC read/offer, <=64 RX bytes, one live TX step
 * and flush. TX step has a save/restore IRQ guard through final core commit.
 * CTR is rearmed only after ADC/TIME service and no queued USB task work.
 * A finite operation count is not a measured service/WCET/throughput bound. */
mt_live_runtime_result mt_live_runtime_step(mt_live_runtime *runtime);
#endif
