#include "mt_wire.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

mt_wire_result baseline_mt_wire_encode(const mt_wire_message *, uint8_t *, size_t, size_t *);
mt_wire_result baseline_mt_wire_decode(const uint8_t *, size_t, mt_wire_message *);
uint32_t baseline_mt_wire_crc32(const uint8_t *, size_t);

static const char *context = "startup";
static void check(int condition, const char *what, int line) {
    if (!condition) {
        fprintf(stderr, "FAIL [%s] line %d: %s\n", context, line, what);
        exit(EXIT_FAILURE);
    }
}
#define CHECK(c) check((c), #c, __LINE__)

static uint16_t get16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}
static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t get64(const uint8_t *p) {
    return (uint64_t)get32(p) | ((uint64_t)get32(p + 4) << 32);
}
static void read_exact(FILE *f, void *p, size_t n) {
    CHECK(fread(p, 1, n, f) == n);
}
static void from_raw(mt_wire_message *m, const uint8_t *raw) {
    memset(m, 0xA7, sizeof *m);
    m->type = raw[2];
    m->payload_length = get16(raw + 4);
    CHECK(m->payload_length <= MT_WIRE_PAYLOAD_MAX);
    m->session = get64(raw + 8);
    m->sequence = get32(raw + 16);
    m->request = get32(raw + 20);
    memcpy(m->payload, raw + 24, m->payload_length);
}
static void compare_fields(const mt_wire_message *m, const uint8_t *raw, size_t n) {
    size_t i;
    CHECK(n == 28u + get16(raw + 4));
    CHECK(m->type == raw[2]);
    CHECK(m->payload_length == get16(raw + 4));
    CHECK(m->session == get64(raw + 8));
    CHECK(m->sequence == get32(raw + 16));
    CHECK(m->request == get32(raw + 20));
    CHECK(memcmp(m->payload, raw + 24, m->payload_length) == 0);
    for (i = m->payload_length; i < MT_WIRE_PAYLOAD_MAX; ++i)
        CHECK(m->payload[i] == 0xA7);
}

static mt_wire_result encode(unsigned api, const mt_wire_message *m, uint8_t *out,
                             size_t cap, size_t *n) {
    if (api == 0) return mt_wire_encode(m, out, cap, n);
    return mt_wire_encode_revision(m, 0, (uint8_t)api, out, cap, n);
}

static void argument_capacity_tests(void) {
    mt_wire_message m, before, decoded;
    uint8_t out[MT_WIRE_FRAME_MAX + 32u], old[sizeof out], frame[MT_WIRE_FRAME_MAX];
    size_t n, cap, frame_n;
    unsigned api, mask;
    context = "argument/capacity contracts";
    memset(&m, 0, sizeof m);
    m.type = 0x7F;
    before = m;
    for (api = 0; api < 3; ++api) {
        for (mask = 0; mask < 7; ++mask) {
            memset(out, 0x5C, sizeof out); memcpy(old, out, sizeof old); n = 0xABCDE;
            CHECK(encode(api, (mask & 1u) ? &m : NULL,
                         (mask & 2u) ? out + 16 : NULL, MT_WIRE_FRAME_MAX,
                         (mask & 4u) ? &n : NULL) == MT_WIRE_ARGUMENT);
            CHECK(n == 0xABCDE); CHECK(memcmp(out, old, sizeof out) == 0);
            CHECK(memcmp(&m, &before, sizeof m) == 0);
        }
        for (cap = 0; cap < MT_WIRE_FRAME_MAX; ++cap) {
            memset(out, 0x5C, sizeof out); memcpy(old, out, sizeof old); n = 0xABCDE;
            CHECK(encode(api, &m, out + 16, cap, &n) == MT_WIRE_CAPACITY);
            CHECK(n == 0xABCDE); CHECK(memcmp(out, old, sizeof out) == 0);
        }
        for (cap = 0; cap < 2; ++cap) {
            m.payload_length = cap == 0 ? 513 : UINT16_MAX;
            memset(out, 0x5C, sizeof out); memcpy(old, out, sizeof old); n = 0xABCDE;
            CHECK(encode(api, &m, out + 16, MT_WIRE_FRAME_MAX, &n) == MT_WIRE_LENGTH);
            CHECK(n == 0xABCDE); CHECK(memcmp(out, old, sizeof out) == 0);
            CHECK(encode(api, &m, out + 16, 0, &n) == MT_WIRE_LENGTH);
            CHECK(n == 0xABCDE); CHECK(memcmp(out, old, sizeof out) == 0);
        }
        m.payload_length = 0;
        CHECK(encode(api, &m, frame, sizeof frame, &frame_n) == MT_WIRE_OK);
        CHECK(encode(api, &m, out + 16, SIZE_MAX, &n) == MT_WIRE_OK);
        CHECK(n == frame_n && memcmp(out + 16, frame, n) == 0);
    }
    memset(&before, 0xA7, sizeof before);
    for (api = 1; api <= 2; ++api) {
        decoded = before;
        CHECK(mt_wire_decode_revision(NULL, 0, 0, (uint8_t)api, &decoded) == MT_WIRE_ARGUMENT);
        CHECK(memcmp(&decoded, &before, sizeof decoded) == 0);
        CHECK(mt_wire_decode_revision(frame, 1, 0, (uint8_t)api, NULL) == MT_WIRE_ARGUMENT);
        CHECK(mt_wire_decode_revision(NULL, SIZE_MAX, 0, (uint8_t)api, NULL) == MT_WIRE_ARGUMENT);
        CHECK(mt_wire_decode_revision(frame, SIZE_MAX, 0, (uint8_t)api, &decoded) == MT_WIRE_LENGTH);
        CHECK(memcmp(&decoded, &before, sizeof decoded) == 0);
    }
    decoded = before;
    CHECK(mt_wire_decode(NULL, 0, &decoded) == MT_WIRE_ARGUMENT);
    CHECK(mt_wire_decode(frame, 1, NULL) == MT_WIRE_ARGUMENT);
    CHECK(mt_wire_decode(frame, SIZE_MAX, &decoded) == MT_WIRE_LENGTH);
    CHECK(memcmp(&decoded, &before, sizeof decoded) == 0);
    CHECK(mt_wire_crc32(NULL, 0) == 0);
    CHECK(mt_wire_crc32((const uint8_t *)"123456789", 9) == UINT32_C(0xCBF43926));
    CHECK(mt_wire_crc32((const uint8_t *)"123456789", 9) ==
          baseline_mt_wire_crc32((const uint8_t *)"123456789", 9));
}

static void valid_encode(const uint8_t *raw, const uint8_t *frame, size_t frame_n,
                         uint8_t major, uint8_t minor) {
    mt_wire_message m, before;
    uint8_t out[MT_WIRE_FRAME_MAX + 32u], legacy[sizeof out], baseline[sizeof out];
    size_t n = 0xABCDE, legacy_n = 0xABCDE, base_n = 0xABCDE, i;
    from_raw(&m, raw); before = m;
    memset(out, 0x5C, sizeof out);
    CHECK(mt_wire_encode_revision(&m, major, minor, out + 16, MT_WIRE_FRAME_MAX, &n) == MT_WIRE_OK);
    CHECK(n == frame_n + 1u); CHECK(n <= MT_WIRE_FRAME_MAX);
    CHECK(memcmp(out + 16, frame, frame_n) == 0); CHECK(out[16u + frame_n] == 0);
    for (i = 0; i < 16; ++i) CHECK(out[i] == 0x5C);
    for (i = 16u + n; i < sizeof out; ++i) CHECK(out[i] == 0x5C);
    CHECK(memcmp(&m, &before, sizeof m) == 0);
    if (major == 0 && minor == 1) {
        memset(legacy, 0x5C, sizeof legacy); memset(baseline, 0x5C, sizeof baseline);
        CHECK(mt_wire_encode(&m, legacy + 16, MT_WIRE_FRAME_MAX, &legacy_n) == MT_WIRE_OK);
        CHECK(baseline_mt_wire_encode(&m, baseline + 16, MT_WIRE_FRAME_MAX, &base_n) == MT_WIRE_OK);
        CHECK(n == legacy_n && n == base_n);
        CHECK(memcmp(out, legacy, sizeof out) == 0);
        CHECK(memcmp(out, baseline, sizeof out) == 0);
    }
}

static void corpus_tests(const char *path) {
    FILE *f = fopen(path, "rb");
    uint8_t header[12], record[10], raw[1024], frame[1024];
    char name[256];
    uint32_t count, index, valid = 0, semantic = 0;
    mt_wire_message m, old, legacy, baseline;
    CHECK(f != NULL); read_exact(f, header, sizeof header);
    CHECK(memcmp(header, "MTWTEST1", 8) == 0); count = get32(header + 8);
    for (index = 0; index < count; ++index) {
        size_t name_n, raw_n, frame_n;
        uint8_t major, minor, expected, flags;
        mt_wire_result got, legacy_r, baseline_r;
        read_exact(f, record, sizeof record);
        name_n = get16(record); major = record[2]; minor = record[3];
        expected = record[4]; flags = record[5];
        raw_n = get16(record + 6); frame_n = get16(record + 8);
        CHECK(name_n < sizeof name && raw_n <= sizeof raw && frame_n <= sizeof frame);
        read_exact(f, name, name_n); name[name_n] = '\0'; context = name;
        read_exact(f, raw, raw_n); read_exact(f, frame, frame_n);
        memset(&m, 0xA7, sizeof m); memcpy(&old, &m, sizeof old);
        got = mt_wire_decode_revision(frame, frame_n, major, minor, &m);
        if (got != (mt_wire_result)expected)
            fprintf(stderr, "expected=%u got=%u\n", (unsigned)expected, (unsigned)got);
        CHECK(got == (mt_wire_result)expected);
        if (got != MT_WIRE_OK) CHECK(memcmp(&m, &old, sizeof m) == 0);
        else { compare_fields(&m, raw, raw_n); ++valid; }
        if ((flags & 1u) != 0) valid_encode(raw, frame, frame_n, major, minor);
        if ((flags & 2u) != 0) ++semantic;

        /* Exact 0.1 behavior is compared to separately compiled immutable base
         * for every corpus input, including 0.2, malformed and future versions. */
        memcpy(&legacy, &old, sizeof legacy); memcpy(&baseline, &old, sizeof baseline);
        legacy_r = mt_wire_decode(frame, frame_n, &legacy);
        baseline_r = baseline_mt_wire_decode(frame, frame_n, &baseline);
        CHECK(legacy_r == baseline_r);
        CHECK(memcmp(&legacy, &baseline, sizeof legacy) == 0);
        if (major == 0 && minor == 1) {
            CHECK(legacy_r == got); CHECK(memcmp(&legacy, &m, sizeof legacy) == 0);
        }
        if (got == MT_WIRE_OK) {
            memcpy(&m, &old, sizeof m);
            CHECK(mt_wire_decode_revision(frame, frame_n, major, (uint8_t)(minor ^ 0x80u), &m) == MT_WIRE_VERSION);
            CHECK(memcmp(&m, &old, sizeof m) == 0);
            memcpy(&m, &old, sizeof m);
            CHECK(mt_wire_decode_revision(frame, frame_n, (uint8_t)(major ^ 0x80u), minor, &m) == MT_WIRE_VERSION);
            CHECK(memcmp(&m, &old, sizeof m) == 0);
        }
    }
    context = "corpus EOF"; CHECK(fgetc(f) == EOF); CHECK(fclose(f) == 0);
    printf("PASS corpus=%" PRIu32 " valid=%" PRIu32 " rejected=%" PRIu32
           " semantic_negative_framing_accepted=%" PRIu32 "\n", count, valid, count - valid, semantic);
}

static uint32_t random_word(uint32_t *state) {
    *state ^= *state << 13; *state ^= *state >> 17; *state ^= *state << 5;
    return *state;
}
static void malformed_memory_tests(void) {
    uint32_t state = UINT32_C(0x5EED1234);
    unsigned iteration;
    context = "deterministic malformed-memory/differential sweep";
    for (iteration = 0; iteration < 20000; ++iteration) {
        /* Exact-size allocations let ASan observe reads beyond the supplied
         * object, instead of hiding those reads in a maximum-size local array. */
        size_t n = random_word(&state) % (MT_WIRE_FRAME_MAX + 3u), i;
        uint8_t *bytes = malloc(n == 0 ? 1 : n);
        mt_wire_message a, b, c, old;
        mt_wire_result ar, br, cr;
        CHECK(bytes != NULL);
        for (i = 0; i < n; ++i) bytes[i] = (uint8_t)random_word(&state);
        memset(&old, 0xA7, sizeof old);
        memcpy(&a, &old, sizeof a); memcpy(&b, &old, sizeof b); memcpy(&c, &old, sizeof c);
        ar = mt_wire_decode(bytes, n, &a);
        br = baseline_mt_wire_decode(bytes, n, &b);
        cr = mt_wire_decode_revision(bytes, n, 0, 1, &c);
        CHECK(ar == br && ar == cr);
        CHECK(memcmp(&a, &b, sizeof a) == 0 && memcmp(&a, &c, sizeof a) == 0);
        if (ar != MT_WIRE_OK) CHECK(memcmp(&a, &old, sizeof a) == 0);
        memcpy(&c, &old, sizeof c);
        cr = mt_wire_decode_revision(bytes, n, 0, 2, &c);
        if (cr != MT_WIRE_OK) CHECK(memcmp(&c, &old, sizeof c) == 0);
        free(bytes);
    }
    puts("PASS exact-size malformed-memory/differential sweep (seed 0x5EED1234, 20000 inputs)");
}

int main(int argc, char **argv) {
    CHECK(argc == 2);
    CHECK(MT_WIRE_MAJOR == 0 && MT_WIRE_MINOR == 1);
    CHECK(MT_WIRE_HEADER == 24 && MT_WIRE_PAYLOAD_MAX == 512);
    CHECK(MT_WIRE_RAW_MAX == 540 && MT_WIRE_FRAME_MAX == 544);
    argument_capacity_tests();
    corpus_tests(argv[1]);
    malformed_memory_tests();
    puts("PASS byte oracle, 0.1 baseline compatibility, revision isolation, failure atomicity, bounds");
    return EXIT_SUCCESS;
}
