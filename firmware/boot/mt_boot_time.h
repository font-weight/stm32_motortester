#ifndef MT_BOOT_TIME_H
#define MT_BOOT_TIME_H
#include "mt_clock_startup.h"
#include "mt_timebase.h"

typedef enum {
    MT_BOOT_TIME_OK, MT_BOOT_TIME_ARGUMENT, MT_BOOT_TIME_BUSY,
    MT_BOOT_TIME_CLOCK, MT_BOOT_TIME_NOT_RESET, MT_BOOT_TIME_GATE,
    MT_BOOT_TIME_RESET_ASSERT, MT_BOOT_TIME_RESET_RELEASE,
    MT_BOOT_TIME_INIT, MT_BOOT_TIME_FINAL_CLOCK
} mt_boot_time_result;
/* Match the existing CLK/TIME ABI in every translation unit using this API. */
_Static_assert(sizeof(mt_clock_hse_mode) == 4u && sizeof(mt_clock_result) == 4u &&
    sizeof(mt_clock_recovery) == 4u && sizeof(mt_timebase_state) == 4u &&
    sizeof(mt_timebase_fault) == 4u && sizeof(mt_boot_time_result) == 4u,
    "BOOT/TIME requires 32-bit enums; use -fno-short-enums");
typedef struct {
    mt_boot_time_result cause;
    unsigned attempted, active, ready;
} mt_boot_time;
/* Genuine-reset, privileged foreground only; IRQs remain masked by the caller.
 * Zero-initialize boot and timer once; do not reinitialize to bypass failure.
 * startup is the unchanged successful mt_clock_apply receipt, not a forged plan.
 * No live TIM2 consumers, other MMIO writers or callback reentry. Objects and
 * callback context are nonoverlapping and outlive consumers. Uses startup's
 * exact MMIO callbacks/context; never invents an independent clock frequency.
 * After argument checks, every attempt is terminal until system reset. A
 * failure may leave TIM2 enabled/owned: preserve storage and admit no consumers.
 * No cleanup, interrupt admission, board identity or timing proof is supplied.
 */
mt_boot_time_result mt_boot_time_start(mt_boot_time *boot,
    const mt_clock_startup *startup, mt_timebase *timer);
#endif
