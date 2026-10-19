/* Reuse the published author's register-bus fixture, not an invented HAL. */
#define main platform_regression_main
#include "../platform/test_adc_timer_dma.c"
#undef main
#include "../../firmware/app/mt_adc_control.h"

static mt_control control;
static mt_adc_control binding;
static uint32_t clock_origin_us, clock_origin_ms, sequence, request;
static uint8_t output_bytes[16384];
static size_t output_used;
static uint32_t millis(void *context) {
    fake_t *f = context;
    return clock_origin_ms + (uint32_t)(f->tick - clock_origin_us) / 1000u;
}
static void le16(uint8_t *p, uint16_t x) {
    p[0] = (uint8_t)x; p[1] = (uint8_t)(x >> 8);
}
static void le32(uint8_t *p, uint32_t x) {
    unsigned i;
    for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(x >> (8u * i));
}
static int collect(void *context, const uint8_t *p, size_t n) {
    (void)context;
    assert(output_used + n <= sizeof output_bytes);
    memcpy(output_bytes + output_used, p, n); output_used += n;
    return (int)n;
}
static mt_wire_message cmd(uint8_t type, uint16_t length) {
    mt_wire_message m = {0};
    m.type = type; m.payload_length = length; m.session = 99;
    m.sequence = sequence++; m.request = request++;
    return m;
}
static void tx_drain(void) {
    unsigned budget = 10000;
    do {
        assert(mt_control_poll(&control, millis(&hw), collect, NULL) == MT_CONTROL_OK);
        assert(budget--);
    } while (control.tx.count || control.state_count ||
             (control.link.phase >= MT_LINK_BEGIN && control.link.phase <= MT_LINK_END));
}
static void connect_and_arm(uint32_t origin_us, uint32_t origin_ms) {
    mt_wire_message m = {0};
    setup(); hw.tick = origin_us;
    clock_origin_us = origin_us; clock_origin_ms = origin_ms;
    output_used = 0; sequence = request = 2;
    assert(mt_adc_control_init(&binding, &control, &driver, millis, &hw, 0) == MT_CONTROL_OK);
    assert(hw.write_count == 0u);
    m.type = 1; m.request = m.sequence = 1; m.payload_length = 8; m.payload[0] = 99;
    assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
    tx_drain(); output_used = 0;
    m = cmd(3, 16); le16(m.payload, 1); m.payload[2] = 1; le16(m.payload + 6, 2048);
    assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
    tx_drain(); output_used = 0;
    m = cmd(4, 4); le32(m.payload, 1);
    assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
    assert(control.hardware_running && driver.state == MT_ADC_DMA_RUNNING);
    tx_drain(); output_used = 0;
}
static uint16_t sample_code(uint32_t index) {
    if (index == 0u) return 100;
    if (index == 1u) return 3000;
    return (uint16_t)(index % 4096u);
}
static void deliver_block(unsigned block) {
    unsigned half = block % 2u, i;
    uint32_t base = driver.start_before_us + block * 2560u;
    hw.tick = base + 1000u;
    *slot(&hw, D_N) = half ? 78u : 206u;
    assert(mt_adc_control_service(&binding) == MT_CONTROL_OK);
    hw.tick = base + 2000u;
    *slot(&hw, D_N) = half ? 28u : 156u;
    assert(mt_adc_control_service(&binding) == MT_CONTROL_OK);
    for (i = 0; i < 128u; ++i)
        driver.dma_samples[half * 128u + i] = sample_code(block * 128u + i);
    hw.tick = base + 2570u;
    *slot(&hw, D_N) = half ? 256u : 128u;
    *slot(&hw, D_ISR) = half ? 2u : 4u;
    assert(mt_adc_control_service(&binding) == MT_CONTROL_OK);
}
static void test_profile_no_writes(void) {
    setup(); assert(mt_adc_dma_profile1_ready(&driver)); assert(hw.write_count == 0u);
    *slot(&hw, D_CCR) = 1;
    assert(!mt_adc_dma_profile1_ready(&driver)); assert(hw.write_count == 0u);
    setup(); driver.hse_hz = 0;
    *slot(&hw, R_CR) = 3u | (3u << 24);
    *slot(&hw, R_CFGR) = 2u | 8u | (4u << 8) | (1u << 14) | (10u << 18);
    assert(!mt_adc_dma_profile1_ready(&driver)); assert(hw.write_count == 0u);
    assert(driver.clock.timer_hz == 48000000u);
}
static void test_window_and_time(uint32_t origin_us, uint32_t origin_ms) {
    unsigned i;
    size_t first = 0, position, count = 0;
    uint8_t raw[4096];
    uint32_t expected_time;
    mt_wire_message m;
    connect_and_arm(origin_us, origin_ms);
    for (i = 0; i < 17u; ++i) deliver_block(i);
    assert(control.link.phase == MT_LINK_BEGIN && !control.hardware_running);
    assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
    assert(control.link.acquisition.retained_count == 2048u);
    expected_time = binding.epoch_ms + binding.first_sample_delay_us / 1000u + 2048u / 50u +
        ((binding.first_sample_delay_us % 1000u + (2048u % 50u) * 20u) / 1000u);
    assert(control.link.acquisition.last_sample_ms == expected_time);
    /* Drain time is later than the sample timestamp, including a partial final block. */
    assert((uint32_t)(millis(&hw) - expected_time) <= 3u);
    tx_drain();
    for (i = 0; i < 2048u; ++i) le16(raw + i * 2u, sample_code(i + 1u));
    for (position = 0; position < output_used; ++position) {
        if (output_bytes[position] != 0u) continue;
        assert(mt_wire_decode(output_bytes + first, position - first, &m) == MT_WIRE_OK);
        first = position + 1u;
        if (m.type == 145u) {
            uint16_t n = (uint16_t)((uint16_t)m.payload[10] | (uint16_t)((uint16_t)m.payload[11] << 8));
            assert(memcmp(raw + count * 2u, m.payload + 12, n * 2u) == 0);
            count += n;
        }
        if (m.type == 146u) {
            uint8_t crc[4]; le32(crc, mt_wire_crc32(raw, sizeof raw));
            assert(memcmp(crc, m.payload + 12, 4) == 0);
        }
    }
    assert(first == output_used && count == 2048u);
}
static void test_stale_and_driver_fault(void) {
    unsigned writes;
    mt_wire_message m;
    connect_and_arm(1000, 1);
    writes = hw.write_count;
    m = cmd(5, 4); le32(m.payload, control.link.generation - 1u);
    assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
    assert(hw.write_count == writes && control.hardware_running);
    *slot(&hw, D_ISR) = 8;
    assert(mt_adc_control_service(&binding) == MT_CONTROL_OK);
    assert(control.ready && !control.hardware_running);
    assert(control.link.phase == MT_LINK_ACQ_ERROR && control.link.error_reason == 1u);
    assert(!driver.owns_peripherals);
}
static void test_reported_fault(unsigned capturing, unsigned deadline, unsigned stop_first) {
    unsigned aborts = 0, errors = 0, stop_acks = 0;
    size_t first = 0, position;
    mt_wire_message m;
    uint32_t dropped;
    uint8_t reason = deadline ? 2u : 1u;
    connect_and_arm(1000, 1);
    if (capturing) deliver_block(0);
    assert(control.link.active_window == capturing);
    dropped = control.link.dropped_windows;
    if (deadline) hw.tick += 3000u;
    else *slot(&hw, D_ISR) = 8;
    assert(mt_adc_control_service(&binding) == MT_CONTROL_OK);
    assert(control.ready && !control.hardware_running && !driver.owns_peripherals);
    assert(control.link.phase == MT_LINK_ACQ_ERROR && !control.link.armed);
    assert(control.link.error_reason == reason);
    assert(control.link.dropped_windows == dropped + capturing);
    assert(mt_adc_control_service(&binding) == MT_CONTROL_IGNORED);
    if (stop_first) {
        m = cmd(5, 4); le32(m.payload, control.link.generation);
        assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
        assert(control.link.dropped_windows == dropped + capturing);
    }
    tx_drain();
    for (position = 0; position < output_used; ++position) {
        if (output_bytes[position]) continue;
        assert(mt_wire_decode(output_bytes + first, position - first, &m) == MT_WIRE_OK);
        first = position + 1u;
        assert(m.type != 144u && m.type != 145u && m.type != 146u);
        if (m.type == 147u) {
            ++aborts; assert(errors == 0 && stop_acks == 0);
            assert(m.payload[8] == (uint8_t)(reason + 1u));
        }
        if (m.type == 134u && m.payload[0] == 5u) {
            ++errors; assert(m.payload[1] == reason && aborts == capturing);
            assert(m.payload[20] == capturing);
        }
        if (m.type == 133u) { ++stop_acks; assert(aborts == capturing); }
    }
    assert(first == output_used && aborts == capturing);
    assert(errors == (stop_first ? 0u : 1u));
    assert(stop_acks == stop_first && control.link.dropped_windows == dropped + capturing);
    if (stop_first) assert(control.link.phase == MT_LINK_IDLE);
    else {
        output_used = 0;
        m = cmd(6, 0);
        assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
        tx_drain();
        assert(mt_wire_decode(output_bytes, output_used - 1u, &m) == MT_WIRE_OK);
        assert(m.type == 134u && m.request != 0u && m.payload[0] == 5u && m.payload[1] == reason);
        output_used = 0;
        m = cmd(5, 4); le32(m.payload, control.link.generation);
        assert(mt_control_receive(&control, &m, millis(&hw)) == MT_CONTROL_OK);
        tx_drain();
        assert(control.link.phase == MT_LINK_IDLE && !control.link.armed);
        assert(control.link.dropped_windows == dropped + capturing);
    }
}
static uint32_t correlation_gap;
static uint32_t delayed_millis(void *context) {
    uint32_t result = millis(context);
    ((fake_t *)context)->tick += correlation_gap;
    return result;
}
static void test_clock_correlation(void) {
    unsigned i, wrap;
    /* Each fixture now_us read itself advances one tick: widths 1/20/21/5000. */
    const uint32_t gaps[] = {0u, 19u, 20u, 4999u};
    for (wrap = 0; wrap < 2u; ++wrap) {
        for (i = 0; i < sizeof gaps / sizeof gaps[0]; ++i) {
            mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
            setup();
            clock_origin_us = wrap ? UINT32_MAX - 10u : 1000u;
            clock_origin_ms = wrap ? UINT32_MAX : 1u;
            hw.tick = clock_origin_us; correlation_gap = gaps[i];
            assert(mt_adc_control_init(&binding, &control, &driver,
                   delayed_millis, &hw, 0) == MT_CONTROL_OK);
            if (gaps[i] + 1u <= 20u) {
                assert(control.io.start(control.io.context, clock_origin_ms));
                assert(driver.state == MT_ADC_DMA_RUNNING);
                assert(binding.first_sample_delay_us >= gaps[i] + 20u);
                assert(control.io.stop(control.io.context));
                assert(mt_adc_first_start_failure.magic == 0u);
            } else {
                assert(!control.io.start(control.io.context, clock_origin_ms));
                assert(hw.write_count == 0u && !driver.owns_peripherals);
                assert(hw.time_reads == 2u);
                assert(mt_adc_first_start_failure.magic == MT_ADC_START_DIAGNOSTIC_MAGIC);
                assert(mt_adc_first_start_failure.version == 1u);
                assert(mt_adc_first_start_failure.stage == MT_ADC_START_CORRELATION);
                assert(mt_adc_first_start_failure.driver_called == 0u);
                assert((uint32_t)(mt_adc_first_start_failure.after_us -
                       mt_adc_first_start_failure.before_us) == gaps[i] + 1u);
            }
        }
    }
}
static void diagnostic_setup(void) {
    setup(); clock_origin_us = hw.tick; clock_origin_ms = 100u;
    assert(mt_adc_control_init(&binding, &control, &driver, millis, &hw, 0) == MT_CONTROL_OK);
}
static uint32_t diag_enter(void *context) { (void)context; return 7u; }
static int diag_deny(void *context) { (void)context; return 0; }
static void diag_leave(void *context, uint32_t key) { (void)context; assert(key == 7u); }
static uint32_t desired_start_delay;
static uint32_t diag_jump_enter(void *context) {
    fake_t *f = context;
    f->tick = clock_origin_us + desired_start_delay;
    return 7u;
}
static int diag_allow(void *context) { (void)context; return 1; }
static void test_diagnostic_delay_boundary(void) {
    const uint32_t delays[] = {UINT32_C(0x7fffffff), UINT32_C(0x80000000)};
    for (unsigned i = 0; i < 2u; ++i) {
        const mt_adc_dma_start_guard guard = {diag_jump_enter, diag_allow, diag_leave, &hw, NULL};
        mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
        diagnostic_setup(); desired_start_delay = delays[i];
        assert(mt_adc_control_init_guarded(&binding, &control, &driver, millis,
               &hw, 0, &guard) == MT_CONTROL_OK);
        assert(control.io.start(control.io.context, 100u) == (i == 0u));
        if (i == 0u) {
            assert(mt_adc_first_start_failure.magic == 0u);
            assert(control.io.stop(control.io.context));
        } else {
            assert(mt_adc_first_start_failure.stage == MT_ADC_START_POSTCHECK);
            assert(mt_adc_first_start_failure.driver_called == 1u);
            assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_OK);
            assert((uint32_t)(mt_adc_first_start_failure.start_before_us -
                   mt_adc_first_start_failure.before_us) == delays[i]);
            assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
        }
    }
}
static void test_first_failure_diagnostic(void) {
    mt_adc_start_diagnostic saved;
    /* Fixture reset represents a fresh boot; production has no reset API. */
    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup();
    assert(!control.io.start(control.io.context, 101u));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_COMMAND_TIME);
    assert(mt_adc_first_start_failure.command_ms == 101u);
    assert(mt_adc_first_start_failure.epoch_ms == 100u);
    assert(mt_adc_first_start_failure.driver_called == 0u);
    saved = mt_adc_first_start_failure;
    assert(mt_control_recover(&control) == MT_CONTROL_RECOVERY);
    diagnostic_setup();
    hw.cal_stuck = true;
    assert(!control.io.start(control.io.context, 100u));
    assert(driver.fault == MT_ADC_DMA_FAULT_CALIBRATION);
    assert(memcmp(&saved, (const void *)&mt_adc_first_start_failure, sizeof saved) == 0);
    assert(control.io.stop(control.io.context));
    diagnostic_setup();
    assert(control.io.start(control.io.context, 100u));
    assert(control.io.stop(control.io.context));
    assert(memcmp(&saved, (const void *)&mt_adc_first_start_failure, sizeof saved) == 0);

    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup(); hw.cal_stuck = true;
    assert(!control.io.start(control.io.context, 100u));
    assert(mt_adc_first_start_failure.magic == MT_ADC_START_DIAGNOSTIC_MAGIC);
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
    assert(mt_adc_first_start_failure.driver_called == 1u);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_ERROR);
    assert(mt_adc_first_start_failure.driver_fault == MT_ADC_DMA_FAULT_CALIBRATION);
    saved = mt_adc_first_start_failure;
    assert(mt_control_recover(&control) == MT_CONTROL_RECOVERY);
    assert(memcmp(&saved, (const void *)&mt_adc_first_start_failure, sizeof saved) == 0);

    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup(); driver.state = MT_ADC_DMA_RUNNING;
    assert(!control.io.start(control.io.context, 100u));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_BUSY);
    assert(mt_adc_first_start_failure.driver_fault == MT_ADC_DMA_FAULT_NONE);
    assert(mt_adc_first_start_failure.driver_state == MT_ADC_DMA_RUNNING);
    assert(hw.write_count == 0u);

    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup();
    assert(!control.io.start(control.io.context, 100u - UINT32_C(0x80000000)));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_COMMAND_TIME);
    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup();
    assert(control.io.start(control.io.context, 100u - UINT32_C(0x7fffffff)));
    assert(mt_adc_first_start_failure.magic == 0u);
    assert(control.io.stop(control.io.context));

    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup(); driver.initialized = false;
    assert(!control.io.start(control.io.context, 100u));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_BAD_ARGUMENT);

    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup();
    {
        const mt_adc_dma_start_guard guard = {diag_enter, diag_deny, diag_leave, NULL, NULL};
        assert(mt_adc_control_init_guarded(&binding, &control, &driver, millis,
               &hw, 0, &guard) == MT_CONTROL_OK);
    }
    assert(!control.io.start(control.io.context, 100u));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_ERROR);
    assert(mt_adc_first_start_failure.driver_fault == MT_ADC_DMA_FAULT_ADMISSION);

    /* A legal noncanonical clock can reach the binding postcheck when the
     * hook is invoked directly, bypassing the controller's profile preflight. */
    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    diagnostic_setup(); driver.hse_hz = 0;
    *slot(&hw, R_CR) = 3u | (3u << 24);
    *slot(&hw, R_CFGR) = 2u | 8u | (4u << 8) | (1u << 14) | (10u << 18);
    assert(!control.io.start(control.io.context, 100u));
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_POSTCHECK);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_OK);
    assert(mt_adc_first_start_failure.driver_state == MT_ADC_DMA_RUNNING);
    assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
}
/* Binding-only coherent-clock model. The production TIM2 validation and its
 * execution cost are tested separately; these callbacks do not model MMIO time. */
static unsigned coherent_reads, coherent_millis_reads, coherent_entered;
static unsigned coherent_allowed, coherent_left, coherent_ok, coherent_denied;
static uint32_t coherent_callback_delay, coherent_pair_us, coherent_pair_ms;
static uint32_t coherent_millis(void *context) {
    ++coherent_millis_reads;
    return millis(context);
}
static uint32_t coherent_now_us(void *context) {
    /* No generic before/after clock read may sneak into coherent binding start:
     * the real driver's first time read follows its preparation MMIO writes. */
    assert(((fake_t *)context)->write_count != 0u);
    return now_us(context);
}
static int coherent_snapshot(void *context, uint32_t *us, uint32_t *ms) {
    fake_t *f = context;
    assert(f == &hw && coherent_reads == 0u);
    assert(f->time_reads == 0u && f->write_count == 0u);
    ++coherent_reads;
    coherent_pair_us = f->tick;
    coherent_pair_ms = millis(f);
    *us = coherent_pair_us; *ms = coherent_pair_ms;
    /* Time after the accepted observation must be retained in sample delay. */
    f->tick += coherent_callback_delay;
    return (int)coherent_ok;
}
static uint32_t coherent_enter(void *context) {
    assert(context == &hw && coherent_entered == 0u);
    ++coherent_entered;
    return 7u;
}
static int coherent_allow(void *context) {
    assert(context == &hw && coherent_entered == 1u && coherent_left == 0u);
    ++coherent_allowed;
    return !coherent_denied;
}
static void coherent_leave(void *context, uint32_t key) {
    assert(context == &hw && key == 7u && coherent_entered == 1u);
    assert(coherent_left == 0u);
    ++coherent_left;
}
static void coherent_setup(uint32_t us, uint32_t ms) {
    const mt_adc_dma_start_guard guard = {
        coherent_enter, coherent_allow, coherent_leave, &hw, NULL
    };
    setup(); hw.tick = clock_origin_us = us; clock_origin_ms = ms;
    coherent_reads = coherent_millis_reads = coherent_entered = 0u;
    coherent_allowed = coherent_left = coherent_denied = 0u;
    coherent_ok = 1u; coherent_callback_delay = 0u;
    coherent_pair_us = coherent_pair_ms = 0u;
    mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
    driver.io.now_us = coherent_now_us;
    assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
           coherent_millis, &hw, 0, &guard, coherent_snapshot) == MT_CONTROL_OK);
    assert(binding.snapshot == coherent_snapshot && binding.guarded);
    assert(hw.write_count == 0u && hw.time_reads == 0u);
}
static void test_coherent_pair_and_delay(void) {
    const uint32_t origins[][3] = {
        {1000u, 1u, 1u},
        {UINT32_MAX - 3u, 4294967u, 4294967u}, /* us32 wraps in driver. */
        {UINT32_MAX - 3u, UINT32_MAX, UINT32_MAX}, /* Both units wrap. */
        {0u, 0u, UINT32_MAX} /* True ms32 has wrapped after command time. */
    };
    for (unsigned row = 0; row < sizeof origins / sizeof origins[0]; ++row) {
        for (unsigned delayed = 0; delayed < 2u; ++delayed) {
            coherent_setup(origins[row][0], origins[row][1]);
            coherent_callback_delay = delayed ? 5000u : 0u;
            assert(control.io.start(control.io.context, origins[row][2]));
            assert(coherent_reads == 1u && coherent_millis_reads == 0u);
            assert(binding.epoch_ms == origins[row][1]);
            assert(coherent_pair_us == origins[row][0]);
            assert(coherent_pair_ms == origins[row][1]);
            assert(binding.first_sample_delay_us ==
                   (uint32_t)(driver.start_before_us - coherent_pair_us) + 20u);
            assert(binding.first_sample_delay_us >= coherent_callback_delay + 20u);
            if (origins[row][1] == UINT32_MAX && delayed)
                assert(binding.epoch_ms + binding.first_sample_delay_us / 1000u == 4u);
            assert((uint32_t)(driver.start_after_us - driver.start_before_us) <= 20u);
            assert(driver.state == MT_ADC_DMA_RUNNING && driver.owns_peripherals);
            assert(coherent_entered == 1u && coherent_allowed == 2u && coherent_left == 1u);
            assert(mt_adc_first_start_failure.magic == 0u);
            assert(control.io.stop(control.io.context));
        }
    }
}
static void test_coherent_failure_and_guard(void) {
    coherent_setup(1000u, 100u); coherent_ok = 0u;
    assert(!control.io.start(control.io.context, 100u));
    assert(coherent_reads == 1u && coherent_millis_reads == 0u);
    assert(hw.write_count == 0u && hw.time_reads == 0u && !coherent_entered);
    assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
    assert(mt_adc_first_start_failure.magic == MT_ADC_START_DIAGNOSTIC_MAGIC);
    assert(mt_adc_first_start_failure.version == 1u);
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_SNAPSHOT);
    assert(mt_adc_first_start_failure.command_ms == 100u);
    assert(mt_adc_first_start_failure.driver_called == 0u);
    assert(mt_adc_first_start_failure.before_us == 0u &&
           mt_adc_first_start_failure.after_us == 0u);

    coherent_setup(1000u, 100u); coherent_denied = 1u;
    assert(!control.io.start(control.io.context, 100u));
    assert(coherent_reads == 1u && coherent_millis_reads == 0u);
    assert(coherent_entered == 1u && coherent_allowed == 1u && coherent_left == 1u);
    assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
    assert(mt_adc_first_start_failure.driver_called == 1u);
    assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_ERROR);
    assert(mt_adc_first_start_failure.driver_fault == MT_ADC_DMA_FAULT_ADMISSION);
    assert(mt_adc_first_start_failure.before_us == coherent_pair_us &&
           mt_adc_first_start_failure.after_us == coherent_pair_us);
    assert(!driver.owns_peripherals);
    for (unsigned i = 0; i < hw.write_count; ++i) {
        if (hw.writes[i].address == T_CR1 || hw.writes[i].address == D_CCR)
            assert(!(hw.writes[i].value & 1u));
        if (hw.writes[i].address == A_CR2)
            assert(!(hw.writes[i].value & (1u << 20)));
    }
}
static void test_coherent_halfwrap_bounds(void) {
    const uint32_t differences[] = {
        UINT32_C(0x7fffffff), UINT32_C(0x80000000), UINT32_MAX
    };
    for (unsigned i = 0; i < sizeof differences / sizeof differences[0]; ++i) {
        coherent_setup(1000u, 100u);
        assert(control.io.start(control.io.context, 100u - differences[i]) == (i == 0u));
        assert(coherent_reads == 1u && coherent_millis_reads == 0u);
        if (i == 0u) {
            assert(mt_adc_first_start_failure.magic == 0u);
            assert(control.io.stop(control.io.context));
        } else {
            assert(hw.write_count == 0u && hw.time_reads == 0u && !coherent_entered);
            assert(mt_adc_first_start_failure.stage == MT_ADC_START_COMMAND_TIME);
            assert(mt_adc_first_start_failure.driver_called == 0u);
        }
        /* Deliberately inject a huge elapsed interval to test modular rejection,
         * not the TIM2 <65536-tick servicing contract or real elapsed time. */
        coherent_setup(UINT32_MAX - 10u, 100u);
        {
            const mt_adc_dma_start_guard guard = {
                diag_jump_enter, diag_allow, diag_leave, &hw, NULL
            };
            desired_start_delay = differences[i];
            assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
                   coherent_millis, &hw, 0, &guard, coherent_snapshot) == MT_CONTROL_OK);
        }
        assert(control.io.start(control.io.context, 100u) == (i == 0u));
        assert(coherent_reads == 1u && coherent_millis_reads == 0u);
        if (i == 0u) {
            assert(mt_adc_first_start_failure.magic == 0u);
            assert(control.io.stop(control.io.context));
        } else {
            assert(mt_adc_first_start_failure.stage == MT_ADC_START_POSTCHECK);
            assert(mt_adc_first_start_failure.driver_called == 1u);
            assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_OK);
            assert((uint32_t)(mt_adc_first_start_failure.start_before_us -
                   mt_adc_first_start_failure.before_us) == differences[i]);
            assert(driver.state == MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
        }
    }
}
static void test_coherent_init_arguments(void) {
    mt_adc_dma_start_guard guard = {coherent_enter, coherent_allow, coherent_leave, &hw, NULL};
    coherent_setup(1000u, 100u);
    assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
           coherent_millis, &hw, 0, &guard, NULL) == MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
           NULL, &hw, 0, &guard, coherent_snapshot) == MT_CONTROL_ARGUMENT);
    assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
           coherent_millis, &hw, 0, NULL, coherent_snapshot) == MT_CONTROL_ARGUMENT);
    guard.allowed = NULL;
    assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
           coherent_millis, &hw, 0, &guard, coherent_snapshot) == MT_CONTROL_ARGUMENT);
    assert(hw.write_count == 0u && hw.time_reads == 0u && coherent_reads == 0u);
    /* Rebinding through either legacy init must remove the optional callback. */
    assert(mt_adc_control_init(&binding, &control, &driver,
           millis, &hw, 0) == MT_CONTROL_OK);
    assert(binding.snapshot == NULL && !binding.guarded);
    guard.allowed = coherent_allow;
    assert(mt_adc_control_init_guarded(&binding, &control, &driver,
           millis, &hw, 0, &guard) == MT_CONTROL_OK);
    assert(binding.snapshot == NULL && binding.guarded);
}
/* Timed-admission model: injected durations are synthetic microseconds, not a
 * measurement of TIM2, Cortex-M3 or the board. The unchanged register fixture
 * above supplies the actual driver/binding call path. */
enum { TIMED_OTHER, TIMED_BEFORE_RETURN, TIMED_CEN_WRITE };
static struct {
    uint32_t mask, prior_mask, early_stamp, stamps[2], cen_tick;
    uint32_t early_cost, before_head, before_tail, after_head, cen_cost;
    unsigned entered, left, allowed, calls, cen_writes, cleanup_writes;
    unsigned event, deny_early, deny_call, poison_output, mutate_clock;
    unsigned corrupt_readback, use_timed, time_reads_on_enter;
} timed;
static uint32_t timed_read32(void *context, uint32_t address) {
    timed.event = TIMED_OTHER;
    return read32(context, address);
}
static void timed_write32(void *context, uint32_t address, uint32_t value) {
    if (address == T_CR1 && (value & 1u)) {
        assert(timed.entered == 1u && timed.left == 0u && timed.mask == 1u);
        if (timed.use_timed) {
            assert(timed.calls == 1u && timed.event == TIMED_BEFORE_RETURN);
            assert(driver.start_before_us == timed.stamps[0]);
        }
        ++timed.cen_writes;
        timed.cen_tick = hw.tick;
        write32(context, address, timed.corrupt_readback ? 0u : value);
        hw.tick += timed.cen_cost;
        timed.event = TIMED_CEN_WRITE;
        return;
    }
    if (timed.entered &&
        ((address == T_CR1 && value == 0u) ||
         (address == A_CR2 && value == 0u) ||
         (address == D_CCR && !(value & 1u)))) {
        /* Cleanup must not run under a mask acquired by this guard, even if
         * DMA disable stalls. An already-masked caller stays masked. */
        assert(timed.left == 1u && timed.mask == timed.prior_mask);
        ++timed.cleanup_writes;
    }
    timed.event = TIMED_OTHER;
    write32(context, address, value);
}
static uint32_t timed_now_us(void *context) {
    if (timed.entered && !timed.left && timed.use_timed)
        assert(timed.allowed == 0u && timed.calls == 0u);
    timed.event = TIMED_OTHER;
    return now_us(context);
}
static uint32_t timed_enter(void *context) {
    assert(context == &hw && timed.entered == 0u && timed.mask == timed.prior_mask);
    assert(!(*slot(&hw, D_CCR) & 1u) && !(*slot(&hw, T_CR1) & 1u));
    assert(!(*slot(&hw, A_CR2) & (1u << 20)));
    timed.time_reads_on_enter = hw.time_reads;
    ++timed.entered;
    timed.mask = 1u;
    return timed.prior_mask;
}
static int timed_allowed(void *context) {
    assert(context == &hw && timed.entered == 1u && !timed.left && timed.mask == 1u);
    ++timed.allowed;
    if (timed.allowed == 1u) {
        assert(hw.time_reads == timed.time_reads_on_enter + 1u);
        assert(!timed.cen_writes && !(*slot(&hw, D_CCR) & 1u));
        assert(!(*slot(&hw, A_CR2) & (1u << 20)));
        timed.early_stamp = driver.start_before_us;
        hw.tick += timed.early_cost;
        if (timed.mutate_clock) *slot(&hw, R_CFGR) ^= 1u << 14;
    } else {
        assert(!timed.use_timed && timed.allowed == 2u && timed.cen_writes == 1u);
    }
    return !timed.deny_early;
}
static void timed_leave(void *context, uint32_t key) {
    assert(context == &hw && timed.entered == 1u && !timed.left && timed.mask == 1u);
    assert(key == timed.prior_mask);
    timed.mask = key;
    ++timed.left;
}
static int timed_snapshot_allowed(void *context, uint32_t *us) {
    unsigned call = ++timed.calls;
    assert(context == &hw && timed.entered == 1u && !timed.left && timed.mask == 1u);
    assert(us && timed.allowed == 1u && call <= 2u);
    assert(*slot(&hw, D_CCR) & 1u);
    assert(*slot(&hw, A_CR2) & (1u << 20));
    if (call == 1u) {
        assert(!timed.cen_writes && !(*slot(&hw, T_CR1) & 1u));
        hw.tick += timed.before_head;
    } else {
        /* No separate clock read or other MMIO is allowed between CEN and
         * entering the fresh after observation. */
        assert(timed.cen_writes == 1u && timed.event == TIMED_CEN_WRITE);
        hw.tick += timed.after_head;
    }
    timed.stamps[call - 1u] = hw.tick;
    if (call == timed.deny_call) {
        /* A false result is authoritative even if a buggy callback has written
         * a plausible timestamp. A conforming callback leaves it untouched. */
        if (timed.poison_output) *us = timed.stamps[0];
        return 0;
    }
    *us = timed.stamps[call - 1u];
    if (call == 1u) {
        hw.tick += timed.before_tail;
        timed.event = TIMED_BEFORE_RETURN;
    }
    return 1;
}
static mt_adc_dma_start_guard timed_setup(uint32_t prior_mask) {
    const mt_adc_dma_start_guard guard = {
        timed_enter, timed_allowed, timed_leave, &hw, timed_snapshot_allowed
    };
    setup();
    memset(&timed, 0, sizeof timed);
    timed.mask = timed.prior_mask = prior_mask;
    timed.use_timed = 1u;
    timed.cen_cost = 1u;
    driver.io.read32 = timed_read32;
    driver.io.write32 = timed_write32;
    driver.io.now_us = timed_now_us;
    return guard;
}
static void timed_assert_cleanup(unsigned cen_count, mt_adc_dma_fault_t fault) {
    assert(timed.entered == 1u && timed.left == 1u && timed.mask == timed.prior_mask);
    assert(timed.cen_writes == cen_count && timed.cleanup_writes == 3u);
    assert(driver.state == MT_ADC_DMA_FAULTED && driver.fault == fault);
    assert(!(*slot(&hw, T_CR1) & 1u) && !*slot(&hw, A_CR2));
    if (fault == MT_ADC_DMA_FAULT_STOP) {
        assert(driver.owns_peripherals && (*slot(&hw, D_CCR) & 1u));
    } else {
        assert(!driver.owns_peripherals && !(*slot(&hw, D_CCR) & 1u));
    }
}
static void test_timed_admission_failures(void) {
    for (uint32_t prior = 0; prior < 2u; ++prior) {
        mt_adc_dma_start_guard guard = timed_setup(prior);
        timed.deny_early = 1u;
        assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_ERROR);
        assert(timed.allowed == 1u && timed.calls == 0u);
        timed_assert_cleanup(0u, MT_ADC_DMA_FAULT_ADMISSION);
        for (unsigned call = 1u; call <= 2u; ++call) {
            for (unsigned poison = 0u; poison < 2u; ++poison) {
                for (unsigned stuck = 0u; stuck < 2u; ++stuck) {
                    guard = timed_setup(prior);
                    timed.deny_call = call; timed.poison_output = poison;
                    hw.stop_stuck = stuck != 0u;
                    assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_ERROR);
                    assert(timed.allowed == 1u && timed.calls == call);
                    timed_assert_cleanup(call - 1u, stuck ? MT_ADC_DMA_FAULT_STOP :
                                                           MT_ADC_DMA_FAULT_ADMISSION);
                    if (stuck) {
                        unsigned writes = hw.write_count;
                        assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_BUSY);
                        assert(hw.write_count == writes && timed.entered == 1u);
                        hw.stop_stuck = false;
                        assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
                        assert(!driver.owns_peripherals && driver.state == MT_ADC_DMA_STOPPED);
                    }
                }
            }
        }
    }
}
static void test_timed_admission_width(void) {
    const uint32_t widths[] = {1u, 20u, 21u, 5000u};
    for (uint32_t prior = 0; prior < 2u; ++prior) {
        for (unsigned wrap = 0u; wrap < 2u; ++wrap) {
            for (unsigned i = 0u; i < sizeof widths / sizeof widths[0]; ++i) {
                mt_adc_dma_start_guard guard = timed_setup(prior);
                hw.tick = wrap ? UINT32_MAX - 16u : 1000u;
                timed.cen_cost = widths[i];
                assert(mt_adc_dma_start_guarded(&driver, &guard) ==
                       (widths[i] <= 20u ? MT_ADC_DMA_OK : MT_ADC_DMA_ERROR));
                assert(timed.calls == 2u && timed.allowed == 1u && timed.cen_writes == 1u);
                assert(driver.start_before_us == timed.stamps[0]);
                assert(driver.start_after_us == timed.stamps[1]);
                assert((uint32_t)(driver.start_after_us - driver.start_before_us) == widths[i]);
                assert((uint32_t)(timed.cen_tick - driver.start_before_us) <= widths[i]);
                if (wrap && widths[i] >= 20u)
                    assert(driver.start_after_us < driver.start_before_us);
                if (widths[i] <= 20u) {
                    assert(driver.state == MT_ADC_DMA_RUNNING && driver.owns_peripherals);
                    assert(driver.last_poll_us == timed.stamps[0]);
                    assert(driver.last_progress_us == timed.stamps[0]);
                    assert(timed.left == 1u && timed.mask == prior && !timed.cleanup_writes);
                    assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
                } else timed_assert_cleanup(1u, MT_ADC_DMA_FAULT_TIMEBASE);
            }
        }
        /* Callback time is not declared zero: the tail after the first sample
         * and the work before the second sample both consume the same bound. */
        for (unsigned side = 0u; side < 2u; ++side) {
            mt_adc_dma_start_guard guard = timed_setup(prior);
            if (side) timed.after_head = 5000u;
            else timed.before_tail = 5000u;
            assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_ERROR);
            assert((uint32_t)(driver.start_after_us - driver.start_before_us) == 5001u);
            timed_assert_cleanup(1u, MT_ADC_DMA_FAULT_TIMEBASE);
        }
    }
}
static void test_timed_checks_and_legacy(void) {
    for (uint32_t prior = 0u; prior < 2u; ++prior) {
        mt_adc_dma_start_guard guard = timed_setup(prior);
        timed.mutate_clock = 1u;
        assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_ERROR);
        assert(timed.calls == 0u && timed.allowed == 1u);
        timed_assert_cleanup(0u, MT_ADC_DMA_FAULT_CLOCK);
        guard = timed_setup(prior); timed.corrupt_readback = 1u;
        assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_ERROR);
        assert(timed.calls == 2u);
        timed_assert_cleanup(1u, MT_ADC_DMA_FAULT_TRANSFER);
        for (unsigned slow = 0u; slow < 2u; ++slow) {
            guard = timed_setup(prior); guard.timed_allowed = NULL;
            timed.use_timed = 0u; timed.early_cost = slow ? 5000u : 0u;
            assert(mt_adc_dma_start_guarded(&driver, &guard) ==
                   (slow ? MT_ADC_DMA_ERROR : MT_ADC_DMA_OK));
            assert(timed.calls == 0u && timed.allowed == 2u && timed.cen_writes == 1u);
            assert(driver.start_before_us == timed.early_stamp);
            assert((uint32_t)(driver.start_after_us - driver.start_before_us) ==
                   (slow ? 5002u : 2u));
            if (slow) timed_assert_cleanup(1u, MT_ADC_DMA_FAULT_TIMEBASE);
            else {
                assert(timed.left == 1u && timed.mask == prior);
                assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
            }
        }
        /* The new opt-in path moves only the CEN bracket, so the same early
         * preparation delay is accepted while remaining in first-sample time. */
        guard = timed_setup(prior); timed.early_cost = 5000u;
        assert(mt_adc_dma_start_guarded(&driver, &guard) == MT_ADC_DMA_OK);
        assert(driver.start_before_us - timed.early_stamp == 5001u);
        assert(driver.start_after_us - driver.start_before_us == 1u);
        assert(mt_adc_dma_stop(&driver) == MT_ADC_DMA_OK);
    }
}
static int timed_binding_snapshot(void *context, uint32_t *us, uint32_t *ms) {
    assert(context == &hw && !timed.entered && !hw.time_reads && !hw.write_count);
    *us = hw.tick; *ms = millis(context);
    return 1;
}
static void test_timed_binding_anchor_and_failure(void) {
    for (unsigned coherent = 0u; coherent < 2u; ++coherent) {
        for (unsigned wrap = 0u; wrap < 2u; ++wrap) {
            mt_adc_dma_start_guard guard = timed_setup(wrap);
            hw.tick = clock_origin_us = wrap ? UINT32_MAX - 16u : 1000u;
            clock_origin_ms = wrap ? UINT32_MAX : 100u;
            timed.early_cost = 5000u; timed.before_head = 19u;
            mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
            if (coherent)
                assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
                       millis, &hw, 0, &guard, timed_binding_snapshot) == MT_CONTROL_OK);
            else
                assert(mt_adc_control_init_guarded(&binding, &control, &driver,
                       millis, &hw, 0, &guard) == MT_CONTROL_OK);
            assert(binding.guard.timed_allowed == timed_snapshot_allowed);
            assert(control.io.start(control.io.context, clock_origin_ms));
            assert(binding.epoch_ms == clock_origin_ms && !mt_adc_first_start_failure.magic);
            assert(driver.start_before_us == timed.stamps[0]);
            assert(binding.first_sample_delay_us ==
                   (uint32_t)(timed.stamps[0] - clock_origin_us) + 20u);
            assert(binding.first_sample_delay_us > 5000u);
            assert(driver.start_before_us - timed.early_stamp == 5020u);
            {
                mt_adc_dma_block_t block = {0};
                first_half_pending();
                assert(mt_adc_dma_read_block(&driver, &block) == MT_ADC_DMA_BLOCK);
                assert(block.first_sample_index == 0u);
                assert(block.start_before_us == timed.stamps[0]);
                assert(block.start_after_us == timed.stamps[1]);
                assert(block.first_trigger_offset_us == 20u);
            }
            assert(control.io.stop(control.io.context));
        }
        for (unsigned call = 1u; call <= 2u; ++call) {
            mt_adc_dma_start_guard guard = timed_setup(0u);
            clock_origin_us = hw.tick; clock_origin_ms = 100u;
            timed.deny_call = call; timed.poison_output = 1u;
            mt_adc_first_start_failure = (mt_adc_start_diagnostic){0};
            if (coherent)
                assert(mt_adc_control_init_coherent_guarded(&binding, &control, &driver,
                       millis, &hw, 0, &guard, timed_binding_snapshot) == MT_CONTROL_OK);
            else
                assert(mt_adc_control_init_guarded(&binding, &control, &driver,
                       millis, &hw, 0, &guard) == MT_CONTROL_OK);
            assert(!control.io.start(control.io.context, clock_origin_ms));
            assert(binding.first_sample_delay_us == 0u);
            timed_assert_cleanup(call - 1u, MT_ADC_DMA_FAULT_ADMISSION);
            assert(mt_adc_first_start_failure.stage == MT_ADC_START_DRIVER);
            assert(mt_adc_first_start_failure.driver_called == 1u);
            assert(mt_adc_first_start_failure.driver_result == MT_ADC_DMA_ERROR);
            assert(mt_adc_first_start_failure.driver_fault == MT_ADC_DMA_FAULT_ADMISSION);
        }
    }
}

int main(void) {
    assert(platform_regression_main() == 0);
    test_profile_no_writes();
    test_clock_correlation();
    test_first_failure_diagnostic();
    test_diagnostic_delay_boundary();
    test_coherent_pair_and_delay();
    test_coherent_failure_and_guard();
    test_coherent_halfwrap_bounds();
    test_coherent_init_arguments();
    test_timed_admission_failures();
    test_timed_admission_width();
    test_timed_checks_and_legacy();
    test_timed_binding_anchor_and_failure();
    test_window_and_time(1000, 1);
    test_window_and_time(UINT32_MAX - 1000u, UINT32_MAX - 20u);
    test_stale_and_driver_fault();
    for (unsigned capturing = 0; capturing < 2u; ++capturing)
        for (unsigned deadline = 0; deadline < 2u; ++deadline)
            for (unsigned stop_first = 0; stop_first < 2u; ++stop_first)
                test_reported_fault(capturing, deadline, stop_first);
    puts("ADC-control: canonical preflight, register-model path, partial freeze, legacy/coherent clock bounds, timed admission, diagnostics and stale Stop passed");
    return 0;
}
