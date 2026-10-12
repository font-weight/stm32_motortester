#include "mt_clock_startup.h"
#include <stddef.h>
#define CR UINT32_C(0x40021000)
#define CFGR UINT32_C(0x40021004)
#define CIR UINT32_C(0x40021008)
#define APB2RSTR UINT32_C(0x4002100c)
#define APB1RSTR UINT32_C(0x40021010)
#define AHBENR UINT32_C(0x40021014)
#define APB2ENR UINT32_C(0x40021018)
#define APB1ENR UINT32_C(0x4002101c)
#define FLASH UINT32_C(0x40022000)
#define CR_MASK UINT32_C(0x030f00fb)
#define CR_RESET UINT32_C(0x83)
#define HSE_ON UINT32_C(0x10000)
#define HSE_READY UINT32_C(0x20000)
#define HSE_BYP UINT32_C(0x40000)
#define PLL_ON UINT32_C(0x1000000)
#define PLL_READY UINT32_C(0x2000000)
#define CFGR_MASK UINT32_C(0x077fffff)
#define PROFILE_MASK UINT32_C(0x007ffff0)
#define APB1_MASK UINT32_C(0x1ae64807)
#define APB2_MASK UINT32_C(0x5e7d)

uint32_t mt_clock_mmio_read32(void *context, uint32_t address) {
    (void)context; return *(volatile uint32_t *)(uintptr_t)address;
}
void mt_clock_mmio_write32(void *context, uint32_t address, uint32_t value) {
    (void)context; *(volatile uint32_t *)(uintptr_t)address = value;
}
static void ordering(void) {
#if defined(__arm__) || defined(__thumb__)
    __asm__ volatile ("dsb 0xf\n\tisb 0xf" ::: "memory");
#else
    __asm__ volatile ("" ::: "memory");
#endif
}
mt_clock_result mt_clock_plan_72(uint32_t hz, mt_clock_hse_mode mode,
                                mt_clock_plan *plan) {
    if (!plan || hz < 4000000u || hz > 16000000u ||
        (mode != MT_CLOCK_CRYSTAL && mode != MT_CLOCK_BYPASS))
        return MT_CLOCK_ARGUMENT;
    for (uint32_t divisor = 1; divisor <= 2u; ++divisor) {
        uint32_t input, multiplier;
        if (hz % divisor) continue;
        input = hz / divisor;
        if (72000000u % input) continue;
        multiplier = 72000000u / input;
        if (multiplier < 2u || multiplier > 16u) continue;
        *plan = (mt_clock_plan){hz, divisor, multiplier,
            UINT32_C(0x18400) | ((divisor - 1u) << 17) |
            ((multiplier - 2u) << 18), mode};
        return MT_CLOCK_OK;
    }
    return MT_CLOCK_ARGUMENT;
}
static uint32_t rd(mt_clock_startup *s, uint32_t reg) {
    return s->io.read32(s->io.context, reg);
}
static void update(mt_clock_startup *s, uint32_t reg, uint32_t mask, uint32_t value) {
    uint32_t old = rd(s, reg);
    s->io.write32(s->io.context, reg, (old & ~mask) | value);
}
static int wait_bits(mt_clock_startup *s, uint32_t reg, uint32_t mask,
                     uint32_t value, uint32_t polls) {
    for (uint32_t n = 0; n < polls; ++n)
        if ((rd(s, reg) & mask) == value) return 1;
    return 0;
}
static void snapshot(mt_clock_startup *s) {
    s->last_cr = rd(s, CR); s->last_cfgr = rd(s, CFGR);
    s->last_flash = rd(s, FLASH);
}
static int domains_idle(mt_clock_startup *s) {
    return !(rd(s, APB1ENR) & APB1_MASK) && !(rd(s, APB2ENR) & APB2_MASK) &&
        !(rd(s, APB1RSTR) & APB1_MASK) && !(rd(s, APB2RSTR) & APB2_MASK) &&
        (rd(s, AHBENR) & 0x55u) == 0x14u && !(rd(s, CIR) & 0x1f80u);
}
static int on_hsi(mt_clock_startup *s) {
    return (rd(s, CR) & 3u) == 3u && !(rd(s, CFGR) & 15u);
}
static mt_clock_result fail(mt_clock_startup *s, mt_clock_result cause, uint32_t polls) {
    s->cause = cause; s->recovery = MT_CLOCK_RESET_REQUIRED;
    /* A timed-out PLL switch may still be pending. Confirm both SW and SWS
     * before disabling either possible active source; never lower latency. */
    update(s, CR, 1u, 1u);
    if (!wait_bits(s, CR, 3u, 3u, polls)) goto done;
    update(s, CFGR, 3u, 0);
    if (!wait_bits(s, CFGR, 15u, 0, polls) || !on_hsi(s)) goto done;
    s->recovery = MT_CLOCK_HSI_CONFIRMED;
    update(s, CR, PLL_ON, 0);
    if (!wait_bits(s, CR, PLL_ON | PLL_READY, 0, polls)) goto done;
    if (!on_hsi(s)) { s->recovery = MT_CLOCK_RESET_REQUIRED; goto done; }
    update(s, CR, HSE_ON, 0);
    if (!wait_bits(s, CR, HSE_ON | HSE_READY, 0, polls)) goto done;
    if (on_hsi(s)) s->recovery = MT_CLOCK_SOURCES_OFF;
    else s->recovery = MT_CLOCK_RESET_REQUIRED;
 done:
    snapshot(s);
    if (s->recovery != MT_CLOCK_RESET_REQUIRED &&
        ((s->last_cr & 3u) != 3u || (s->last_cfgr & 15u)))
        s->recovery = MT_CLOCK_RESET_REQUIRED;
    s->active = 0;
    return cause;
}
static int matches(mt_clock_startup *s, uint32_t cr, uint32_t cfgr) {
    snapshot(s);
    return (s->last_cr & CR_MASK) == cr &&
        (s->last_cfgr & CFGR_MASK) == cfgr &&
        (s->last_flash & 0x3fu) == 0x32u && domains_idle(s);
}
mt_clock_result mt_clock_apply(mt_clock_startup *s, const mt_clock_io *io,
    uint32_t hz, mt_clock_hse_mode mode, uint32_t polls) {
    mt_clock_plan plan;
    uint32_t target_cr;
    if (!s || !io || !io->read32 || !io->write32 || !polls ||
        polls > MT_CLOCK_MAX_POLLS || mt_clock_plan_72(hz, mode, &plan) != MT_CLOCK_OK)
        return MT_CLOCK_ARGUMENT;
    if (s->active || s->attempted) return MT_CLOCK_BUSY;
    s->active = 1; s->io = *io; s->plan = plan;
    s->applied = 0; s->recovery = MT_CLOCK_RECOVERY_NONE;
    snapshot(s);
    if ((s->last_cr & CR_MASK) != CR_RESET || (s->last_cfgr & CFGR_MASK) ||
        (s->last_flash & 0x3fu) != 0x30u || !domains_idle(s)) {
        s->cause = MT_CLOCK_NOT_RESET; s->active = 0;
        return s->cause;
    }
    s->attempted = 1;
    update(s, FLASH, 0x1fu, 0x12u);
    if (!wait_bits(s, FLASH, 0x3fu, 0x32u, polls)) return fail(s, MT_CLOCK_FLASH, polls);
    ordering();
    target_cr = CR_RESET | (mode == MT_CLOCK_BYPASS ? HSE_BYP : 0u);
    update(s, CR, HSE_BYP, target_cr & HSE_BYP);
    if ((rd(s, CR) & CR_MASK) != target_cr) return fail(s, MT_CLOCK_CONFIG, polls);
    update(s, CR, HSE_ON, HSE_ON);
    if (!wait_bits(s, CR, HSE_READY, HSE_READY, polls)) return fail(s, MT_CLOCK_HSE_TIMEOUT, polls);
    target_cr |= HSE_ON | HSE_READY;
    if (!matches(s, target_cr, 0)) return fail(s, MT_CLOCK_CONFIG, polls);
    update(s, CFGR, PROFILE_MASK, plan.cfgr_fields);
    if (!matches(s, target_cr, plan.cfgr_fields)) return fail(s, MT_CLOCK_CONFIG, polls);
    update(s, CR, PLL_ON, PLL_ON);
    if (!wait_bits(s, CR, PLL_READY, PLL_READY, polls)) return fail(s, MT_CLOCK_PLL_TIMEOUT, polls);
    target_cr |= PLL_ON | PLL_READY;
    if (!matches(s, target_cr, plan.cfgr_fields)) return fail(s, MT_CLOCK_CONFIG, polls);
    update(s, CFGR, 3u, 2u);
    if (!wait_bits(s, CFGR, 15u, 10u, polls)) return fail(s, MT_CLOCK_SWITCH_TIMEOUT, polls);
    ordering();
    if (!matches(s, target_cr, plan.cfgr_fields | 10u)) return fail(s, MT_CLOCK_FINAL_STATE, polls);
    s->cause = MT_CLOCK_OK; s->applied = 1; s->active = 0;
    return MT_CLOCK_OK;
}
