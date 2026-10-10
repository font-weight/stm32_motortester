#include "mt_capture_link.h"
#include <string.h>

static void put16(uint8_t *p, uint16_t x) {
    p[0] = (uint8_t)x; p[1] = (uint8_t)(x >> 8);
}
static void put32(uint8_t *p, uint32_t x) {
    unsigned i; for (i = 0; i < 4u; ++i) p[i] = (uint8_t)(x >> (8u * i));
}
static uint32_t crc_byte(uint32_t crc, uint8_t byte) {
    unsigned bit;
    crc ^= byte;
    for (bit = 0; bit < 8u; ++bit)
        crc = (crc >> 1) ^ ((crc & 1u) ? UINT32_C(0xedb88320) : 0u);
    return crc;
}
static void init_message(mt_wire_message *m, uint8_t type, uint16_t length,
                         uint32_t request) {
    memset(m, 0, sizeof *m);
    m->type = type; m->payload_length = length; m->request = request;
}
static int valid(const mt_capture_link *l) { return l && l->tx; }
static mt_link_result fail(mt_capture_link *l) {
    (void)mt_acq_stop(&l->acquisition);
    l->armed = 0; l->phase = MT_LINK_FAILED;
    return MT_LINK_RECOVERY;
}
void mt_capture_link_init(mt_capture_link *l, mt_tx *tx) {
    if (!l) return;
    memset(l, 0, sizeof *l); l->tx = tx;
    l->session = tx ? tx->session : 0;
    (void)mt_acq_init(&l->acquisition);
}
mt_link_result mt_capture_link_configure(mt_capture_link *l,
                                         const mt_acq_config_t *cfg,
                                         uint32_t config_id) {
    if (!valid(l) || !cfg || config_id == 0u || config_id == UINT32_MAX)
        return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (l->armed || l->active_window || l->phase != MT_LINK_IDLE)
        return MT_LINK_BUSY;
    if (mt_acq_configure(&l->acquisition, cfg) != MT_ACQ_OK)
        return MT_LINK_ARGUMENT;
    l->config_id = config_id;
    return MT_LINK_OK;
}
mt_link_result mt_capture_link_arm(mt_capture_link *l, uint32_t config_id,
                                   uint32_t generation, uint32_t now) {
    if (!valid(l) || generation == 0u || generation == UINT32_MAX)
        return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (config_id == 0u || config_id != l->config_id) return MT_LINK_STALE;
    if (l->armed || l->active_window || l->phase != MT_LINK_IDLE)
        return MT_LINK_BUSY;
    if (mt_acq_arm(&l->acquisition, now) != MT_ACQ_OK) return MT_LINK_BUSY;
    l->generation = generation; l->armed = 1;
    return MT_LINK_OK;
}
mt_link_result mt_capture_link_sample(mt_capture_link *l, uint16_t code,
                                      uint32_t time_ms) {
    mt_acq_result_t r;
    if (!valid(l)) return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (!l->armed || l->phase != MT_LINK_IDLE) return MT_LINK_BUSY;
    r = mt_acq_push(&l->acquisition, code, time_ms);
    if (r == MT_ACQ_TRIGGERED) {
        if (l->last_window >= UINT32_MAX - 1u) return fail(l);
        l->active_window = ++l->last_window;
        return MT_LINK_TRIGGERED;
    }
    if (r == MT_ACQ_WINDOW_READY) {
        if (l->active_window == 0u) return fail(l);
        l->offset = 0; l->crc_running = UINT32_C(0xffffffff);
        l->phase = MT_LINK_BEGIN;
        return MT_LINK_WINDOW_READY;
    }
    if (r < MT_ACQ_OK) return fail(l);
    return MT_LINK_OK;
}
static mt_link_result queue_bulk(mt_capture_link *l, uint32_t now) {
    mt_wire_message m;
    mt_tx_result tx;
    uint32_t crc = l->crc_running;
    uint16_t count = 0, i, code;
    if (l->phase == MT_LINK_BEGIN) {
        mt_acq_window_t w;
        if (mt_acq_window(&l->acquisition, &w) != MT_ACQ_OK) return fail(l);
        init_message(&m, 144, 40, 0);
        put32(m.payload, l->generation); put32(m.payload + 4, l->active_window);
        put32(m.payload + 8, l->config_id);
        put16(m.payload + 12, 1); put16(m.payload + 14, w.sample_count);
        put16(m.payload + 16, w.pretrigger_samples);
        put16(m.payload + 18, w.trigger_index);
        m.payload[20] = (uint8_t)w.trigger_kind;
        m.payload[21] = l->acquisition.config.source;
        m.payload[22] = l->acquisition.config.edge;
        put16(m.payload + 24, l->acquisition.config.level_code);
        put16(m.payload + 26, w.quality_flags);
        put32(m.payload + 28, UINT32_C(72000000));
        put16(m.payload + 32, 71); put16(m.payload + 34, 19);
    } else if (l->phase == MT_LINK_DATA) {
        count = (uint16_t)(MT_ACQ_WINDOW_SAMPLES - l->offset);
        if (count > 250u) count = 250;
        init_message(&m, 145, (uint16_t)(12u + 2u * count), 0);
        put32(m.payload, l->generation); put32(m.payload + 4, l->active_window);
        put16(m.payload + 8, l->offset); put16(m.payload + 10, count);
        for (i = 0; i < count; ++i) {
            if (mt_acq_read(&l->acquisition, (uint16_t)(l->offset + i), &code)
                != MT_ACQ_OK) return fail(l);
            put16(m.payload + 12u + 2u * i, code);
            crc = crc_byte(crc, (uint8_t)code);
            crc = crc_byte(crc, (uint8_t)(code >> 8));
        }
    } else if (l->phase == MT_LINK_END) {
        init_message(&m, 146, 16, 0);
        put32(m.payload, l->generation); put32(m.payload + 4, l->active_window);
        put16(m.payload + 8, MT_ACQ_WINDOW_SAMPLES);
        put32(m.payload + 12, l->window_crc);
    } else return MT_LINK_OK;
    tx = mt_tx_enqueue(l->tx, &m, now);
    if (tx == MT_TX_FULL) return MT_LINK_TX_BLOCKED;
    if (tx != MT_TX_OK) return fail(l);
    if (l->phase == MT_LINK_BEGIN) l->phase = MT_LINK_DATA;
    else if (l->phase == MT_LINK_DATA) {
        l->crc_running = crc;
        l->offset = (uint16_t)(l->offset + count);
        if (l->offset == MT_ACQ_WINDOW_SAMPLES) {
            l->window_crc = crc ^ UINT32_C(0xffffffff);
            l->phase = MT_LINK_END;
        }
    } else l->phase = MT_LINK_END_QUEUED;
    return MT_LINK_OK;
}
mt_link_result mt_capture_link_poll(mt_capture_link *l, uint32_t now,
                                    mt_tx_write_fn write, void *context) {
    mt_link_result queued;
    mt_tx_result r;
    mt_tx_event e;
    if (!valid(l) || !write) return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (l->phase == MT_LINK_WAIT_RELEASE &&
        (uint32_t)(now - l->release_since_ms) >= UINT32_C(5000))
        return fail(l);
    queued = queue_bulk(l, now);
    if (queued == MT_LINK_RECOVERY) return queued;
    r = mt_tx_poll(l->tx, now, write, context, &e);
    if (r == MT_TX_PROGRESS) {
        if (e.session == l->session && e.type == 146u &&
            e.generation == l->generation &&
            e.window_id == l->active_window) {
            if ((e.flags & MT_TX_STARTED) != 0u) {
                l->tomb_generation = l->generation;
                l->tomb_window = l->active_window;
                l->tomb_crc = l->window_crc;
                l->have_tombstone = 1;
            }
            if ((e.flags & MT_TX_FINISHED) != 0u &&
                l->phase != MT_LINK_STOPPING) {
                l->phase = MT_LINK_WAIT_RELEASE;
                l->release_since_ms = now;
            }
        }
        if (e.session == l->session &&
            (e.flags & MT_TX_STOP_FINISHED) != 0u) {
            l->phase = MT_LINK_IDLE; l->active_window = 0; l->armed = 0;
        }
        return MT_LINK_OK;
    }
    if (r == MT_TX_IDLE) return queued;
    if (r == MT_TX_BLOCKED) return MT_LINK_TX_BLOCKED;
    return fail(l);
}
mt_link_result mt_capture_link_stop(mt_capture_link *l, uint32_t generation,
                                    uint32_t request, uint32_t now) {
    mt_wire_message abort_message, ack, status;
    mt_tx_result r;
    unsigned cancelled;
    if (!valid(l) || request < 2u || request == UINT32_MAX)
        return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (generation != l->generation) return MT_LINK_STALE;
    if (l->phase == MT_LINK_STOPPING) return MT_LINK_BUSY;
    cancelled = l->active_window != 0u &&
        mt_tx_terminal_committed(l->tx, generation, l->active_window) == 0u;
    if (cancelled && l->dropped_windows >= UINT32_MAX - 1u) return fail(l);
    init_message(&abort_message, 147, 12, 0);
    put32(abort_message.payload, generation);
    put32(abort_message.payload + 4, l->active_window);
    put16(abort_message.payload + 8, 1); /* STOP */
    init_message(&ack, 133, 12, request);
    put32(ack.payload, generation); put32(ack.payload + 4, l->last_window);
    init_message(&status, 134, 24, 0);
    put32(status.payload + 4, generation);
    put32(status.payload + 8, l->last_window);
    put32(status.payload + 20, l->dropped_windows + cancelled);
    (void)mt_acq_stop(&l->acquisition);
    l->armed = 0;
    r = mt_tx_stop(l->tx, generation, l->active_window,
                   &abort_message, &ack, &status, now);
    if (r != MT_TX_OK) return fail(l);
    l->dropped_windows += cancelled;
    l->phase = MT_LINK_STOPPING;
    return MT_LINK_OK;
}
mt_link_result mt_capture_link_release(mt_capture_link *l, uint32_t generation,
                                       uint32_t window, uint32_t crc,
                                       uint32_t request, uint32_t now) {
    mt_wire_message ack;
    mt_tx_result r;
    int current;
    if (!valid(l) || request < 2u || request == UINT32_MAX)
        return MT_LINK_ARGUMENT;
    if (l->phase == MT_LINK_FAILED) return MT_LINK_RECOVERY;
    if (!l->session || l->session != l->tx->session) return fail(l);
    if (l->phase == MT_LINK_STOPPING) return MT_LINK_BUSY;
    if (l->phase == MT_LINK_WAIT_RELEASE &&
        (uint32_t)(now - l->release_since_ms) >= UINT32_C(5000))
        return fail(l);
    if (!l->have_tombstone || generation != l->tomb_generation ||
        window != l->tomb_window || crc != l->tomb_crc)
        return MT_LINK_BAD_RELEASE;
    current = l->phase == MT_LINK_WAIT_RELEASE &&
              generation == l->generation && window == l->active_window;
    /* A committed but not fully transmitted END is not releasable yet. */
    if (!current && window == l->active_window &&
        generation == l->generation) return MT_LINK_BUSY;
    init_message(&ack, 135, 8, request);
    put32(ack.payload, generation); put32(ack.payload + 4, window);
    r = mt_tx_enqueue(l->tx, &ack, now);
    if (r != MT_TX_OK) return fail(l);
    if (current) {
        if (mt_acq_release(&l->acquisition, now) != MT_ACQ_OK) return fail(l);
        l->active_window = 0; l->phase = MT_LINK_IDLE;
        if (l->acquisition.config.mode == MT_ACQ_SINGLE) l->armed = 0;
    }
    return MT_LINK_OK;
}
