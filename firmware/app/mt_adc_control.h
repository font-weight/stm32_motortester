#ifndef MT_ADC_CONTROL_H
#define MT_ADC_CONTROL_H
#include "mt_control.h"
#include "../platform/adc_timer_dma.h"

/* Debugger-only first failed binding start since reset. Not a wire message or
 * a physical timing certificate. One foreground writer; readers accept only
 * magic == MT_ADC_START_DIAGNOSTIC_MAGIC, published after all fields. Ordinary
 * Stop, recovery and a new HELLO do not clear this record. */
#define MT_ADC_START_DIAGNOSTIC_MAGIC UINT32_C(0x4d544431)
typedef enum {
    MT_ADC_START_CORRELATION = 1,
    MT_ADC_START_COMMAND_TIME = 2,
    MT_ADC_START_DRIVER = 3,
    MT_ADC_START_POSTCHECK = 4,
    MT_ADC_START_SNAPSHOT = 5
} mt_adc_start_stage;
typedef struct {
    uint32_t magic, version, stage;
    uint32_t command_ms, epoch_ms, before_us, after_us;
    uint32_t driver_called;
    int32_t driver_result;
    /* Raw returned driver snapshot: its internal cleanup may have replaced
     * fault; timestamps may belong to an earlier start on an early rejection.
     * driver_result is meaningful only when driver_called is 1. */
    uint32_t driver_fault, driver_state;
    uint32_t start_before_us, start_after_us;
} mt_adc_start_diagnostic;
extern volatile mt_adc_start_diagnostic mt_adc_first_start_failure;

/* This binding owns no second sample buffer. Driver and controller must remain
 * alive even when hardware Stop fails. One foreground owner, no ISR calls. */
typedef struct {
    mt_control *control;
    mt_adc_dma_t *driver;
    uint32_t (*now_ms)(void *context);
    void *clock_context;
    uint32_t epoch_ms, first_sample_delay_us;
    mt_adc_dma_start_guard guard;
    unsigned guarded;
    /* Optional coherent same-observation pair. Fixed by init. Uses
     * clock_context; returns 1 only
     * for a freshly validated pair, or 0 on failure. Same clock as now_us/ms.
     * NULL retains the standalone bounded triple-read correlation path. */
    int (*snapshot)(void *context, uint32_t *us, uint32_t *ms);
} mt_adc_control;
/* driver was initialized with MMIO/time callbacks and verified nominal HSE.
 * now_ms and driver's now_us describe the SAME monotonic clock; each read has
 * its documented quantization. now_ms must be a true wrapping32-bit millisecond
 * clock, not a wrapping32-bit microsecond counter divided by1000.
 * All receive/poll timestamps use this same millisecond clock. Clock callbacks
 * must return promptly and cannot reenter. Objects must not overlap.
 * init resets software control only; hardware must be quiescent before rebinding.
 */
mt_control_result mt_adc_control_init(mt_adc_control *binding,
    mt_control *control, mt_adc_dma_t *driver,
    uint32_t (*now_ms)(void *context), void *clock_context,
    uint32_t firmware_build);
/* Same initialization with a copied optional-integration guard definition.
 * Its context stays alive; callbacks obey adc_timer_dma.h and never reenter.
 * The original init intentionally retains unguarded standalone behavior. */
mt_control_result mt_adc_control_init_guarded(mt_adc_control *binding,
    mt_control *control, mt_adc_dma_t *driver,
    uint32_t (*now_ms)(void *context), void *clock_context,
    uint32_t firmware_build, const mt_adc_dma_start_guard *guard);
/* Explicit opt-in for a shared, freshly validated ms/us observation. Callback
 * failure rejects start before touching the driver. All other init contracts
 * apply; this does not change the driver's separate CEN timing bound. */
mt_control_result mt_adc_control_init_coherent_guarded(mt_adc_control *binding,
    mt_control *control, mt_adc_dma_t *driver,
    uint32_t (*now_ms)(void *context), void *clock_context,
    uint32_t firmware_build, const mt_adc_dma_start_guard *guard,
    int (*snapshot)(void *context, uint32_t *us, uint32_t *ms));
/* Call frequently enough to meet driver <2560us service budget, even with USB
 * backpressure. Reads at most one128-code block, no output write or busy wait.
 * Stop consuming the snapshot immediately when the controller freezes/stops it.
 * Other receive/poll calls remain mt_control APIs on the bound controller. */
mt_control_result mt_adc_control_service(mt_adc_control *binding);
#endif
