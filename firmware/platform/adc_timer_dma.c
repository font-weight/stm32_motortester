#include "mt_stream_diag.h"
#include "adc_timer_dma.h"

#include <stddef.h>

/* STM32F103x8/B register addresses, RM0008 chapters 7, 9, 11, 13, 15. */
#define RCC_CR       UINT32_C(0x40021000)
#define RCC_CFGR     UINT32_C(0x40021004)
#define RCC_APB2RSTR UINT32_C(0x4002100c)
#define RCC_APB1RSTR UINT32_C(0x40021010)
#define RCC_AHBENR   UINT32_C(0x40021014)
#define RCC_APB2ENR  UINT32_C(0x40021018)
#define RCC_APB1ENR  UINT32_C(0x4002101c)
#define GPIOA_CRL    UINT32_C(0x40010800)
#define ADC_SR       UINT32_C(0x40012400)
#define ADC_CR1      UINT32_C(0x40012404)
#define ADC_CR2      UINT32_C(0x40012408)
#define ADC_SMPR1    UINT32_C(0x4001240c)
#define ADC_SMPR2    UINT32_C(0x40012410)
#define ADC_SQR1     UINT32_C(0x4001242c)
#define ADC_SQR2     UINT32_C(0x40012430)
#define ADC_SQR3     UINT32_C(0x40012434)
#define ADC_DR       UINT32_C(0x4001244c)
#define TIM_CR1      UINT32_C(0x40000400)
#define TIM_CR2      UINT32_C(0x40000404)
#define TIM_SMCR     UINT32_C(0x40000408)
#define TIM_DIER     UINT32_C(0x4000040c)
#define TIM_SR       UINT32_C(0x40000410)
#define TIM_EGR      UINT32_C(0x40000414)
#define TIM_CCER     UINT32_C(0x40000420)
#define TIM_CNT      UINT32_C(0x40000424)
#define TIM_PSC      UINT32_C(0x40000428)
#define TIM_ARR      UINT32_C(0x4000042c)
#define DMA_ISR      UINT32_C(0x40020000)
#define DMA_IFCR     UINT32_C(0x40020004)
#define DMA_CCR      UINT32_C(0x40020008)
#define DMA_CNDTR    UINT32_C(0x4002000c)
#define DMA_CPAR     UINT32_C(0x40020010)
#define DMA_CMAR     UINT32_C(0x40020014)
#define ADC_ADON     UINT32_C(1)
#define ADC_CAL      (UINT32_C(1) << 2)
#define ADC_RSTCAL   (UINT32_C(1) << 3)
#define ADC_DMA      (UINT32_C(1) << 8)
#define ADC_TIM3     (UINT32_C(4) << 17)
#define ADC_EXTTRIG  (UINT32_C(1) << 20)
#define DMA_EN       UINT32_C(1)
#define DMA_TC       UINT32_C(2)
#define DMA_HT       UINT32_C(4)
#define DMA_TE       UINT32_C(8)
#define DMA_EVENTS   (DMA_TC | DMA_HT | DMA_TE)
/* High priority, 16-bit source/destination, MINC, circular, peripheral->memory.
 * No DMA interrupt enables: this bounded backend is foreground-polled. */
#define DMA_CONFIG   ((UINT32_C(2) << 12) | (UINT32_C(1) << 10) | \
                      (UINT32_C(1) << 8) | (UINT32_C(1) << 7) | \
                      (UINT32_C(1) << 5))
#define CLOCK_MASK   UINT32_C(0x003fffff)

static void barrier(void)
{
#if defined(__arm__) || defined(__thumb__)
    __asm__ volatile ("dsb" ::: "memory");
#else
    __asm__ volatile ("" ::: "memory");
#endif
}

uint32_t mt_adc_dma_mmio_read32(void *user, uint32_t address)
{
    (void)user;
    return *(volatile uint32_t *)(uintptr_t)address;
}

void mt_adc_dma_mmio_write32(void *user, uint32_t address, uint32_t value)
{
    (void)user;
    *(volatile uint32_t *)(uintptr_t)address = value;
}

static uint32_t rd(const mt_adc_dma_t *d, uint32_t reg)
{
    return d->io.read32(d->io.user, reg);
}

static void wr(const mt_adc_dma_t *d, uint32_t reg, uint32_t value)
{
    d->io.write32(d->io.user, reg, value);
}

static uint32_t now(const mt_adc_dma_t *d)
{
    return d->io.now_us(d->io.user);
}

static bool valid(const mt_adc_dma_t *d)
{
    return d != NULL && d->initialized;
}

static uint32_t apb_div(uint32_t bits)
{
    return bits < 4u ? 1u : (UINT32_C(1) << (bits - 3u));
}

static bool clock_plan(mt_adc_dma_t *d, bool initial)
{
    static const uint16_t ahb_divs[16] = {
        1, 1, 1, 1, 1, 1, 1, 1, 2, 4, 8, 16, 64, 128, 256, 512
    };
    uint32_t cfgr = rd(d, RCC_CFGR);
    uint32_t cr = rd(d, RCC_CR);
    uint32_t source = (cfgr >> 2) & 3u;
    uint32_t input;
    uint32_t sysclk;
    uint32_t div1 = apb_div((cfgr >> 8) & 7u);
    uint32_t div2 = apb_div((cfgr >> 11) & 7u);
    uint32_t adcdiv = 2u + 2u * ((cfgr >> 14) & 3u);
    mt_adc_dma_clock_t p;
    if ((cfgr & 3u) != source || source == 3u ||
        (!initial && (cfgr & CLOCK_MASK) != d->clock_cfgr)) {
        return false;
    }
    if (source == 0u || (source == 2u && (cfgr & (1u << 16)) == 0u)) {
        if ((cr & 3u) != 3u) { return false; } /* HSION and HSIRDY */
        input = source == 2u ? 4000000u : 8000000u;
    } else {
        if (d->hse_hz < 4000000u || d->hse_hz > 16000000u ||
            (cr & (3u << 16)) != (3u << 16)) { return false; }
        input = d->hse_hz;
        if (source == 2u && (cfgr & (1u << 17)) != 0u) {
            if ((input & 1u) != 0u) { return false; }
            input /= 2u;
        }
    }
    sysclk = input;
    if (source == 2u) {
        uint32_t mult = ((cfgr >> 18) & 15u) + 2u;
        if ((cr & (3u << 24)) != (3u << 24)) { return false; }
        if (mult > 16u) { mult = 16u; }
        sysclk *= mult;
    }
    if (sysclk > 72000000u || sysclk % ahb_divs[(cfgr >> 4) & 15u] != 0u) {
        return false;
    }
    p.sysclk_hz = sysclk;
    p.hclk_hz = sysclk / ahb_divs[(cfgr >> 4) & 15u];
    if (p.hclk_hz % div1 != 0u || p.hclk_hz % div2 != 0u) { return false; }
    p.pclk1_hz = p.hclk_hz / div1;
    p.pclk2_hz = p.hclk_hz / div2;
    p.timer_hz = p.pclk1_hz * (div1 == 1u ? 1u : 2u);
    p.adc_hz = p.pclk2_hz / adcdiv;
    if (p.pclk1_hz > 36000000u || p.pclk2_hz > 72000000u ||
        p.pclk2_hz % adcdiv != 0u || p.adc_hz != 12000000u ||
        p.timer_hz % UINT32_C(1000000) != 0u ||
        p.timer_hz < UINT32_C(1000000) ||
        p.timer_hz / UINT32_C(1000000) > 65536u) { return false; }
    p.timer_psc = p.timer_hz / UINT32_C(1000000) - 1u;
    p.timer_arr = 19u;
    if (initial) {
        d->clock = p;
        d->clock_cfgr = cfgr & CLOCK_MASK;
    }
    return true;
}

/* Both elapsed time and iteration count are bounded, even for a frozen tick.
 * A nonreturning platform callback remains outside this C module's control. */
static bool wait_clear(const mt_adc_dma_t *d, uint32_t reg, uint32_t mask)
{
    uint32_t start = now(d);
    uint32_t i;
    for (i = 0u; i < MT_ADC_DMA_WAIT_POLLS; ++i) {
        if ((rd(d, reg) & mask) == 0u) { return true; }
        if ((uint32_t)(now(d) - start) >= MT_ADC_DMA_WAIT_US) { return false; }
    }
    return false;
}

static bool stabilize(const mt_adc_dma_t *d)
{
    uint32_t start = now(d);
    uint32_t i;
    for (i = 0u; i < MT_ADC_DMA_WAIT_POLLS; ++i) {
        uint32_t elapsed = (uint32_t)(now(d) - start);
        if (elapsed >= MT_ADC_DMA_WAIT_US) { return false; }
        if (elapsed >= 2u) { return true; }
    }
    return false;
}

static bool gates_enabled(const mt_adc_dma_t *d)
{
    return (rd(d, RCC_AHBENR) & 1u) != 0u &&
           (rd(d, RCC_APB2ENR) & ((1u << 2) | (1u << 9))) == ((1u << 2) | (1u << 9)) &&
           (rd(d, RCC_APB1ENR) & (1u << 1)) != 0u;
}

static void invalidate_history(mt_adc_dma_t *d, mt_adc_dma_fault_t fault)
{
    d->stream_history_valid = false;
    d->stop_dma_known = false;
    d->stop_count = 0u;
    d->stop_dma_end = 0u;
    if (d->first_fault == MT_ADC_DMA_FAULT_NONE) d->first_fault = fault;
}

static bool hardware_off(const mt_adc_dma_t *d)
{
    return gates_enabled(d) &&
        (rd(d, RCC_APB2RSTR) & (1u << 9)) == 0u &&
        (rd(d, RCC_APB1RSTR) & (1u << 1)) == 0u && (rd(d, TIM_CR1) & 1u) == 0u &&
        rd(d, ADC_CR2) == 0u && (rd(d, DMA_CCR) & DMA_EN) == 0u &&
        gates_enabled(d);
}

/* Called only after a verified disable and before clearing sticky DMA flags.
 * Stable count/flags plus an unbroken service history identify at most one
 * pending half and one partial half. Never infer missed circular revolutions.
 */
static bool capture_stopped_dma(mt_adc_dma_t *d, bool history)
{
    uint32_t f = rd(d, DMA_ISR) & DMA_EVENTS;
    uint32_t n = rd(d, DMA_CNDTR);
    uint32_t k = 0u;
    uint32_t stamp = now(d);
    d->stop_captured = true;
    d->stop_dma_known = false;
    d->stop_count = 0u;
    d->stop_dma_end = 0u;
    d->stop_first_index = d->first_sample_index;
    d->stop_offset = d->next_half ? MT_ADC_DMA_BLOCK_SAMPLES : 0u;
    if ((f & DMA_TE) != 0u) {
        invalidate_history(d, MT_ADC_DMA_FAULT_TRANSFER);
        return hardware_off(d);
    }
    if (!history || !d->stream_history_valid) return hardware_off(d);
    if ((uint32_t)(stamp - d->last_poll_us) >= MT_ADC_DMA_BLOCK_US) {
        invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);
        return hardware_off(d);
    }
    if (n == 0u || n > MT_ADC_DMA_SAMPLES ||
        d->first_sample_index % MT_ADC_DMA_BLOCK_SAMPLES != 0u ||
        ((d->first_sample_index / MT_ADC_DMA_BLOCK_SAMPLES) & 1u) !=
            (d->next_half ? 1u : 0u)) {
        invalidate_history(d, MT_ADC_DMA_FAULT_TRANSFER);
        return hardware_off(d);
    }
    if (!d->next_half && ((f == 0u && n > 128u) || (f == DMA_HT && n <= 128u))) {
        k = 256u - n;
    } else if (d->next_half && f == 0u && n <= 128u) {
        k = 128u - n;
    } else if (d->next_half && f == DMA_TC && n > 128u) {
        k = 384u - n;
    } else {
        invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);
        return hardware_off(d);
    }
    barrier();
    if (!hardware_off(d)) return false;
    if (rd(d, DMA_CNDTR) != n || (rd(d, DMA_ISR) & DMA_EVENTS) != f) {
        invalidate_history(d, MT_ADC_DMA_FAULT_TRANSFER);
        return hardware_off(d);
    }
    if (!clock_plan(d, false)) {
        invalidate_history(d, MT_ADC_DMA_FAULT_CLOCK);
        return hardware_off(d);
    }
    if ((uint32_t)(now(d) - d->last_poll_us) >= MT_ADC_DMA_BLOCK_US) {
        invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);
        return hardware_off(d);
    }
    if (UINT64_MAX - d->first_sample_index < k) {
        invalidate_history(d, MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
        return hardware_off(d);
    }
    d->stop_count = k;
    d->stop_dma_end = d->first_sample_index + k;
    d->stop_dma_known = true;
    return hardware_off(d);
}

static bool quiesce(mt_adc_dma_t *d)
{
    bool clocks_present;
    bool history = false;
    if (!d->owns_peripherals) {
        /* Legacy ownership API remains a no-op; stream certificate requires
         * actual register evidence and never steals an unknown active owner. */
        return !d->stream_mode || hardware_off(d);
    }
    clocks_present = gates_enabled(d);
    if (d->stream_mode) {
        /* Observe acquisition causes before teardown can introduce STOP.
         * Never read a missing DMA clock as a valid flags observation. */
        if (!clocks_present) invalidate_history(d, MT_ADC_DMA_FAULT_CLOCK);
        else {
            uint32_t events = rd(d, DMA_ISR) & DMA_EVENTS;
            if ((events & DMA_TE) != 0u)
                invalidate_history(d, MT_ADC_DMA_FAULT_TRANSFER);
            if (d->stream_history_valid &&
                (events & (d->next_half ? DMA_HT : DMA_TC)) != 0u)
                invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);
            if (!clock_plan(d, false)) invalidate_history(d, MT_ADC_DMA_FAULT_CLOCK);
            if (d->stream_history_valid &&
                (uint32_t)(now(d) - d->last_poll_us) >= MT_ADC_DMA_BLOCK_US)
                invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);
        }
        history = d->stream_history_valid;
    }
    wr(d, TIM_CR1, 0u); /* No new update triggers. */
    wr(d, ADC_CR2, 0u); /* Abort conversion/calibration; disable DMA requests. */
    wr(d, DMA_CCR, rd(d, DMA_CCR) & ~DMA_EN);
    if (!wait_clear(d, DMA_CCR, DMA_EN)) {
        if (d->stream_mode) invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        return false;
    }
    barrier();
    if (!clocks_present || (d->stream_mode ? !hardware_off(d) :
        (!gates_enabled(d) || (rd(d, TIM_CR1) & 1u) != 0u || rd(d, ADC_CR2) != 0u))) {
        if (d->stream_mode) invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        return false;
    }
    if (d->stream_mode && !capture_stopped_dma(d, history)) {
        invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        return false;
    }
    wr(d, DMA_IFCR, DMA_EVENTS); /* Never use CGIF, even during teardown. */
    wr(d, TIM_SR, 0u);
    wr(d, ADC_SR, 0u);
    if (d->stream_mode && !hardware_off(d)) {
        invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        return false;
    }
    d->owns_peripherals = false;
    return true;
}

static mt_adc_dma_result_t fail(mt_adc_dma_t *d, mt_adc_dma_fault_t fault)
{
    d->state = MT_ADC_DMA_FAULTED;
    d->fault = fault;
    if (d->stream_mode) invalidate_history(d, fault);
    if (!quiesce(d)) { d->fault = MT_ADC_DMA_FAULT_STOP; }
    return MT_ADC_DMA_ERROR;
}

mt_adc_dma_result_t mt_adc_dma_init(mt_adc_dma_t *d,
                                  const mt_adc_dma_io_t *io, uint32_t hse_hz)
{
    if (d == NULL || io == NULL || io->read32 == NULL ||
        io->write32 == NULL || io->now_us == NULL) { return MT_ADC_DMA_BAD_ARGUMENT; }
    *d = (mt_adc_dma_t){0};
    d->io = *io;
    d->hse_hz = hse_hz;
    d->initialized = true;
    return MT_ADC_DMA_OK;
}

int mt_adc_dma_profile1_ready(mt_adc_dma_t *d)
{
    if (!valid(d) || d->state != MT_ADC_DMA_STOPPED || d->owns_peripherals)
        return 0;
    if (!clock_plan(d, true)) return 0;
    return d->clock.timer_hz == 72000000u && d->clock.timer_psc == 71u &&
        d->clock.timer_arr == 19u && d->clock.adc_hz == 12000000u &&
        (rd(d, DMA_CCR) & DMA_EN) == 0u && (rd(d, TIM_CR1) & 1u) == 0u &&
        (rd(d, ADC_CR2) & ADC_ADON) == 0u;
}

static mt_adc_dma_result_t start(mt_adc_dma_t *d, const mt_adc_dma_start_guard *guard)
{
    uint32_t key = 0;
    mt_adc_dma_fault_t commit_fault = MT_ADC_DMA_FAULT_NONE;
    if (!valid(d)) { return MT_ADC_DMA_BAD_ARGUMENT; }
    if (d->state != MT_ADC_DMA_STOPPED || d->owns_peripherals) { return MT_ADC_DMA_BUSY; }
#if defined(__arm__) || defined(__thumb__)
    /* C8 has only the documented 20 KiB SRAM. No bit-band/CCM/flash target. */
    if ((uintptr_t)d < UINT32_C(0x20000000) ||
        (uintptr_t)d > UINT32_C(0x20005000) - sizeof(*d)) {
        return MT_ADC_DMA_BAD_ARGUMENT;
    }
#endif
    d->stop_captured = false;
    d->stop_dma_known = false;
    d->stop_count = 0u;
    d->stop_dma_end = 0u;
    d->stream_history_valid = false;
    d->first_fault = MT_ADC_DMA_FAULT_NONE;
    if (!clock_plan(d, true)) { return fail(d, MT_ADC_DMA_FAULT_CLOCK); }
    /* Reject active owners before changing gates or peripheral registers. */
    if ((rd(d, DMA_CCR) & DMA_EN) != 0u || (rd(d, TIM_CR1) & 1u) != 0u ||
        (rd(d, ADC_CR2) & ADC_ADON) != 0u) { return MT_ADC_DMA_BUSY; }
    wr(d, RCC_AHBENR, rd(d, RCC_AHBENR) | 1u);
    wr(d, RCC_APB2ENR, rd(d, RCC_APB2ENR) | (1u << 2) | (1u << 9));
    wr(d, RCC_APB1ENR, rd(d, RCC_APB1ENR) | (1u << 1));
    (void)rd(d, RCC_APB1ENR);
    barrier();
    if (!gates_enabled(d)) { return fail(d, MT_ADC_DMA_FAULT_CLOCK); }
    d->owns_peripherals = true;
    wr(d, TIM_CR1, 0u);
    wr(d, ADC_CR2, 0u);
    wr(d, DMA_CCR, 0u);
    if (!wait_clear(d, DMA_CCR, DMA_EN)) { return fail(d, MT_ADC_DMA_FAULT_STOP); }
    wr(d, GPIOA_CRL, rd(d, GPIOA_CRL) & ~UINT32_C(15));
    wr(d, ADC_CR1, 0u); /* Independent, one regular channel, no injected group. */
    wr(d, ADC_SMPR1, 0u);
    wr(d, ADC_SMPR2, 6u); /* SMP0=110: 71.5 ADC cycles. */
    wr(d, ADC_SQR1, 0u);
    wr(d, ADC_SQR2, 0u);
    wr(d, ADC_SQR3, 0u); /* Length=1, channel=0, right aligned. */
    wr(d, TIM_CR2, 0u);
    wr(d, TIM_SMCR, 0u);
    wr(d, TIM_DIER, 0u);
    wr(d, TIM_CCER, 0u); /* No output pins; TRGO is an internal route. */
    wr(d, TIM_PSC, d->clock.timer_psc);
    wr(d, TIM_ARR, d->clock.timer_arr);
    wr(d, TIM_CNT, 0u);
    /* ADC clock is already enabled (ES096 2.6.2). UG loads PSC while ADC
     * external trigger is off; MMS set only after UG avoids an initial sample. */
    wr(d, TIM_EGR, 1u);
    wr(d, TIM_SR, 0u);
    wr(d, TIM_CR2, 2u << 4); /* MMS=010, update event is TRGO. */
    wr(d, ADC_CR2, ADC_TIM3 | ADC_ADON);
    if (!stabilize(d)) { return fail(d, MT_ADC_DMA_FAULT_TIMEBASE); }
    wr(d, ADC_CR2, ADC_TIM3 | ADC_ADON | ADC_RSTCAL);
    if (!wait_clear(d, ADC_CR2, ADC_RSTCAL)) { return fail(d, MT_ADC_DMA_FAULT_CALIBRATION); }
    wr(d, ADC_CR2, ADC_TIM3 | ADC_ADON | ADC_CAL);
    if (!wait_clear(d, ADC_CR2, ADC_CAL)) { return fail(d, MT_ADC_DMA_FAULT_CALIBRATION); }
    (void)rd(d, ADC_DR); /* Discard calibration result before DMA is armed. */
    wr(d, ADC_SR, 0u);
    wr(d, DMA_IFCR, DMA_EVENTS);
    wr(d, DMA_CPAR, ADC_DR);
    wr(d, DMA_CMAR, (uint32_t)(uintptr_t)&d->dma_samples[0]);
    wr(d, DMA_CNDTR, MT_ADC_DMA_SAMPLES);
    wr(d, DMA_CCR, DMA_CONFIG);
    d->first_sample_index = 0u;
    d->next_half = false;
    d->last_remaining = MT_ADC_DMA_SAMPLES;
    d->fault = MT_ADC_DMA_FAULT_NONE;
    barrier();
    if (guard) {
        key = guard->enter(guard->context);
        d->start_before_us = now(d);
        if (!guard->allowed(guard->context)) {
            commit_fault = MT_ADC_DMA_FAULT_ADMISSION;
            goto committed;
        }
    }
    wr(d, DMA_CCR, DMA_CONFIG | DMA_EN);
    wr(d, ADC_CR2, ADC_TIM3 | ADC_ADON | ADC_DMA | ADC_EXTTRIG);
    if (!clock_plan(d, false) || !gates_enabled(d)) {
        commit_fault = MT_ADC_DMA_FAULT_CLOCK;
        goto committed;
    }
    if (!guard) d->start_before_us = now(d);
    if (guard && guard->timed_allowed &&
        !guard->timed_allowed(guard->context, &d->start_before_us)) {
        commit_fault = MT_ADC_DMA_FAULT_ADMISSION;
        goto committed;
    }
    wr(d, TIM_CR1, 1u);
    if (guard && guard->timed_allowed) {
        if (!guard->timed_allowed(guard->context, &d->start_after_us)) {
            commit_fault = MT_ADC_DMA_FAULT_ADMISSION;
            goto committed;
        }
    } else {
        d->start_after_us = now(d);
        if (guard && !guard->allowed(guard->context)) {
            commit_fault = MT_ADC_DMA_FAULT_ADMISSION;
            goto committed;
        }
    }
    if ((uint32_t)(d->start_after_us - d->start_before_us) > MT_ADC_DMA_SAMPLE_US) {
        commit_fault = MT_ADC_DMA_FAULT_TIMEBASE;
        goto committed;
    }
    if ((rd(d, TIM_CR1) & 1u) == 0u ||
        rd(d, DMA_CCR) != (DMA_CONFIG | DMA_EN) ||
        rd(d, ADC_CR2) != (ADC_TIM3 | ADC_ADON | ADC_DMA | ADC_EXTTRIG)) {
        commit_fault = MT_ADC_DMA_FAULT_TRANSFER;
        goto committed;
    }
    d->last_poll_us = d->start_before_us;
    d->last_progress_us = d->start_before_us;
    d->state = MT_ADC_DMA_RUNNING;
    d->stream_history_valid = d->stream_mode;
committed:
    if (guard) guard->leave(guard->context, key);
    /* quiesce can poll: never invoke it while holding the commit guard. */
    if (commit_fault != MT_ADC_DMA_FAULT_NONE) return fail(d, commit_fault);
    return MT_ADC_DMA_OK;
}

static bool safe_half(bool second, uint32_t remaining)
{
    /* At CNDTR=0/reload boundary ownership is ambiguous: reject conservatively. */
    return second ? (remaining > MT_ADC_DMA_BLOCK_SAMPLES && remaining <= MT_ADC_DMA_SAMPLES)
                  : (remaining > 0u && remaining <= MT_ADC_DMA_BLOCK_SAMPLES);
}

mt_adc_dma_result_t mt_adc_dma_start(mt_adc_dma_t *d) {
    return start(d, NULL);
}
mt_adc_dma_result_t mt_adc_dma_start_guarded(mt_adc_dma_t *d,
                                            const mt_adc_dma_start_guard *guard) {
    if (!guard || !guard->enter || !guard->allowed || !guard->leave)
        return MT_ADC_DMA_BAD_ARGUMENT;
    return start(d, guard);
}

mt_adc_dma_result_t mt_adc_dma_read_block(mt_adc_dma_t *d, mt_adc_dma_block_t *block)
{
    uint32_t stamp;
    uint32_t flags;
    uint32_t expected;
    uint32_t other;
    uint32_t remaining;
    uint32_t i;
    uint32_t offset;
    uint32_t end;
    if (!valid(d) || block == NULL) { return MT_ADC_DMA_BAD_ARGUMENT; }
    if (d->state != MT_ADC_DMA_RUNNING) { return MT_ADC_DMA_NOT_RUNNING; }
    if (d->stream_mode && !d->stream_history_valid)
        return fail(d, d->first_fault == MT_ADC_DMA_FAULT_NONE ?
                    MT_ADC_DMA_FAULT_DEADLINE : d->first_fault);
    stamp = now(d);
    MT_DIAG_POLL((uint32_t)(stamp - d->last_poll_us));
    if ((uint32_t)(stamp - d->last_poll_us) >= MT_ADC_DMA_BLOCK_US) {
        return fail(d, MT_ADC_DMA_FAULT_DEADLINE);
    }
    if (!clock_plan(d, false) || !gates_enabled(d)) { return fail(d, MT_ADC_DMA_FAULT_CLOCK); }
    flags = rd(d, DMA_ISR) & DMA_EVENTS;
    if ((flags & DMA_TE) != 0u) { return fail(d, MT_ADC_DMA_FAULT_TRANSFER); }
    if ((rd(d, DMA_CCR) & DMA_EN) == 0u || (rd(d, TIM_CR1) & 1u) == 0u ||
        rd(d, ADC_CR2) != (ADC_TIM3 | ADC_ADON | ADC_DMA | ADC_EXTTRIG)) {
        return fail(d, MT_ADC_DMA_FAULT_TRANSFER);
    }
    expected = d->next_half ? DMA_TC : DMA_HT;
    other = d->next_half ? DMA_HT : DMA_TC;
    if ((flags & other) != 0u) { return fail(d, MT_ADC_DMA_FAULT_DEADLINE); }
    remaining = rd(d, DMA_CNDTR);
    if (remaining > MT_ADC_DMA_SAMPLES) { return fail(d, MT_ADC_DMA_FAULT_TRANSFER); }
    if (remaining != d->last_remaining) { d->last_progress_us = stamp; }
    d->last_remaining = remaining;
    if ((flags & expected) == 0u) {
        /* Detect stalled ADC/DMA even if caller polls rapidly. */
        if ((uint32_t)(stamp - d->last_progress_us) >= MT_ADC_DMA_BLOCK_US) {
            return fail(d, MT_ADC_DMA_FAULT_DEADLINE);
        }
        d->last_poll_us = stamp;
        return MT_ADC_DMA_EMPTY;
    }
    if (!safe_half(d->next_half, remaining)) { return fail(d, MT_ADC_DMA_FAULT_DEADLINE); }
    offset = d->next_half ? MT_ADC_DMA_BLOCK_SAMPLES : 0u;
    barrier();
    for (i = 0u; i < MT_ADC_DMA_BLOCK_SAMPLES; ++i) {
        d->snapshot[i] = d->dma_samples[offset + i];
    }
    barrier();
    remaining = rd(d, DMA_CNDTR);
    flags = rd(d, DMA_ISR) & DMA_EVENTS;
    end = now(d);
    MT_DIAG_COPY((uint32_t)(end - stamp));
    if ((flags & DMA_TE) != 0u) { return fail(d, MT_ADC_DMA_FAULT_TRANSFER); }
    if (!safe_half(d->next_half, remaining) || (flags & other) != 0u ||
        (flags & expected) == 0u || (uint32_t)(end - stamp) >= MT_ADC_DMA_BLOCK_US) {
        return fail(d, MT_ADC_DMA_FAULT_DEADLINE);
    }
    if (d->first_sample_index > (d->stream_mode ? UINT64_MAX : UINT32_MAX) -
        MT_ADC_DMA_BLOCK_SAMPLES) {
        return fail(d, MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
    }
    /* Clear only the completed half. A later opposite event remains latched;
     * if it arrives after copy validation our private snapshot is still safe. */
    wr(d, DMA_IFCR, expected);
    *block = (mt_adc_dma_block_t){
        d->snapshot, d->first_sample_index, MT_ADC_DMA_BLOCK_SAMPLES,
        MT_ADC_DMA_RATE_HZ, d->start_before_us, d->start_after_us, MT_ADC_DMA_SAMPLE_US
    };
    d->first_sample_index += MT_ADC_DMA_BLOCK_SAMPLES;
    d->next_half = !d->next_half;
    d->last_poll_us = stamp; /* Includes copy time in next call's deadline. */
    d->last_progress_us = stamp;
    return MT_ADC_DMA_BLOCK;
}

mt_adc_dma_result_t mt_adc_dma_stop(mt_adc_dma_t *d)
{
    if (!valid(d)) { return MT_ADC_DMA_BAD_ARGUMENT; }
    if (!quiesce(d)) {
        d->state = MT_ADC_DMA_FAULTED;
        d->fault = MT_ADC_DMA_FAULT_STOP;
        if (d->stream_mode) invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        return MT_ADC_DMA_ERROR;
    }
    d->state = MT_ADC_DMA_STOPPED;
    return MT_ADC_DMA_OK;
}

mt_adc_dma_result_t mt_adc_dma_enable_stream(mt_adc_dma_t *d)
{
    if (!valid(d)) return MT_ADC_DMA_BAD_ARGUMENT;
    if (d->owns_peripherals || d->state != MT_ADC_DMA_STOPPED) return MT_ADC_DMA_BUSY;
    d->stream_mode = true;
    return MT_ADC_DMA_OK;
}

void mt_adc_dma_invalidate_stream_history(mt_adc_dma_t *d, mt_adc_dma_fault_t fault)
{
    if (valid(d) && d->stream_mode) invalidate_history(d,
        fault == MT_ADC_DMA_FAULT_NONE ? MT_ADC_DMA_FAULT_DEADLINE : fault);
}

mt_adc_dma_result_t mt_adc_dma_stop_snapshot(mt_adc_dma_t *d,
                                           mt_adc_dma_stop_snapshot_t *out)
{
    mt_adc_dma_result_t result;
    uint32_t i;
    if (!valid(d) || !d->stream_mode || out == NULL) return MT_ADC_DMA_BAD_ARGUMENT;
    *out = (mt_adc_dma_stop_snapshot_t){0};
    result = mt_adc_dma_stop(d);
    out->hardware_off = result == MT_ADC_DMA_OK;
    out->first_fault = d->first_fault;
    if (!out->hardware_off || !d->stop_captured || !d->stop_dma_known) return result;
    barrier();
    for (i = 0u; i < d->stop_count; ++i) {
        uint16_t sample = d->dma_samples[(d->stop_offset + i) % MT_ADC_DMA_SAMPLES];
        if (sample > 4095u) {
            invalidate_history(d, MT_ADC_DMA_FAULT_TRANSFER);
            *out = (mt_adc_dma_stop_snapshot_t){0};
            out->hardware_off = true;
            out->first_fault = d->first_fault;
            return MT_ADC_DMA_OK;
        }
        out->samples[i] = sample;
    }
    barrier();
    if (!hardware_off(d)) {
        invalidate_history(d, MT_ADC_DMA_FAULT_STOP);
        d->owns_peripherals = true;
        d->state = MT_ADC_DMA_FAULTED;
        d->fault = MT_ADC_DMA_FAULT_STOP;
        *out = (mt_adc_dma_stop_snapshot_t){0};
        out->first_fault = d->first_fault;
        return MT_ADC_DMA_ERROR;
    }
    out->first_sample_index = d->stop_first_index;
    out->dma_end = d->stop_dma_end;
    out->count = d->stop_count;
    out->dma_end_known = true;
    return result;
}
