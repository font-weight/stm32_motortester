#include "mt_timebase.h"
#include <stddef.h>
#define RCC_CR UINT32_C(0x40021000)
#define RCC_CFGR UINT32_C(0x40021004)
#define RCC_APB1RSTR UINT32_C(0x40021010)
#define RCC_APB1ENR UINT32_C(0x4002101c)
#define TIM_CR1 UINT32_C(0x40000000)
#define TIM_CR2 UINT32_C(0x40000004)
#define TIM_SMCR UINT32_C(0x40000008)
#define TIM_DIER UINT32_C(0x4000000c)
#define TIM_SR UINT32_C(0x40000010)
#define TIM_EGR UINT32_C(0x40000014)
#define TIM_CCMR1 UINT32_C(0x40000018)
#define TIM_CCMR2 UINT32_C(0x4000001c)
#define TIM_CCER UINT32_C(0x40000020)
#define TIM_CNT UINT32_C(0x40000024)
#define TIM_PSC UINT32_C(0x40000028)
#define TIM_ARR UINT32_C(0x4000002c)
#define CLOCK_CR_MASK UINT32_C(0x030f00fb)
#define CLOCK_CFGR_MASK UINT32_C(0x003f3fff)
/* Flatten the validated observation at pinned -Os without removing any
 * register read, short-circuit check or clock-fault handling. */
static inline __attribute__((always_inline)) uint32_t read_reg(mt_timebase *t, uint32_t reg) {
    return t->io.read32(t->io.context, reg);
}
static void write_reg(mt_timebase *t, uint32_t reg, uint32_t value) {
    t->io.write32(t->io.context, reg, value);
}
static inline __attribute__((always_inline)) int valid(const mt_timebase *t) {
    return t && t->initialized && t->io.read32 && t->io.write32;
}
static int fault(mt_timebase *t, mt_timebase_fault why) {
    t->state = MT_TIMEBASE_FAULTED;
    t->fault = why;
    return 0;
}
static inline __attribute__((always_inline)) int accessible(mt_timebase *t) {
    return (read_reg(t, RCC_APB1ENR) & 1u) && !(read_reg(t, RCC_APB1RSTR) & 1u);
}
static inline __attribute__((always_inline)) int clock_matches(mt_timebase *t) {
    return accessible(t) &&
        (read_reg(t, RCC_CR) & CLOCK_CR_MASK) == t->clock_cr &&
        (read_reg(t, RCC_CFGR) & CLOCK_CFGR_MASK) == t->clock_cfgr;
}
static inline __attribute__((always_inline)) int timer_matches(mt_timebase *t) {
    return read_reg(t, TIM_CR1) == 1u && read_reg(t, TIM_CR2) == 0u &&
        read_reg(t, TIM_SMCR) == 0u && read_reg(t, TIM_DIER) == 0u &&
        read_reg(t, TIM_CCER) == 0u && read_reg(t, TIM_CCMR1) == 0u &&
        read_reg(t, TIM_CCMR2) == 0u && read_reg(t, TIM_PSC) == 71u &&
        read_reg(t, TIM_ARR) == 65535u;
}
int mt_timebase_init(mt_timebase *t, const mt_timebase_io *io, uint32_t hz) {
    uint32_t cr, cfgr;
    if (!t || !io || !io->read32 || !io->write32 || hz != 72000000u) return 0;
    if (t->initialized && (t->owns_timer || t->state == MT_TIMEBASE_RUNNING)) return 0;
    *t = (mt_timebase){0}; t->io = *io; t->initialized = 1;
    cr = read_reg(t, RCC_CR); cfgr = read_reg(t, RCC_CFGR);
    t->clock_cr = cr & CLOCK_CR_MASK;
    t->clock_cfgr = cfgr & CLOCK_CFGR_MASK;
    /* The application's nominal72MHz profile uses ready HSE/PLL, HCLK /1,
     * APB1 /2 and timer x2. PLL ratio/HSE accuracy are startup's verified input. */
    if ((cr & UINT32_C(0x03030000)) != UINT32_C(0x03030000) ||
        (cfgr & UINT32_C(0x000107ff)) != UINT32_C(0x0001040a) ||
        !clock_matches(t)) return fault(t, MT_TIMEBASE_CLOCK_FAULT);
    if ((read_reg(t, TIM_CR1) & 1u) || read_reg(t, TIM_DIER) != 0u)
        return fault(t, MT_TIMEBASE_TIMER_FAULT);
    t->owns_timer = 1;
    write_reg(t, TIM_CR1, 0); write_reg(t, TIM_CR2, 0);
    write_reg(t, TIM_SMCR, 0); write_reg(t, TIM_DIER, 0);
    write_reg(t, TIM_CCER, 0); write_reg(t, TIM_CCMR1, 0);
    write_reg(t, TIM_CCMR2, 0); write_reg(t, TIM_PSC, 71);
    write_reg(t, TIM_ARR, 65535); write_reg(t, TIM_EGR, 1);
    write_reg(t, TIM_SR, 0); write_reg(t, TIM_CNT, 0);
    write_reg(t, TIM_CR1, 1);
    if (!clock_matches(t)) return fault(t, MT_TIMEBASE_CLOCK_FAULT);
    if (!timer_matches(t)) return fault(t, MT_TIMEBASE_TIMER_FAULT);
    t->state = MT_TIMEBASE_RUNNING;
    return mt_timebase_update(t);
}
int mt_timebase_update(mt_timebase *t) {
    uint16_t counter, delta;
    uint32_t part;
    if (!valid(t) || t->state != MT_TIMEBASE_RUNNING) return 0;
    if (!clock_matches(t)) return fault(t, MT_TIMEBASE_CLOCK_FAULT);
    if (!timer_matches(t)) return fault(t, MT_TIMEBASE_TIMER_FAULT);
    counter = (uint16_t)read_reg(t, TIM_CNT);
    if (!clock_matches(t)) return fault(t, MT_TIMEBASE_CLOCK_FAULT);
    if (!timer_matches(t)) return fault(t, MT_TIMEBASE_TIMER_FAULT);
    delta = (uint16_t)(counter - t->last_counter);
    if (delta > MT_TIMEBASE_MAX_DELTA) return fault(t, MT_TIMEBASE_SERVICE_FAULT);
    t->last_counter = counter;
    t->us += delta;
    part = t->remainder_us + delta;
    t->ms += part / 1000u;
    t->remainder_us = part % 1000u;
    return 1;
}
int mt_timebase_healthy(const mt_timebase *t) {
    return valid(t) && t->state == MT_TIMEBASE_RUNNING && t->fault == MT_TIMEBASE_NO_FAULT;
}
uint32_t mt_timebase_now_us(void *clock) {
    mt_timebase *t = clock;
    if (!valid(t)) return 0;
    (void)mt_timebase_update(t);
    return t->us;
}
uint32_t mt_timebase_now_ms(void *clock) {
    mt_timebase *t = clock;
    if (!valid(t)) return 0;
    (void)mt_timebase_update(t);
    return t->ms;
}
int mt_timebase_snapshot(void *clock, uint32_t *us, uint32_t *ms) {
    mt_timebase *t = clock;
    if (!us || !ms || us == ms || !mt_timebase_update(t)) return 0;
    *us = t->us;
    *ms = t->ms;
    return 1;
}
int mt_timebase_stop(mt_timebase *t) {
    if (!valid(t)) return 0;
    if (t->owns_timer) {
        /* A missing clock gate/reset assertion prevents trustworthy readback. */
        if (!accessible(t)) return fault(t, MT_TIMEBASE_STOP_FAULT);
        write_reg(t, TIM_CR1, 0);
        if ((read_reg(t, TIM_CR1) & 1u) || !accessible(t))
            return fault(t, MT_TIMEBASE_STOP_FAULT);
        t->owns_timer = 0;
    }
    t->state = MT_TIMEBASE_STOPPED;
    return 1;
}
