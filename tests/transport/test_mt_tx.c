#include "../../firmware/transport/mt_tx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    uint8_t bytes[32768];
    size_t used, step, calls, largest;
    int fail;
} fake_usb;
static int write_fake(void *context, const uint8_t *p, size_t n) {
    fake_usb *f = context;
    ++f->calls;
    if (n > f->largest) f->largest = n;
    if (f->fail) return f->fail;
    if (n > f->step) n = f->step;
    assert(f->used + n <= sizeof f->bytes);
    memcpy(f->bytes + f->used, p, n);
    f->used += n;
    return (int)n;
}
static void put32(uint8_t *p, uint32_t v) {
    unsigned i;
    for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(v >> (8u * i));
}
static mt_wire_message message(uint8_t type, uint32_t request) {
    mt_wire_message m = {0};
    m.type = type;
    m.request = request;
    switch (type) {
    case 129: m.payload_length = 16; break;
    case 130: m.payload_length = 30; break;
    case 131: m.payload_length = 20; break;
    case 132: case 135: case 255: m.payload_length = 8; break;
    case 133: case 147: m.payload_length = 12; break;
    case 134: m.payload_length = 24; break;
    case 144: m.payload_length = 40; break;
    case 145: m.payload_length = 512; break;
    case 146: m.payload_length = 16; break;
    default: assert(0);
    }
    if (type >= 144u && type <= 147u) {
        put32(m.payload, 7);
        put32(m.payload + 4, 3);
    }
    return m;
}
static void drain(mt_tx *t, fake_usb *f, uint32_t now) {
    mt_tx_event event;
    size_t budget = 10000;
    mt_tx_result r;
    do {
        assert(budget-- != 0u);
        r = mt_tx_poll(t, now, write_fake, f, &event);
        assert(r == MT_TX_PROGRESS || r == MT_TX_IDLE);
    } while (r != MT_TX_IDLE);
}
static size_t decode(const fake_usb *f, mt_wire_message *out, size_t cap) {
    size_t start = 0, i, count = 0;
    for (i = 0; i < f->used; ++i) if (f->bytes[i] == 0u) {
        assert(count < cap);
        assert(mt_wire_decode(f->bytes + start, i - start, out + count) == MT_WIRE_OK);
        ++count;
        start = i + 1;
    }
    assert(start == f->used);
    return count;
}
static void connected(mt_tx *t, fake_usb *f, uint32_t now) {
    mt_wire_message hello = message(129, 1);
    mt_tx_init(t);
    memset(f, 0, sizeof *f);
    f->step = 64;
    assert(mt_tx_hello(t, UINT64_C(0x1122334455667788), &hello, now) == MT_TX_OK);
    drain(t, f, now);
    f->used = 0;
}
static mt_tx_result stop_request(mt_tx *t, uint32_t win, uint32_t now, uint32_t request) {
    mt_wire_message abort = message(147, 0), ack = message(133, request);
    mt_wire_message status = message(134, 0);
    abort.payload[8] = 1; /* STOP */
    put32(ack.payload, 7);
    put32(ack.payload + 4, 3);
    put32(status.payload + 4, 7);
    put32(status.payload + 8, 3);
    return mt_tx_stop(t, 7, win, &abort, &ack, &status, now);
}
static mt_tx_result stop(mt_tx *t, uint32_t win, uint32_t now) {
    return stop_request(t, win, now, 9);
}
static void test_fragmentation(void) {
    mt_tx t;
    fake_usb f;
    mt_wire_message src = message(145, 0), out[2];
    size_t step, i;
    for (i = 8; i < sizeof src.payload; ++i) src.payload[i] = (uint8_t)i;
    for (step = 1; step <= 80; ++step) {
        connected(&t, &f, 0);
        f.step = step;
        assert(mt_tx_enqueue(&t, &src, 0) == MT_TX_OK);
        drain(&t, &f, 1);
        assert(decode(&f, out, 2) == 1u);
        assert(out[0].sequence == 2u && out[0].session == t.session);
        assert(memcmp(out[0].payload, src.payload, 512) == 0);
        assert(f.largest <= MT_TX_CHUNK_MAX);
    }
}
static void test_reservation_and_stop_order(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event event;
    mt_wire_message data = message(145, 0), reply = message(132, 7), out[8];
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    f.step = 1;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &event) == MT_TX_PROGRESS);
    assert(event.flags == MT_TX_STARTED);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_OK);
    ++reply.request;
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_OK);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_FULL);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_FULL);
    assert(stop(&t, 3, 0) == MT_TX_OK);
    assert(t.count == MT_TX_CAPACITY);
    assert(stop(&t, 3, 0) == MT_TX_DRAINING);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_DRAINING);
    f.step = 64;
    drain(&t, &f, 1);
    assert(decode(&f, out, 8) == 6u);
    assert(out[0].type == 145 && out[1].type == 132 && out[2].type == 132);
    assert(out[3].type == 147 && out[4].type == 133 && out[5].type == 134);
    for (size_t i = 0; i < 6u; ++i) assert(out[i].sequence == i + 2u);
    assert(out[1].request == 7u && out[2].request == 8u);
    assert(!t.stopping && t.count == 0u);
}
static void test_stop_before_end_commit(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event event = {0};
    mt_wire_message end = message(146, 0), out[4];
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &end, 0) == MT_TX_OK);
    f.step = 0;
    assert(mt_tx_poll(&t, 20, write_fake, &f, &event) == MT_TX_BLOCKED);
    assert(t.next_sequence == 2u && t.length != 0u && t.offset == 0u);
    assert(mt_tx_terminal_committed(&t, 7, 3) == 0u);
    assert(stop(&t, 3, 21) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 22);
    assert(decode(&f, out, 4) == 3u);
    assert(out[0].type == 147 && out[0].sequence == 2u);
    assert(out[1].type == 133 && out[1].sequence == 3u);
    assert(mt_tx_terminal_committed(&t, 7, 3) == 147u);
}
static void test_stop_after_end_commit(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message end = message(146, 0), out[4];
    size_t initial;
    for (initial = 1; initial <= 64u; ++initial) {
        connected(&t, &f, 0);
        assert(mt_tx_enqueue(&t, &end, 0) == MT_TX_OK);
        f.step = initial;
        assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
        assert((e.flags & MT_TX_STARTED) != 0u && e.type == 146u);
        assert(mt_tx_terminal_committed(&t, 7, 3) == 146u);
        assert(stop(&t, 3, 0) == MT_TX_OK);
        f.step = 64;
        drain(&t, &f, 1);
        assert(decode(&f, out, 4) == 3u);
        assert(out[0].type == 146u && out[1].type == 133u && out[2].type == 134u);
        assert(out[0].sequence == 2u && out[1].sequence == 3u);
    }
}
static void test_existing_abort_and_idle_stop(void) {
    mt_tx t;
    fake_usb f;
    mt_wire_message abort = message(147, 0), out[4];
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &abort, 0) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(stop(&t, 3, 1) == MT_TX_OK);
    drain(&t, &f, 1);
    assert(decode(&f, out, 4) == 3u && out[0].type == 147u && out[1].type == 133u);
    connected(&t, &f, 0);
    assert(stop(&t, 0, 0) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(decode(&f, out, 4) == 2u && out[0].type == 133u);
}
static void test_hello_flush(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message data = message(145, 0), reply = message(132, 7);
    mt_wire_message hello = message(129, 1), out[4];
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    f.step = 1;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_OK);
    assert(mt_tx_hello(&t, 42, &hello, 1) == MT_TX_OK);
    assert(t.count == 2u && t.next_sequence == 1u);
    /* Another new HELLO replaces an ACK not yet started, retaining old partial. */
    assert(mt_tx_hello(&t, 43, &hello, 2) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 3);
    assert(decode(&f, out, 4) == 2u);
    assert(out[0].type == 145u && out[0].session == UINT64_C(0x1122334455667788));
    assert(out[1].type == 129u && out[1].session == 43u && out[1].sequence == 1u);
    assert(t.next_sequence == 2u);
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_OK);
    f.step = 0;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_BLOCKED);
    assert(mt_tx_hello(&t, 99, &hello, 1) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 1);
    assert(decode(&f, out, 4) == 1u && out[0].session == 99u);
}
static void test_timeouts_and_recovery(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message data = message(145, 0), hello = message(129, 1), out[3];
    uint32_t start = UINT32_MAX - 499u;
    connected(&t, &f, start);
    assert(mt_tx_enqueue(&t, &data, start) == MT_TX_OK);
    f.step = 0;
    assert(mt_tx_poll(&t, start + 999u, write_fake, &f, &e) == MT_TX_BLOCKED);
    assert(mt_tx_poll(&t, start + 1000u, write_fake, &f, &e) == MT_TX_TIMEOUT);
    assert(mt_tx_poll(&t, start + 1001u, write_fake, &f, &e) == MT_TX_FAULT);
    assert(mt_tx_enqueue(&t, &data, start + 1001u) == MT_TX_FAULT);
    assert(mt_tx_hello(&t, 55, &hello, 600) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 600);
    assert(decode(&f, out, 3) == 1u && out[0].session == 55u);
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    f.step = 1;
    for (uint32_t now = 0; now < 3000u; now += 500u)
        assert(mt_tx_poll(&t, now, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_poll(&t, 3000, write_fake, &f, &e) == MT_TX_TIMEOUT);
    assert(mt_tx_hello(&t, 56, &hello, 3001) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 3001);
    assert(decode(&f, out, 3) == 2u && out[1].session == 56u);
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    f.step = 1;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(stop(&t, 3, 1) == MT_TX_OK);
    assert(mt_tx_poll(&t, 500, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_poll(&t, 1000, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_poll(&t, 1001, write_fake, &f, &e) == MT_TX_TIMEOUT);
}
static void test_input_immutability_and_sequence(void) {
    mt_tx t, before;
    fake_usb f;
    mt_tx_event e, old_event;
    mt_wire_message data = message(145, 0), hello = message(129, 1);
    mt_tx_init(&t);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_NOT_NEGOTIATED);
    connected(&t, &f, 0);
    before = t;
    assert(mt_tx_hello(&t, t.session, &hello, 0) == MT_TX_ARGUMENT);
    assert(memcmp(&before, &t, sizeof t) == 0);
    data.payload_length = 513;
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_ARGUMENT);
    assert(memcmp(&before, &t, sizeof t) == 0);
    data.payload_length = 512;
    data.sequence = 2;
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_ARGUMENT);
    data.sequence = 0;
    put32(data.payload + 4, 0);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_ARGUMENT);
    put32(data.payload + 4, 3);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    t.next_sequence = UINT32_MAX - 1u;
    drain(&t, &f, 0);
    assert(t.next_sequence == UINT32_MAX);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    memset(&e, 0xa5, sizeof e);
    old_event = e;
    size_t calls = f.calls;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_SEQUENCE);
    assert(f.calls == calls && memcmp(&old_event, &e, sizeof e) == 0);
    assert(mt_tx_hello(&t, 77, &hello, 0) == MT_TX_OK);
    assert(t.next_sequence == 1u);
}
static void test_io_faults_and_reset(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message data = message(145, 0);
    int errors[2] = {-1, 1000};
    for (size_t i = 0; i < 2u; ++i) {
        connected(&t, &f, 0);
        assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
        f.fail = errors[i];
        assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_IO);
        size_t calls = f.calls;
        assert(mt_tx_poll(&t, 1, write_fake, &f, &e) == MT_TX_FAULT);
        assert(f.calls == calls);
        mt_tx_init(&t);
        assert(t.session == 0u && t.count == 0u && t.offset == 0u);
        assert(mt_tx_enqueue(&t, &data, 1) == MT_TX_NOT_NEGOTIATED);
    }
}
static void test_stop_flags_and_drop_events(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message status = message(134, 0), data = message(145, 0), out[4];
    unsigned stop_done = 0, ack_done = 0;
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &status, 0) == MT_TX_OK);
    assert(mt_tx_enqueue(&t, &data, 0) == MT_TX_OK);
    assert(stop(&t, 3, 0) == MT_TX_OK);
    while (mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS) {
        if ((e.flags & MT_TX_STOP_FINISHED) != 0u) {
            ++stop_done;
            assert(e.type == 133u && (e.flags & MT_TX_FINISHED) != 0u);
        }
        if (e.type == 133u && (e.flags & MT_TX_FINISHED) != 0u) ++ack_done;
    }
    assert(stop_done == 1u && ack_done == 1u);
    assert(decode(&f, out, 4) == 3u && out[0].type == 147u);
}
static void test_queued_abort_reason_preserved(void) {
    mt_tx t;
    fake_usb f;
    mt_wire_message abort = message(147, 0), ack = message(133, 9);
    mt_wire_message reply = message(132, 7), status = message(134, 0), out[5];
    connected(&t, &f, 0);
    abort.payload[8] = 2; /* DMA_ERROR must not silently become STOP. */
    assert(mt_tx_enqueue(&t, &abort, 0) == MT_TX_OK);
    assert(mt_tx_enqueue(&t, &reply, 0) == MT_TX_OK);
    put32(ack.payload, 7);
    put32(ack.payload + 4, 3);
    put32(status.payload + 4, 7);
    put32(status.payload + 8, 3);
    assert(mt_tx_stop(&t, 7, 3, NULL, &ack, &status, 0) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(decode(&f, out, 5) == 4u);
    assert(out[0].type == 132u && out[1].type == 147u);
    assert(out[1].payload[8] == 2u && out[2].type == 133u);
}
static void through_stop_ack(mt_tx *t, fake_usb *f) {
    mt_tx_event e;
    do {
        assert(mt_tx_poll(t, 0, write_fake, f, &e) == MT_TX_PROGRESS);
    } while ((e.flags & MT_TX_STOP_FINISHED) == 0u);
    assert(e.type == 133u && t->count == 1u && !t->stopping);
}
static void test_ack_boundary_new_command_and_repeat(void) {
    mt_tx t;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message arm_ack = message(132, 10), out[8];
    connected(&t, &f, 0);
    assert(stop(&t, 0, 0) == MT_TX_OK);
    through_stop_ack(&t, &f);
    assert(mt_tx_enqueue(&t, &arm_ack, 0) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(decode(&f, out, 8) == 3u);
    assert(out[0].type == 133u && out[1].type == 134u && out[2].type == 132u);
    connected(&t, &f, 0);
    assert(stop(&t, 3, 0) == MT_TX_OK);
    through_stop_ack(&t, &f);
    /* Repeat after acknowledged Stop: no new active window, stale event drops. */
    assert(stop_request(&t, 0, 0, 10) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(decode(&f, out, 8) == 4u);
    assert(out[0].type == 147u && out[1].type == 133u && out[2].type == 133u);
    for (size_t i = 0; i < 4u; ++i) assert(out[i].sequence == i + 2u);
    connected(&t, &f, 0);
    assert(stop(&t, 0, 0) == MT_TX_OK);
    through_stop_ack(&t, &f);
    f.step = 1;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(e.type == 134u && (e.flags & MT_TX_STARTED) != 0u);
    assert(mt_tx_enqueue(&t, &arm_ack, 0) == MT_TX_OK);
    assert(stop_request(&t, 0, 0, 11) == MT_TX_OK);
    f.step = 64;
    drain(&t, &f, 0);
    assert(decode(&f, out, 8) == 5u);
    assert(out[0].type == 133u && out[1].type == 134u && out[2].type == 132u);
    assert(out[3].type == 133u && out[4].type == 134u);
}
static void test_phase_and_atomic_invalid_stop(void) {
    mt_tx t, before;
    fake_usb f;
    mt_tx_event e;
    mt_wire_message begin = message(144, 0), end = message(146, 0);
    mt_wire_message abort = message(147, 0), ack = message(133, 9);
    mt_wire_message status = message(134, 0), out[4];
    connected(&t, &f, 0);
    assert(mt_tx_window_status(&t, 7, 0) == MT_TX_NO_WINDOW);
    assert(mt_tx_window_status(&t, 7, 3) == MT_TX_WINDOW_NO_TX);
    assert(mt_tx_enqueue(&t, &begin, 0) == MT_TX_OK);
    assert(mt_tx_window_status(&t, 7, 3) == MT_TX_WINDOW_QUEUED);
    put32(ack.payload, 7);
    put32(ack.payload + 4, 3);
    put32(status.payload + 4, 7);
    put32(status.payload + 8, 3);
    before = t;
    assert(mt_tx_stop(&t, 7, 3, NULL, &ack, &status, 0) == MT_TX_ARGUMENT);
    assert(memcmp(&before, &t, sizeof t) == 0);
    put32(abort.payload + 4, 4);
    assert(mt_tx_stop(&t, 7, 3, &abort, &ack, &status, 0) == MT_TX_ARGUMENT);
    assert(memcmp(&before, &t, sizeof t) == 0);
    /* Allocated window whose BEGIN has not started still needs one ABORT. */
    assert(stop(&t, 3, 0) == MT_TX_OK);
    drain(&t, &f, 0);
    assert(mt_tx_window_status(&t, 7, 3) == MT_TX_WINDOW_ABORT_COMMITTED);
    assert(decode(&f, out, 4) == 3u && out[0].type == 147u);
    connected(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &begin, 0) == MT_TX_OK);
    f.step = 1;
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_window_status(&t, 7, 3) == MT_TX_WINDOW_STARTED);
    f.step = 64;
    drain(&t, &f, 0);
    assert(mt_tx_enqueue(&t, &end, 0) == MT_TX_OK);
    assert(mt_tx_poll(&t, 0, write_fake, &f, &e) == MT_TX_PROGRESS);
    assert(mt_tx_window_status(&t, 7, 3) == MT_TX_WINDOW_END_COMMITTED);
    assert(mt_tx_window_status(&t, 8, 3) == MT_TX_WINDOW_NO_TX);
}
int main(void) {
    test_fragmentation();
    test_reservation_and_stop_order();
    test_stop_before_end_commit();
    test_stop_after_end_commit();
    test_existing_abort_and_idle_stop();
    test_hello_flush();
    test_timeouts_and_recovery();
    test_input_immutability_and_sequence();
    test_io_faults_and_reset();
    test_stop_flags_and_drop_events();
    test_phase_and_atomic_invalid_stop();
    test_queued_abort_reason_preserved();
    test_ack_boundary_new_command_and_repeat();
    printf("mt_tx: 13 suites passed; sizeof(mt_tx)=%zu, slot=%zu\n", sizeof(mt_tx), sizeof(mt_tx_slot));
    return 0;
}
