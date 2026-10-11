#ifndef MT_CLOCK_STARTUP_H
#define MT_CLOCK_STARTUP_H
#include <stdint.h>
#define MT_CLOCK_MAX_POLLS UINT32_C(1000000)
typedef enum { MT_CLOCK_CRYSTAL, MT_CLOCK_BYPASS } mt_clock_hse_mode;
typedef enum {
    MT_CLOCK_OK, MT_CLOCK_ARGUMENT, MT_CLOCK_BUSY, MT_CLOCK_NOT_RESET,
    MT_CLOCK_FLASH, MT_CLOCK_CONFIG, MT_CLOCK_HSE_TIMEOUT,
    MT_CLOCK_PLL_TIMEOUT, MT_CLOCK_SWITCH_TIMEOUT, MT_CLOCK_FINAL_STATE
} mt_clock_result;
typedef enum {
    MT_CLOCK_RECOVERY_NONE, MT_CLOCK_HSI_CONFIRMED,
    MT_CLOCK_SOURCES_OFF, MT_CLOCK_RESET_REQUIRED
} mt_clock_recovery;
typedef struct {
    uint32_t (*read32)(void *context, uint32_t address);
    void (*write32)(void *context, uint32_t address, uint32_t value);
    void *context;
} mt_clock_io;
typedef struct {
    uint32_t hse_hz, hse_divisor, pll_multiplier, cfgr_fields;
    mt_clock_hse_mode mode;
} mt_clock_plan;
typedef struct {
    mt_clock_io io;
    mt_clock_plan plan;
    mt_clock_result cause;
    mt_clock_recovery recovery;
    uint32_t last_cr, last_cfgr, last_flash;
    unsigned active, attempted, applied;
} mt_clock_startup;
/* Pure arithmetic. HSE4..16MHz is the existing ADC compatibility bound.
 * Exact PLL72 profile only; no default board oscillator or measured accuracy.
 * Successful plan means nominal HCLK/APB2/TIM2/3=72MHz, APB1=36MHz,
 * ADC=12MHz and USB=48MHz after a separately successful apply. */
mt_clock_result mt_clock_plan_72(uint32_t verified_hse_hz,
    mt_clock_hse_mode mode, mt_clock_plan *plan);
/* Zero-init context after a genuine system reset. Privileged early boot only,
 * no live peripherals/SysTick/IRQ writers; clock arguments must be verified.
 * Context/inputs do not overlap. Paired prompt callbacks, no reentry.
 * Invalid entry performs no writes. Once attempted, no retry before real reset.
 * polls is a per-wait read-count budget, NOT milliseconds. Only OK publishes
 * applied=1; recovery does not permit consumer startup or another attempt. */
mt_clock_result mt_clock_apply(mt_clock_startup *startup, const mt_clock_io *io,
    uint32_t verified_hse_hz, mt_clock_hse_mode mode, uint32_t polls);
uint32_t mt_clock_mmio_read32(void *context, uint32_t address);
void mt_clock_mmio_write32(void *context, uint32_t address, uint32_t value);
#endif
