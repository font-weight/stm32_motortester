#include "mt_command.h"

static uint16_t read16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}
static uint32_t read32(const uint8_t *p) {
    uint32_t v = 0;
    unsigned i;
    for (i = 0; i < 4u; ++i) v |= (uint32_t)p[i] << (8u * i);
    return v;
}
static uint64_t read64(const uint8_t *p) {
    uint64_t v = 0;
    unsigned i;
    for (i = 0; i < 8u; ++i) v |= (uint64_t)p[i] << (8u * i);
    return v;
}
static int device_type(uint8_t type) {
    switch (type) {
    case 129: case 130: case 131: case 132: case 133: case 134: case 135:
    case 144: case 145: case 146: case 147: case 255:
        return 1;
    default:
        return 0;
    }
}

mt_command_result mt_command_parse(const mt_wire_message *m, mt_command *out) {
    mt_command candidate = {0};
    const uint8_t *p;
    size_t expected;
    if (!m || !out) return MT_COMMAND_ARGUMENT;
    if (device_type(m->type)) return MT_COMMAND_WRONG_DIRECTION;
    switch (m->type) {
    case MT_CMD_HELLO: expected = 8; break;
    case MT_CMD_GET_CAPS: case MT_CMD_GET_STATUS: expected = 0; break;
    case MT_CMD_CONFIG: expected = 16; break;
    case MT_CMD_ARM: case MT_CMD_STOP: expected = 4; break;
    case MT_CMD_RELEASE_WINDOW: expected = 12; break;
    default: return MT_COMMAND_UNKNOWN_TYPE;
    }
    if (m->payload_length != expected) return MT_COMMAND_LENGTH;
    p = m->payload;
    candidate.type = m->type;
    switch (m->type) {
    case MT_CMD_HELLO:
        candidate.value.nonce = read64(p);
        if (candidate.value.nonce == 0u) return MT_COMMAND_INVALID_HELLO;
        break;
    case MT_CMD_CONFIG:
        if (p[5] != 0u || read16(p + 10) != 0u) return MT_COMMAND_RESERVED;
        candidate.value.config.profile = read16(p);
        candidate.value.config.mode = p[2];
        candidate.value.config.source = p[3];
        candidate.value.config.edge = p[4];
        candidate.value.config.level_code = read16(p + 6);
        candidate.value.config.pretrigger_samples = read16(p + 8);
        candidate.value.config.auto_timeout_ms = read32(p + 12);
        break;
    case MT_CMD_ARM:
        candidate.value.config_id = read32(p);
        break;
    case MT_CMD_STOP:
        candidate.value.generation = read32(p);
        break;
    case MT_CMD_RELEASE_WINDOW:
        candidate.value.release.generation = read32(p);
        candidate.value.release.window_id = read32(p + 4);
        candidate.value.release.samples_crc32 = read32(p + 8);
        break;
    default: /* GET_CAPS / GET_STATUS have no payload. */
        break;
    }
    *out = candidate;
    return MT_COMMAND_OK;
}

int mt_command_config_valid(const mt_config *c, const mt_command_support *s) {
    if (!c || !s) return 0;
    if ((s->mode_bits & ~7u) != 0u ||
        (s->source_bits & ~3u) != 0u ||
        (s->edge_bits & ~3u) != 0u || s->pretrigger > 1u) return 0;
    if (c->profile != 1u || c->mode > 2u || c->source > 1u ||
        c->edge > 1u || c->level_code < 1u || c->level_code > 4094u)
        return 0;
    /* Range checks precede shifts, including for malformed input values. */
    if ((s->mode_bits & (1u << c->mode)) == 0u ||
        (s->source_bits & (1u << c->source)) == 0u ||
        (s->edge_bits & (1u << c->edge)) == 0u) return 0;
    if (c->pretrigger_samples != 0u &&
        (!s->pretrigger || (c->pretrigger_samples != 512u &&
                           c->pretrigger_samples != 1024u))) return 0;
    if (c->mode == 0u)
        return c->auto_timeout_ms >= 100u && c->auto_timeout_ms <= 60000u;
    return c->auto_timeout_ms == 0u;
}
