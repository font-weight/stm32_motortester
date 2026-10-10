#ifndef MT_STREAM_H
#define MT_STREAM_H
#include "mt_wire.h"

#define MT_STREAM_INTERBYTE_MS UINT32_C(1000)
#define MT_STREAM_FRAME_MS UINT32_C(3000)

typedef enum {
    MT_STREAM_NONE = 0,
    MT_STREAM_MESSAGE,
    MT_STREAM_REJECTED,
    MT_STREAM_OVERFLOW,
    MT_STREAM_TIMEOUT,
    MT_STREAM_ARGUMENT
} mt_stream_event;

/* Single-owner foreground object. Do not call from an IRQ or concurrently.
 * Storage is fixed; no heap. Fields are implementation-owned.
 * last_decode is meaningful only after MT_STREAM_REJECTED/MESSAGE. */
typedef struct {
    uint8_t bytes[MT_WIRE_FRAME_MAX - 1u];
    size_t used;
    uint32_t first_ms;
    uint32_t last_ms;
    unsigned discarding;
    mt_wire_result last_decode;
} mt_stream;

/* Initialize before first use and after an explicit transport reset.
 * This does not reset session/sequence/acquisition state. NULL is ignored. */
void mt_stream_init(mt_stream *stream);

/* now_ms is a wrapping uint32_t monotonic millisecond clock.
 * Feed/poll must be serviced with gaps < 2^31 ms and in clock order.
 * Poll regularly even when USB is idle. Threshold comparison is >=.
 * A timeout clears the candidate and discards until a zero delimiter. */
mt_stream_event mt_stream_poll(mt_stream *stream, uint32_t now_ms);

/* Consume exactly one byte. USB packet/read boundaries have no significance.
 * stream and message must be valid non-overlapping writable objects.
 * message is changed only when MT_STREAM_MESSAGE is returned.
 * On timeout, this byte is consumed as discarded data; a zero also resyncs.
 * A valid byte-codec message still needs direction/session/sequence/payload
 * validation by the dispatcher. This API does not send responses.
 * NULL arguments return ARGUMENT without consuming the byte/changing state. */
mt_stream_event mt_stream_feed(mt_stream *stream, uint8_t byte,
                              uint32_t now_ms, mt_wire_message *message);
#endif
