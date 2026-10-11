/* Supporting native model. This is neither an IRQ controller nor target proof. */
#define main legacy_platform_main
#include "../platform/test_adc_timer_dma.c"
#undef main
#include "../../firmware/app/mt_adc_control.h"

enum injection {
    INJ_NONE, INJ_RESET_CAL, INJ_CAL_READ, INJ_LAST_PREP, INJ_ENTER_EDGE,
    INJ_ALLOWED_EDGE, INJ_PENDING_DMA, INJ_PENDING_ADC, INJ_PENDING_CEN,
    INJ_CLOCK_BEFORE, INJ_CLOCK_AFTER, INJ_RCC_COMMIT, INJ_DROP_CEN,
    INJ_DROP_DMA, INJ_DROP_ADC, INJ_WIDE_TIME, INJ_REVERSE_TIME
};
typedef struct {
    uint32_t mask, saved_mask, epoch, original_actor;
    unsigned depth, enters, leaves, allowed_calls, guarded_now, calibrations;
    unsigned enables, cen_writes, cleanup_writes, cleanup_now, irq_calls;
    unsigned pending, fired, stop_failure, bad_guard, health;
    enum injection injection;
} admission_model;
static admission_model guard_state;
static mt_control controller;
static mt_adc_control adapter;
static unsigned cases;

static void invalidate(void) {
    ++guard_state.epoch;
}
static void dispatch_irq(void) {
    if (guard_state.pending && guard_state.mask == 0u) {
        assert(guard_state.depth == 0u);
        guard_state.pending = 0u;
        ++guard_state.irq_calls;
        invalidate();
    }
}
static void request_irq(void) {
    guard_state.pending = 1u;
    dispatch_irq();
}
static int fire(enum injection injection) {
    if (guard_state.injection != injection || guard_state.fired) return 0;
    guard_state.fired = 1u;
    return 1;
}
static uint32_t observed_read(void *context, uint32_t address) {
    if (address == A_CR2 && (*slot(&hw, A_CR2) & 4u) != 0u && fire(INJ_CAL_READ)) {
        assert(guard_state.depth == 0u);
        invalidate();
    }
    return read32(context, address);
}
static void observed_write(void *context, uint32_t address, uint32_t value) {
    if ((address == T_CR1 || address == A_CR2) && value == 0u) {
        assert(guard_state.depth == 0u); /* Includes cleanup before a faulty/missing leave. */
    }
    if (address == A_CR2 && (value & 12u) != 0u) {
        assert(guard_state.depth == 0u); /* Calibration must stay outside guard. */
        ++guard_state.calibrations;
        if ((value & 8u) != 0u && fire(INJ_RESET_CAL)) invalidate();
    }
    if (address == D_CCR && value == 0x25a0u && fire(INJ_LAST_PREP)) invalidate();
    if ((address == D_CCR && (value & 1u) != 0u) ||
        (address == A_CR2 && (value & (1u << 20)) != 0u) ||
        (address == T_CR1 && (value & 1u) != 0u)) {
        if (guard_state.bad_guard == 0u) {
            assert(guard_state.depth == 1u && guard_state.mask == 1u);
            assert(guard_state.allowed_calls == 1u);
        }
        ++guard_state.enables;
    }
    if (address == T_CR1 && value == 1u) {
        ++guard_state.cen_writes;
        if (fire(INJ_PENDING_CEN)) request_irq();
        if (fire(INJ_DROP_CEN)) return;
    }
    if (address == D_CCR && (value & 1u) != 0u) {
        if (fire(INJ_PENDING_DMA)) request_irq();
        if (fire(INJ_DROP_DMA)) return;
    }
    if (address == A_CR2 && (value & (1u << 20)) != 0u) {
        if (fire(INJ_PENDING_ADC)) request_irq();
        if (fire(INJ_RCC_COMMIT)) *slot(&hw, R_CR) &= ~(1u << 25);
        if (fire(INJ_DROP_ADC)) return;
    }
    if (address == T_CR1 && value == 0u && guard_state.leaves != 0u) {
        assert(guard_state.depth == 0u);
        ++guard_state.cleanup_writes;
    }
    write32(context, address, value);
}
static uint32_t observed_now(void *context) {
    uint32_t value = now_us(context);
    if (guard_state.depth) {
        ++guard_state.guarded_now;
        assert(guard_state.mask == 1u);
        if (guard_state.guarded_now == 1u && fire(INJ_CLOCK_BEFORE)) guard_state.health = 0u;
        if (guard_state.guarded_now == 2u) {
            if (fire(INJ_CLOCK_AFTER)) guard_state.health = 0u;
            if (fire(INJ_WIDE_TIME)) value += MT_ADC_DMA_SAMPLE_US;
            if (fire(INJ_REVERSE_TIME)) value -= 2u;
        }
    }
    if (guard_state.leaves && driver.state == MT_ADC_DMA_FAULTED) {
        assert(guard_state.depth == 0u);
        ++guard_state.cleanup_now;
    }
    return value;
}
static uint32_t guard_enter(void *context) {
    admission_model *g = context;
    assert(g == &guard_state && g->depth == 0u);
    /* The event on the immediately preceding instruction is visible before masking. */
    if (fire(INJ_ENTER_EDGE)) invalidate();
    g->saved_mask = g->mask;
    g->mask = 1u;
    g->depth = 1u;
    ++g->enters;
    assert(g->calibrations == 2u);
    return g->saved_mask;
}
static int guard_allowed(void *context) {
    admission_model *g = context;
    assert(g == &guard_state && g->depth == 1u && g->mask == 1u);
    ++g->allowed_calls;
    assert(g->guarded_now == g->allowed_calls); /* Fresh clock observation first. */
    if (fire(INJ_ALLOWED_EDGE)) invalidate();
    return g->epoch == g->original_actor && g->health != 0u;
}
static void guard_leave(void *context, uint32_t previous) {
    admission_model *g = context;
    assert(g == &guard_state && g->depth == 1u && g->mask == 1u);
    assert(previous == g->saved_mask);
    g->depth = 0u;
    g->mask = previous;
    ++g->leaves;
    if (g->stop_failure) {
        hw.stop_stuck = true;
        *slot(&hw, D_CCR) |= 1u;
    }
    dispatch_irq();
}
static mt_adc_dma_start_guard model_guard(void) {
    mt_adc_dma_start_guard g = {guard_enter, guard_allowed, guard_leave, &guard_state};
    return g;
}
static void reset_model(uint32_t previous_mask, enum injection injection) {
    setup();
    memset(&guard_state, 0, sizeof guard_state);
    guard_state.mask = previous_mask;
    guard_state.epoch = guard_state.original_actor = 9u;
    guard_state.health = 1u;
    guard_state.injection = injection;
    driver.io.read32 = observed_read;
    driver.io.write32 = observed_write;
    driver.io.now_us = observed_now;
}
static void returned(uint32_t previous_mask, unsigned allowed_calls) {
    assert(guard_state.depth == 0u && guard_state.mask == previous_mask);
    assert(guard_state.enters == 1u && guard_state.leaves == 1u);
    assert(guard_state.allowed_calls == allowed_calls);
    assert(guard_state.guarded_now == allowed_calls);
}
static void check_released(mt_adc_dma_fault_t fault) {
    assert(driver.state == MT_ADC_DMA_FAULTED && driver.fault == fault);
    assert(!driver.owns_peripherals);
    assert(*slot(&hw, T_CR1) == 0u && *slot(&hw, A_CR2) == 0u);
    assert((*slot(&hw, D_CCR) & 1u) == 0u);
    assert(guard_state.cleanup_writes == 1u && guard_state.cleanup_now >= 1u);
}
static void test_admission_denials(void) {
    const enum injection points[] = {INJ_NONE, INJ_RESET_CAL, INJ_CAL_READ,
        INJ_LAST_PREP, INJ_ENTER_EDGE, INJ_ALLOWED_EDGE, INJ_CLOCK_BEFORE};
    unsigned mask, i;
    for (mask = 0; mask < 2u; ++mask) {
        for (i = 0; i < sizeof points / sizeof points[0]; ++i) {
            mt_adc_dma_start_guard g;
            reset_model(mask, points[i]); g = model_guard();
            if (points[i] == INJ_NONE) invalidate();
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_ERROR);
            returned(mask, 1u);
            check_released(MT_ADC_DMA_FAULT_ADMISSION);
            assert(guard_state.enables == 0u && guard_state.cen_writes == 0u);
            assert(guard_state.original_actor == 9u); /* No fresh-token laundering. */
            ++cases;
        }
    }
}
static void test_pending_irqs(void) {
    const enum injection points[] = {INJ_PENDING_DMA, INJ_PENDING_ADC, INJ_PENDING_CEN};
    unsigned mask, i;
    for (mask = 0; mask < 2u; ++mask) {
        for (i = 0; i < sizeof points / sizeof points[0]; ++i) {
            mt_adc_dma_start_guard g;
            reset_model(mask, points[i]); g = model_guard();
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_OK);
            returned(mask, 2u);
            assert(guard_state.enables == 3u && guard_state.cen_writes == 1u);
            assert(driver.state == MT_ADC_DMA_RUNNING && driver.owns_peripherals);
            if (mask) {
                assert(guard_state.irq_calls == 0u && guard_state.pending == 1u);
                assert(guard_state.epoch == guard_state.original_actor);
                guard_state.mask = 0u; dispatch_irq();
            }
            assert(guard_state.irq_calls == 1u && guard_state.pending == 0u);
            assert(guard_state.epoch != guard_state.original_actor);
            /* The caller observes its stale actor and stops before output. */
            assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
            assert(!driver.owns_peripherals);
            ++cases;
        }
    }
}
static void test_commit_failures(void) {
    const enum injection points[] = {INJ_CLOCK_AFTER, INJ_RCC_COMMIT, INJ_DROP_CEN,
        INJ_DROP_DMA, INJ_DROP_ADC, INJ_WIDE_TIME, INJ_REVERSE_TIME};
    const mt_adc_dma_fault_t faults[] = {MT_ADC_DMA_FAULT_ADMISSION, MT_ADC_DMA_FAULT_CLOCK,
        MT_ADC_DMA_FAULT_TRANSFER, MT_ADC_DMA_FAULT_TRANSFER, MT_ADC_DMA_FAULT_TRANSFER,
        MT_ADC_DMA_FAULT_TIMEBASE, MT_ADC_DMA_FAULT_TIMEBASE};
    unsigned mask, i;
    for (mask = 0; mask < 2u; ++mask) {
        for (i = 0; i < sizeof points / sizeof points[0]; ++i) {
            mt_adc_dma_start_guard g;
            reset_model(mask, points[i]); g = model_guard();
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_ERROR);
            returned(mask, points[i] == INJ_RCC_COMMIT ? 1u : 2u);
            check_released(faults[i]);
            assert(guard_state.cen_writes == (points[i] == INJ_RCC_COMMIT ? 0u : 1u));
            ++cases;
        }
    }
}
static void test_failed_stop_ownership(void) {
    unsigned mask, denied;
    for (mask = 0; mask < 2u; ++mask) {
        for (denied = 0; denied < 2u; ++denied) {
            mt_adc_dma_start_guard g;
            reset_model(mask, denied ? INJ_ALLOWED_EDGE : INJ_DROP_CEN);
            guard_state.stop_failure = 1u; g = model_guard();
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_ERROR);
            returned(mask, denied ? 1u : 2u);
            assert(driver.owns_peripherals && driver.state == MT_ADC_DMA_FAULTED);
            assert(driver.fault == MT_ADC_DMA_FAULT_STOP);
            assert(guard_state.cleanup_writes == 1u && guard_state.cleanup_now > 900u);
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_BUSY);
            assert(guard_state.enters == 1u);
            assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_ERROR);
            assert(driver.owns_peripherals && guard_state.depth == 0u);
            hw.stop_stuck = false;
            assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
            assert(!driver.owns_peripherals && driver.state == MT_ADC_DMA_STOPPED);
            ++cases;
        }
    }
}
static void test_preparation_failures(void) {
    unsigned mask, mode;
    for (mask = 0; mask < 2u; ++mask) {
        for (mode = 0; mode < 4u; ++mode) {
            mt_adc_dma_start_guard g;
            reset_model(mask, INJ_NONE); g = model_guard();
            if (mode == 0u) hw.tick_step = 0u;
            if (mode == 1u) hw.reset_stuck = true;
            if (mode == 2u) hw.cal_stuck = true;
            if (mode == 3u) *slot(&hw, R_CR) &= ~(1u << 25);
            assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_ERROR);
            assert(guard_state.enters == 0u && guard_state.leaves == 0u);
            assert(guard_state.depth == 0u && guard_state.mask == mask);
            assert(!driver.owns_peripherals && guard_state.cen_writes == 0u);
            ++cases;
        }
    }
}
static uint32_t binding_ms(void *context) {
    assert(context == &hw);
    return 1u + (hw.tick - 1000u) / 1000u;
}
static int sink(void *context, const uint8_t *p, size_t n) {
    (void)context; (void)p;
    assert(n <= 65535u);
    return (int)n;
}
static void drain(void) {
    unsigned budget = 100u;
    while (controller.tx.count || controller.state_count) {
        assert(budget-- != 0u);
        assert(mt_control_poll(&controller, binding_ms(&hw), sink, NULL) == MT_CONTROL_OK);
    }
}
static void prepare_controller(unsigned guarded) {
    mt_wire_message m = {0};
    mt_adc_dma_start_guard g = model_guard();
    if (guarded) {
        assert(mt_adc_control_init_guarded(&adapter, &controller, &driver,
            binding_ms, &hw, 42u, &g) == MT_CONTROL_OK);
        memset(&g, 0, sizeof g); /* The binding must own a copied definition. */
    } else {
        assert(mt_adc_control_init(&adapter, &controller, &driver,
            binding_ms, &hw, 42u) == MT_CONTROL_OK);
    }
    assert(hw.write_count == 0u && adapter.guarded == guarded);
    m.type = MT_CMD_HELLO; m.sequence = m.request = 1u;
    m.payload_length = 8u; m.payload[0] = 99u;
    assert(mt_control_receive(&controller, &m, binding_ms(&hw)) == MT_CONTROL_OK);
    drain();
    m = (mt_wire_message){0};
    m.type = MT_CMD_CONFIG; m.sequence = m.request = 2u; m.session = 99u;
    m.payload_length = 16u; m.payload[0] = 1u; m.payload[2] = 1u;
    m.payload[7] = 8u; /* analog/rising/normal, level 2048, no pretrigger */
    assert(mt_control_receive(&controller, &m, binding_ms(&hw)) == MT_CONTROL_OK);
    drain();
}
static mt_control_result arm_controller(void) {
    mt_wire_message m = {0};
    m.type = MT_CMD_ARM; m.sequence = m.request = 3u; m.session = 99u;
    m.payload_length = 4u; m.payload[0] = 1u;
    return mt_control_receive(&controller, &m, binding_ms(&hw));
}
static void test_actual_controller_binding(void) {
    const enum injection points[] = {INJ_NONE, INJ_CAL_READ, INJ_LAST_PREP,
        INJ_ALLOWED_EDGE, INJ_CLOCK_BEFORE, INJ_CLOCK_AFTER, INJ_DROP_CEN};
    unsigned mask, i;
    for (mask = 0; mask < 2u; ++mask) {
        for (i = 0; i < sizeof points / sizeof points[0]; ++i) {
            reset_model(mask, points[i]); prepare_controller(1u);
            if (points[i] == INJ_NONE) {
                assert(arm_controller() == MT_CONTROL_OK);
                assert(controller.ready && controller.hardware_running);
                assert(adapter.first_sample_delay_us >= MT_ADC_DMA_SAMPLE_US);
                assert(mt_control_recover(&controller) == MT_CONTROL_RECOVERY);
            } else {
                assert(arm_controller() == MT_CONTROL_RECOVERY);
                assert(!controller.ready && !controller.hardware_running);
            }
            assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
            assert(guard_state.enters == 1u && guard_state.leaves == 1u);
            assert(guard_state.mask == mask && guard_state.depth == 0u);
            ++cases;
        }
        reset_model(mask, INJ_ALLOWED_EDGE); prepare_controller(1u);
        guard_state.stop_failure = 1u;
        assert(arm_controller() == MT_CONTROL_RECOVERY);
        assert(!controller.ready && driver.owns_peripherals);
        assert(driver.state == MT_ADC_DMA_FAULTED && driver.fault == MT_ADC_DMA_FAULT_STOP);
        assert(guard_state.cleanup_writes == 2u && guard_state.depth == 0u);
        hw.stop_stuck = false;
        assert(mt_control_recover(&controller) == MT_CONTROL_RECOVERY);
        assert(!driver.owns_peripherals); ++cases;
        reset_model(mask, INJ_NONE); guard_state.bad_guard = 1u;
        prepare_controller(0u); invalidate();
        assert(arm_controller() == MT_CONTROL_OK); /* Retained standalone API is unguarded. */
        assert(guard_state.enters == 0u && guard_state.allowed_calls == 0u);
        assert(guard_state.mask == mask && controller.hardware_running);
        assert(mt_control_recover(&controller) == MT_CONTROL_RECOVERY); ++cases;
    }
}
static void test_invalid_guards(void) {
    mt_adc_dma_start_guard g;
    reset_model(0u, INJ_NONE); g = model_guard();
    assert(mt_adc_dma_start_guarded(&driver, NULL) == MT_ADC_DMA_BAD_ARGUMENT);
    g.enter = NULL;
    assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_BAD_ARGUMENT);
    g = model_guard(); g.allowed = NULL;
    assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_BAD_ARGUMENT);
    g = model_guard(); g.leave = NULL;
    assert(mt_adc_dma_start_guarded(&driver, &g) == MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_control_init_guarded(&adapter, &controller, &driver,
        binding_ms, &hw, 0u, &g) == MT_CONTROL_ARGUMENT);
    assert(hw.write_count == 0u && guard_state.enters == 0u); ++cases;
}
int main(void) {
    test_invalid_guards();
    test_admission_denials();
    test_pending_irqs();
    test_commit_failures();
    test_failed_stop_ownership();
    test_preparation_failures();
    test_actual_controller_binding();
    printf("ADC-003 admission supporting: %u cases passed; native model, no hardware/PRIMASK proof\n", cases);
    return 0;
}
