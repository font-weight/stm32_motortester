#ifndef MT_STREAM_DIAG_H
#define MT_STREAM_DIAG_H
#include <stdint.h>
#ifndef CFG_MT_STREAM_DIAGNOSTICS
#define CFG_MT_STREAM_DIAGNOSTICS 0
#endif
#if CFG_MT_STREAM_DIAGNOSTICS != 0 && CFG_MT_STREAM_DIAGNOSTICS != 1
#error "STREAM diagnostics must be 0 or 1"
#endif
/* Foreground-only, one attempted run per boot, no IRQ writers or new locks.
 * Raw modulo cycles and existing driver timestamps are observations, not WCET.
 * No debugger halt/live watch; each measured interval must be below 2^32 cycles.
 * The module cannot prove these external assumptions or physical clock accuracy.
 */
typedef struct {
    uint32_t magic, version, bytes, firmware_build, phase, flags, generation;
    uint32_t step_count, step_body_max_cycles, step_gap_max_cycles;
    uint32_t tx_count, tx_max_cycles;
    uint32_t poll_count, driver_observed_poll_gap_us, poll_gap_max_cycles;
    uint32_t copy_count, copy_max_us, queue_high_water;
    uint32_t first_runtime_result, first_local_error, first_acquisition_reason;
    uint32_t first_adc_fault, first_epoch;
    uint32_t final_runtime_result, final_core_state, final_epoch;
    uint32_t final_adc_state, final_adc_owned, final_hardware_off;
    uint32_t start_cycle, last_cycle, freeze_cycle;
    uint32_t nonce_low, nonce_high;
} mt_stream_diag_record;
_Static_assert(sizeof(mt_stream_diag_record) == 136u, "diagnostic ABI v1");
/* phase: 0 empty,1 armed,2 active,3 frozen,4 unavailable.
 * flags: bit0 DWT unsupported/unusable, bit1 count saturation, bit2 first fault, bit3 replacement HELLO (partial final step), bit4 observed invalid cycle source.
 * Nonzero phase alone never certifies timing validity. */
#if CFG_MT_STREAM_DIAGNOSTICS
extern volatile mt_stream_diag_record mt_stream_diagnostics;
int mt_stream_diag_init(uint32_t (*read32)(void *,uint32_t),
    void (*write32)(void *,uint32_t,uint32_t),void *context,uint32_t build);
uint32_t mt_stream_diag_cycle(void);
void mt_stream_diag_begin(uint32_t generation,uint64_t nonce);
void mt_stream_diag_replaced(uint32_t state,uint32_t epoch,uint32_t adc_state);
void mt_stream_diag_poll(uint32_t existing_delta_us);
void mt_stream_diag_copy(uint32_t existing_delta_us);
void mt_stream_diag_tx(uint32_t begin_cycle);
void mt_stream_diag_note(uint32_t result,uint32_t epoch,uint32_t queue_used,
    uint32_t local_error,uint32_t reason,uint32_t adc_fault);
void mt_stream_diag_observe(uint32_t begin_cycle,uint32_t result,uint32_t state,
    uint32_t epoch,uint32_t adc_state,uint32_t adc_owned,uint32_t hardware_off,
    uint32_t queue_used,uint32_t local_error,uint32_t reason,uint32_t adc_fault,
    unsigned freeze_eligible);
#define MT_DIAG_STAMP(name) uint32_t name=mt_stream_diag_cycle()
#define MT_DIAG_BEGIN(generation,nonce) mt_stream_diag_begin(generation,nonce)
#define MT_DIAG_POLL(delta) mt_stream_diag_poll(delta)
#define MT_DIAG_COPY(delta) mt_stream_diag_copy(delta)
#define MT_DIAG_TX_END(stamp) mt_stream_diag_tx(stamp)
#else
#define MT_DIAG_STAMP(name) ((void)0)
#define MT_DIAG_BEGIN(generation,nonce) ((void)0)
#define MT_DIAG_POLL(delta) ((void)0)
#define MT_DIAG_COPY(delta) ((void)0)
#define MT_DIAG_TX_END(stamp) ((void)0)
#endif
#endif
