#ifndef MT_COMMAND_H
#define MT_COMMAND_H
#include "mt_wire.h"

enum {
    MT_CMD_HELLO = 1, MT_CMD_GET_CAPS = 2, MT_CMD_CONFIG = 3,
    MT_CMD_ARM = 4, MT_CMD_STOP = 5, MT_CMD_GET_STATUS = 6,
    MT_CMD_RELEASE_WINDOW = 7
};

typedef struct {
    uint16_t profile;
    uint8_t mode;
    uint8_t source;
    uint8_t edge;
    uint16_t level_code;
    uint16_t pretrigger_samples;
    uint32_t auto_timeout_ms;
} mt_config;

/* These masks describe actually implemented features, never planned ones.
 * Only profile 1 is represented here. This is NOT a wire CAPS payload. */
typedef struct {
    uint8_t mode_bits;   /* bit 0 Auto, bit 1 Normal, bit 2 Single */
    uint8_t source_bits; /* bit 0 analog, bit 1 external */
    uint8_t edge_bits;   /* bit 0 rising, bit 1 falling */
    uint8_t pretrigger;  /* 0: only 0 samples; 1: 0, 512 or 1024 */
} mt_command_support;

typedef struct {
    uint8_t type;
    union {
        uint64_t nonce;
        mt_config config;
        uint32_t config_id;
        uint32_t generation;
        struct {
            uint32_t generation;
            uint32_t window_id;
            uint32_t samples_crc32;
        } release;
    } value;
} mt_command;

typedef enum {
    MT_COMMAND_OK = 0,
    MT_COMMAND_ARGUMENT,
    MT_COMMAND_UNKNOWN_TYPE,
    MT_COMMAND_WRONG_DIRECTION,
    MT_COMMAND_LENGTH,
    MT_COMMAND_RESERVED,
    MT_COMMAND_INVALID_HELLO
} mt_command_result;

/* Parse a HOST command from an already decoded byte-codec message.
 * No session/sequence/request/state check is performed here. Caller owns order.
 * Successful HELLO payload parsing does NOT validate its special header.
 * Output remains unchanged on failure; input/output must not overlap.
 * CONFIG semantic range/support checks are a separate function below.
 * IDs (including zero) are parsed unchanged; state logic decides stale/busy. */
mt_command_result mt_command_parse(const mt_wire_message *message,
                                   mt_command *command);

/* Pure CONFIG semantic validation against actually implemented profile 1.
 * Returns 1 only for a valid combination, 0 otherwise (including NULL).
 * Call only after the dispatcher has verified session/order/state.
 * This function does not configure hardware or allocate a config ID. */
int mt_command_config_valid(const mt_config *config,
                            const mt_command_support *support);
#endif
