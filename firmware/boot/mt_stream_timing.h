#ifndef MT_STREAM_TIMING_H
#define MT_STREAM_TIMING_H
#include <stdint.h>
/* Diagnostic declaration only. Numeric syntax never establishes WCET.
 * Required separate immutable input; no fallback or qualified mode exists. */
typedef struct {
    uint32_t schema_version;
    uint32_t diagnostic_provisional;
    uint32_t declared_transaction_bound_ms;
} mt_stream_timing_inputs;
extern const mt_stream_timing_inputs mt_stream_timing;
_Static_assert(sizeof(mt_stream_timing_inputs) == 12u, "timing declaration ABI");
#endif
