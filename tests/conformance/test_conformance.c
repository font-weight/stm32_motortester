/* Independent protocol conformance: expected bytes originate in the contract
 * and Python zlib fixtures, never in a production encode/decode round trip.
 * Tests are deliberately active even if a caller defines NDEBUG. */
#include "mt_wire.h"
#include "mt_stream.h"
#include "mt_command.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vectors.h"

static uint64_t checks;
static const char *phase = "startup";
#define CHECK(expr) do { ++checks; if (!(expr)) { \
    fprintf(stderr, "FAIL [%s] %s:%d: %s\n", phase, __FILE__, __LINE__, #expr); \
    exit(1); } } while (0)
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))

typedef struct { unsigned char pre[16]; mt_wire_message value; unsigned char post[16]; } message_guard;
typedef struct { unsigned char pre[16]; mt_command value; unsigned char post[16]; } command_guard;
typedef struct { unsigned char pre[16]; mt_stream value; unsigned char post[16]; } stream_guard;

static int filled(const void *data, size_t n, unsigned char byte) {
    const unsigned char *p = data;
    size_t i;
    for (i = 0; i < n; ++i) if (p[i] != byte) return 0;
    return 1;
}
static void message_canaries(const message_guard *m) {
    CHECK(filled(m->pre, sizeof m->pre, 0xa5));
    CHECK(filled(m->post, sizeof m->post, 0xa5));
}
static uint32_t random_state = UINT32_C(0x6d747031);
static uint32_t random32(void) {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}
static uint32_t reflect32(uint32_t x) {
    uint32_t result = 0;
    unsigned i;
    for (i = 0; i < 32; ++i) { result = (result << 1) | (x & 1u); x >>= 1; }
    return result;
}
/* MSB-first polynomial and reflected input/output: intentionally different
 * from production's reflected, LSB-first byte loop. zlib anchors test it. */
static uint32_t reference_crc(const uint8_t *data, size_t n) {
    uint32_t crc = UINT32_MAX;
    size_t i;
    unsigned bit;
    for (i = 0; i < n; ++i) {
        crc ^= reflect32(data[i]);
        for (bit = 0; bit < 8; ++bit)
            crc = (crc << 1) ^ ((crc & UINT32_C(0x80000000)) ? UINT32_C(0x04c11db7) : 0);
    }
    return reflect32(crc) ^ UINT32_MAX;
}
static void put(uint8_t *dest, uint64_t value, size_t n) {
    size_t i;
    for (i = 0; i < n; ++i) { dest[i] = (uint8_t)value; value >>= 8; }
}
static uint64_t get(const uint8_t *src, size_t n) {
    uint64_t value = 0;
    while (n != 0u) value = (value << 8) | src[--n];
    return value;
}
static size_t reference_cobs(const uint8_t *raw, size_t n, uint8_t *encoded) {
    size_t start = 0, end, output = 0, run;
    for (;;) {
        end = start;
        while (end < n && raw[end] != 0u) ++end;
        run = end - start;
        while (run >= 254u) {
            encoded[output++] = 255;
            memcpy(encoded + output, raw + start, 254);
            output += 254; start += 254; run -= 254;
        }
        encoded[output++] = (uint8_t)(run + 1u);
        memcpy(encoded + output, raw + start, run); output += run;
        if (end == n) return output;
        start = end + 1u;
    }
}
static void repair_crc(uint8_t *raw, size_t n) {
    put(raw + n - 4u, reference_crc(raw, n - 4u), 4);
}
static size_t reference_raw(const mt_wire_message *m, uint8_t *raw) {
    size_t n = 28u + m->payload_length;
    memset(raw, 0, 24);
    raw[1] = 1; raw[2] = m->type;
    put(raw + 4, m->payload_length, 2); put(raw + 8, m->session, 8);
    put(raw + 16, m->sequence, 4); put(raw + 20, m->request, 4);
    memcpy(raw + 24, m->payload, m->payload_length);
    repair_crc(raw, n);
    return n;
}
static void message_matches(const mt_wire_message *m, const golden_vector *g) {
    CHECK(m->type == g->type); CHECK(m->session == g->session);
    CHECK(m->sequence == g->sequence); CHECK(m->request == g->request);
    CHECK(m->payload_length == g->payload_n);
    CHECK(memcmp(m->payload, g->payload, g->payload_n) == 0);
}
static mt_wire_message golden_message(const golden_vector *g) {
    mt_wire_message m = {0};
    m.type = g->type; m.session = g->session; m.sequence = g->sequence;
    m.request = g->request; m.payload_length = (uint16_t)g->payload_n;
    memcpy(m.payload, g->payload, g->payload_n);
    return m;
}
static void reject_frame(const uint8_t *frame, size_t n, mt_wire_result expected) {
    message_guard m, saved;
    memset(&m, 0xa5, sizeof m); saved = m;
    CHECK(mt_wire_decode(frame, n, &m.value) == expected);
    CHECK(memcmp(&m, &saved, sizeof m) == 0);
}
static void reject_raw(const uint8_t *raw, size_t n, mt_wire_result expected) {
    uint8_t encoded[600];
    size_t encoded_n = reference_cobs(raw, n, encoded);
    reject_frame(encoded, encoded_n, expected);
}
static void test_crc_and_goldens(void) {
    uint8_t data[541], reference[600];
    size_t i, j;
    phase = "CRC anchors and independent wire vectors";
    CHECK(MT_WIRE_HEADER == 24u); CHECK(MT_WIRE_PAYLOAD_MAX == 512u);
    CHECK(MT_WIRE_RAW_MAX == 540u); CHECK(MT_WIRE_FRAME_MAX == 544u);
    CHECK(mt_wire_crc32(NULL, 0) == 0);
    CHECK(mt_wire_crc32((const uint8_t *)"123456789", 9) == UINT32_C(0xcbf43926));
    for (i = 0; i < sizeof data; ++i) data[i] = (uint8_t)(i * 73u + 19u);
    for (i = 0; i < COUNT(prefix_crc); ++i) {
        CHECK(reference_crc(data, i) == prefix_crc[i]);
        CHECK(mt_wire_crc32(data, i) == prefix_crc[i]);
    }
    /* Literal COBS anchors, including empty and a trailing raw zero. */
    CHECK(reference_cobs(data, 0, reference) == 1u && reference[0] == 1u);
    { const uint8_t raw[] = {0x11,0x22,0,0x33,0};
      const uint8_t expected[] = {3,0x11,0x22,2,0x33,1};
      CHECK(reference_cobs(raw, sizeof raw, reference) == sizeof expected);
      CHECK(memcmp(reference, expected, sizeof expected) == 0); }
    for (i = 0; i < COUNT(golden); ++i) {
        const golden_vector *g = &golden[i];
        message_guard decoded;
        mt_wire_message input = golden_message(g), saved = input;
        struct { uint8_t pre[16], output[544], post[16]; } encoded;
        size_t written = 123456u, raw_n;
        memset(&decoded, 0xa5, sizeof decoded); memset(&encoded, 0xa5, sizeof encoded);
        raw_n = reference_raw(&input, reference);
        CHECK(raw_n == g->raw_n && memcmp(reference, g->raw, raw_n) == 0);
        CHECK(reference_cobs(g->raw, g->raw_n, reference) == g->frame_n - 1u);
        CHECK(memcmp(reference, g->frame, g->frame_n - 1u) == 0);
        CHECK(mt_wire_encode(&input, encoded.output, sizeof encoded.output, &written) == MT_WIRE_OK);
        CHECK(written == g->frame_n && memcmp(encoded.output, g->frame, written) == 0);
        CHECK(memcmp(&input, &saved, sizeof input) == 0);
        CHECK(filled(encoded.pre, sizeof encoded.pre, 0xa5));
        CHECK(filled(encoded.post, sizeof encoded.post, 0xa5));
        CHECK(encoded.output[written - 1u] == 0u);
        for (j = 0; j + 1u < written; ++j) CHECK(encoded.output[j] != 0u);
        CHECK(mt_wire_decode(g->frame, g->frame_n - 1u, &decoded.value) == MT_WIRE_OK);
        message_matches(&decoded.value, g); message_canaries(&decoded);
    }
}

static void test_wire_errors(void) {
    uint8_t raw[550], encoded[600], output[560], saved_output[560];
    mt_wire_message input = golden_message(&golden[3]), saved_input;
    message_guard decoded, saved_decoded;
    size_t i, j, n, written, saved_written;
    const golden_vector *g = &golden[2];
    phase = "wire errors, precedence and output atomicity";
    memset(output, 0x6d, sizeof output); memcpy(saved_output, output, sizeof output);
    saved_input = input;
    for (i = 0; i < 544u; ++i) {
        written = 0x9876u;
        CHECK(mt_wire_encode(&input, output, i, &written) == MT_WIRE_CAPACITY);
        CHECK(written == 0x9876u && memcmp(output, saved_output, sizeof output) == 0);
        CHECK(memcmp(&input, &saved_input, sizeof input) == 0);
    }
    input.payload_length = 513; written = 0x9876u;
    CHECK(mt_wire_encode(&input, output, sizeof output, &written) == MT_WIRE_LENGTH);
    CHECK(written == 0x9876u && memcmp(output, saved_output, sizeof output) == 0);
    input.payload_length = UINT16_MAX;
    CHECK(mt_wire_encode(&input, output, sizeof output, &written) == MT_WIRE_LENGTH);
    input = saved_input; saved_written = written;
    CHECK(mt_wire_encode(NULL, output, sizeof output, &written) == MT_WIRE_ARGUMENT);
    CHECK(mt_wire_encode(&input, NULL, sizeof output, &written) == MT_WIRE_ARGUMENT);
    CHECK(mt_wire_encode(&input, output, sizeof output, NULL) == MT_WIRE_ARGUMENT);
    CHECK(written == saved_written && memcmp(output, saved_output, sizeof output) == 0);
    CHECK(mt_wire_decode(g->frame, g->frame_n - 1u, NULL) == MT_WIRE_ARGUMENT);
    reject_frame(NULL, 0, MT_WIRE_ARGUMENT);
    reject_frame(g->frame, 0, MT_WIRE_LENGTH);
    memset(encoded, 1, sizeof encoded);
    reject_frame(encoded, 544, MT_WIRE_LENGTH);
    reject_frame(encoded, 550, MT_WIRE_LENGTH);
    { const uint8_t a[] = {0}, b[] = {2}, c[] = {2,0}, d[] = {255,1,2};
      reject_frame(a, sizeof a, MT_WIRE_FRAMING);
      reject_frame(b, sizeof b, MT_WIRE_FRAMING);
      reject_frame(c, sizeof c, MT_WIRE_FRAMING);
      reject_frame(d, sizeof d, MT_WIRE_FRAMING); }
    memset(raw, 0, sizeof raw);
    for (i = 0; i < 28u; ++i) reject_raw(raw, i, MT_WIRE_LENGTH);
    reject_raw(raw, 541, MT_WIRE_LENGTH);
    /* Every truncation must reject without touching the previous output. */
    for (i = 0; i + 1u < g->frame_n; ++i) {
        memset(&decoded, 0xa5, sizeof decoded); saved_decoded = decoded;
        CHECK(mt_wire_decode(g->frame, i, &decoded.value) != MT_WIRE_OK);
        CHECK(memcmp(&decoded, &saved_decoded, sizeof decoded) == 0);
    }
    reject_frame(g->frame, g->frame_n, MT_WIRE_FRAMING); /* delimiter forbidden */
    memcpy(raw, g->raw, g->raw_n);
    raw[4] = 17; repair_crc(raw, g->raw_n); reject_raw(raw, g->raw_n, MT_WIRE_LENGTH);
    raw[4] = 1; raw[5] = 2; repair_crc(raw, g->raw_n); reject_raw(raw, g->raw_n, MT_WIRE_LENGTH);
    /* CRC must precede version/reserved; version must precede reserved. */
    for (i = 0; i < 2u; ++i) {
        for (j = 0; j < 256u; ++j) {
            if (j == (i == 0 ? 0u : 1u)) continue;
            memcpy(raw, g->raw, g->raw_n); raw[i] = (uint8_t)j;
            reject_raw(raw, g->raw_n, MT_WIRE_CRC);
            repair_crc(raw, g->raw_n); reject_raw(raw, g->raw_n, MT_WIRE_VERSION);
        }
    }
    { const size_t reserved[] = {3,6,7};
      for (i = 0; i < COUNT(reserved); ++i) for (j = 1; j <= 255u; ++j) {
          memcpy(raw, g->raw, g->raw_n); raw[reserved[i]] = (uint8_t)j;
          reject_raw(raw, g->raw_n, MT_WIRE_CRC);
          repair_crc(raw, g->raw_n); reject_raw(raw, g->raw_n, MT_WIRE_RESERVED);
      } }
    memcpy(raw, g->raw, g->raw_n); raw[0] = 1; raw[3] = 1;
    repair_crc(raw, g->raw_n); reject_raw(raw, g->raw_n, MT_WIRE_VERSION);
    for (i = 0; i < g->raw_n; ++i) for (j = 0; j < 8u; ++j) {
        memcpy(raw, g->raw, g->raw_n); raw[i] ^= (uint8_t)(1u << j);
        reject_raw(raw, g->raw_n, i == 4u || i == 5u ? MT_WIRE_LENGTH : MT_WIRE_CRC);
    }
    /* Deterministic malformed-input robustness; success is not assumed impossible. */
    for (i = 0; i < 10000u; ++i) {
        mt_wire_result result;
        n = random32() % 550u;
        for (j = 0; j < n; ++j) encoded[j] = (uint8_t)random32();
        memset(&decoded, 0xa5, sizeof decoded); saved_decoded = decoded;
        result = mt_wire_decode(encoded, n, &decoded.value);
        message_canaries(&decoded);
        if (result != MT_WIRE_OK) CHECK(memcmp(&decoded, &saved_decoded, sizeof decoded) == 0);
        else CHECK(decoded.value.payload_length <= 512u);
    }
}

static void test_random_wire(void) {
    uint8_t raw[540], reference[544], encoded[544];
    size_t i, j, raw_n, expected_n, written;
    phase = "deterministic wire differential cases";
    for (i = 0; i < 4096u; ++i) {
        mt_wire_message input = {0};
        message_guard decoded;
        input.type = (uint8_t)random32(); input.session = (uint64_t)random32() << 32;
        input.session |= random32(); input.sequence = random32(); input.request = random32();
        input.payload_length = (uint16_t)(i < 513u ? i : random32() % 513u);
        for (j = 0; j < input.payload_length; ++j) input.payload[j] = (uint8_t)random32();
        raw_n = reference_raw(&input, raw);
        expected_n = reference_cobs(raw, raw_n, reference); reference[expected_n++] = 0;
        written = 0;
        CHECK(mt_wire_encode(&input, encoded, sizeof encoded, &written) == MT_WIRE_OK);
        CHECK(written == expected_n && memcmp(encoded, reference, written) == 0);
        memset(&decoded, 0xa5, sizeof decoded);
        CHECK(mt_wire_decode(reference, expected_n - 1u, &decoded.value) == MT_WIRE_OK);
        CHECK(input.type == decoded.value.type && input.session == decoded.value.session);
        CHECK(input.sequence == decoded.value.sequence && input.request == decoded.value.request);
        CHECK(input.payload_length == decoded.value.payload_length);
        CHECK(memcmp(input.payload, decoded.value.payload, input.payload_length) == 0);
        message_canaries(&decoded);
    }
}

static mt_stream_event feed_checked(stream_guard *stream, uint8_t byte,
                                    uint32_t now, message_guard *message) {
    message_guard previous;
    mt_stream_event result;
    memcpy(&previous, message, sizeof previous);
    result = mt_stream_feed(&stream->value, byte, now, &message->value);
    CHECK(filled(stream->pre, sizeof stream->pre, 0xa5));
    CHECK(filled(stream->post, sizeof stream->post, 0xa5));
    message_canaries(message);
    if (result != MT_STREAM_MESSAGE) CHECK(memcmp(message, &previous, sizeof previous) == 0);
    return result;
}
static void init_stream(stream_guard *stream, message_guard *message) {
    memset(stream, 0xa5, sizeof *stream); mt_stream_init(&stream->value);
    memset(message, 0xa5, sizeof *message);
}
static void feed_golden(stream_guard *stream, message_guard *message,
                        const golden_vector *g, uint32_t now) {
    size_t i;
    for (i = 0; i < g->frame_n; ++i)
        CHECK(feed_checked(stream, g->frame[i], now, message) ==
              (i + 1u == g->frame_n ? MT_STREAM_MESSAGE : MT_STREAM_NONE));
    CHECK(stream->value.last_decode == MT_WIRE_OK);
    message_matches(&message->value, g);
}
static void test_stream_splits(void) {
    stream_guard stream;
    message_guard message;
    const golden_vector *g = &golden[COUNT(golden) - 1u];
    const size_t chunks[] = {1,2,3,7,63,64,65,127,255,544};
    size_t split, i, j, at, next;
    uint32_t now;
    phase = "stream fragmentation, coalescing and resync";
    CHECK(MT_STREAM_INTERBYTE_MS == 1000u && MT_STREAM_FRAME_MS == 3000u);
    for (split = 0; split <= g->frame_n; ++split) {
        init_stream(&stream, &message);
        for (i = 0; i < g->frame_n; ++i)
            CHECK(feed_checked(&stream, g->frame[i], i < split ? 100u : 101u, &message) ==
                  (i + 1u == g->frame_n ? MT_STREAM_MESSAGE : MT_STREAM_NONE));
        message_matches(&message.value, g);
    }
    for (j = 0; j < COUNT(chunks); ++j) {
        init_stream(&stream, &message); now = 100;
        for (i = 0; i < g->frame_n; ++i) {
            if (i % chunks[j] == 0u) ++now;
            CHECK(feed_checked(&stream, g->frame[i], now, &message) ==
                  (i + 1u == g->frame_n ? MT_STREAM_MESSAGE : MT_STREAM_NONE));
        }
        message_matches(&message.value, g);
    }
    /* All seven commands coalesced, no parser reset and identical timestamps. */
    init_stream(&stream, &message);
    for (j = 0; j < 7u; ++j) feed_golden(&stream, &message, &golden[j], 500u);
    for (i = 0; i < 20u; ++i) CHECK(feed_checked(&stream, 0, 501u, &message) == MT_STREAM_NONE);
    for (j = 0; j < 200u; ++j) {
        g = &golden[random32() % COUNT(golden)];
        init_stream(&stream, &message); now = UINT32_MAX - 100u; at = 0;
        while (at < g->frame_n) {
            next = at + 1u + random32() % 97u;
            if (next > g->frame_n) next = g->frame_n;
            now += 1u + random32() % 7u;
            while (at < next) {
                CHECK(feed_checked(&stream, g->frame[at], now, &message) ==
                      (at + 1u == g->frame_n ? MT_STREAM_MESSAGE : MT_STREAM_NONE));
                ++at;
            }
        }
        message_matches(&message.value, g);
    }
    /* Rejected frames recover at their own delimiter, without an extra delimiter. */
    init_stream(&stream, &message);
    CHECK(feed_checked(&stream, 2, 1, &message) == MT_STREAM_NONE);
    CHECK(feed_checked(&stream, 0, 2, &message) == MT_STREAM_REJECTED);
    CHECK(stream.value.last_decode == MT_WIRE_FRAMING);
    feed_golden(&stream, &message, &golden[0], 3);
    { uint8_t raw[540], bytes[544]; size_t n;
      memcpy(raw, golden[2].raw, golden[2].raw_n); raw[24] ^= 1;
      n = reference_cobs(raw, golden[2].raw_n, bytes);
      for (i = 0; i < n; ++i) CHECK(feed_checked(&stream, bytes[i], 4, &message) == MT_STREAM_NONE);
      CHECK(feed_checked(&stream, 0, 4, &message) == MT_STREAM_REJECTED);
      CHECK(stream.value.last_decode == MT_WIRE_CRC);
      feed_golden(&stream, &message, &golden[6], 5); }
}

static void test_stream_deadlines(void) {
    stream_guard stream, previous;
    message_guard message;
    const golden_vector *g = &golden[2];
    uint32_t start;
    size_t i, rollover;
    phase = "stream exact deadlines, clock wrap and overflow";
    for (rollover = 0; rollover < 2u; ++rollover) {
        start = rollover == 0u ? 100u : UINT32_MAX - 500u;
        init_stream(&stream, &message);
        CHECK(feed_checked(&stream, g->frame[0], start, &message) == MT_STREAM_NONE);
        CHECK(mt_stream_poll(&stream.value, start + 999u) == MT_STREAM_NONE);
        CHECK(mt_stream_poll(&stream.value, start + 1000u) == MT_STREAM_TIMEOUT);
        CHECK(mt_stream_poll(&stream.value, start + 1001u) == MT_STREAM_NONE);
        /* A whole valid frame is ignored while discarding; its zero resynchronizes. */
        for (i = 0; i < g->frame_n; ++i)
            CHECK(feed_checked(&stream, g->frame[i], start + 1002u, &message) == MT_STREAM_NONE);
        feed_golden(&stream, &message, g, start + 1003u);
        /* Just below interbyte deadline is valid; exact-deadline delimiter times out. */
        init_stream(&stream, &message);
        for (i = 0; i + 1u < g->frame_n; ++i)
            CHECK(feed_checked(&stream, g->frame[i], start, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 0, start + 999u, &message) == MT_STREAM_MESSAGE);
        message_matches(&message.value, g);
        init_stream(&stream, &message);
        for (i = 0; i + 1u < g->frame_n; ++i)
            CHECK(feed_checked(&stream, g->frame[i], start, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 0, start + 1000u, &message) == MT_STREAM_TIMEOUT);
        feed_golden(&stream, &message, g, start + 1001u);
        /* Feed-caused timeout consumes its nonzero byte, then discards until zero. */
        init_stream(&stream, &message);
        CHECK(feed_checked(&stream, 1, start, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 1, start + 1000u, &message) == MT_STREAM_TIMEOUT);
        CHECK(feed_checked(&stream, 2, start + 1001u, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 0, start + 1002u, &message) == MT_STREAM_NONE);
        feed_golden(&stream, &message, g, start + 1003u);
        /* Total deadline: interbyte gaps remain strictly below 1000 ms. */
        init_stream(&stream, &message);
        CHECK(feed_checked(&stream, 1, start, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 1, start + 900u, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 1, start + 1800u, &message) == MT_STREAM_NONE);
        CHECK(feed_checked(&stream, 1, start + 2700u, &message) == MT_STREAM_NONE);
        CHECK(mt_stream_poll(&stream.value, start + 2999u) == MT_STREAM_NONE);
        CHECK(mt_stream_poll(&stream.value, start + 3000u) == MT_STREAM_TIMEOUT);
        CHECK(feed_checked(&stream, 0, start + 3001u, &message) == MT_STREAM_NONE);
        feed_golden(&stream, &message, g, start + 3002u);
        /* A real valid frame finishing at 2999 succeeds; at 3000 it must not. */
        for (i = 0; i < 2u; ++i) {
            size_t k;
            init_stream(&stream, &message);
            for (k = 0; k + 1u < g->frame_n; ++k) {
                uint32_t t = k == 0u ? 0u : k == 1u ? 900u : k == 2u ? 1800u : 2700u;
                CHECK(feed_checked(&stream, g->frame[k], start + t, &message) == MT_STREAM_NONE);
            }
            CHECK(feed_checked(&stream, 0, start + 2999u + (uint32_t)i, &message) ==
                  (i == 0u ? MT_STREAM_MESSAGE : MT_STREAM_TIMEOUT));
            feed_golden(&stream, &message, g, start + 3001u);
        }
    }
    init_stream(&stream, &message);
    for (i = 0; i < 543u; ++i) CHECK(feed_checked(&stream, 1, 5, &message) == MT_STREAM_NONE);
    CHECK(feed_checked(&stream, 0, 6, &message) == MT_STREAM_REJECTED);
    CHECK(stream.value.last_decode == MT_WIRE_LENGTH);
    feed_golden(&stream, &message, g, 7);
    for (i = 0; i < 543u; ++i) CHECK(feed_checked(&stream, 1, 8, &message) == MT_STREAM_NONE);
    CHECK(feed_checked(&stream, 1, 8, &message) == MT_STREAM_OVERFLOW);
    for (i = 0; i < 2000u; ++i) CHECK(feed_checked(&stream, 1, 9, &message) == MT_STREAM_NONE);
    CHECK(mt_stream_poll(&stream.value, 5000) == MT_STREAM_NONE);
    CHECK(feed_checked(&stream, 0, 5001, &message) == MT_STREAM_NONE);
    feed_golden(&stream, &message, g, 5002);
    /* Null calls must not poll, consume, or otherwise change a pending candidate. */
    CHECK(feed_checked(&stream, g->frame[0], 6000, &message) == MT_STREAM_NONE);
    memcpy(&previous, &stream, sizeof previous);
    CHECK(mt_stream_feed(&stream.value, 0, 8000, NULL) == MT_STREAM_ARGUMENT);
    CHECK(memcmp(&previous, &stream, sizeof previous) == 0);
    CHECK(mt_stream_feed(NULL, 0, 8000, &message.value) == MT_STREAM_ARGUMENT);
    CHECK(mt_stream_poll(NULL, 8000) == MT_STREAM_ARGUMENT);
    mt_stream_init(NULL);
    mt_stream_init(&stream.value); /* explicit reset drops pending bytes */
    feed_golden(&stream, &message, g, 8001);
    CHECK(mt_stream_poll(&stream.value, 100000u) == MT_STREAM_NONE);
}

static void command_canaries(const command_guard *command) {
    CHECK(filled(command->pre, sizeof command->pre, 0xa5));
    CHECK(filled(command->post, sizeof command->post, 0xa5));
}
static void command_expected(const mt_command *command, const mt_wire_message *m) {
    const uint8_t *p = m->payload;
    CHECK(command->type == m->type);
    switch (m->type) {
    case 1: CHECK(command->value.nonce == get(p, 8)); break;
    case 3:
        CHECK(command->value.config.profile == get(p, 2));
        CHECK(command->value.config.mode == p[2]); CHECK(command->value.config.source == p[3]);
        CHECK(command->value.config.edge == p[4]);
        CHECK(command->value.config.level_code == get(p + 6, 2));
        CHECK(command->value.config.pretrigger_samples == get(p + 8, 2));
        CHECK(command->value.config.auto_timeout_ms == get(p + 12, 4)); break;
    case 4: CHECK(command->value.config_id == get(p, 4)); break;
    case 5: CHECK(command->value.generation == get(p, 4)); break;
    case 7:
        CHECK(command->value.release.generation == get(p, 4));
        CHECK(command->value.release.window_id == get(p + 4, 4));
        CHECK(command->value.release.samples_crc32 == get(p + 8, 4)); break;
    default: break;
    }
}
static void command_reject(const mt_wire_message *m, mt_command_result expected) {
    command_guard command, previous;
    memset(&command, 0xa5, sizeof command); memcpy(&previous, &command, sizeof previous);
    CHECK(mt_command_parse(m, &command.value) == expected);
    CHECK(memcmp(&command, &previous, sizeof command) == 0);
}
static int known_device_type(unsigned type) {
    static const uint8_t types[] = {129,130,131,132,133,134,135,144,145,146,147,255};
    size_t i;
    for (i = 0; i < COUNT(types); ++i) if (type == types[i]) return 1;
    return 0;
}
static void test_commands(void) {
    size_t i, n, j;
    const size_t reserved[] = {5,10,11};
    phase = "command payload syntax and unchanged outputs";
    for (i = 0; i < 7u; ++i) {
        mt_wire_message m = golden_message(&golden[i]), previous;
        command_guard command;
        memset(&command, 0xa5, sizeof command); memcpy(&previous, &m, sizeof previous);
        CHECK(mt_command_parse(&m, &command.value) == MT_COMMAND_OK);
        command_expected(&command.value, &m); command_canaries(&command);
        CHECK(memcmp(&m, &previous, sizeof m) == 0);
        for (n = 0; n <= 513u; ++n) {
            if (n == golden[i].payload_n) continue;
            m.payload_length = (uint16_t)n; command_reject(&m, MT_COMMAND_LENGTH);
        }
        m.payload_length = UINT16_MAX; command_reject(&m, MT_COMMAND_LENGTH);
        m = previous; m.session = UINT64_MAX; m.sequence = UINT32_MAX; m.request = 0;
        CHECK(mt_command_parse(&m, &command.value) == MT_COMMAND_OK);
        command_expected(&command.value, &m); /* session/order is deliberately not parser work */
    }
    for (i = 0; i < 256u; ++i) {
        mt_wire_message m = {0};
        if (i >= 1u && i <= 7u) continue;
        m.type = (uint8_t)i;
        command_reject(&m, known_device_type((unsigned)i) ? MT_COMMAND_WRONG_DIRECTION : MT_COMMAND_UNKNOWN_TYPE);
    }
    for (i = 0; i < COUNT(reserved); ++i) for (j = 1; j <= 255u; ++j) {
        mt_wire_message m = golden_message(&golden[2]);
        m.payload[reserved[i]] = (uint8_t)j; command_reject(&m, MT_COMMAND_RESERVED);
    }
    { mt_wire_message m = golden_message(&golden[0]); command_guard command;
      memset(m.payload, 0, 8); command_reject(&m, MT_COMMAND_INVALID_HELLO);
      for (i = 0; i < 64u; ++i) {
          memset(m.payload, 0, 8); m.payload[i / 8u] = (uint8_t)(1u << (i % 8u));
          memset(&command, 0xa5, sizeof command);
          CHECK(mt_command_parse(&m, &command.value) == MT_COMMAND_OK);
          CHECK(command.value.value.nonce == (UINT64_C(1) << i)); command_canaries(&command);
      }
      command_reject(NULL, MT_COMMAND_ARGUMENT);
      CHECK(mt_command_parse(&m, NULL) == MT_COMMAND_ARGUMENT); }
    /* Parser must preserve extreme IDs and semantic-invalid CONFIG for dispatcher. */
    for (i = 2; i < 7u; ++i) {
        mt_wire_message m = golden_message(&golden[i]); command_guard command;
        memset(m.payload, 0xff, m.payload_length);
        if (m.type == 3u) m.payload[5] = m.payload[10] = m.payload[11] = 0;
        memset(&command, 0xa5, sizeof command);
        CHECK(mt_command_parse(&m, &command.value) == MT_COMMAND_OK);
        command_expected(&command.value, &m); command_canaries(&command);
    }
}

static void config_result(const mt_config *config, const mt_command_support *support,
                          int expected) {
    mt_config saved_config;
    mt_command_support saved_support;
    memcpy(&saved_config, config, sizeof saved_config);
    memcpy(&saved_support, support, sizeof saved_support);
    CHECK(mt_command_config_valid(config, support) == expected);
    CHECK(memcmp(config, &saved_config, sizeof saved_config) == 0);
    CHECK(memcmp(support, &saved_support, sizeof saved_support) == 0);
}
static void test_config_capabilities(void) {
    const uint16_t pre_values[] = {0,512,1024};
    const uint32_t time_values[] = {0,1,99,100,101,59999,60000,60001,65535,UINT32_MAX};
    const unsigned mode_membership[] = {1,2,4};
    const unsigned binary_membership[] = {1,2};
    mt_config config = {1,0,0,0,2048,0,100};
    const mt_config baseline = {1,0,0,0,2048,0,100};
    mt_command_support support = {7,3,3,1};
    unsigned mb, sb, eb, pb, mode, source, edge, pre, value;
    size_t i;
    phase = "CONFIG capabilities Cartesian product and semantic boundaries";
    CHECK(mt_command_config_valid(NULL, &support) == 0);
    CHECK(mt_command_config_valid(&config, NULL) == 0);
    for (mb = 0; mb <= 7u; ++mb) for (sb = 0; sb <= 3u; ++sb)
    for (eb = 0; eb <= 3u; ++eb) for (pb = 0; pb <= 1u; ++pb) {
        support.mode_bits = (uint8_t)mb; support.source_bits = (uint8_t)sb;
        support.edge_bits = (uint8_t)eb; support.pretrigger = (uint8_t)pb;
        for (mode = 0; mode < 3u; ++mode) for (source = 0; source < 2u; ++source)
        for (edge = 0; edge < 2u; ++edge) for (pre = 0; pre < 3u; ++pre) {
            int expected = (mb & mode_membership[mode]) != 0u &&
                           (sb & binary_membership[source]) != 0u &&
                           (eb & binary_membership[edge]) != 0u && (pre == 0u || pb == 1u);
            config = baseline; config.mode = (uint8_t)mode; config.source = (uint8_t)source;
            config.edge = (uint8_t)edge; config.pretrigger_samples = pre_values[pre];
            config.auto_timeout_ms = mode == 0u ? 100u : 0u;
            config_result(&config, &support, expected);
        }
    }
    support = (mt_command_support){7,3,3,1}; config = baseline;
    /* All values of every 8/16-bit semantic field, varying one at a time. */
    for (value = 0; value <= UINT16_MAX; ++value) {
        config.profile = (uint16_t)value; config_result(&config, &support, value == 1u);
    }
    config = baseline;
    for (value = 0; value <= UINT16_MAX; ++value) {
        config.level_code = (uint16_t)value;
        config_result(&config, &support, value >= 1u && value <= 4094u);
        config.source = 1; config_result(&config, &support, value >= 1u && value <= 4094u);
        config.source = 0;
    }
    config = baseline;
    for (value = 0; value <= UINT16_MAX; ++value) {
        config.pretrigger_samples = (uint16_t)value;
        config_result(&config, &support, value == 0u || value == 512u || value == 1024u);
        support.pretrigger = 0; config_result(&config, &support, value == 0u); support.pretrigger = 1;
    }
    for (value = 0; value <= UINT8_MAX; ++value) {
        config = baseline; config.mode = (uint8_t)value;
        config.auto_timeout_ms = value == 0u ? 100u : 0u;
        config_result(&config, &support, value <= 2u);
        config = baseline; config.source = (uint8_t)value; config_result(&config, &support, value <= 1u);
        config = baseline; config.edge = (uint8_t)value; config_result(&config, &support, value <= 1u);
    }
    config = baseline;
    for (value = 8; value <= UINT8_MAX; ++value) {
        support.mode_bits = (uint8_t)value; config_result(&config, &support, 0);
    }
    support.mode_bits = 7;
    for (value = 4; value <= UINT8_MAX; ++value) {
        support.source_bits = (uint8_t)value; config_result(&config, &support, 0);
    }
    support.source_bits = 3;
    for (value = 4; value <= UINT8_MAX; ++value) {
        support.edge_bits = (uint8_t)value; config_result(&config, &support, 0);
    }
    support.edge_bits = 3;
    for (value = 2; value <= UINT8_MAX; ++value) {
        support.pretrigger = (uint8_t)value; config_result(&config, &support, 0);
    }
    support.pretrigger = 1;
    for (mode = 0; mode < 3u; ++mode) for (i = 0; i < COUNT(time_values); ++i) {
        config = baseline; config.mode = (uint8_t)mode; config.auto_timeout_ms = time_values[i];
        config_result(&config, &support, mode == 0u ? time_values[i] >= 100u && time_values[i] <= 60000u : time_values[i] == 0u);
    }
}

int main(void) {
    printf("Independent conformance, deterministic xorshift seed 0x6d747031\n");
    test_crc_and_goldens(); puts("PASS: independent CRC/COBS and fixed wire vectors");
    test_wire_errors(); puts("PASS: wire rejection, precedence and failure atomicity");
    test_random_wire(); puts("PASS: 4096 differential wire messages");
    test_stream_splits(); puts("PASS: stream fragmentation/coalescing/recovery");
    test_stream_deadlines(); puts("PASS: stream deadlines/wrap/overflow/reset");
    test_commands(); puts("PASS: command payload syntax and parsing");
    test_config_capabilities(); puts("PASS: CONFIG capabilities and semantic domains");
    printf("PASS: all independent conformance checks (%" PRIu64 " assertions)\n", checks);
    return 0;
}
