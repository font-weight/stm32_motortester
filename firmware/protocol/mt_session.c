#include "mt_session.h"
#include <string.h>

void mt_session_init(mt_session *s) {
    if (!s) return;
    s->nonce = 0;
    s->next_sequence = 0;
    s->last_request = 0;
    s->active = 0;
    s->have_previous = 0;
}

void mt_session_require_hello(mt_session *s) {
    if (!s) return;
    s->active = 0;
    s->have_previous = 0;
    s->next_sequence = 0;
    s->last_request = 0;
}

static int same_message(const mt_wire_message *a, const mt_wire_message *b) {
    return a->type == b->type && a->session == b->session &&
        a->sequence == b->sequence && a->request == b->request &&
        a->payload_length == b->payload_length &&
        memcmp(a->payload, b->payload, a->payload_length) == 0;
}

static void remember(mt_wire_message *to, const mt_wire_message *from) {
    to->type = from->type;
    to->session = from->session;
    to->sequence = from->sequence;
    to->request = from->request;
    to->payload_length = from->payload_length;
    memcpy(to->payload, from->payload, from->payload_length);
}

mt_session_event mt_session_receive(mt_session *s, const mt_wire_message *m) {
    mt_command command;
    mt_command_result parsed;
    if (!s || !m || m->payload_length > MT_WIRE_PAYLOAD_MAX)
        return MT_SESSION_ARGUMENT;

    /* Parsing here is pure. Only direction and HELLO validity affect RX state;
     * ordinary payload errors must still consume an otherwise valid header. */
    parsed = mt_command_parse(m, &command);
    if (parsed == MT_COMMAND_WRONG_DIRECTION) {
        mt_session_require_hello(s);
        return MT_SESSION_RECOVERY;
    }
    if (m->type == MT_CMD_HELLO) {
        if (m->session != 0u || m->sequence != 1u || m->request != 1u ||
            parsed != MT_COMMAND_OK) return MT_SESSION_INVALID_HELLO;
        if (command.value.nonce == s->nonce) return MT_SESSION_DUPLICATE;
        s->nonce = command.value.nonce;
        s->next_sequence = 2;
        s->last_request = 1;
        s->active = 1;
        s->have_previous = 0;
        return MT_SESSION_NEW_HELLO;
    }
    if (!s->active) return MT_SESSION_NEED_HELLO;
    if (m->session != s->nonce) return MT_SESSION_WRONG_SESSION;

    if (s->have_previous && m->sequence == s->previous.sequence) {
        if (same_message(m, &s->previous)) return MT_SESSION_DUPLICATE;
        mt_session_require_hello(s);
        return MT_SESSION_RECOVERY;
    }
    if (m->sequence == UINT32_MAX || m->request == UINT32_MAX) {
        mt_session_require_hello(s);
        return MT_SESSION_EXHAUSTED;
    }
    if (m->sequence != s->next_sequence || m->request < 2u ||
        m->request <= s->last_request) {
        mt_session_require_hello(s);
        return MT_SESSION_RECOVERY;
    }
    remember(&s->previous, m);
    s->have_previous = 1;
    s->next_sequence = m->sequence + 1u;
    s->last_request = m->request;
    return MT_SESSION_ACCEPT;
}
