#include "mt_boot_time.h"
#include <stddef.h>
#define RCC_CR UINT32_C(0x40021000)
#define RCC_CFGR UINT32_C(0x40021004)
#define RCC_APB1RSTR UINT32_C(0x40021010)
#define RCC_APB1ENR UINT32_C(0x4002101c)
#define FLASH_ACR UINT32_C(0x40022000)
#define CR_MASK UINT32_C(0x030f00fb)
#define CFGR_MASK UINT32_C(0x077fffff)

static uint32_t read_reg(const mt_clock_startup *s, uint32_t address) {
    return s->io.read32(s->io.context, address);
}
static void write_reg(const mt_clock_startup *s, uint32_t address, uint32_t value) {
    s->io.write32(s->io.context, address, value);
}
static int same_clock(const mt_clock_startup *s) {
    return (read_reg(s, RCC_CR) & CR_MASK) == (s->last_cr & CR_MASK) &&
        (read_reg(s, RCC_CFGR) & CFGR_MASK) == (s->last_cfgr & CFGR_MASK) &&
        (read_reg(s, FLASH_ACR) & 0x3fu) == 0x32u &&
        (s->last_flash & 0x3fu) == 0x32u;
}
static mt_boot_time_result finish(mt_boot_time *b, mt_boot_time_result cause) {
    b->cause = cause;
    b->ready = cause == MT_BOOT_TIME_OK ? 1u : 0u;
    b->active = 0;
    return cause;
}
mt_boot_time_result mt_boot_time_start(mt_boot_time *b,
    const mt_clock_startup *s, mt_timebase *t) {
    mt_timebase_io io;
    uint32_t gates, resets;
    if (b == NULL || s == NULL || t == NULL ||
        s->io.read32 == NULL || s->io.write32 == NULL)
        return MT_BOOT_TIME_ARGUMENT;
    if (b->active || b->attempted) return MT_BOOT_TIME_BUSY;
    /* Never destroy or stop a pre-existing timebase, including a faulted one. */
    if (t->initialized || t->owns_timer || t->state != MT_TIMEBASE_STOPPED)
        return MT_BOOT_TIME_ARGUMENT;
    b->active = 1; b->attempted = 1; b->ready = 0;
    if (s->active || s->attempted != 1u || s->applied != 1u ||
        s->cause != MT_CLOCK_OK || s->recovery != MT_CLOCK_RECOVERY_NONE ||
        !same_clock(s)) return finish(b, MT_BOOT_TIME_CLOCK);
    gates = read_reg(s, RCC_APB1ENR);
    resets = read_reg(s, RCC_APB1RSTR);
    if ((gates & 1u) || (resets & 1u))
        return finish(b, MT_BOOT_TIME_NOT_RESET);
    /* Single observed readbacks, not an invented microsecond pulse duration. */
    write_reg(s, RCC_APB1ENR, gates | 1u);
    if (!(read_reg(s, RCC_APB1ENR) & 1u)) return finish(b, MT_BOOT_TIME_GATE);
    write_reg(s, RCC_APB1RSTR, resets | 1u);
    if (!(read_reg(s, RCC_APB1RSTR) & 1u))
        return finish(b, MT_BOOT_TIME_RESET_ASSERT);
    write_reg(s, RCC_APB1RSTR, resets & ~UINT32_C(1));
    if ((read_reg(s, RCC_APB1RSTR) & 1u) ||
        !(read_reg(s, RCC_APB1ENR) & 1u))
        return finish(b, MT_BOOT_TIME_RESET_RELEASE);
    if (!same_clock(s)) return finish(b, MT_BOOT_TIME_CLOCK);
    io.read32 = s->io.read32; io.write32 = s->io.write32;
    io.context = s->io.context;
    if (!mt_timebase_init(t, &io, UINT32_C(72000000)) || !mt_timebase_healthy(t))
        return finish(b, MT_BOOT_TIME_INIT);
    if (!same_clock(s)) return finish(b, MT_BOOT_TIME_FINAL_CLOCK);
    return finish(b, MT_BOOT_TIME_OK);
}
