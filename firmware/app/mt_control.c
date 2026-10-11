#include "mt_control.h"
#include <string.h>

static void put16(uint8_t *p, uint16_t x) {
    p[0] = (uint8_t)x; p[1] = (uint8_t)(x >> 8);
}
static void put32(uint8_t *p, uint32_t x) {
    unsigned i;
    for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(x >> (8u * i));
}
static void message(mt_wire_message *m, uint8_t type, uint16_t length,
                    uint32_t request) {
    memset(m, 0, sizeof *m);
    m->type = type; m->payload_length = length; m->request = request;
}
static uint8_t state(const mt_control *c) {
    if (!c->ready || c->link.phase == MT_LINK_FAILED ||
        c->link.phase == MT_LINK_RELEASE_EXPIRED) return 5;
    if (c->link.phase == MT_LINK_WAIT_RELEASE) return 6;
    if (c->link.phase != MT_LINK_IDLE) return 4;
    if (!c->link.armed) return 0;
    return (uint8_t)c->link.acquisition.state;
}
static void fill_status(mt_wire_message *m, const mt_control_status *s,
                        uint32_t request) {
    message(m, 134, 24, request);
    m->payload[0] = s->state; m->payload[1] = s->reason;
    put32(m->payload + 4, s->generation);
    put32(m->payload + 8, s->window);
    put32(m->payload + 20, s->dropped);
}
static mt_control_status status(const mt_control *c) {
    mt_control_status s;
    s.state = state(c);
    s.reason = c->link.phase == MT_LINK_RELEASE_EXPIRED ? 3u :
               (s.state == 5u ? 5u : 0u);
    s.generation = c->link.generation; s.window = c->link.last_window;
    s.dropped = c->link.dropped_windows;
    return s;
}
mt_control_result mt_control_recover(mt_control *c) {
    if (!c) return MT_CONTROL_ARGUMENT;
    c->ready = 0;
    mt_session_require_hello(&c->session);
    (void)mt_acq_stop(&c->link.acquisition);
    c->link.armed = 0;
    c->state_count = 0;
    /* Always retry the hardware stop, including after a failed start. */
    if (c->io.stop(c->io.context)) c->hardware_running = 0;
    return MT_CONTROL_RECOVERY;
}
static mt_control_result remember_state(mt_control *c) {
    mt_control_status s = status(c);
    if (s.state == c->last_state) return MT_CONTROL_OK;
    if (c->state_count == MT_CONTROL_STATUS_MAX) return mt_control_recover(c);
    c->states[c->state_count++] = s;
    c->last_state = s.state;
    return MT_CONTROL_OK;
}
static int flush_states(mt_control *c, uint32_t now);
static int enqueue(mt_control *c, const mt_wire_message *m, uint32_t now) {
    if ((m->type == 131u || m->type == 132u || m->type == 134u) &&
        !flush_states(c, now)) {
        (void)mt_control_recover(c);
        return 0;
    }
    if (mt_tx_enqueue(&c->tx, m, now) == MT_TX_OK) return 1;
    (void)mt_control_recover(c);
    return 0;
}
/* Replies must never overtake an older state snapshot held outside TX. */
static int flush_states(mt_control *c, uint32_t now) {
    while (c->state_count) {
        mt_wire_message m;
        mt_tx_result result;
        fill_status(&m, &c->states[0], 0);
        result = mt_tx_enqueue(&c->tx, &m, now);
        if (result == MT_TX_FULL) return 0;
        if (result != MT_TX_OK) {
            (void)mt_control_recover(c);
            return 0;
        }
        --c->state_count;
        memmove(c->states, c->states + 1, c->state_count * sizeof c->states[0]);
    }
    return 1;
}
static mt_control_result error(mt_control *c, uint8_t type, uint8_t code,
                               uint32_t request, uint32_t now) {
    mt_wire_message m;
    message(&m, 255, 8, request);
    m.payload[0] = type; m.payload[1] = code;
    put32(m.payload + 4, c->link.generation);
    return enqueue(c, &m, now) ? MT_CONTROL_OK : MT_CONTROL_RECOVERY;
}
mt_control_result mt_control_init(mt_control *c, const mt_control_io *io) {
    if (!c || !io || !io->start || !io->stop || !io->profile_ready)
        return MT_CONTROL_ARGUMENT;
    memset(c, 0, sizeof *c); c->io = *io;
    mt_session_init(&c->session); mt_tx_init(&c->tx);
    mt_capture_link_init(&c->link, &c->tx);
    return MT_CONTROL_OK;
}
static int idle(const mt_control *c) {
    return c->link.phase == MT_LINK_IDLE && !c->link.armed &&
           !c->link.active_window && !c->tx.stopping;
}
static mt_control_result hello(mt_control *c, uint32_t now) {
    mt_wire_message m;
    if (!c->io.stop(c->io.context) || !c->io.profile_ready(c->io.context))
        return mt_control_recover(c);
    c->hardware_running = 0;
    message(&m, 129, 16, 1);
    m.payload[1] = 1; m.payload[2] = 1; m.payload[3] = 12;
    put16(m.payload + 4, 512); put16(m.payload + 6, 2048);
    put32(m.payload + 8, c->io.firmware_build);
    put32(m.payload + 12, 61); /* analog, Auto, Normal, Single, pretrigger */
    if (mt_tx_hello(&c->tx, c->session.nonce, &m, now) != MT_TX_OK)
        return mt_control_recover(c);
    mt_capture_link_init(&c->link, &c->tx);
    c->state_count = 0; c->last_state = 0; c->ready = 1;
    return MT_CONTROL_OK;
}
static mt_control_result configure(mt_control *c, const mt_wire_message *input,
                                   const mt_config *config, uint32_t now) {
    const mt_command_support support = {7, 1, 3, 1};
    mt_acq_config_t acq = {0};
    mt_wire_message m;
    uint32_t id = c->link.config_id + 1u;
    if (!idle(c)) return error(c, input->type, 2, input->request, now);
    if (!mt_command_config_valid(config, &support))
        return error(c, input->type, 1, input->request, now);
    if (id == 0u || id == UINT32_MAX) return mt_control_recover(c);
    acq.profile_id = config->profile; acq.mode = config->mode;
    acq.source = config->source; acq.edge = config->edge;
    acq.level_code = config->level_code;
    acq.pretrigger_samples = config->pretrigger_samples;
    acq.auto_timeout_ms = config->auto_timeout_ms;
    message(&m, 131, 20, input->request);
    put32(m.payload, id); memcpy(m.payload + 4, input->payload, 16);
    if (!enqueue(c, &m, now)) return MT_CONTROL_RECOVERY;
    if (mt_capture_link_configure(&c->link, &acq, id) != MT_LINK_OK)
        return mt_control_recover(c);
    return MT_CONTROL_OK;
}
static mt_control_result arm(mt_control *c, const mt_wire_message *input,
                             uint32_t config_id, uint32_t now) {
    mt_wire_message m;
    if (!idle(c)) return error(c, input->type, 2, input->request, now);
    if (!config_id || config_id != c->link.config_id)
        return error(c, input->type, 3, input->request, now);
    if (!c->io.profile_ready(c->io.context)) return mt_control_recover(c);
    message(&m, 132, 8, input->request);
    put32(m.payload, input->request); put32(m.payload + 4, config_id);
    if (!enqueue(c, &m, now)) return MT_CONTROL_RECOVERY;
    if (mt_capture_link_arm(&c->link, config_id, input->request, now) != MT_LINK_OK)
        return mt_control_recover(c);
    if (!c->io.start(c->io.context, now)) return mt_control_recover(c);
    c->hardware_running = 1;
    return remember_state(c);
}
mt_control_result mt_control_receive(mt_control *c,
                                     const mt_wire_message *input, uint32_t now) {
    mt_session_event event;
    mt_command command;
    mt_command_result parsed;
    mt_wire_message m;
    mt_link_result result;
    int releases_current;
    if (!c || !input) return MT_CONTROL_ARGUMENT;
    event = mt_session_receive(&c->session, input);
    if (event == MT_SESSION_NEW_HELLO) return hello(c, now);
    if (event == MT_SESSION_DUPLICATE || event == MT_SESSION_WRONG_SESSION ||
        event == MT_SESSION_NEED_HELLO || event == MT_SESSION_INVALID_HELLO)
        return MT_CONTROL_IGNORED;
    if (event != MT_SESSION_ACCEPT || !c->ready) return mt_control_recover(c);
    parsed = mt_command_parse(input, &command);
    if (mt_capture_link_expire_release(&c->link, now) &&
        remember_state(c) != MT_CONTROL_OK) return mt_control_recover(c);
    /* Stop replaces queued observational states itself. Other replies must
     * not overtake a newly observed timeout, including a late RELEASE_ACK. */
    if (c->link.phase == MT_LINK_RELEASE_EXPIRED &&
        (parsed != MT_COMMAND_OK || command.type != MT_CMD_STOP ||
         command.value.generation != c->link.generation) &&
        !flush_states(c, now)) return mt_control_recover(c);
    if (parsed != MT_COMMAND_OK)
        return error(c, input->type, parsed == MT_COMMAND_UNKNOWN_TYPE ? 5 : 6,
                     input->request, now);
    /* A second outstanding Stop cannot admit another ordinary reply safely. */
    if (c->link.phase == MT_LINK_STOPPING) return mt_control_recover(c);
    switch (command.type) {
    case MT_CMD_GET_CAPS:
        message(&m, 130, 30, input->request);
        m.payload[0] = 1; put16(m.payload + 2, 1);
        put32(m.payload + 4, 72000000); put16(m.payload + 8, 71);
        put16(m.payload + 10, 19); put32(m.payload + 12, 12000000);
        put16(m.payload + 16, 143); put16(m.payload + 18, 2048);
        m.payload[20] = 3; m.payload[21] = 7;
        m.payload[22] = 1; m.payload[23] = 3;
        put16(m.payload + 26, 512); put16(m.payload + 28, 1024);
        return enqueue(c, &m, now) ? MT_CONTROL_OK : MT_CONTROL_RECOVERY;
    case MT_CMD_CONFIG: return configure(c, input, &command.value.config, now);
    case MT_CMD_ARM: return arm(c, input, command.value.config_id, now);
    case MT_CMD_STOP:
        if (command.value.generation != c->link.generation)
            return error(c, input->type, 4, input->request, now);
        if (!c->io.stop(c->io.context)) return mt_control_recover(c);
        c->hardware_running = 0;
        result = mt_capture_link_stop(&c->link, command.value.generation,
                                      input->request, now);
        if (result != MT_LINK_OK) return mt_control_recover(c);
        /* Stop already queues terminal/ACK/STOPPED in contractual order.
         * Pending observational states are stale and must not follow that ACK. */
        c->state_count = 0; c->last_state = 0;
        return MT_CONTROL_OK;
    case MT_CMD_GET_STATUS: {
        mt_control_status current = status(c);
        fill_status(&m, &current, input->request);
        return enqueue(c, &m, now) ? MT_CONTROL_OK : MT_CONTROL_RECOVERY;
    }
    case MT_CMD_RELEASE_WINDOW:
        releases_current = c->link.phase == MT_LINK_WAIT_RELEASE &&
            command.value.release.generation == c->link.generation &&
            command.value.release.window_id == c->link.active_window &&
            c->link.have_tombstone &&
            command.value.release.samples_crc32 == c->link.tomb_crc;
        if (releases_current && !flush_states(c, now))
            return mt_control_recover(c);
        result = mt_capture_link_release(&c->link,
            command.value.release.generation, command.value.release.window_id,
            command.value.release.samples_crc32, input->request, now);
        if (result == MT_LINK_BAD_RELEASE)
            return error(c, input->type, 7, input->request, now);
        if (result == MT_LINK_BUSY)
            return error(c, input->type, 2, input->request, now);
        if (result != MT_LINK_OK) return mt_control_recover(c);
        if (releases_current && c->link.armed && !c->hardware_running) {
            if (!c->io.profile_ready(c->io.context) ||
                !c->io.start(c->io.context, now)) return mt_control_recover(c);
            c->hardware_running = 1;
        }
        return remember_state(c);
    default: return mt_control_recover(c);
    }
}
mt_control_result mt_control_sample(mt_control *c, uint16_t code, uint32_t now) {
    mt_link_result result;
    if (!c) return MT_CONTROL_ARGUMENT;
    if (!c->ready || !c->hardware_running) return MT_CONTROL_IGNORED;
    result = mt_capture_link_sample(&c->link, code, now);
    if (result == MT_LINK_WINDOW_READY) {
        if (!c->io.stop(c->io.context)) return mt_control_recover(c);
        c->hardware_running = 0;
    } else if (result == MT_LINK_RECOVERY || result == MT_LINK_ARGUMENT)
        return mt_control_recover(c);
    return remember_state(c);
}
mt_control_result mt_control_poll(mt_control *c, uint32_t now,
                                  mt_tx_write_fn write, void *context) {
    mt_link_result result;
    if (!c || !write) return MT_CONTROL_ARGUMENT;
    if (!c->ready) return MT_CONTROL_IGNORED;
    if (c->state_count && c->link.phase != MT_LINK_STOPPING) {
        mt_wire_message m;
        mt_tx_result queued;
        fill_status(&m, &c->states[0], 0);
        queued = mt_tx_enqueue(&c->tx, &m, now);
        if (queued == MT_TX_OK) {
            --c->state_count;
            memmove(c->states, c->states + 1, c->state_count * sizeof c->states[0]);
        } else if (queued != MT_TX_FULL) return mt_control_recover(c);
    }
    result = mt_capture_link_poll(&c->link, now, write, context);
    if (result != MT_LINK_OK && result != MT_LINK_TX_BLOCKED)
        return mt_control_recover(c);
    if (c->link.phase == MT_LINK_STOPPING) return MT_CONTROL_OK;
    return remember_state(c);
}
