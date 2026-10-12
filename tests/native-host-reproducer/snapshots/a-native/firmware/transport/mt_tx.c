#include "mt_tx.h"
#include <string.h>

static uint32_t read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static int base_valid(const mt_wire_message *m) {
    return m && m->session == 0u && m->sequence == 0u &&
           m->payload_length <= MT_WIRE_PAYLOAD_MAX;
}
static int shape(const mt_wire_message *m, uint8_t type, size_t length,
                 int reply) {
    return base_valid(m) && m->type == type && m->payload_length == length &&
           (reply ? m->request != 0u : m->request == 0u);
}
static int is_reply(const mt_wire_message *m) {
    return m->request != 0u;
}
static int regular_valid(const mt_wire_message *m) {
    if (!base_valid(m)) return 0;
    if (m->type >= 144u && m->type <= 147u &&
        (m->payload_length < 8u || read32(m->payload) == 0u ||
         read32(m->payload + 4) == 0u)) return 0;
    switch (m->type) {
    case 130: return shape(m, 130, 30, 1);
    case 131: return shape(m, 131, 20, 1);
    case 132: return shape(m, 132, 8, 1);
    case 134: return m->payload_length == 24u;
    case 135: return shape(m, 135, 8, 1);
    case 144: return shape(m, 144, 40, 0);
    case 145: return m->request == 0u && m->payload_length >= 14u &&
                     (m->payload_length & 1u) == 0u;
    case 146: return shape(m, 146, 16, 0);
    case 147: return shape(m, 147, 12, 0);
    case 255: return shape(m, 255, 8, 1);
    default: return 0;
    }
}
static void append(mt_tx *t, const mt_wire_message *m) {
    mt_tx_slot *s = &t->slots[t->count++];
    s->message = *m;
    s->message.session = t->session;
    s->reply = (unsigned)is_reply(m);
}
static void head_timer(mt_tx *t, uint32_t now) {
    t->head_ms = now;
    t->progress_ms = now;
}
static mt_tx_result latch(mt_tx *t, mt_tx_result fault) {
    t->fault = fault;
    return fault;
}
void mt_tx_init(mt_tx *t) {
    if (t) memset(t, 0, sizeof *t);
}
mt_tx_result mt_tx_hello(mt_tx *t, uint64_t nonce,
                        const mt_wire_message *ack, uint32_t now) {
    if (!t || nonce == 0u || nonce == t->session ||
        !shape(ack, 129, 16, 1) || ack->request != 1u)
        return MT_TX_ARGUMENT;
    /* frame bytes stay untouched when its prefix has already left this object. */
    t->count = t->offset != 0u ? 1u : 0u;
    if (t->count == 0u) t->length = 0;
    t->session = nonce;
    t->next_sequence = 1;
    t->stopping = 0;
    t->terminal_type = 0;
    t->terminal_generation = 0;
    t->terminal_window = 0;
    t->fault = MT_TX_OK;
    head_timer(t, now);
    append(t, ack);
    return MT_TX_OK;
}
mt_tx_result mt_tx_enqueue(mt_tx *t, const mt_wire_message *m, uint32_t now) {
    size_t limit;
    if (!t || !regular_valid(m)) return MT_TX_ARGUMENT;
    if (t->fault != MT_TX_OK) return MT_TX_FAULT;
    if (t->session == 0u) return MT_TX_NOT_NEGOTIATED;
    if (t->stopping) return MT_TX_DRAINING;
    limit = is_reply(m) ? MT_TX_NORMAL_LIMIT : MT_TX_BULK_LIMIT;
    if (t->count >= limit) return MT_TX_FULL;
    if (t->count == 0u) head_timer(t, now);
    append(t, m);
    return MT_TX_OK;
}
uint8_t mt_tx_terminal_committed(const mt_tx *t, uint32_t gen, uint32_t win) {
    if (!t || win == 0u || t->terminal_generation != gen ||
        t->terminal_window != win) return 0;
    return t->terminal_type;
}
mt_tx_window_phase mt_tx_window_status(const mt_tx *t, uint32_t gen,
                                      uint32_t win) {
    size_t i;
    uint8_t terminal;
    if (!t || win == 0u) return MT_TX_NO_WINDOW;
    terminal = mt_tx_terminal_committed(t, gen, win);
    if (terminal == 146u) return MT_TX_WINDOW_END_COMMITTED;
    if (terminal == 147u) return MT_TX_WINDOW_ABORT_COMMITTED;
    for (i = 0; i < t->count; ++i) {
        const mt_wire_message *m = &t->slots[i].message;
        if (m->session == t->session && m->type >= 144u && m->type <= 147u &&
            read32(m->payload) == gen && read32(m->payload + 4) == win)
            return i == 0u && t->offset != 0u ? MT_TX_WINDOW_STARTED
                                            : MT_TX_WINDOW_QUEUED;
    }
    return MT_TX_WINDOW_NO_TX;
}
mt_tx_result mt_tx_stop(mt_tx *t, uint32_t gen, uint32_t win,
                       const mt_wire_message *abort_message,
                       const mt_wire_message *ack,
                       const mt_wire_message *status, uint32_t now) {
    size_t i, kept = 0;
    int terminal_needed;
    uint8_t retained_abort[12];
    int has_retained_abort = 0;
    const mt_wire_message *terminal = abort_message;
    if (!t || (win != 0u && gen == 0u) || !shape(ack, 133, 12, 1) || !shape(status, 134, 24, 0) ||
        read32(ack->payload) != gen || status->payload[0] != 0u ||
        status->payload[1] != 0u || read32(status->payload + 4) != gen ||
        read32(status->payload + 8) != read32(ack->payload + 4) ||
        (win != 0u && read32(ack->payload + 4) != win)) return MT_TX_ARGUMENT;
    if (t->fault != MT_TX_OK) return MT_TX_FAULT;
    if (t->session == 0u) return MT_TX_NOT_NEGOTIATED;
    if (t->stopping) return MT_TX_DRAINING;
    terminal_needed = win != 0u && mt_tx_terminal_committed(t, gen, win) == 0u;
    /* Preserve a pre-existing failure reason; move its unstarted ABORT behind
     * prior replies. A partially started ABORT is already terminal-committed. */
    if (terminal_needed) {
        for (i = 0; i < t->count; ++i) {
            const mt_wire_message *m = &t->slots[i].message;
            if (m->session == t->session && m->type == 147u &&
                read32(m->payload) == gen && read32(m->payload + 4) == win) {
                if (m->payload_length != sizeof retained_abort || m->request != 0u)
                    return MT_TX_ARGUMENT;
                memcpy(retained_abort, m->payload, sizeof retained_abort);
                has_retained_abort = 1;
                break;
            }
        }
        if (!has_retained_abort && (!shape(terminal, 147, 12, 0) ||
            read32(terminal->payload) != gen ||
            read32(terminal->payload + 4) != win)) return MT_TX_ARGUMENT;
    }
    /* Preflight preserves atomicity even if queue layout changes in future. */
    for (i = 0; i < t->count; ++i)
        if ((i == 0u && t->offset != 0u) || t->slots[i].reply) ++kept;
    if (kept + 2u + (size_t)terminal_needed > MT_TX_CAPACITY)
        return MT_TX_FULL;
    kept = 0;
    for (i = 0; i < t->count; ++i)
        if ((i == 0u && t->offset != 0u) || t->slots[i].reply)
            t->slots[kept++] = t->slots[i];
    t->count = kept;
    if (t->offset == 0u) {
        t->length = 0;
        head_timer(t, now);
    }
    if (terminal_needed) {
        if (has_retained_abort) {
            /* Only the 12 meaningful bytes survive compaction. Rebuild the
             * normalized message in its final slot, after atomic preflight. */
            mt_tx_slot *slot = &t->slots[t->count++];
            memset(&slot->message, 0, sizeof slot->message);
            slot->message.type = 147u;
            slot->message.session = t->session;
            slot->message.payload_length = (uint16_t)sizeof retained_abort;
            memcpy(slot->message.payload, retained_abort, sizeof retained_abort);
            slot->reply = 0;
        } else append(t, terminal);
    }
    append(t, ack);
    append(t, status);
    t->stopping = 1;
    t->stop_ms = now;
    return MT_TX_OK;
}
mt_tx_result mt_tx_poll(mt_tx *t, uint32_t now, mt_tx_write_fn write,
                       void *context, mt_tx_event *event) {
    mt_wire_message *m;
    mt_tx_event e = {0};
    size_t offered;
    int accepted;
    if (!t || !write || !event) return MT_TX_ARGUMENT;
    if (t->fault != MT_TX_OK) return MT_TX_FAULT;
    if (t->count == 0u) return MT_TX_IDLE;
    if ((uint32_t)(now - t->progress_ms) >= MT_TX_STALL_MS ||
        (uint32_t)(now - t->head_ms) >= MT_TX_FRAME_MS ||
        (t->stopping && (uint32_t)(now - t->stop_ms) >= MT_TX_STOP_MS))
        return latch(t, MT_TX_TIMEOUT);
    m = &t->slots[0].message;
    if (t->offset == 0u) {
        if (t->next_sequence == UINT32_MAX)
            return latch(t, MT_TX_SEQUENCE);
        /* A speculative number is encoded, but not consumed yet. */
        m->sequence = t->next_sequence;
        if (mt_wire_encode(m, t->frame, sizeof t->frame, &t->length) != MT_WIRE_OK)
            return latch(t, MT_TX_ARGUMENT);
    }
    offered = t->length - t->offset;
    if (offered > MT_TX_CHUNK_MAX) offered = MT_TX_CHUNK_MAX;
    accepted = write(context, t->frame + t->offset, offered);
    if (accepted < 0 || (size_t)accepted > offered) return latch(t, MT_TX_IO);
    if (accepted == 0) return MT_TX_BLOCKED;
    e.type = m->type;
    e.session = m->session;
    e.sequence = m->sequence;
    e.request = m->request;
    e.accepted = (size_t)accepted;
    if (m->type >= 144u && m->type <= 147u) {
        e.generation = read32(m->payload);
        e.window_id = read32(m->payload + 4);
    }
    if (t->offset == 0u) {
        e.flags |= MT_TX_STARTED;
        ++t->next_sequence;
        if (m->type == 146u || m->type == 147u) {
            t->terminal_type = m->type;
            t->terminal_generation = e.generation;
            t->terminal_window = e.window_id;
        }
    }
    t->offset += (size_t)accepted;
    t->progress_ms = now;
    if (t->offset == t->length) {
        e.flags |= MT_TX_FINISHED;
        --t->count;
        memmove(t->slots, t->slots + 1, t->count * sizeof t->slots[0]);
        t->offset = 0;
        t->length = 0;
        head_timer(t, now);
        if (t->stopping && e.type == 133u && e.session == t->session) {
            t->stopping = 0;
            e.flags |= MT_TX_STOP_FINISHED;
        }
    }
    *event = e;
    return MT_TX_PROGRESS;
}
