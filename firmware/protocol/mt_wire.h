#ifndef MT_WIRE_H
#define MT_WIRE_H
#include <stddef.h>
#include <stdint.h>
/* Legacy entry points remain wire 0.1. No USB or session state here. */
#define MT_WIRE_MAJOR 0u
#define MT_WIRE_MINOR 1u
#define MT_WIRE_HEADER 24u
#define MT_WIRE_PAYLOAD_MAX 512u
#define MT_WIRE_RAW_MAX (MT_WIRE_HEADER + MT_WIRE_PAYLOAD_MAX + 4u)
#define MT_WIRE_FRAME_MAX (MT_WIRE_RAW_MAX + MT_WIRE_RAW_MAX / 254u + 2u)
typedef enum {
    MT_WIRE_OK = 0, MT_WIRE_ARGUMENT, MT_WIRE_CAPACITY,
    MT_WIRE_FRAMING, MT_WIRE_LENGTH, MT_WIRE_CRC, MT_WIRE_VERSION,
    MT_WIRE_RESERVED
} mt_wire_result;
typedef struct {
    uint8_t type;
    uint64_t session;
    uint32_t sequence;
    uint32_t request;
    uint16_t payload_length;
    uint8_t payload[MT_WIRE_PAYLOAD_MAX];
} mt_wire_message;
/* CRC-32/ISO-HDLC: poly 0x04C11DB7 reflected, init/xorout 0xffffffff. */
uint32_t mt_wire_crc32(const uint8_t *bytes, size_t length);
/* Encode one message with trailing zero delimiter. Output capacity must be
 * MT_WIRE_FRAME_MAX; output, message and written must be pairwise non-overlapping objects.
 * On failure neither
 * output nor *written changes. Version/reserved fields are generated here. */
mt_wire_result mt_wire_encode(const mt_wire_message *message,
                             uint8_t *output, size_t capacity, size_t *written);
/* Decode exactly one COBS frame, EXCLUDING delimiter. No zero bytes allowed.
 * On failure *message stays unchanged. Caller owns stream accumulation,
 * timeout, session/sequence/request validation and message-type semantics. */
mt_wire_result mt_wire_decode(const uint8_t *frame, size_t length,
                             mt_wire_message *message);

/* Explicit envelope revision for isolated protocol prototypes. These functions
 * have the same bounds, non-overlap and unchanged-on-failure contracts above.
 * Decode accepts only the exact expected revision after checking length/CRC;
 * no fallback or negotiation occurs. Any revision pair is representable, but
 * this does not establish payload/state compatibility or enable acquisition.
 * Production legacy callers must continue to use the 0.1 entry points above. */
mt_wire_result mt_wire_encode_revision(const mt_wire_message *message,
                                      uint8_t major, uint8_t minor,
                                      uint8_t *output, size_t capacity,
                                      size_t *written);
mt_wire_result mt_wire_decode_revision(const uint8_t *frame, size_t length,
                                      uint8_t major, uint8_t minor,
                                      mt_wire_message *message);
#endif
