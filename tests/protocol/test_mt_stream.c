/* Author-written draft regression tests; not independent conformance evidence.
 * No execution has been performed for this revision. */
#include "../../firmware/protocol/mt_stream.h"
#include <assert.h>
#include <string.h>

static size_t make_frame(uint8_t *frame, uint16_t length) {
    mt_wire_message m;
    size_t n = 0, i;
    memset(&m, 0, sizeof m);
    m.type = 127;
    m.session = UINT64_C(0x0807060504030201);
    m.sequence = 1;
    m.request = 2;
    m.payload_length = length;
    for (i = 0; i < length; ++i) m.payload[i] = (uint8_t)i;
    assert(mt_wire_encode(&m, frame, MT_WIRE_FRAME_MAX, &n) == MT_WIRE_OK);
    return n;
}

static void expect_frame(mt_stream *s, const uint8_t *frame, size_t n,
                         uint32_t now) {
    mt_wire_message out;
    size_t i;
    memset(&out, 0xa5, sizeof out);
    for (i = 0; i < n; ++i) {
        mt_stream_event e = mt_stream_feed(s, frame[i], now, &out);
        assert(e == (i + 1u == n ? MT_STREAM_MESSAGE : MT_STREAM_NONE));
    }
    assert(out.type == 127u);
    assert(out.session == UINT64_C(0x0807060504030201));
    assert(out.sequence == 1u && out.request == 2u);
}

/* Driver boundaries deliberately split the same byte sequence at every offset.
 * Separate calls to feed represent separate reads; no state reset between them. */
static void test_splits_and_coalescing(void) {
    uint8_t frame[MT_WIRE_FRAME_MAX];
    size_t n = make_frame(frame, 512), split, i;
    mt_stream s;
    mt_wire_message out;
    for (split = 0; split <= n; ++split) {
        unsigned ready = 0;
        mt_stream_init(&s);
        for (i = 0; i < split; ++i)
            if (mt_stream_feed(&s, frame[i], 10, &out) == MT_STREAM_MESSAGE)
                ++ready;
        for (; i < n; ++i)
            if (mt_stream_feed(&s, frame[i], 11, &out) == MT_STREAM_MESSAGE)
                ++ready;
        assert(ready == 1u);
        assert(out.payload_length == 512u);
        for (i = 0; i < 512u; ++i) assert(out.payload[i] == (uint8_t)i);
    }
    mt_stream_init(&s);
    expect_frame(&s, frame, n, 50);
    expect_frame(&s, frame, n, 50);
}

static void test_empty_and_rejection(void) {
    mt_stream s;
    mt_wire_message out, saved;
    uint8_t frame[MT_WIRE_FRAME_MAX];
    size_t n = make_frame(frame, 0);
    mt_stream_init(&s);
    memset(&out, 0xa5, sizeof out);
    memcpy(&saved, &out, sizeof out);
    assert(mt_stream_feed(&s, 0, 0, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 2, 0, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 0, 0, &out) == MT_STREAM_REJECTED);
    assert(s.last_decode == MT_WIRE_FRAMING);
    assert(memcmp(&out, &saved, sizeof out) == 0);
    expect_frame(&s, frame, n, 0);
}

static void test_overflow_and_resync(void) {
    mt_stream s;
    mt_wire_message out, saved;
    uint8_t frame[MT_WIRE_FRAME_MAX];
    size_t n = make_frame(frame, 1), i;
    mt_stream_init(&s);
    memset(&out, 0xa5, sizeof out);
    memcpy(&saved, &out, sizeof out);
    for (i = 0; i < MT_WIRE_FRAME_MAX - 1u; ++i)
        assert(mt_stream_feed(&s, 1, 0, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 1, 0, &out) == MT_STREAM_OVERFLOW);
    assert(s.used == 0u && s.discarding != 0u);
    /* Discard a whole valid frame up to and including its delimiter. */
    for (i = 0; i < n; ++i)
        assert(mt_stream_feed(&s, frame[i], 1, &out) == MT_STREAM_NONE);
    assert(memcmp(&out, &saved, sizeof out) == 0);
    expect_frame(&s, frame, n, 2);
}

static void test_timeouts(void) {
    mt_stream s;
    mt_wire_message out, saved;
    uint8_t frame[MT_WIRE_FRAME_MAX];
    size_t n = make_frame(frame, 6);
    mt_stream_init(&s);
    memset(&out, 0xa5, sizeof out);
    memcpy(&saved, &out, sizeof out);
    assert(mt_stream_feed(&s, 1, 100, &out) == MT_STREAM_NONE);
    assert(mt_stream_poll(&s, 1099) == MT_STREAM_NONE);
    assert(mt_stream_poll(&s, 1100) == MT_STREAM_TIMEOUT);
    assert(mt_stream_poll(&s, 1101) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 0, 1101, &out) == MT_STREAM_NONE);
    assert(memcmp(&out, &saved, sizeof out) == 0);
    expect_frame(&s, frame, n, 1101);

    /* Total-frame timeout wins despite recent bytes. */
    mt_stream_init(&s);
    assert(mt_stream_feed(&s, 1, 0, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 1, 900, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 1, 1800, &out) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 1, 2700, &out) == MT_STREAM_NONE);
    assert(mt_stream_poll(&s, 2999) == MT_STREAM_NONE);
    assert(mt_stream_feed(&s, 0, 3000, &out) == MT_STREAM_TIMEOUT);
    assert(s.discarding == 0u); /* Expiring delimiter consumed and resynced. */
    expect_frame(&s, frame, n, 3000);

    /* A regular clock rollover is not a backwards-time event. */
    mt_stream_init(&s);
    assert(mt_stream_feed(&s, 1, UINT32_MAX - 499u, &out) == MT_STREAM_NONE);
    assert(mt_stream_poll(&s, 499) == MT_STREAM_NONE);
    assert(mt_stream_poll(&s, 500) == MT_STREAM_TIMEOUT);
    assert(mt_stream_feed(&s, 0, 500, &out) == MT_STREAM_NONE);
    expect_frame(&s, frame, n, 500);
}

static void test_arguments_and_transport_reset(void) {
    mt_stream s, saved;
    mt_wire_message out;
    mt_stream_init(NULL);
    assert(mt_stream_poll(NULL, 0) == MT_STREAM_ARGUMENT);
    assert(mt_stream_feed(NULL, 0, 0, &out) == MT_STREAM_ARGUMENT);
    memset(&s, 0, sizeof s);
    mt_stream_init(&s);
    assert(mt_stream_feed(&s, 1, 0, &out) == MT_STREAM_NONE);
    memcpy(&saved, &s, sizeof s);
    assert(mt_stream_feed(&s, 2, 1, NULL) == MT_STREAM_ARGUMENT);
    assert(memcmp(&s, &saved, sizeof s) == 0);
    mt_stream_init(&s);
    assert(s.used == 0u && s.discarding == 0u);
    assert(mt_stream_poll(&s, 4000) == MT_STREAM_NONE);
}

int main(void) {
    test_splits_and_coalescing();
    test_empty_and_rejection();
    test_overflow_and_resync();
    test_timeouts();
    test_arguments_and_transport_reset();
    return 0;
}
