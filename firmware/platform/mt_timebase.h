#ifndef MT_TIMEBASE_H
#define MT_TIMEBASE_H
#include <stdint.h>
#define MT_TIMEBASE_MAX_DELTA 32000u
/* Foreground-only, prompt aligned32 MMIO callbacks; no IRQ/reentrancy. */
typedef struct {
    uint32_t (*read32)(void *context, uint32_t address);
    void (*write32)(void *context, uint32_t address, uint32_t value);
    void *context;
} mt_timebase_io;
typedef enum {
    MT_TIMEBASE_STOPPED = 0, MT_TIMEBASE_RUNNING, MT_TIMEBASE_FAULTED
} mt_timebase_state;
typedef enum {
    MT_TIMEBASE_NO_FAULT = 0, MT_TIMEBASE_CLOCK_FAULT,
    MT_TIMEBASE_TIMER_FAULT, MT_TIMEBASE_SERVICE_FAULT, MT_TIMEBASE_STOP_FAULT
} mt_timebase_fault;
typedef struct {
    mt_timebase_io io;
    uint32_t clock_cr, clock_cfgr;
    uint32_t us, ms, remainder_us;
    uint16_t last_counter;
    mt_timebase_state state;
    mt_timebase_fault fault;
    unsigned initialized, owns_timer;
} mt_timebase;
/* Zero-initialize storage before its first init. Use nonoverlapping objects,
 * with exclusive TIM2 and TRGO/CC consumers. Init refuses an owned/live context.
 * Startup already enabled its APB1 gate, deasserted reset, and verified nominal
 * TIM2 input72MHz for the observed RCC state. This argument is NOT measurement.
 * No RCC/GPIO/NVIC writes. Reinit is forbidden until consumers are stopped and
 * all old timestamps invalidated. Failed Stop retains ownership. */
int mt_timebase_init(mt_timebase *clock, const mt_timebase_io *io,
                     uint32_t verified_timer_hz);
/* Returns1 with fresh state only while healthy; sticky faults require reset of
 * time-dependent application state. Fewer than65536 timer increments may pass
 * between snapshots. Observed delta>32000 faults; aliased multiple wraps cannot
 * be detected. Maintain the clock in ALL states, including USB idle/backpressure.
 * Active ADC still requires its much tighter <2560us service deadline. */
int mt_timebase_update(mt_timebase *clock);
/* Reports latched state only; update performs fresh register observations. */
int mt_timebase_healthy(const mt_timebase *clock);
/* These update the same state and return last good value on fault. Callers MUST
 * check health before AND after each time-using operation, and before output.
 * A stale value is not a valid clock. No sleep/debug-halt continuity guarantee. */
uint32_t mt_timebase_now_us(void *clock);
uint32_t mt_timebase_now_ms(void *clock);
/* One fully validated CNT observation supplies both modulo clocks. Foreground
 * only, no reentrancy; outputs must be distinct and outside the clock object.
 * Returns 0 without publishing outputs on any fault or invalid argument. */
int mt_timebase_snapshot(void *clock, uint32_t *us, uint32_t *ms);
/* Stop TIM2 only after its application consumers have been stopped. */
int mt_timebase_stop(mt_timebase *clock);
#endif
