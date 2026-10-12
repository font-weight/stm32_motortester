#ifndef MT_RUNTIME_H
#define MT_RUNTIME_H
#include "mt_adc_control.h"
#include "mt_control_rx.h"
#include "mt_usb_cdc.h"
#include "mt_usb_bridge.h"
#include "mt_timebase.h"

typedef struct {
    uint32_t (*read32)(void *context, uint32_t address);
    void (*write32)(void *context, uint32_t address, uint32_t value);
    void *context;
} mt_runtime_mmio;
typedef enum {
    MT_RUNTIME_OK, MT_RUNTIME_IDLE, MT_RUNTIME_RECOVERY,
    MT_RUNTIME_TIME_FAULT, MT_RUNTIME_ARGUMENT
} mt_runtime_result;
/* Static DMA-accessible storage, one foreground owner, no ISR/reentrancy.
 * Zero-initialize before first init. No live reinitialization. Clock and MMIO
 * context outlive this object and do not overlap its storage. */
typedef struct {
    mt_adc_dma_t adc;
    mt_control control;
    mt_control_rx rx;
    mt_usb_cdc usb;
    mt_adc_control binding;
    mt_timebase *clock;
    mt_runtime_mmio mmio;
    uint32_t epoch, soft_serial, faults;
    unsigned exhausted, pending, clock_fault, initialized, in_step;
} mt_runtime;
/* Before tusb_init, after external startup initialized a healthy TIM2 clock
 * and quiescent ADC1/TIM3/DMA1ch1. Does not configure RCC, descriptors or NVIC.
 * Initializes the singleton USB bridge with a proven save/restore guard.
 * ADC now_us and controller now_ms are wired to the SAME supplied clock.
 */
mt_runtime_result mt_runtime_init(mt_runtime *runtime, mt_timebase *clock,
    const mt_runtime_mmio *mmio, uint32_t verified_hse_hz,
    mt_usb_lock_fn enter, mt_usb_unlock_fn leave,
    uint32_t firmware_build, unsigned require_dtr);
/* USB must be initialized. One event, at most one ADC block and one bounded
 * adapter step. Maintain service <2560us while sampling; operation counts do
 * not prove this timing. Clock faults latch terminally; no implicit reinit.
 */
mt_runtime_result mt_runtime_step(mt_runtime *runtime);
#endif
