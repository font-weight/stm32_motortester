#include "mt_wire.h"
#include <string.h>

static void put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
static uint16_t get16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}
static void put32(uint8_t *p, uint32_t v) {
    unsigned i; for (i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8u*i));
}
static uint32_t get32(const uint8_t *p) {
    uint32_t v = 0; unsigned i;
    for (i = 0; i < 4; ++i) v |= (uint32_t)p[i] << (8u*i);
    return v;
}
static void put64(uint8_t *p, uint64_t v) {
    unsigned i; for (i = 0; i < 8; ++i) p[i] = (uint8_t)(v >> (8u*i));
}
static uint64_t get64(const uint8_t *p) {
    uint64_t v = 0; unsigned i;
    for (i = 0; i < 8; ++i) v |= (uint64_t)p[i] << (8u*i);
    return v;
}
uint32_t mt_wire_crc32(const uint8_t *bytes, size_t length) {
    uint32_t crc = UINT32_C(0xffffffff); size_t i; unsigned bit;
    /* Contract: bytes points to length readable bytes; NULL only for length 0. */
    for (i = 0; i < length; ++i) {
        crc ^= bytes[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((crc & 1u) ? UINT32_C(0xedb88320) : 0u);
    }
    return crc ^ UINT32_C(0xffffffff);
}
mt_wire_result mt_wire_encode_revision(const mt_wire_message *m,
                                      uint8_t major, uint8_t minor,
                                      uint8_t *out, size_t capacity,
                                      size_t *written) {
    uint8_t raw[MT_WIRE_RAW_MAX];
    size_t n, read, write = 1, mark = 0;
    uint8_t code = 1;
    if (!m || !out || !written) return MT_WIRE_ARGUMENT;
    if (m->payload_length > MT_WIRE_PAYLOAD_MAX) return MT_WIRE_LENGTH;
    if (capacity < MT_WIRE_FRAME_MAX) return MT_WIRE_CAPACITY;
    raw[0] = major; raw[1] = minor; raw[2] = m->type; raw[3] = 0;
    put16(raw + 4, m->payload_length); put16(raw + 6, 0);
    put64(raw + 8, m->session); put32(raw + 16, m->sequence);
    put32(raw + 20, m->request);
    memcpy(raw + MT_WIRE_HEADER, m->payload, m->payload_length);
    n = MT_WIRE_HEADER + m->payload_length;
    put32(raw + n, mt_wire_crc32(raw, n)); n += 4;
    for (read = 0; read < n; ++read) {
        if (raw[read] == 0) {
            out[mark] = code; mark = write++; code = 1;
        } else {
            out[write++] = raw[read]; ++code;
            if (code == 255u) {
                out[mark] = code; mark = write++; code = 1;
            }
        }
    }
    out[mark] = code; out[write++] = 0; *written = write;
    return MT_WIRE_OK;
}
mt_wire_result mt_wire_decode_revision(const uint8_t *frame, size_t length,
                                      uint8_t major, uint8_t minor,
                                      mt_wire_message *m) {
    uint8_t raw[MT_WIRE_RAW_MAX];
    size_t read = 0, n = 0, count, i;
    uint8_t code; uint16_t payload_length;
    if (!frame || !m) return MT_WIRE_ARGUMENT;
    if (length == 0 || length >= MT_WIRE_FRAME_MAX) return MT_WIRE_LENGTH;
    while (read < length) {
        code = frame[read++];
        if (code == 0) return MT_WIRE_FRAMING;
        count = (size_t)code - 1u;
        if (count > length - read) return MT_WIRE_FRAMING;
        if (count > sizeof raw - n) return MT_WIRE_LENGTH;
        for (i = 0; i < count; ++i) {
            if (frame[read] == 0) return MT_WIRE_FRAMING;
            raw[n++] = frame[read++];
        }
        if (code != 255u && read < length) {
            if (n == sizeof raw) return MT_WIRE_LENGTH;
            raw[n++] = 0;
        }
    }
    if (n < MT_WIRE_HEADER + 4u) return MT_WIRE_LENGTH;
    payload_length = get16(raw + 4);
    if (payload_length > MT_WIRE_PAYLOAD_MAX ||
        n != MT_WIRE_HEADER + (size_t)payload_length + 4u) return MT_WIRE_LENGTH;
    if (mt_wire_crc32(raw, n - 4u) != get32(raw + n - 4u)) return MT_WIRE_CRC;
    if (raw[0] != major || raw[1] != minor) return MT_WIRE_VERSION;
    if (raw[3] != 0 || get16(raw + 6) != 0) return MT_WIRE_RESERVED;
    /* No fallible operation after this point; commit outputs only after validation. */
    m->type = raw[2]; m->session = get64(raw + 8);
    m->sequence = get32(raw + 16); m->request = get32(raw + 20);
    m->payload_length = payload_length;
    memcpy(m->payload, raw + MT_WIRE_HEADER, payload_length);
    return MT_WIRE_OK;
}

mt_wire_result mt_wire_encode(const mt_wire_message *m,
                             uint8_t *out, size_t capacity, size_t *written) {
    return mt_wire_encode_revision(m, MT_WIRE_MAJOR, MT_WIRE_MINOR,
                                   out, capacity, written);
}

mt_wire_result mt_wire_decode(const uint8_t *frame, size_t length,
                             mt_wire_message *m) {
    return mt_wire_decode_revision(frame, length, MT_WIRE_MAJOR, MT_WIRE_MINOR, m);
}
