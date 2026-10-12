/* Author-written 0.2 integration cases using the current controller fixture. */
#define main existing_control_main
#include "../control/test_control.c"
#undef main

static void expect_error(mt_wire_message *m, unsigned code) {
    unsigned starts = f.hardware.starts;
    uint32_t generation = f.control.link.generation;
    assert(receive(m) == MT_CONTROL_OK); drain();
    assert(decode() == 1u && replies[0].type == 255u);
    assert(replies[0].payload[0] == m->type && replies[0].payload[1] == code);
    assert(f.hardware.starts == starts && f.control.link.generation == generation);
    f.used = 0;
}
static uint32_t immediate(void) {
    mt_wire_message m = command(MT_CMD_RECORD_NOW, 4);
    put32(m.payload, f.control.link.config_id);
    assert(receive(&m) == MT_CONTROL_OK);
    assert(f.control.hardware_running && f.control.link.active_window == 0u);
    assert(f.control.link.acquisition.retained_count == 0u);
    drain(); assert(decode() == 2u && replies[0].type == 132u && replies[1].type == 134u);
    assert(get32(replies[0].payload) == m.request);
    f.used = 0; return m.request;
}
static void preflight(void) {
    mt_wire_message m;
    setup();
    m = command(MT_CMD_RECORD_NOW, 4); expect_error(&m, 3);
    configure(MT_ACQ_SINGLE);
    for (unsigned len = 0; len < 6u; ++len) {
        if (len == 4u) continue;
        m = command(MT_CMD_RECORD_NOW, (uint16_t)len); expect_error(&m, 6);
    }
    m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 2); expect_error(&m, 3);
    for (unsigned pre = 512; pre <= 1024u; pre += 512u) {
        m = command(MT_CMD_CONFIG, 16); put16(m.payload, 1);
        m.payload[2] = MT_ACQ_SINGLE; put16(m.payload + 6, 2048); put16(m.payload + 8, (uint16_t)pre);
        assert(receive(&m) == MT_CONTROL_OK); drain(); f.used = 0;
        m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, f.control.link.config_id);
        expect_error(&m, 1);
        assert(f.control.link.acquisition.config.pretrigger_samples == pre);
    }
    setup(); configure(MT_ACQ_SINGLE); (void)immediate();
    m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 1); expect_error(&m, 2);
}
static void complete(unsigned step) {
    uint8_t raw[4096]; mt_wire_message m;
    unsigned begins = 0, ends = 0; size_t count = 0;
    setup(); configure(MT_ACQ_SINGLE); uint32_t generation = immediate();
    for (unsigned i = 0; i < 2048u; ++i) {
        put16(raw + 2u*i, (uint16_t)i);
        assert(mt_control_sample(&f.control, (uint16_t)i, i/50u) == MT_CONTROL_OK);
        if (!i) assert(f.control.link.active_window == 1u);
    }
    assert(!f.control.hardware_running); f.step = step; drain();
    size_t n = decode();
    for (size_t i = 0; i < n; ++i) {
        m = replies[i];
        if (m.type == 144u) {
            ++begins; assert(get16(m.payload+16) == 0u && get16(m.payload+18) == 0xffffu);
            assert(m.payload[20] == 3u && get32(m.payload+36) == 0u);
            assert(get16(m.payload+26) == MT_ACQ_QUALITY_RAIL_CODE);
        } else if (m.type == 145u) {
            unsigned codes = get16(m.payload+10); assert(get16(m.payload+8) == count);
            assert(memcmp(m.payload+12, raw+2u*count, 2u*codes) == 0); count += codes;
        } else if (m.type == 146u) {
            ++ends; assert(get32(m.payload+12) == mt_wire_crc32(raw, sizeof raw));
        }
    }
    assert(begins == 1u && ends == 1u && count == 2048u);
    f.used = 0; unsigned starts = f.hardware.starts;
    m = command(7, 12); put32(m.payload, generation); put32(m.payload+4, 1);
    put32(m.payload+8, mt_wire_crc32(raw, sizeof raw));
    assert(receive(&m) == MT_CONTROL_OK); drain();
    assert(!f.control.hardware_running && f.hardware.starts == starts && !f.control.link.armed);
    f.used = 0; (void)arm_capture();
    assert(mt_control_sample(&f.control, 100, 100) == MT_CONTROL_OK);
    assert(f.control.link.active_window == 0u); /* Software marker is not sticky. */
}
static int checked_start(void *context, uint32_t now) {
    unsigned ack = 0;
    for (unsigned i = 0; i < f.control.tx.count; ++i)
        if (f.control.tx.slots[i].message.type == 132u) ++ack;
    assert(ack == 1u && f.control.link.armed && f.control.link.generation != 0u);
    return start(context, now);
}
static void failures(void) {
    mt_wire_message m;
    setup(); configure(MT_ACQ_SINGLE); f.hardware.profile_ok = 0;
    m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_RECOVERY && f.hardware.starts == 0u);
    setup(); configure(MT_ACQ_SINGLE); f.control.io.start = checked_start;
    f.hardware.start_ok = 0;
    m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 1);
    unsigned stops = f.hardware.stops;
    assert(receive(&m) == MT_CONTROL_RECOVERY && f.hardware.starts == 1u);
    assert(f.hardware.stops > stops && !f.control.ready);
    setup(); configure(MT_ACQ_SINGLE); f.control.io.start = checked_start; (void)immediate();
    f.hardware.stop_ok = 0;
    m = command(MT_CMD_STOP, 4); put32(m.payload, f.control.link.generation);
    assert(receive(&m) == MT_CONTROL_RECOVERY && !f.control.ready);
    setup(); configure(MT_ACQ_SINGLE);
    /* Fill mandatory replies using valid requests and no writer progress. */
    for (unsigned i = 0; i < MT_TX_NORMAL_LIMIT; ++i) {
        m = command(MT_CMD_GET_CAPS, 0);
        assert(receive(&m) == MT_CONTROL_OK);
    }
    m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_RECOVERY && f.hardware.starts == 0u);
}
static void stop_before_sample(void) {
    setup(); configure(MT_ACQ_SINGLE);
    mt_wire_message m = command(MT_CMD_RECORD_NOW, 4); put32(m.payload, 1);
    assert(receive(&m) == MT_CONTROL_OK); uint32_t generation = m.request;
    m = command(MT_CMD_STOP, 4); put32(m.payload, generation);
    assert(receive(&m) == MT_CONTROL_OK); f.step = 1; drain();
    size_t n = decode(); unsigned acks = 0;
    assert(replies[0].type == 132u);
    for (size_t i=0; i<n; ++i) {
        assert(replies[i].type != 144u && replies[i].type != 145u && replies[i].type != 146u && replies[i].type != 147u);
        if (replies[i].type == 133u) ++acks;
    }
    assert(acks == 1u && !f.control.hardware_running && !f.control.link.dropped_windows);
}
int main(void) {
    assert(existing_control_main() == 0);
    preflight(); failures(); stop_before_sample(); complete(1); complete(7); complete(64);
    puts("record-now controller: preflight, fresh sample0, full arrays, release and Stop passed");
    return 0;
}
