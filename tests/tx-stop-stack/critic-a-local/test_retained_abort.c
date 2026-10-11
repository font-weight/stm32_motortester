/* Independent TX002 oracle: API-reachable compaction and actual encoded output.
 * Payload bytes 8..11 are opaque at this transport boundary; these representation
 * sweeps do not claim that every pattern is a semantically valid wire reason. */
#include "mt_tx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static unsigned long cases, checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)
typedef struct { uint8_t bytes[4096]; size_t used, step, calls; unsigned block; } sink;
static int accept(void *context, const uint8_t *data, size_t n) {
    sink *s = context;
    ++s->calls;
    assert(n <= 64u);
    if (s->block || s->calls % 3u == 0u) return 0;
    if (n > s->step) n = s->step;
    assert(s->used + n <= sizeof s->bytes);
    memcpy(s->bytes + s->used, data, n); s->used += n;
    return (int)n;
}
static void put(uint8_t *p, uint32_t n) {
    size_t i; for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(n >> (8u * i));
}
static mt_wire_message make(uint8_t type, uint16_t length, uint32_t request) {
    mt_wire_message m;
    /* Defined, distinctive unused storage. It must never enter the wire. */
    memset(&m, 0xa5, sizeof m);
    m.type = type; m.payload_length = length;
    m.session = 0; m.sequence = 0; m.request = request;
    memset(m.payload, 0, length);
    return m;
}
static int drain(mt_tx *tx, sink *s, unsigned *stop_finishes) {
    size_t budget = 20000;
    while (tx->count) {
        mt_tx_event e, before;
        mt_tx_result r;
        CHECK(budget-- > 0u);
        memset(&e, 0x5a, sizeof e); memcpy(&before, &e, sizeof e);
        r = mt_tx_poll(tx, 25, accept, s, &e);
        CHECK(r == MT_TX_PROGRESS || r == MT_TX_BLOCKED);
        if (r == MT_TX_BLOCKED) {
            CHECK(memcmp(&e, &before, sizeof e) == 0);
            continue;
        }
        if ((e.flags & MT_TX_STOP_FINISHED) != 0u) {
            CHECK(e.type == 133u && (e.flags & MT_TX_FINISHED) != 0u);
            CHECK(tx->stopping == 0u && tx->count == 1u);
            ++*stop_finishes;
        }
    }
    return 0;
}
static int one(size_t layout, unsigned pattern, size_t step, unsigned stage, unsigned fallback) {
    mt_tx tx, before;
    sink s = {{0}, 0, 64, 0, 0};
    mt_tx_event e, untouched;
    mt_wire_message hello = make(129,16,1), abort = make(147,12,0);
    mt_wire_message data = make(145,14,0), reply = make(130,30,7);
    mt_wire_message other_reply = make(132,8,8), ack = make(133,12,9);
    mt_wire_message status = make(134,24,0), alternate = make(147,12,0), bad;
    const uint64_t session = UINT64_C(0x0123456789abcdef);
    const uint32_t gen = UINT32_C(0xfedc9876), win = UINT32_C(0xabcdef01);
    uint8_t expected_types[5]; uint32_t expected_requests[5];
    size_t n = 0, i, start, frames; unsigned finished = 0;
    put(abort.payload,gen); put(abort.payload+4,win);
    for (i = 8; i < 12u; ++i) abort.payload[i] = (uint8_t)(pattern ^ (unsigned)(i * 37u));
    put(data.payload,gen); put(data.payload+4,win);
    put(alternate.payload,gen); put(alternate.payload+4,win); alternate.payload[8] = 1;
    put(ack.payload,gen); put(ack.payload+4,win);
    put(status.payload+4,gen); put(status.payload+8,win);
    mt_tx_init(&tx);
    CHECK(mt_tx_hello(&tx,session,&hello,0) == MT_TX_OK);
    CHECK(drain(&tx,&s,&finished) == 0); CHECK(finished == 0u);
    s.used = 0; s.calls = 0;
    if (layout == 0u) {
        CHECK(mt_tx_enqueue(&tx,&abort,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&data,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&reply,25) == MT_TX_OK);
    } else if (layout == 1u) {
        CHECK(mt_tx_enqueue(&tx,&data,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&abort,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&reply,25) == MT_TX_OK);
    } else {
        CHECK(mt_tx_enqueue(&tx,&abort,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&reply,25) == MT_TX_OK);
        CHECK(mt_tx_enqueue(&tx,&other_reply,25) == MT_TX_OK);
    }
    if (stage) {
        memset(&e,0x6b,sizeof e); memcpy(&untouched,&e,sizeof e);
        s.block = 1;
        CHECK(mt_tx_poll(&tx,25,accept,&s,&e) == MT_TX_BLOCKED);
        CHECK(memcmp(&e,&untouched,sizeof e) == 0);
        CHECK(tx.offset == 0u && tx.length != 0u && tx.slots[0].message.sequence == 2u);
    }
    CHECK(tx.next_sequence == 2u);
    /* Invalid Stop arguments must leave every byte unchanged, even if a staged
     * ABORT exists. No private-field corruption is used in these tests. */
    memcpy(&before,&tx,sizeof tx); bad = ack; bad.request = 0;
    CHECK(mt_tx_stop(&tx,gen,win,NULL,&bad,&status,25) == MT_TX_ARGUMENT);
    CHECK(memcmp(&before,&tx,sizeof tx) == 0);
    bad = status; bad.payload[0] = 1;
    CHECK(mt_tx_stop(&tx,gen,win,NULL,&ack,&bad,25) == MT_TX_ARGUMENT);
    CHECK(memcmp(&before,&tx,sizeof tx) == 0);
    CHECK(mt_tx_stop(&tx,gen,win,fallback ? &alternate : NULL,&ack,&status,25) == MT_TX_OK);
    CHECK(tx.next_sequence == 2u && tx.offset == 0u && tx.length == 0u);
    expected_types[n] = 130; expected_requests[n++] = 7;
    if (layout == 2u) { expected_types[n] = 132; expected_requests[n++] = 8; }
    expected_types[n] = 147; expected_requests[n++] = 0;
    expected_types[n] = 133; expected_requests[n++] = 9;
    expected_types[n] = 134; expected_requests[n++] = 0;
    CHECK(tx.count == n);
    CHECK(tx.slots[n-3u].message.sequence == 0u);
    CHECK(tx.slots[n-3u].message.session == session);
    CHECK(tx.slots[n-3u].message.payload_length == 12u);
    CHECK(memcmp(tx.slots[n-3u].message.payload,abort.payload,12) == 0);
    memcpy(&before,&tx,sizeof tx);
    CHECK(mt_tx_stop(&tx,gen,win,NULL,&ack,&status,25) == MT_TX_DRAINING);
    CHECK(memcmp(&before,&tx,sizeof tx) == 0);
    s.block = 0; s.step = step;
    CHECK(drain(&tx,&s,&finished) == 0); CHECK(finished == 1u);
    CHECK(tx.next_sequence == 2u+n && tx.stopping == 0u);
    CHECK(mt_tx_terminal_committed(&tx,gen,win) == 147u);
    start = 0; frames = 0;
    for (i = 0; i < s.used; ++i) if (s.bytes[i] == 0u) {
        mt_wire_message out;
        CHECK(frames < n);
        CHECK(mt_wire_decode(s.bytes+start,i-start,&out) == MT_WIRE_OK);
        CHECK(out.type == expected_types[frames] && out.request == expected_requests[frames]);
        CHECK(out.sequence == 2u+frames && out.session == session);
        if (out.type == 147u) {
            CHECK(out.payload_length == 12u && memcmp(out.payload,abort.payload,12) == 0);
        }
        ++frames; start = i+1u;
    }
    CHECK(frames == n && start == s.used);
    ++cases; return 0;
}
int main(void) {
    static const size_t steps[] = {1,2,7,64};
    size_t l,s; unsigned p,b,f;
    for (l=0;l<3u;++l) for (p=0;p<256u;++p) for(s=0;s<4u;++s)
        for(b=0;b<2u;++b) for(f=0;f<2u;++f)
            if(one(l,p,steps[s],b,f)) return 1;
    printf("PASS %lu API-reachable compaction cases, %lu checks\n",cases,checks);
    return 0;
}
