#ifndef MT_ACQUISITION_H
#define MT_ACQUISITION_H

#include <stdbool.h>
#include <stdint.h>

#define MT_ACQ_WINDOW_SAMPLES 2048u
#define MT_ACQ_PROFILE_ID 1u
#define MT_ACQ_NOMINAL_RATE_HZ 50000u
#define MT_ACQ_NO_TRIGGER_INDEX UINT16_MAX
#define MT_ACQ_QUALITY_AUTO_TIMEOUT 1u
#define MT_ACQ_QUALITY_RAIL_CODE 2u

typedef enum {
    MT_ACQ_AUTO = 0,
    MT_ACQ_NORMAL = 1,
    MT_ACQ_SINGLE = 2
} mt_acq_mode_t;

typedef enum {
    MT_ACQ_RISING = 0,
    MT_ACQ_FALLING = 1
} mt_acq_edge_t;

/* Internal states: these values are NOT wire state numbers. */
typedef enum {
    MT_ACQ_STOPPED,
    MT_ACQ_PREPARING,
    MT_ACQ_WAITING,
    MT_ACQ_CAPTURING,
    MT_ACQ_FROZEN,
    MT_ACQ_ERROR
} mt_acq_state_t;

typedef enum {
    MT_ACQ_TRIGGER_ANALOG = 0,
    MT_ACQ_TRIGGER_AUTO_TIMEOUT = 2,
    MT_ACQ_TRIGGER_NONE = 255
} mt_acq_trigger_t;

typedef enum {
    MT_ACQ_FAULT_NONE,
    MT_ACQ_FAULT_SAMPLE_RANGE,
    MT_ACQ_FAULT_TIME_ORDER,
    MT_ACQ_FAULT_DMA,
    MT_ACQ_FAULT_DEADLINE
} mt_acq_fault_t;

typedef enum {
    MT_ACQ_OK = 0,
    MT_ACQ_TRIGGERED = 1,
    MT_ACQ_WINDOW_READY = 2,
    MT_ACQ_IGNORED = 3,
    MT_ACQ_INVALID_ARGUMENT = -1,
    MT_ACQ_INVALID_CONFIG = -2,
    MT_ACQ_BUSY = -3,
    MT_ACQ_NOT_READY = -4,
    MT_ACQ_INPUT_FAULT = -5
} mt_acq_result_t;

typedef struct {
    uint16_t profile_id;          /* Only profile 1: nominal 50 kS/s, 2048 codes. */
    uint16_t pretrigger_samples;  /* Only 0, 512, 1024. */
    uint16_t level_code;          /* 1..4094, raw 12-bit ADC code. */
    uint8_t mode;                /* mt_acq_mode_t, numeric values checked. */
    uint8_t source;              /* Only 0 (analog); external input not implemented. */
    uint8_t edge;                /* mt_acq_edge_t, numeric values checked. */
    uint32_t auto_timeout_ms;    /* Auto: 100..60000; Normal/Single: 0. */
} mt_acq_config_t;

typedef struct {
    uint16_t sample_count;
    uint16_t pretrigger_samples;
    uint16_t trigger_index;
    uint16_t quality_flags;
    mt_acq_trigger_t trigger_kind;
} mt_acq_window_t;

/* Allocate statically, once per channel. Members are public for fixed allocation
 * and inspection; callers must not modify them. No heap, peripheral or wire ABI.
 * Calls are serialized by the caller; this module is not interrupt/thread safe.
 */
typedef struct {
    uint16_t samples[MT_ACQ_WINDOW_SAMPLES];
    mt_acq_config_t config;
    mt_acq_window_t window;
    uint32_t waiting_since_ms;
    uint32_t last_sample_ms;
    uint16_t write_index;
    uint16_t retained_count;
    uint16_t first_index;
    uint16_t post_remaining;
    uint16_t previous_code;
    uint16_t rail_count;
    mt_acq_state_t state;
    mt_acq_fault_t fault;
    bool configured;
    bool have_previous;
} mt_acq_t;

/* Every pointer must designate a valid object. Output/config arguments must not
 * overlap the context. NULL is rejected. init is the only required first call.
 */
mt_acq_result_t mt_acq_init(mt_acq_t *acq);
mt_acq_result_t mt_acq_configure(mt_acq_t *acq, const mt_acq_config_t *config);
mt_acq_result_t mt_acq_arm(mt_acq_t *acq, uint32_t now_ms);

/* sample_ms is the sample's acquisition time, not its later DMA-drain time.
 * It uses the same monotonic modulo-2^32 ms clock as arm/release. Equal times
 * are valid (many samples per ms). Each consecutive advance must be < 2^31 ms.
 * The driver must detect DMA loss/deadlines; this API cannot infer missing codes.
 * FROZEN/STOPPED/ERROR consume nothing and return IGNORED without mutation.
 */
mt_acq_result_t mt_acq_push(mt_acq_t *acq, uint16_t code, uint32_t sample_ms);

/* Reads succeed only for a complete frozen window, in chronological order.
 * Failure leaves *window / *code unchanged. Physical samples[] may be wrapped.
 */
mt_acq_result_t mt_acq_window(const mt_acq_t *acq, mt_acq_window_t *window);
mt_acq_result_t mt_acq_read(const mt_acq_t *acq, uint16_t index, uint16_t *code);

/* Call only after session-level RELEASE validation. Single stops; Auto/Normal
 * start a fresh preparation. Never call on duplicate RELEASE (session owns it).
 */
mt_acq_result_t mt_acq_release(mt_acq_t *acq, uint32_t now_ms);

/* Software cancellation only: invalidates reads and future ingestion. Caller
 * disables timer/DMA and handles USB terminal/TX drain ordering separately.
 * Raw bytes are retained in memory, but must not be published as a full window.
 */
mt_acq_result_t mt_acq_stop(mt_acq_t *acq);
mt_acq_result_t mt_acq_fail(mt_acq_t *acq, mt_acq_fault_t fault);

#endif
