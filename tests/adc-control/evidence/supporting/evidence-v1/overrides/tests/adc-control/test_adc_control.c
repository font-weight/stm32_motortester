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
    assert(mt_adc_control_service(&binding) == MT_CONTROL_RECOVERY);
    assert(!control.ready && !control.hardware_running);
    assert(!driver.owns_peripherals);
}
int main(void) {
    assert(platform_regression_main() == 0);
    test_profile_no_writes();
    test_window_and_time(1000, 1);
    test_window_and_time(UINT32_MAX - 1000u, UINT32_MAX - 20u);
    test_stale_and_driver_fault();
    puts("ADC-control: canonical preflight, actual register-model path, partial freeze, clock wrap and stale Stop passed");
    return 0;
}
