#include "mt_stream.h"

void mt_stream_init(mt_stream *s) {
    if (!s) return;
    s->used = 0;
    s->first_ms = 0;
    s->last_ms = 0;
    s->discarding = 0;
    s->last_decode = MT_WIRE_OK;
}

mt_stream_event mt_stream_poll(mt_stream *s, uint32_t now_ms) {
    if (!s) return MT_STREAM_ARGUMENT;
    if (!s->discarding && s->used != 0u &&
        ((uint32_t)(now_ms - s->last_ms) >= MT_STREAM_INTERBYTE_MS ||
         (uint32_t)(now_ms - s->first_ms) >= MT_STREAM_FRAME_MS)) {
        s->used = 0;
        s->discarding = 1;
        return MT_STREAM_TIMEOUT;
    }
    return MT_STREAM_NONE;
}

mt_stream_event mt_stream_feed(mt_stream *s, uint8_t byte,
                              uint32_t now_ms, mt_wire_message *message) {
    mt_stream_event event;
    if (!s || !message) return MT_STREAM_ARGUMENT;
    event = mt_stream_poll(s, now_ms);
    if (s->discarding) {
        if (byte == 0u) s->discarding = 0;
        return event;
    }
    if (byte == 0u) {
        if (s->used == 0u) return MT_STREAM_NONE;
        s->last_decode = mt_wire_decode(s->bytes, s->used, message);
        s->used = 0;
        return s->last_decode == MT_WIRE_OK
            ? MT_STREAM_MESSAGE : MT_STREAM_REJECTED;
    }
    if (s->used == sizeof s->bytes) {
        s->used = 0;
        s->discarding = 1;
        return MT_STREAM_OVERFLOW;
    }
    if (s->used == 0u) s->first_ms = now_ms;
    s->bytes[s->used++] = byte;
    s->last_ms = now_ms;
    return MT_STREAM_NONE;
}
