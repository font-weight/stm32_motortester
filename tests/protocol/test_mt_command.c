/* Author-written, unexecuted regression sources. No independent PASS claim. */
#include "../../firmware/protocol/mt_command.h"
#include <assert.h>
#include <string.h>

static void set16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
}
static void set32(uint8_t *p, uint32_t value) {
    unsigned i;
    for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(value >> (8u * i));
}
static void init_message(mt_wire_message *m, uint8_t type, uint16_t length) {
    memset(m, 0, sizeof *m);
    m->type = type;
    m->payload_length = length;
}
static void expect_failure(const mt_wire_message *m, mt_command_result want) {
    mt_command out, saved;
    memset(&out, 0xa5, sizeof out);
    memcpy(&saved, &out, sizeof out);
    assert(mt_command_parse(m, &out) == want);
    assert(memcmp(&out, &saved, sizeof out) == 0);
}

static void test_lengths_and_direction(void) {
    static const uint16_t lengths[] = {0, 8, 0, 16, 4, 4, 0, 12, 4};
    static const uint8_t device_types[] = {
        129, 130, 131, 132, 133, 134, 135, 144, 145, 146, 147, 255
    };
    mt_wire_message m;
    mt_command out;
    unsigned type;
    size_t i;
    for (type = 1; type <= 8u; ++type) {
        init_message(&m, (uint8_t)type, (uint16_t)(lengths[type] + 1u));
        expect_failure(&m, MT_COMMAND_LENGTH);
        if (lengths[type] != 0u) {
            m.payload_length = (uint16_t)(lengths[type] - 1u);
            expect_failure(&m, MT_COMMAND_LENGTH);
        }
        m.payload_length = lengths[type];
        if (type == MT_CMD_HELLO) m.payload[0] = 1;
        assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
        assert(out.type == type);
    }
    for (i = 0; i < sizeof device_types / sizeof device_types[0]; ++i) {
        init_message(&m, device_types[i], 0);
        expect_failure(&m, MT_COMMAND_WRONG_DIRECTION);
    }
    init_message(&m, 127, 0);
    expect_failure(&m, MT_COMMAND_UNKNOWN_TYPE);
    init_message(&m, 0, 0);
    expect_failure(&m, MT_COMMAND_UNKNOWN_TYPE);
    init_message(&m, MT_CMD_CONFIG, UINT16_MAX);
    expect_failure(&m, MT_COMMAND_LENGTH);
}

static void test_payloads(void) {
    mt_wire_message m;
    mt_command out;
    unsigned i;
    init_message(&m, MT_CMD_HELLO, 8);
    expect_failure(&m, MT_COMMAND_INVALID_HELLO);
    for (i = 0; i < 8u; ++i) m.payload[i] = (uint8_t)(i + 1u);
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.nonce == UINT64_C(0x0807060504030201));

    init_message(&m, MT_CMD_CONFIG, 16);
    set16(m.payload, 1);
    m.payload[2] = 0; m.payload[3] = 1; m.payload[4] = 1;
    set16(m.payload + 6, 2048); set16(m.payload + 8, 512);
    set32(m.payload + 12, 1000);
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.config.profile == 1u);
    assert(out.value.config.mode == 0u && out.value.config.source == 1u);
    assert(out.value.config.edge == 1u && out.value.config.level_code == 2048u);
    assert(out.value.config.pretrigger_samples == 512u);
    assert(out.value.config.auto_timeout_ms == 1000u);
    m.payload[5] = 1;
    expect_failure(&m, MT_COMMAND_RESERVED);
    m.payload[5] = 0; m.payload[10] = 1;
    expect_failure(&m, MT_COMMAND_RESERVED);
    m.payload[10] = 0; m.payload[11] = 1;
    expect_failure(&m, MT_COMMAND_RESERVED);

    init_message(&m, MT_CMD_ARM, 4);
    set32(m.payload, UINT32_C(0x78563412));
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.config_id == UINT32_C(0x78563412));
    init_message(&m, MT_CMD_RECORD_NOW, 4);
    set32(m.payload, UINT32_C(0x78563412));
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.config_id == UINT32_C(0x78563412));
    init_message(&m, MT_CMD_STOP, 4);
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.generation == 0u); /* STOP(0) remains state-dependent. */

    init_message(&m, MT_CMD_RELEASE_WINDOW, 12);
    set32(m.payload, 2); set32(m.payload + 4, 3);
    set32(m.payload + 8, UINT32_C(0xfedcba98));
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    assert(out.value.release.generation == 2u);
    assert(out.value.release.window_id == 3u);
    assert(out.value.release.samples_crc32 == UINT32_C(0xfedcba98));
}

static void test_config_semantics(void) {
    const mt_command_support all = {7, 3, 3, 1};
    mt_command_support support;
    mt_config c = {1, 0, 0, 0, 2048, 0, 1000};
    unsigned mode, source, edge;
    static const uint16_t pres[] = {0, 512, 1024};
    size_t i;
    for (mode = 0; mode <= 2u; ++mode)
        for (source = 0; source <= 1u; ++source)
            for (edge = 0; edge <= 1u; ++edge)
                for (i = 0; i < sizeof pres / sizeof pres[0]; ++i) {
                    c.mode = (uint8_t)mode; c.source = (uint8_t)source;
                    c.edge = (uint8_t)edge; c.pretrigger_samples = pres[i];
                    c.auto_timeout_ms = mode == 0u ? 1000u : 0u;
                    assert(mt_command_config_valid(&c, &all));
                }
    c.mode = 0; c.source = 0; c.edge = 0; c.pretrigger_samples = 0;
    c.auto_timeout_ms = 99;
    assert(!mt_command_config_valid(&c, &all));
    c.auto_timeout_ms = 100;
    assert(mt_command_config_valid(&c, &all));
    c.auto_timeout_ms = 60000;
    assert(mt_command_config_valid(&c, &all));
    c.auto_timeout_ms = 60001;
    assert(!mt_command_config_valid(&c, &all));
    c.mode = 1; c.auto_timeout_ms = 1;
    assert(!mt_command_config_valid(&c, &all));
    c.auto_timeout_ms = 0;
    c.level_code = 0;
    assert(!mt_command_config_valid(&c, &all));
    c.level_code = 1;
    assert(mt_command_config_valid(&c, &all));
    c.level_code = 4094;
    assert(mt_command_config_valid(&c, &all));
    c.level_code = 4095;
    assert(!mt_command_config_valid(&c, &all));
    c.level_code = 2048;
    c.pretrigger_samples = 1;
    assert(!mt_command_config_valid(&c, &all));
    c.pretrigger_samples = 512;
    support = all; support.pretrigger = 0;
    assert(!mt_command_config_valid(&c, &support));
    c.pretrigger_samples = 0;
    assert(mt_command_config_valid(&c, &support));
    support = all; support.mode_bits = 1;
    assert(!mt_command_config_valid(&c, &support));
    support = all; support.source_bits = 2;
    assert(!mt_command_config_valid(&c, &support));
    support = all; support.edge_bits = 2;
    assert(!mt_command_config_valid(&c, &support));
    support = all; support.mode_bits = 128;
    assert(!mt_command_config_valid(&c, &support));
    support = all; support.pretrigger = 2;
    assert(!mt_command_config_valid(&c, &support));
    c.profile = 2;
    assert(!mt_command_config_valid(&c, &all));
    c.profile = 1; c.mode = 255;
    assert(!mt_command_config_valid(&c, &all));
    c.mode = 1; c.source = 255;
    assert(!mt_command_config_valid(&c, &all));
    c.source = 0; c.edge = 255;
    assert(!mt_command_config_valid(&c, &all));
    assert(!mt_command_config_valid(NULL, &all));
    assert(!mt_command_config_valid(&c, NULL));
}

int main(void) {
    mt_wire_message m;
    mt_command out;
    init_message(&m, MT_CMD_GET_CAPS, 0);
    assert(mt_command_parse(&m, NULL) == MT_COMMAND_ARGUMENT);
    expect_failure(NULL, MT_COMMAND_ARGUMENT);
    assert(mt_command_parse(&m, &out) == MT_COMMAND_OK);
    test_lengths_and_direction();
    test_payloads();
    test_config_semantics();
    return 0;
}
