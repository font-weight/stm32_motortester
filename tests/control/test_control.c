#include "../../firmware/app/mt_control.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    unsigned starts, stops;
    int profile_ok, start_ok, stop_ok;
} hardware;
typedef struct {
    mt_control control;
    hardware hardware;
    uint8_t output[32768];
    size_t used, step;
    uint64_t nonce;
    uint32_t sequence, request;
} fixture;
static fixture f;
static mt_wire_message replies[64];
static int profile(void *context) { return ((hardware *)context)->profile_ok; }
static int start(void *context, uint32_t now) {
    hardware *h = context;
    (void)now; ++h->starts;
    return h->start_ok;
}
static int stop(void *context) {
    hardware *h = context;
    ++h->stops;
    return h->stop_ok;
}
static void put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
static void put32(uint8_t *p, uint32_t v) {
    unsigned i;
    for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(v >> (i * 8u));
}
static uint16_t get16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | (uint16_t)((uint16_t)p[1] << 8));
}
static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static int write_bytes(void *context, const uint8_t *p, size_t n) {
    fixture *x = context;
    assert(n <= 64u);
    if (n > x->step) n = x->step;
    assert(x->used + n <= sizeof x->output);
    memcpy(x->output + x->used, p, n); x->used += n;
    return (int)n;
}
static mt_wire_message command(uint8_t type, uint16_t length) {
    mt_wire_message m = {0};
    m.type = type; m.payload_length = length; m.session = f.nonce;
    m.sequence = f.sequence++; m.request = f.request++;
    return m;
}
static mt_control_result receive(const mt_wire_message *m) {
    uint8_t encoded[MT_WIRE_FRAME_MAX];
    mt_wire_message decoded;
    size_t length;
    assert(mt_wire_encode(m, encoded, sizeof encoded, &length) == MT_WIRE_OK);
    assert(encoded[length - 1u] == 0u);
    assert(mt_wire_decode(encoded, length - 1u, &decoded) == MT_WIRE_OK);
    return mt_control_receive(&f.control, &decoded, 0);
}
static void drain(void) {
    unsigned budget = 20000;
    do {
        assert(mt_control_poll(&f.control, 0, write_bytes, &f) == MT_CONTROL_OK);
        assert(budget--);
    } while (f.control.tx.count || f.control.state_count ||
             (f.control.link.phase >= MT_LINK_BEGIN &&
              f.control.link.phase <= MT_LINK_END));
}
static size_t decode(void) {
    size_t i, first = 0, count = 0;
    for (i = 0; i < f.used; ++i) {
        if (f.output[i] != 0u) continue;
        assert(count < 64u);
        assert(mt_wire_decode(f.output + first, i - first, &replies[count]) == MT_WIRE_OK);
        ++count; first = i + 1u;
    }
    assert(first == f.used);
    return count;
}
static void new_hello(uint64_t nonce) {
    mt_wire_message m = {0};
    unsigned i;
    m.type = 1; m.sequence = 1; m.request = 1; m.payload_length = 8;
    for (i = 0; i < 8u; ++i) m.payload[i] = (uint8_t)(nonce >> (8u * i));
    assert(receive(&m) == MT_CONTROL_OK);
    f.nonce = nonce; f.sequence = 2; f.request = 2;
    drain();
}
static void setup(void) {
    mt_control_io io;
    memset(&f, 0, sizeof f);
    f.step = 64;
    f.hardware.profile_ok = f.hardware.start_ok = f.hardware.stop_ok = 1;
    io.profile_ready = profile; io.start = start; io.stop = stop;
    io.context = &f.hardware; io.firmware_build = 0x12345678;
    assert(mt_control_init(&f.control, &io) == MT_CONTROL_OK);
    assert(f.hardware.stops == 0u);
    new_hello(42);
    assert(decode() == 1u && replies[0].type == 129u);
    assert(get32(replies[0].payload + 12) == 113u);
    f.used = 0;
}
static void configure(uint8_t mode) {
    mt_wire_message m = command(3, 16);
    put16(m.payload, 1); m.payload[2] = mode;
    put16(m.payload + 6, 2048);
    if (mode == 0u) put32(m.payload + 12, 100);
    assert(receive(&m) == MT_CONTROL_OK);
    drain(); assert(decode() == 1u && replies[0].type == 131u);
    assert(get32(replies[0].payload) == 1u);
    assert(memcmp(replies[0].payload + 4, m.payload, 16) == 0);
    f.used = 0;
}
static uint32_t arm_capture(void) {
    mt_wire_message m = command(4, 4);
    put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_OK);
    drain();
    assert(decode() == 2u && replies[0].type == 132u && replies[1].type == 134u);
    assert(get32(replies[0].payload) == m.request);
    f.used = 0;
    return m.request;
}
static void test_preflight(void) {
    mt_wire_message m;
    unsigned stops, starts;
    uint32_t generation;
    setup(); configure(2); generation = arm_capture();
    stops = f.hardware.stops; starts = f.hardware.starts;
    m = command(5, 4); put32(m.payload, generation - 1u);
    assert(receive(&m) == MT_CONTROL_OK); drain();
    assert(f.hardware.stops == stops && f.hardware.starts == starts);
    assert(f.control.hardware_running);
    assert(decode() == 1u && replies[0].type == 255u && replies[0].payload[1] == 4u);
    f.used = 0;
    m = command(4, 4); put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_OK); drain();
    assert(decode() == 1u && replies[0].type == 255u && replies[0].payload[1] == 2u);
    assert(f.hardware.starts == starts); f.used = 0;
    m = command(5, 4); put32(m.payload, generation);
    assert(receive(&m) == MT_CONTROL_OK);
    assert(f.hardware.stops == stops + 1u && !f.control.hardware_running);
    drain(); assert(decode() == 2u && replies[0].type == 133u);
}
static void test_twenty_windows(void) {
    uint32_t generation;
    unsigned window, i;
    setup(); configure(2); generation = arm_capture();
    for (window = 1; window <= 20u; ++window) {
        uint8_t raw[4096];
        mt_wire_message release;
        size_t n, j, offset = 0;
        unsigned starts;
        assert(mt_control_sample(&f.control, 100, 0) == MT_CONTROL_OK);
        assert(mt_control_sample(&f.control, 3000, 0) == MT_CONTROL_OK);
        put16(raw, 3000);
        for (i = 1; i < 2048u; ++i) {
            uint16_t code = (uint16_t)((i + window) % 4096u);
            put16(raw + 2u * i, code);
            assert(mt_control_sample(&f.control, code, 0) == MT_CONTROL_OK);
        }
        assert(!f.control.hardware_running);
        f.step = window % 2u ? 1u : 64u;
        drain(); n = decode();
        for (j = 0; j < n; ++j) {
            if (replies[j].type == 145u) {
                uint16_t count = get16(replies[j].payload + 10);
                assert(get16(replies[j].payload + 8) == offset);
                assert(memcmp(raw + offset * 2u, replies[j].payload + 12, count * 2u) == 0);
                offset += count;
            }
            if (replies[j].type == 146u)
                assert(get32(replies[j].payload + 12) == mt_wire_crc32(raw, sizeof raw));
        }
        assert(offset == 2048u && f.control.link.phase == MT_LINK_WAIT_RELEASE);
        f.used = 0; starts = f.hardware.starts;
        release = command(7, 12);
        put32(release.payload, generation); put32(release.payload + 4, window);
        put32(release.payload + 8, mt_wire_crc32(raw, sizeof raw));
        assert(receive(&release) == MT_CONTROL_OK); drain();
        assert(f.hardware.starts == starts && !f.control.hardware_running);
        f.used = 0;
        release.sequence = f.sequence++; release.request = f.request++;
        assert(receive(&release) == MT_CONTROL_OK); drain();
        assert(f.hardware.starts == starts);
        f.used = 0;
        if (window < 20u) generation = arm_capture();
    }
}
static void test_failed_hardware(void) {
    mt_wire_message m;
    size_t bytes;
    setup(); configure(2);
    f.hardware.start_ok = 0;
    m = command(4, 4); put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_RECOVERY);
    assert(!f.control.ready && !f.control.link.armed);
    bytes = f.used;
    assert(mt_control_poll(&f.control, 0, write_bytes, &f) == MT_CONTROL_IGNORED);
    assert(f.used == bytes);
    f.hardware.start_ok = 1; new_hello(43);
    assert(decode() == 1u && replies[0].type == 129u);
    f.used = 0; configure(2);
    (void)arm_capture();
    m = command(5, 4); put32(m.payload, f.control.link.generation);
    f.hardware.stop_ok = 0;
    assert(receive(&m) == MT_CONTROL_RECOVERY);
    bytes = f.used;
    assert(mt_control_poll(&f.control, 0, write_bytes, &f) == MT_CONTROL_IGNORED);
    assert(f.used == bytes);
}
int main(void) {
    test_preflight(); test_twenty_windows(); test_failed_hardware();
    puts("control: stale Stop preflight, 20 encoded window loops and hardware failure recovery passed");
    return 0;
}
