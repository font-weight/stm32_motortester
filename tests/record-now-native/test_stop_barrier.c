/* Author-maintained 0.2 derivative of the historical DOC-007 review fixture.
 * Hardware hooks are explicit synchronous fakes; no USB/board claim. */
#include "firmware/app/mt_control_rx.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks, scenarios;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct {
    mt_control c;
    mt_control_rx rx;
    unsigned starts, stops, profiles;
    uint32_t next;
    uint8_t bytes[8192];
    size_t length, cap;
} fixture;
static int profile(void *v) { ++((fixture *)v)->profiles; return 1; }
static int start(void *v, uint32_t now) { (void)now; ++((fixture *)v)->starts; return 1; }
static int stop(void *v) { ++((fixture *)v)->stops; return 1; }
static int write_bytes(void *v, const uint8_t *p, size_t n) {
    fixture *f = v;
    if (n > f->cap) n = f->cap;
    CHECK(n <= 64u && f->length + n <= sizeof f->bytes);
    memcpy(f->bytes + f->length, p, n); f->length += n;
    return (int)n;
}
static mt_wire_message make(uint8_t type, uint16_t len, uint32_t seq) {
    mt_wire_message m = {0};
    m.type = type; m.session = 11; m.sequence = seq; m.request = seq;
    m.payload_length = len;
    return m;
}
static mt_control_result send(fixture *f, const mt_wire_message *m) {
    uint8_t b[MT_WIRE_FRAME_MAX];
    size_t n = 0, at = 0;
    mt_control_result r = MT_CONTROL_OK;
    CHECK(mt_wire_encode(m, b, sizeof b, &n) == MT_WIRE_OK);
    while (at < n) {
        size_t consumed = 0, chunk = n - at;
        if (chunk > 7u) chunk = 7u;
        r = mt_control_rx_feed(&f->rx, b + at, chunk, 0, &consumed);
        CHECK(consumed == chunk);
        at += consumed;
        if (r == MT_CONTROL_RECOVERY) { CHECK(at == n); break; }
        CHECK(r == MT_CONTROL_OK);
    }
    return r;
}
static void poll(fixture *f, size_t cap) {
    f->cap = cap;
    CHECK(mt_control_poll(&f->c, 0, write_bytes, f) == MT_CONTROL_OK);
}
static void drain(fixture *f) {
    unsigned tries = 0;
    while (f->c.tx.count || f->c.state_count) { CHECK(++tries < 64u); poll(f, 64); }
}
static unsigned frames(const fixture *f, uint8_t type, uint32_t request,
                       uint8_t error_code) {
    size_t begin = 0, i;
    unsigned count = 0;
    for (i = 0; i < f->length; ++i) if (f->bytes[i] == 0u) {
        mt_wire_message m;
        CHECK(mt_wire_decode(f->bytes + begin, i - begin, &m) == MT_WIRE_OK);
        if (m.type == type && m.request == request) {
            ++count;
            if (type == 255u) CHECK(m.payload[1] == error_code);
        }
        begin = i + 1u;
    }
    return count;
}
static void init(fixture *f) {
    mt_control_io io = {profile, start, stop, f, 0};
    mt_wire_message m = make(1, 8, 1);
    memset(f, 0, sizeof *f);
    CHECK(mt_control_init(&f->c, &io) == MT_CONTROL_OK);
    CHECK(mt_control_rx_init(&f->rx, &f->c) == MT_CONTROL_OK);
    m.session = 0; m.payload[0] = 11;
    CHECK(send(f, &m) == MT_CONTROL_OK);
    drain(f);
    CHECK(frames(f, 129, 1, 0) == 1u);
    f->length = 0; f->next = 2;
}
/* Real STOP, optionally following an earlier unreplied GET_CAPS. No manual
 * link.phase/tx.stopping mutations. Stage 0/1/2/3 means zero/one/all-but-last/
 * all ACK bytes accepted into the fake ordered writer. */
static mt_wire_message setup_stop(fixture *f, unsigned prior, unsigned stage) {
    mt_wire_message m;
    init(f);
    if (prior) {
        m = make(2, 0, f->next++);
        CHECK(send(f, &m) == MT_CONTROL_OK);
    }
    m = make(5, 4, f->next++);
    CHECK(send(f, &m) == MT_CONTROL_OK);
    CHECK(f->c.tx.stopping == 1u && f->c.link.phase == MT_LINK_STOPPING);
    while (f->c.tx.slots[0].message.type != 133u) poll(f, 64);
    CHECK(f->c.tx.count == 2u && f->c.tx.offset == 0u);
    if (stage) {
        poll(f, 0); /* Serialize without committing a sequence or byte. */
        CHECK(f->c.tx.offset == 0u && f->c.tx.length > 2u);
        poll(f, stage == 1u ? 1u : f->c.tx.length - (stage == 2u ? 1u : 0u));
    }
    if (stage == 3u) {
        CHECK(!f->c.tx.stopping && f->c.link.phase == MT_LINK_IDLE);
        CHECK(f->c.tx.count == 1u && f->c.tx.slots[0].message.type == 134u);
        CHECK(frames(f, 133, m.request, 0) == 1u);
    } else {
        CHECK(f->c.tx.stopping && f->c.link.phase == MT_LINK_STOPPING);
        CHECK(frames(f, 133, m.request, 0) == 0u);
    }
    return m;
}
static mt_wire_message probe(unsigned kind, uint32_t seq) {
    static const uint8_t types[] = {2, 3, 4, 5, 6, 7, 6, 3, 8, 0, 128};
    static const uint16_t lengths[] = {0, 16, 4, 4, 0, 12, 1, 16, 0, 0, 0};
    mt_wire_message m = make(types[kind], lengths[kind], seq);
    if (kind == 1u || kind == 7u) {
        m.payload[0] = 1; m.payload[2] = 2; m.payload[6] = 1;
        if (kind == 7u) m.payload[5] = 1; /* Reserved CONFIG byte. */
    }
    return m;
}
static void blocked_requests(void) {
    unsigned prior, stage, kind;
    for (prior = 0; prior < 2u; ++prior)
        for (stage = 0; stage < 3u; ++stage)
            for (kind = 0; kind < 11u; ++kind) {
                fixture f;
                mt_wire_message m;
                size_t count, length;
                unsigned stops;
                (void)setup_stop(&f, prior, stage);
                count = f.c.tx.count; length = f.length; stops = f.stops;
                m = probe(kind, f.next++);
                CHECK(send(&f, &m) == MT_CONTROL_RECOVERY);
                CHECK(!f.c.ready && !f.c.session.active);
                CHECK(f.c.tx.count == count && f.length == length);
                CHECK(f.c.tx.slots[0].message.type == 133u);
                CHECK(f.c.tx.slots[1].message.type == 134u);
                CHECK(f.starts == 0u && f.stops == stops + 1u);
                CHECK(mt_control_poll(&f.c, 0, write_bytes, &f) == MT_CONTROL_IGNORED);
                CHECK(f.length == length);
                ++scenarios;
            }
}
static void outside_barrier(void) {
    unsigned kind, boundary;
    for (boundary = 0; boundary < 2u; ++boundary)
        for (kind = 0; kind < 11u; ++kind) {
            fixture f;
            mt_wire_message m;
            if (boundary) (void)setup_stop(&f, 1, 3);
            else init(&f);
            m = probe(kind, f.next++);
            CHECK(send(&f, &m) == MT_CONTROL_OK);
            CHECK(f.c.ready && f.c.session.active);
            drain(&f);
            if (kind >= 6u)
                CHECK(frames(&f, 255, m.request, kind <= 8u ? 6u : 5u) == 1u);
            else if (kind == 0u) CHECK(frames(&f, 130, m.request, 0) == 1u);
            else if (kind == 4u) CHECK(frames(&f, 134, m.request, 0) == 1u);
            else if (kind == 1u) CHECK(frames(&f, 131, m.request, 0) == 1u);
            else if (kind == 2u) CHECK(frames(&f, 255, m.request, 3) == 1u);
            else if (kind == 3u) CHECK(frames(&f, 133, m.request, 0) == 1u);
            else CHECK(frames(&f, 255, m.request, 7) == 1u);
            ++scenarios;
        }
}
static void exceptions(void) {
    unsigned stage, which;
    for (stage = 0; stage < 3u; ++stage)
        for (which = 0; which < 5u; ++which) {
            fixture f;
            mt_wire_message old_stop = setup_stop(&f, 1, stage), m;
            unsigned stops = f.stops;
            size_t count = f.c.tx.count;
            if (which == 0u) m = old_stop; /* Immediate exact duplicate. */
            else if (which == 1u) { m = old_stop; m.payload[0] = 1; }
            else if (which == 2u || which == 3u) {
                m = make(1, 8, 1); m.session = 0;
                m.payload[0] = which == 2u ? 11 : 12;
            } else { m = make(6, 0, f.next); m.session = 99; }
            CHECK(send(&f, &m) == (which == 1u ? MT_CONTROL_RECOVERY : MT_CONTROL_OK));
            if (which == 0u || which == 2u || which == 4u) {
                CHECK(f.c.ready && f.c.tx.stopping && f.c.tx.count == count);
                CHECK(f.stops == stops && f.starts == 0u);
            } else if (which == 1u) CHECK(!f.c.ready && f.stops == stops + 1u);
            else {
                CHECK(f.c.ready && f.c.session.nonce == 12u && !f.c.tx.stopping);
                CHECK(f.stops == stops + 1u);
                drain(&f);
                CHECK(frames(&f, 129, 1, 0) == 1u);
            }
            ++scenarios;
        }
}
int main(void) {
    blocked_requests(); outside_barrier(); exceptions();
    printf("PASS: %u real STOP-barrier scenarios, %u checks; 7-byte encoded RX chunks.\n", scenarios, checks);
    return 0;
}
