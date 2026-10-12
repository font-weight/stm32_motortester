#include "fake_dcd.h"
#include "firmware/transport/mt_usb_cdc.h"
#include "tusb.h"
#include "device/dcd.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    mt_control control;
    mt_control_rx rx;
    mt_usb_cdc adapter;
    int stable, stop_ok, stop_drops_gate;
    unsigned starts, stops, profiles;
} fixture;
static fixture f;
static uint8_t expected[FAKE_LOG_CAPACITY];
static size_t expected_size;
static int stable(void *ctx) { return ((fixture *)ctx)->stable; }
static int stop(void *ctx) { fixture *x = ctx; ++x->stops; if (x->stop_drops_gate) x->stable = 0; return x->stop_ok; }
static int start(void *ctx, uint32_t ms) { fixture *x = ctx; (void)ms; ++x->starts; return 1; }
static int profile(void *ctx) { ++((fixture *)ctx)->profiles; return 1; }
static void begin(unsigned dtr) {
    fake_start(); memset(&f, 0, sizeof(f)); f.stable = f.stop_ok = 1;
    mt_control_io io = {profile, start, stop, &f, 123};
    assert(mt_control_init(&f.control, &io) == MT_CONTROL_OK);
    assert(mt_control_rx_init(&f.rx, &f.control) == MT_CONTROL_OK);
    assert(mt_usb_cdc_init(&f.adapter, &f.rx, stable, &f, dtr) == MT_CONTROL_OK);
    assert(f.adapter.state == MT_USB_CLOSED);
    uint8_t closed_byte = 1; size_t closed_count = fake_record_count;
    assert(mt_usb_cdc_write(&f.adapter, &closed_byte, 1) == 0 && fake_record_count == closed_count);
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_OK);
    expected_size = 0;
}
static void put32(uint8_t *p, uint32_t n) { for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(n >> (8*i)); }
static mt_wire_message hello(uint64_t nonce) {
    mt_wire_message m = {0}; m.type = MT_CMD_HELLO; m.sequence = m.request = 1; m.payload_length = 8;
    for (unsigned i = 0; i < 8; ++i) m.payload[i] = (uint8_t)(nonce >> (8*i));
    return m;
}
static mt_wire_message command(uint8_t type, uint64_t nonce, uint32_t seq) {
    mt_wire_message m = {0}; m.type = type; m.session = nonce; m.sequence = m.request = seq; return m;
}
static mt_wire_message configuration(uint64_t nonce) {
    mt_wire_message m = command(MT_CMD_CONFIG, nonce, 2); m.payload_length = 16;
    m.payload[0] = 1; m.payload[2] = 1; m.payload[6] = 0x00; m.payload[7] = 0x08; return m;
}
static size_t encode(const mt_wire_message *m, uint8_t *bytes) {
    size_t n = 0; assert(mt_wire_encode(m, bytes, MT_WIRE_FRAME_MAX, &n) == MT_WIRE_OK); return n;
}
static mt_control_result input_bytes(const uint8_t *p, size_t n, uint32_t ms) {
    assert(n <= 64); fake_out(p, n, true); return mt_usb_cdc_step(&f.adapter, ms);
}
static mt_control_result input(const mt_wire_message *m, uint32_t ms) {
    uint8_t bytes[MT_WIRE_FRAME_MAX]; size_t n = encode(m, bytes); assert(n <= 64); return input_bytes(bytes, n, ms);
}
static void handshake(uint64_t nonce) {
    mt_wire_message m = hello(nonce); assert(input(&m, 0) == MT_CONTROL_OK); assert(f.control.ready);
    fake_drain(); fake_clear_logs();
}
static int write_record(const uint8_t *bytes, size_t n) {
    int accepted = mt_usb_cdc_write(&f.adapter, bytes, n); assert(accepted >= 0 && (size_t)accepted <= n);
    assert(expected_size + (size_t)accepted <= sizeof(expected));
    memcpy(expected + expected_size, bytes, (size_t)accepted); expected_size += (size_t)accepted; return accepted;
}
static void compare_delivery(void) {
    fake_drain(); assert(fake_host_size == expected_size); assert(memcmp(fake_host, expected, expected_size) == 0);
}
static size_t decode_host(mt_wire_message *messages, size_t capacity) {
    size_t n = 0, first = 0;
    for (size_t i = 0; i < fake_host_size; ++i) if (fake_host[i] == 0) {
        assert(n < capacity); assert(mt_wire_decode(fake_host + first, i - first, &messages[n]) == MT_WIRE_OK);
        ++n; first = i+1;
    }
    assert(first == fake_host_size); return n;
}
static void pump_adapter(uint32_t start_ms) {
    for (unsigned i = 0; i < 200; ++i) {
        mt_control_result r = mt_usb_cdc_step(&f.adapter, start_ms+i); assert(r != MT_CONTROL_ARGUMENT && r != MT_CONTROL_RECOVERY);
        fake_drain();
        if (!f.control.tx.count && !f.control.state_count && !tud_cdc_n_available(0) && f.adapter.offset == f.adapter.length) return;
    }
    assert(!"adapter drain exhausted");
}
static void save_bytes(const char *name, const uint8_t *bytes, size_t length) {
    const char *directory = getenv("USB_TEST_ARTIFACT_DIR"); if (!directory) return;
    char path[4096]; int n = snprintf(path, sizeof(path), "%s/%s", directory, name); assert(n > 0 && (size_t)n < sizeof(path));
    FILE *fp = fopen(path, "wb"); assert(fp); assert(fwrite(bytes, 1, length, fp) == length); assert(fclose(fp) == 0);
}
static void save_submissions(void) {
    const char *directory = getenv("USB_TEST_ARTIFACT_DIR"); if (!directory) return;
    char path[4096]; int n = snprintf(path, sizeof(path), "%s/prefix-submissions.csv", directory); assert(n > 0 && (size_t)n < sizeof(path));
    FILE *fp = fopen(path, "w"); assert(fp); fputs("epoch,endpoint,length,accepted,submission_byte_offset\n", fp);
    for (size_t i = 0; i < fake_record_count; ++i) {
        const fake_submission *r = &fake_records[i];
        assert(fprintf(fp, "%u,%u,%u,%u,%zu\n", r->epoch, r->ep_addr, r->length, r->accepted, r->offset) > 0);
    }
    assert(fclose(fp) == 0);
}
static void test_prefix(void) {
    begin(0); uint8_t bytes[64];
    const size_t frees[] = {1,7,63};
    for (unsigned round = 0; round < 90; ++round) {
        for (unsigned i = 0; i < 64; ++i) bytes[i] = (uint8_t)(round * 67 + i);
        size_t n = frees[round % 3];
        assert(write_record(bytes, 64) == 64); assert(fake_ep[FAKE_CDC_IN].pending);
        assert(write_record(bytes, 64-n) == (int)(64-n));
        assert(write_record(bytes, 64) == (int)n); assert(write_record(bytes, 64) == 0);
        compare_delivery();
    }
    assert(expected_size == 90*128);
    save_bytes("prefix-positive.bin", expected, expected_size);
    save_bytes("prefix-host-completed.bin", fake_host, fake_host_size);
    save_bytes("prefix-submitted.bin", fake_submitted, fake_submitted_size); save_submissions();
    fake_finish();
    puts("PASS positive-prefix byte equality across 90 FIFO wraps; exact short writes 1/7/63; zero pressure; delayed host completion");
}
static void test_gates(void) {
    uint8_t bytes[64]; memset(bytes, 0x5a, sizeof(bytes));
    for (unsigned require = 0; require < 2; ++require) {
        begin(require);
        assert(mt_usb_cdc_write(NULL, bytes, 1) == -1);
        assert(mt_usb_cdc_write(&f.adapter, NULL, 1) == -1);
        assert(mt_usb_cdc_write(&f.adapter, bytes, 65) == -1);
        assert(mt_usb_cdc_write(&f.adapter, NULL, 0) == 0);
        assert(write_record(bytes, 64) == (require ? 0 : 64));
        fake_line_state(1); assert(write_record(bytes, 64) == 64);
        fake_line_state(0); size_t room = tud_cdc_n_write_available(0);
        assert(write_record(bytes, 1) == (require || !room ? 0 : 1));
        fake_line_state(2); assert(!fake_last_dtr && fake_last_rts); assert(tud_cdc_n_write_available(0) == room);
        fake_line_state(2); assert(tud_cdc_n_write_available(0) == room);
        fake_line_state(3);
        f.stable = 0; assert(write_record(bytes, 1) == 0); f.stable = 1;
        fake_signal(DCD_EVENT_SUSPEND, false); assert(tud_suspended()); assert(write_record(bytes, 1) == 0);
        fake_pump(); assert(write_record(bytes, 1) == 0); assert(f.adapter.state == MT_USB_OPEN && f.adapter.epoch == 1);
        fake_signal(DCD_EVENT_RESUME, true); compare_delivery(); fake_finish();
    }
    puts("PASS DTR optional/required via EP0 ACK, repeated/RTS-only states, stable gate, queued suspend and resume preserve ordered bytes");
}
static void test_config_splits(void) {
    unsigned variants = 0;
    uint8_t bytes[MT_WIRE_FRAME_MAX]; mt_wire_message m = configuration(11); size_t n = encode(&m, bytes); assert(n == 46);
    for (size_t split = 0; split <= n; ++split) {
        begin(0); handshake(11);
        if (split) assert(input_bytes(bytes, split, 1) == MT_CONTROL_OK);
        if (split < n) { assert(f.control.link.config_id == 0); assert(input_bytes(bytes+split, n-split, 2) == MT_CONTROL_OK); }
        assert(f.control.link.config_id == 1 && f.control.session.next_sequence == 3); pump_adapter(3); fake_finish(); ++variants;
    }
    for (unsigned packet = 1; packet <= 64; packet = packet == 1 ? 7 : 64) {
        begin(0); handshake(11);
        for (size_t pos = 0; pos < n; ) {
            size_t take = n-pos < packet ? n-pos : packet; assert(input_bytes(bytes+pos, take, (uint32_t)pos+1) == MT_CONTROL_OK); pos += take;
        }
        assert(f.control.link.config_id == 1); pump_adapter(60); fake_finish(); ++variants;
        if (packet == 64) break;
    }
    printf("PASS CONFIG every split 0..46 plus 1/7/64 packetization (%u variants)\n", variants);
}
/* Parser/adapter plumbing stress only; does not adopt host pipelining policy. */
static void test_multiple_frames(void) {
    begin(0); handshake(11);
    mt_wire_message caps = command(MT_CMD_GET_CAPS, 11, 2), status = command(MT_CMD_GET_STATUS, 11, 3);
    uint8_t a[MT_WIRE_FRAME_MAX], b[MT_WIRE_FRAME_MAX], both[64];
    size_t an = encode(&caps, a), bn = encode(&status, b); assert(an+bn == 60);
    memcpy(both, a, an); memcpy(both+an, b, bn);
    assert(input_bytes(both, an+bn, 1) == MT_CONTROL_OK);
    assert(f.control.session.next_sequence == 4); pump_adapter(2);
    mt_wire_message out[4]; assert(decode_host(out, 4) == 2);
    assert(out[0].type == 130 && out[0].request == 2 && out[1].type == 134 && out[1].request == 3);
    fake_finish(); puts("PASS two complete commands in one USB read dispatch once each with ordered replies");
}
static void test_recovery_suffix(void) {
    begin(0); handshake(11);
    mt_wire_message bad = command(MT_CMD_GET_STATUS, 11, 3), fresh = hello(12);
    uint8_t b[MT_WIRE_FRAME_MAX], h[MT_WIRE_FRAME_MAX], combined[64];
    size_t bn = encode(&bad, b), hn = encode(&fresh, h); assert(bn < 64 && hn > 64-bn);
    memcpy(combined, b, bn); memcpy(combined+bn, h, 64-bn);
    unsigned stops = f.stops;
    assert(input_bytes(combined, 64, 1) == MT_CONTROL_RECOVERY);
    assert(f.adapter.offset == bn && f.adapter.length == 64); assert(!f.control.ready && f.stops == stops+1);
    assert(mt_usb_cdc_step(&f.adapter, 2) == MT_CONTROL_IGNORED);
    assert(f.rx.stream.used == 64-bn && f.adapter.offset == 64 && f.stops == stops+1);
    assert(input_bytes(h+64-bn, hn-(64-bn), 3) == MT_CONTROL_OK);
    assert(f.control.ready && f.control.session.nonce == 12 && f.control.session.next_sequence == 2);
    fake_drain(); mt_wire_message outputs[4]; assert(decode_host(outputs, 4) == 1);
    assert(outputs[0].type == 129 && outputs[0].session == 12 && outputs[0].sequence == 1);
    fake_finish();
    puts("PASS recovery stops exactly at delimiter, preserves carry suffix, fresh HELLO completes next read without prefix replay");
}
static void test_timeouts(void) {
    const uint32_t starts[] = {0, UINT32_MAX-499u};
    for (unsigned i = 0; i < 2; ++i) {
        begin(0); uint8_t byte = 1; uint32_t t = starts[i];
        assert(input_bytes(&byte, 1, t) == MT_CONTROL_IGNORED);
        assert(mt_usb_cdc_step(&f.adapter, t+999u) == MT_CONTROL_IGNORED && f.rx.timeouts == 0);
        assert(mt_usb_cdc_step(&f.adapter, t+1000u) == MT_CONTROL_IGNORED && f.rx.timeouts == 1);
        assert(f.rx.stream.used == 0 && f.rx.stream.discarding); fake_finish();
        begin(0);
        for (unsigned j = 0; j < 6; ++j) assert(input_bytes(&byte, 1, t+j*500u) == MT_CONTROL_IGNORED);
        assert(mt_usb_cdc_step(&f.adapter, t+2999u) == MT_CONTROL_IGNORED && f.rx.timeouts == 0);
        assert(mt_usb_cdc_step(&f.adapter, t+3000u) == MT_CONTROL_IGNORED && f.rx.timeouts == 1); fake_finish();
    }
    begin(0); handshake(11); uint8_t bytes[64] = {0};
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64); assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
    mt_wire_message request = command(MT_CMD_GET_STATUS, 11, 2);
    assert(input(&request, 1) == MT_CONTROL_OK); assert(f.control.tx.next_sequence == 2);
    assert(mt_usb_cdc_step(&f.adapter, 1000) == MT_CONTROL_OK);
    assert(mt_usb_cdc_step(&f.adapter, 1001) == MT_CONTROL_RECOVERY && !f.control.ready && f.control.tx.fault == MT_TX_TIMEOUT);
    fake_finish(); puts("PASS idle RX 1000/3000 ms thresholds including uint32 wrap, and TX timeout still polled under full FIFO pressure");
}
static void test_soft_order(void) {
    begin(0); handshake(11);
    mt_wire_message data = {0}; data.type = 145; data.payload_length = 512;
    for (size_t i = 0; i < 512; ++i) data.payload[i] = (uint8_t)(i*17+3);
    put32(data.payload, 1); put32(data.payload+4, 1);
    assert(mt_tx_enqueue(&f.control.tx, &data, 1) == MT_TX_OK);
    assert(mt_usb_cdc_step(&f.adapter, 1) == MT_CONTROL_OK);
    assert(mt_usb_cdc_step(&f.adapter, 2) == MT_CONTROL_OK);
    assert(f.control.tx.offset == 128 && fake_ep[FAKE_CDC_IN].pending && !tud_cdc_n_write_available(0));
    size_t old_offset = f.control.tx.offset; assert(mt_usb_cdc_soft_pause(&f.adapter) == MT_CONTROL_RECOVERY);
    assert(f.control.tx.offset == old_offset && !tud_cdc_n_write_available(0));
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_OK);
    mt_wire_message same = hello(11); assert(input(&same, 3) == MT_CONTROL_IGNORED && !f.control.ready);
    mt_wire_message fresh = hello(12); assert(input(&fresh, 4) == MT_CONTROL_OK && f.control.ready);
    pump_adapter(5); mt_wire_message outputs[4]; assert(decode_host(outputs, 4) == 2);
    assert(outputs[0].type == 145 && outputs[0].session == 11 && outputs[0].sequence == 2);
    assert(outputs[0].payload_length == 512 && !memcmp(outputs[0].payload, data.payload, 512));
    assert(outputs[1].type == 129 && outputs[1].session == 12 && outputs[1].sequence == 1);
    fake_finish(); puts("PASS soft recovery retains EP + FIFO + partial TX frame, same nonce ignored, complete old frame before fresh HELLO_ACK sequence 1");
}
static void test_soft_pause_rearm_observation(void) {
    begin(0); uint8_t bytes[64]; memset(bytes, 1, sizeof(bytes)); fake_out(bytes, 64, true);
    assert(!fake_ep[FAKE_CDC_OUT].pending && tud_cdc_n_available(0) == 64);
    fake_signal(DCD_EVENT_SUSPEND, false); f.stable = 0; size_t submissions = fake_record_count;
    assert(mt_usb_cdc_soft_pause(&f.adapter) == MT_CONTROL_RECOVERY);
    assert(fake_record_count == submissions && !fake_ep[FAKE_CDC_OUT].pending);
    assert(f.adapter.rx_flush_pending && f.adapter.state == MT_USB_SOFT_PAUSED);
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_IGNORED && fake_record_count == submissions);
    fake_signal(DCD_EVENT_RESUME, true); f.stable = 1;
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_OK);
    assert(tud_cdc_n_available(0) == 0 && fake_ep[FAKE_CDC_OUT].pending && !f.adapter.rx_flush_pending);
    puts("PASS soft_pause suspended/stable=false performs no DCD rearm; RX flush/rearm deferred until explicit stable resume");
    fake_pump(); fake_finish();
}

static void test_gate_between_commands(void) {
    begin(0); handshake(11);
    mt_wire_message first = command(MT_CMD_STOP, 11, 2), second = command(MT_CMD_GET_STATUS, 11, 3);
    first.payload_length = 4;
    uint8_t a[MT_WIRE_FRAME_MAX], b[MT_WIRE_FRAME_MAX], joined[64];
    size_t an = encode(&first, a), bn = encode(&second, b); assert(an+bn == 64);
    memcpy(joined, a, an); memcpy(joined+an, b, bn); f.stop_drops_gate = 1;
    assert(input_bytes(joined, 64, 1) == MT_CONTROL_IGNORED);
    assert(!f.stable && f.control.session.next_sequence == 3);
    assert(f.adapter.offset == an && f.adapter.length == 64);
    size_t count = fake_record_count;
    assert(mt_usb_cdc_step(&f.adapter, 2) == MT_CONTROL_OK);
    assert(f.adapter.offset == an && f.control.session.next_sequence == 3 && fake_record_count == count);
    fake_finish(); puts("PASS external gate closes during first command: second frame in same 64-byte read is not dispatched");
}
static void tx_drain(void) {
    for (unsigned i = 0; i < 100; ++i) {
        mt_tx_event ev;
        mt_tx_result r = mt_tx_poll(&f.control.tx, i+10u, mt_usb_cdc_write, &f.adapter, &ev);
        assert(r == MT_TX_IDLE || r == MT_TX_PROGRESS || r == MT_TX_BLOCKED);
        fake_drain(); if (!f.control.tx.count) return;
    }
    assert(!"TX drain exhausted");
}
static void test_tx_deadlines(void) {
    uint8_t bytes[64]; memset(bytes, 0x77, sizeof(bytes));
    const uint32_t times[] = {0, UINT32_MAX-1000u};
    for (unsigned wrap = 0; wrap < 2; ++wrap) {
        begin(0); handshake(11); uint32_t t = times[wrap];
        mt_wire_message data = {0}; data.type = 145; data.payload_length = 512; put32(data.payload, 1); put32(data.payload+4, 1);
        assert(mt_tx_enqueue(&f.control.tx, &data, t) == MT_TX_OK);
        assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
        for (unsigned i = 0; i < 6; ++i) {
            assert(mt_usb_cdc_write(&f.adapter, bytes, 63) == 63);
            mt_tx_event ev; assert(mt_tx_poll(&f.control.tx, t+i*500u, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_PROGRESS);
            assert(ev.accepted == 1);
            fake_complete_in(FAKE_CDC_IN, true);
        }
        mt_tx_event ev; assert(mt_tx_poll(&f.control.tx, t+2999u, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_PROGRESS);
        assert(mt_tx_poll(&f.control.tx, t+3000u, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_TIMEOUT);
        fake_finish();
    }
    begin(0); handshake(11);
    mt_wire_message ack = {0}, status = {0}; ack.type = 133; ack.payload_length = 12; ack.request = 2;
    status.type = 134; status.payload_length = 24;
    assert(mt_tx_stop(&f.control.tx, 0, 0, NULL, &ack, &status, 0) == MT_TX_OK);
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
    assert(mt_usb_cdc_write(&f.adapter, bytes, 63) == 63);
    mt_tx_event ev; assert(mt_tx_poll(&f.control.tx, 999, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_PROGRESS && ev.accepted == 1);
    assert(mt_tx_poll(&f.control.tx, 1000, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_TIMEOUT);
    fake_finish(); puts("PASS TX 3000 ms frame deadline despite recent short progress, including wrap; STOP 1000 ms deadline despite last-ms progress");
}
static void test_terminal_commit(void) {
    for (unsigned committed = 0; committed < 2; ++committed) {
        begin(0); handshake(11); uint8_t bytes[64]; memset(bytes, 0xa5, sizeof(bytes));
        assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
        assert(mt_usb_cdc_write(&f.adapter, bytes, 64-committed) == (int)(64-committed));
        mt_wire_message end = {0}, reply = {0}, abort_m = {0}, ack = {0}, status = {0};
        end.type = 146; end.payload_length = 16; put32(end.payload, 1); put32(end.payload+4, 1);
        reply.type = 130; reply.payload_length = 30; reply.request = 2;
        abort_m.type = 147; abort_m.payload_length = 12; put32(abort_m.payload, 1); put32(abort_m.payload+4, 1);
        ack.type = 133; ack.payload_length = 12; ack.request = 3; put32(ack.payload, 1); put32(ack.payload+4, 1);
        status.type = 134; status.payload_length = 24; put32(status.payload+4, 1); put32(status.payload+8, 1);
        assert(mt_tx_enqueue(&f.control.tx, &end, 1) == MT_TX_OK);
        mt_tx_event ev = {0};
        assert(mt_tx_poll(&f.control.tx, 1, mt_usb_cdc_write, &f.adapter, &ev) == (committed ? MT_TX_PROGRESS : MT_TX_BLOCKED));
        assert(f.control.tx.next_sequence == 2+committed && fake_host_size == 0);
        assert(mt_tx_terminal_committed(&f.control.tx, 1, 1) == (committed ? 146 : 0));
        if (committed) assert(ev.accepted == 1 && (ev.flags & MT_TX_STARTED) && !(ev.flags & MT_TX_FINISHED));
        assert(mt_tx_enqueue(&f.control.tx, &reply, 2) == MT_TX_OK);
        assert(mt_tx_stop(&f.control.tx, 1, 1, &abort_m, &ack, &status, 2) == MT_TX_OK);
        tx_drain(); size_t filler = 128-committed;
        assert(fake_host_size >= filler); memmove(fake_host, fake_host+filler, fake_host_size-filler); fake_host_size -= filler;
        mt_wire_message out[8]; size_t n = decode_host(out, 8); assert(n == 4);
        assert(out[0].type == (committed ? 146 : 130) && out[1].type == (committed ? 130 : 147));
        assert(out[2].type == 133 && out[3].type == 134);
        for (size_t i = 0; i < n; ++i) assert(out[i].sequence == i+2 && out[i].session == 11);
        fake_finish();
    }
    begin(0); handshake(11); uint8_t prefix[64] = {0};
    assert(mt_usb_cdc_write(&f.adapter, prefix, 64) == 64);
    mt_wire_message end = {0}; end.type = 146; end.payload_length = 16; put32(end.payload, 1); put32(end.payload+4, 1);
    assert(mt_tx_enqueue(&f.control.tx, &end, 1) == MT_TX_OK);
    mt_tx_event ev; assert(mt_tx_poll(&f.control.tx, 1, mt_usb_cdc_write, &f.adapter, &ev) == MT_TX_PROGRESS);
    assert((ev.flags & MT_TX_FINISHED) && !fake_host_size && mt_tx_terminal_committed(&f.control.tx, 1, 1) == 146);
    fake_finish();
    puts("PASS zero does not consume sequence; first END byte commits before host receipt; STOP before/after commit yields exactly one terminal and ordered old reply before STOP_ACK");
}
static void drop_gate(void) { f.stable = 0; }
static void queue_reset(void) { f.stable = 0; fake_bus_reset(false); }
static void test_hard_quarantine(void) {
    begin(0); handshake(11);
    mt_wire_message config = configuration(11); assert(input(&config, 1) == MT_CONTROL_OK); pump_adapter(2);
    mt_wire_message arm = command(MT_CMD_ARM, 11, 3); arm.payload_length = 4; put32(arm.payload, 1);
    assert(input(&arm, 10) == MT_CONTROL_OK); pump_adapter(11); assert(f.control.hardware_running && f.starts == 1);
    uint8_t rejected[] = {1,0}; assert(input_bytes(rejected, sizeof(rejected), 20) == MT_CONTROL_OK); assert(f.rx.rejected_frames == 1);
    fake_drain(); fake_clear_logs();
    mt_wire_message data = {0}; data.type = 145; data.payload_length = 512; put32(data.payload, 1); put32(data.payload+4, 1);
    assert(mt_tx_enqueue(&f.control.tx, &data, 21) == MT_TX_OK);
    assert(mt_usb_cdc_step(&f.adapter, 21) == MT_CONTROL_OK); assert(mt_usb_cdc_step(&f.adapter, 22) == MT_CONTROL_OK);
    assert(f.control.tx.offset == 128 && !tud_cdc_n_write_available(0));
    uint8_t partial[] = {3,4,5}; assert(input_bytes(partial, 3, 23) == MT_CONTROL_OK); assert(f.rx.stream.used == 3);
    uint8_t bytes[64]; memset(bytes, 7, sizeof(bytes)); fake_out(bytes, 64, true);
    fake_on_out_submit = drop_gate;
    assert(mt_usb_cdc_step(&f.adapter, 24) == MT_CONTROL_IGNORED);
    assert(!f.stable && f.adapter.offset == 0 && f.adapter.length == 64 && f.rx.stream.used == 3);
    f.stop_ok = 0; size_t submissions = fake_record_count; unsigned stops = f.stops;
    assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY);
    assert(f.adapter.state == MT_USB_QUARANTINED && f.adapter.epoch == 2);
    assert(!f.control.ready && !f.control.session.active && f.control.session.nonce == 11);
    assert(f.control.hardware_running && f.stops == stops+1 && f.control.link.config_id == 1);
    assert(!f.control.tx.count && !f.control.tx.offset && f.control.tx.session == 0);
    assert(!f.adapter.length && !f.adapter.offset && !f.rx.stream.used && f.rx.rejected_frames == 1);
    assert(tud_cdc_n_write_available(0) == 64 && fake_record_count == submissions);
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 0);
    assert(mt_usb_cdc_open_epoch(&f.adapter, 2) == MT_CONTROL_IGNORED);
    unsigned umounts = fake_umounts; fake_bus_reset(true); assert(fake_umounts == umounts);
    fake_configuration(1); assert(tud_cdc_n_available(0) == 0 && fake_host_size == 0);
    f.stable = 1; assert(mt_usb_cdc_step(&f.adapter, 25) == MT_CONTROL_IGNORED);
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_ARGUMENT);
    assert(mt_usb_cdc_open_epoch(&f.adapter, 2) == MT_CONTROL_OK);
    mt_wire_message old = hello(11); assert(input(&old, 26) == MT_CONTROL_IGNORED && f.control.hardware_running);
    assert(input(&config, 27) == MT_CONTROL_IGNORED && f.starts == 1);
    mt_wire_message fresh = hello(12); assert(input(&fresh, 28) == MT_CONTROL_RECOVERY && f.control.hardware_running && !f.control.ready);
    f.stop_ok = 1; fresh = hello(13); assert(input(&fresh, 29) == MT_CONTROL_OK && !f.control.hardware_running && f.control.ready);
    fake_drain(); mt_wire_message out[4]; assert(decode_host(out, 4) == 1);
    assert(out[0].type == 129 && out[0].sequence == 1 && out[0].session == 13);
    assert(f.rx.rejected_frames == 1 && f.control.link.config_id == 0 && f.starts == 1);
    fake_finish(); puts("PASS explicit foreground hard quarantine clears carry/framing/TX, retains diagnostics and failed hardware Stop; real BUS_RESET/reconfigure isolates old bytes; fresh HELLO retries Stop");
}
static void test_positive_during_loss(void) {
    uint8_t bytes[64]; memset(bytes, 0x55, sizeof(bytes));
    begin(0); fake_on_in_submit = queue_reset;
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
    assert(!f.stable && !fake_host_size && !fake_ep[FAKE_CDC_IN].pending);
    size_t count = fake_record_count;
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 0 && fake_record_count == count);
    assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY);
    fake_pump(); fake_configuration(1); f.stable = 1;
    assert(mt_usb_cdc_open_epoch(&f.adapter, 2) == MT_CONTROL_OK);
    assert(tud_cdc_n_write_available(0) == 64 && !fake_host_size); fake_finish();
    begin(0); fake_reject_next_in = true;
    assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64);
    assert(!fake_records[fake_record_count-1].accepted && !fake_host_size && !fake_ep[FAKE_CDC_IN].pending);
    /* Submission failure detection is outside this adapter. Explicitly close the external test gate. */
    f.stable = 0; assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 0);
    assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY); fake_finish();
    puts("PASS real positive copy retained if fake reset occurs during submission or DCD rejects submission; no host-delivery claim; external failure detection remains required");
}
static void test_explicit_hard_boundaries(void) {
    for (unsigned unplug = 0; unplug < 2; ++unplug) {
        begin(0); handshake(11); uint8_t bytes[64]; memset(bytes, 0xab, sizeof(bytes));
        assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 64); f.stable = 0;
        assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY);
        unsigned unmounts = fake_umounts;
        if (unplug) {
            dcd_edpt_close_all(0); fake_signal(DCD_EVENT_UNPLUGGED, true);
            assert(fake_umounts == unmounts+1 && !tud_mounted()); fake_bus_reset(true);
        } else {
            fake_configuration(0); assert(fake_umounts == unmounts+1 && !tud_mounted());
        }
        assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY);
        assert(mt_usb_cdc_hard_loss(&f.adapter, 3) == MT_CONTROL_RECOVERY);
        fake_configuration(1); f.stable = 1;
        assert(mt_usb_cdc_write(&f.adapter, bytes, 64) == 0 && f.adapter.state == MT_USB_QUARANTINED);
        assert(mt_usb_cdc_open_epoch(&f.adapter, 2) == MT_CONTROL_ARGUMENT);
        assert(mt_usb_cdc_open_epoch(&f.adapter, 3) == MT_CONTROL_OK);
        mt_wire_message m = hello(12); assert(input(&m, 1) == MT_CONTROL_OK); fake_drain();
        mt_wire_message out[4]; assert(decode_host(out, 4) == 1 && out[0].type == 129 && out[0].session == 12);
        fake_finish();
    }
    begin(0); uint8_t bytes[64]; memset(bytes, 1, sizeof(bytes)); fake_out(bytes, 64, true);
    assert(mt_usb_cdc_soft_pause(&f.adapter) == MT_CONTROL_RECOVERY);
    fake_on_out_submit = queue_reset;
    assert(mt_usb_cdc_open_epoch(&f.adapter, 1) == MT_CONTROL_IGNORED);
    assert(f.adapter.state == MT_USB_SOFT_PAUSED && f.adapter.rx_flush_pending && !f.stable);
    assert(mt_usb_cdc_hard_loss(&f.adapter, 2) == MT_CONTROL_RECOVERY && !f.adapter.rx_flush_pending);
    fake_pump(); fake_finish();
    puts("PASS explicit deconfigure/unplug foreground quarantine and repeated hard epochs require latest explicit open; fake reset during deferred RX rearm leaves gate closed");
}
int main(void) {
    printf("INFO TinyUSB native dedicated-HWFIFO=%u\n", (unsigned)CFG_TUD_EDPT_DEDICATED_HWFIFO);
    test_prefix(); test_gates(); test_config_splits(); test_multiple_frames(); test_recovery_suffix(); test_timeouts(); test_soft_order(); test_soft_pause_rearm_observation(); test_gate_between_commands(); test_tx_deadlines(); test_terminal_commit(); test_hard_quarantine(); test_positive_during_loss(); test_explicit_hard_boundaries();
    puts("SCOPED ADAPTER TESTS PASSED; production DCD/IRQ reset latch, queue overflow and epoch barrier NOT IMPLEMENTED/NOT TESTED"); return 0;
}
