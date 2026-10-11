#ifndef MT_BOARD_INPUTS_H
#define MT_BOARD_INPUTS_H
#include "mt_clock_startup.h"
#include "mt_usb_descriptors.h"
typedef struct {
    uint32_t verified_hse_hz, clock_poll_budget, usb_irq_priority, firmware_build;
    mt_clock_hse_mode hse_mode;
    unsigned require_dtr, verified_fixed_pullup;
    mt_usb_identity usb;
} mt_board_inputs;
/* Required immutable board-specific definition; intentionally no default.
 * Verified hardware HSE/mode, authorized USB IDs, actual power budget and
 * stable unique physical serial are caller obligations, not proved by fields.
 * This minimal main supports only a verified fixed pull-up topology (value1).
 * Controlled pull-up requires a separate actual board hook and new review.
 * The definition and every pointed string must remain readable for startup. */
extern const mt_board_inputs mt_board;
#endif
