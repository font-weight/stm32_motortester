#include "../../firmware/protocol/mt_session.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>

static mt_wire_message message(uint8_t type, uint64_t nonce,
                               uint32_t seq, uint32_t request) {
    mt_wire_message m;
    memset(&m, 0, sizeof m);
    m.type = type; m.session = nonce; m.sequence = seq; m.request = request;
    return m;
}
static mt_wire_message hello(uint64_t nonce) {
    mt_wire_message m = message(MT_CMD_HELLO, 0, 1, 1);
    unsigned i;
    m.payload_length = 8;
    for (i = 0; i < 8u; ++i) m.payload[i] = (uint8_t)(nonce >> (8u * i));
    return m;
}
static void establish(mt_session *s, uint64_t nonce) {
    mt_wire_message h = hello(nonce);
    mt_session_init(s);
    assert(mt_session_receive(s, &h) == MT_SESSION_NEW_HELLO);
    assert(s->active && s->nonce == nonce && s->next_sequence == 2u);
}
static void test_hello(void) {
    mt_session s, before;
    mt_wire_message h;
    memset(&s, 0, sizeof s);
    mt_session_init(&s);
    h = hello(0);
    memcpy(&before, &s, sizeof s);
    assert(mt_session_receive(&s, &h) == MT_SESSION_INVALID_HELLO);
    assert(memcmp(&s, &before, sizeof s) == 0);
    h = hello(10); h.session = 10;
    assert(mt_session_receive(&s, &h) == MT_SESSION_INVALID_HELLO);
    h.session = 0; h.sequence = 2;
    assert(mt_session_receive(&s, &h) == MT_SESSION_INVALID_HELLO);
    h.sequence = 1; h.request = 2;
    assert(mt_session_receive(&s, &h) == MT_SESSION_INVALID_HELLO);
    h.request = 1; h.payload_length = 7;
    assert(mt_session_receive(&s, &h) == MT_SESSION_INVALID_HELLO);
    h = hello(10);
    assert(mt_session_receive(&s, &h) == MT_SESSION_NEW_HELLO);
    assert(mt_session_receive(&s, &h) == MT_SESSION_DUPLICATE);
    mt_session_require_hello(&s);
    assert(!s.active && s.nonce == 10u);
    assert(mt_session_receive(&s, &h) == MT_SESSION_DUPLICATE);
    assert(!s.active);
    h = hello(11);
    assert(mt_session_receive(&s, &h) == MT_SESSION_NEW_HELLO);
    assert(s.active && s.last_request == 1u && !s.have_previous);
}
static void test_sequence_and_duplicate(void) {
    mt_session s;
    mt_wire_message m, previous;
    establish(&s, 10);
    m = message(MT_CMD_GET_CAPS, 10, 2, 2);
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    assert(mt_session_receive(&s, &m) == MT_SESSION_DUPLICATE);
    m.payload[100] = 255; /* Unused bytes are not wire identity. */
    assert(mt_session_receive(&s, &m) == MT_SESSION_DUPLICATE);
    previous = m;
    m = message(MT_CMD_GET_STATUS, 10, 3, 99); /* Request gaps permitted. */
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    assert(mt_session_receive(&s, &previous) == MT_SESSION_RECOVERY);
    assert(!s.active);
    assert(mt_session_receive(&s, &m) == MT_SESSION_NEED_HELLO);

    establish(&s, 20);
    m = message(MT_CMD_GET_CAPS, 20, 3, 2);
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
    establish(&s, 20);
    m = message(MT_CMD_GET_CAPS, 20, 2, 1);
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
    establish(&s, 20);
    m = message(MT_CMD_GET_CAPS, 20, 2, 2);
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    m.type = MT_CMD_GET_STATUS;
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
}
static void test_payload_failures_and_session(void) {
    mt_session s;
    mt_wire_message m;
    mt_command parsed;
    establish(&s, 10);
    m = message(MT_CMD_CONFIG, 9, 200, 100);
    assert(mt_session_receive(&s, &m) == MT_SESSION_WRONG_SESSION);
    assert(s.active && s.next_sequence == 2u);
    m = message(MT_CMD_CONFIG, 10, 2, 2); /* Invalid zero-length CONFIG. */
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    assert(mt_command_parse(&m, &parsed) == MT_COMMAND_LENGTH);
    assert(mt_session_receive(&s, &m) == MT_SESSION_DUPLICATE);
    m = message(127, 10, 3, 3); /* Unknown type still consumes valid header. */
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    assert(mt_command_parse(&m, &parsed) == MT_COMMAND_UNKNOWN_TYPE);
    m = message(MT_CMD_GET_STATUS, 10, 4, 4);
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    m = message(129, 10, 5, 5); /* Device response in host direction. */
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
    assert(!s.active);
}
static void test_conflicting_payload(void) {
    mt_session s;
    mt_wire_message m;
    establish(&s, 10);
    m = message(MT_CMD_ARM, 10, 2, 2);
    m.payload_length = 4; m.payload[0] = 1;
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    m.payload[0] = 2;
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
    establish(&s, 10);
    m = message(MT_CMD_ARM, 10, 2, 2);
    m.payload_length = 4;
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    m.payload_length = 3;
    assert(mt_session_receive(&s, &m) == MT_SESSION_RECOVERY);
}
static void test_exhaustion(void) {
    mt_session s;
    mt_wire_message m;
    establish(&s, 10);
    /* Construct a reachable late-session state without billions of messages. */
    s.next_sequence = UINT32_MAX - 1u;
    s.last_request = UINT32_MAX - 2u;
    m = message(MT_CMD_GET_STATUS, 10, UINT32_MAX - 1u, UINT32_MAX - 1u);
    assert(mt_session_receive(&s, &m) == MT_SESSION_ACCEPT);
    assert(s.next_sequence == UINT32_MAX);
    assert(mt_session_receive(&s, &m) == MT_SESSION_DUPLICATE);
    m.sequence = UINT32_MAX;
    assert(mt_session_receive(&s, &m) == MT_SESSION_EXHAUSTED);
    assert(!s.active);
    establish(&s, 11);
    m = message(MT_CMD_GET_STATUS, 11, 2, UINT32_MAX);
    assert(mt_session_receive(&s, &m) == MT_SESSION_EXHAUSTED);
}
static void test_arguments(void) {
    mt_session s, saved;
    mt_wire_message m = hello(10);
    memset(&s, 0, sizeof s);
    mt_session_init(&s);
    memcpy(&saved, &s, sizeof s);
    mt_session_init(NULL); mt_session_require_hello(NULL);
    assert(mt_session_receive(NULL, &m) == MT_SESSION_ARGUMENT);
    assert(mt_session_receive(&s, NULL) == MT_SESSION_ARGUMENT);
    m.payload_length = MT_WIRE_PAYLOAD_MAX + 1u;
    assert(mt_session_receive(&s, &m) == MT_SESSION_ARGUMENT);
    assert(memcmp(&s, &saved, sizeof s) == 0);
}
int main(void) {
    test_hello(); test_sequence_and_duplicate();
    test_payload_failures_and_session(); test_conflicting_payload();
    test_exhaustion(); test_arguments();
    printf("PASS author session tests; sizeof(mt_session)=%zu\n", sizeof(mt_session));
    return 0;
}
