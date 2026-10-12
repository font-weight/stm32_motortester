#define main controller_regression_main
#include "../control/test_control.c"
#undef main
#include "../../firmware/app/mt_control_rx.h"
static mt_control_rx rx;
static mt_control before;
static uint8_t wire[MT_WIRE_FRAME_MAX * 2u];
static size_t encode_at(mt_wire_message *m, size_t offset) {
    size_t n;
    assert(mt_wire_encode(m, wire + offset, sizeof wire - offset, &n) == MT_WIRE_OK);
    return n;
}
static mt_wire_message hello(uint8_t nonce) {
    mt_wire_message m = {0};
    m.type = 1; m.sequence = m.request = 1; m.payload_length = 8;
    m.payload[0] = nonce;
    return m;
}
static void feed_exact(const uint8_t *p, size_t n, uint32_t now) {
    size_t consumed;
    assert(n <= 64u);
    assert(mt_control_rx_feed(&rx, p, n, now, &consumed) == MT_CONTROL_OK);
    assert(consumed == n);
}
static void fresh(void) {
    mt_wire_message m;
    size_t n;
    setup();
    assert(mt_control_rx_init(&rx, &f.control) == MT_CONTROL_OK);
    m = hello(43); n = encode_at(&m, 0); feed_exact(wire, n, 0);
    f.nonce = 43; f.sequence = f.request = 2;
    drain(); assert(decode() == 1 && replies[0].type == 129);
    f.used = 0;
}
static void test_config_splits_and_stop(void) {
    size_t split;
    for (split = 0; split <= 46u; ++split) {
        mt_wire_message m;
        size_t n;
        fresh();
        m = command(3, 16); put16(m.payload, 1); m.payload[2] = 2;
        put16(m.payload + 6, 2048); n = encode_at(&m, 0);
        assert(n == 46u);
        feed_exact(wire, split, 0); feed_exact(wire + split, n - split, 0);
        drain(); assert(decode() == 1 && replies[0].type == 131); f.used = 0;
        m = command(4, 4); put32(m.payload, 1); n = encode_at(&m, 0);
        feed_exact(wire, n, 0); assert(f.control.hardware_running); drain(); f.used = 0;
        m = command(5, 4); put32(m.payload, f.control.link.generation);
        n = encode_at(&m, 0); feed_exact(wire, n, 0);
        assert(!f.control.hardware_running); drain();
        assert(decode() == 2 && replies[0].type == 133);
    }
}
static void test_rejected_and_timeouts(void) {
    uint8_t bad[] = {2, 1, 0};
    uint8_t nonzero[64];
    unsigned i;
    fresh(); configure(2); (void)arm_capture();
    before = f.control;
    feed_exact(bad, sizeof bad, 0);
    assert(rx.rejected_frames == 1 && memcmp(&before, &f.control, sizeof before) == 0);
    rx.rejected_frames = UINT32_MAX; feed_exact(bad, sizeof bad, 0);
    assert(rx.rejected_frames == UINT32_MAX);
    /* Start a separate RX clock epoch for the rollover case. */
    assert(mt_control_rx_init(&rx, &f.control) == MT_CONTROL_OK);
    feed_exact(bad, 1, UINT32_MAX - 500u);
    assert(mt_control_rx_poll(&rx, 499u) == MT_CONTROL_OK);
    assert(rx.timeouts == 1 && rx.stream.discarding);
    feed_exact(bad + 2, 1, 499u);
    memset(nonzero, 1, sizeof nonzero);
    for (i = 0; i < 9u; ++i) feed_exact(nonzero, sizeof nonzero, 499u);
    assert(rx.overflows == 1 && rx.stream.discarding);
    assert(memcmp(&before, &f.control, sizeof before) == 0);
    feed_exact(bad + 2, 1, 499u);
    assert(!rx.stream.discarding);
    assert(mt_control_rx_transport_lost(&rx) == MT_CONTROL_RECOVERY);
    assert(!f.control.ready && !f.control.hardware_running && !rx.stream.used);
}
static void test_recovery_suffix(void) {
    mt_wire_message m;
    size_t first, second, consumed;
    fresh();
    m = command(6, 0); m.sequence += 2;
    first = encode_at(&m, 0); m = hello(44); second = encode_at(&m, first);
    assert(first + second > 64u && first < 64u && second < 64u);
    assert(mt_control_rx_feed(&rx, wire, 64, 0, &consumed) == MT_CONTROL_RECOVERY);
    assert(consumed == first && !f.control.ready);
    feed_exact(wire + consumed, second, 0);
    assert(f.control.ready && f.control.tx.session == 44u);
}
static void test_arguments(void) {
    size_t consumed = 42;
    fresh(); before = f.control;
    assert(mt_control_rx_feed(&rx, wire, 65, 0, &consumed) == MT_CONTROL_ARGUMENT);
    assert(consumed == 0 && memcmp(&before, &f.control, sizeof before) == 0);
    assert(mt_control_rx_feed(&rx, NULL, 1, 0, &consumed) == MT_CONTROL_ARGUMENT);
    assert(mt_control_rx_feed(&rx, wire, 1, 0, NULL) == MT_CONTROL_ARGUMENT);
    feed_exact(NULL, 0, 0);
}
int main(void) {
    assert(controller_regression_main() == 0);
    test_config_splits_and_stop(); test_rejected_and_timeouts();
    test_recovery_suffix(); test_arguments();
    puts("RX: encoded command splits, rejection isolation, timeout/wrap, overflow and recovery suffix passed");
    return 0;
}
