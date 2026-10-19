#ifndef MT_SAMPLE_QUEUE_H
#define MT_SAMPLE_QUEUE_H
#include <stddef.h>
#include <stdint.h>

#define MT_SAMPLE_QUEUE_SLOTS 16u
#define MT_SAMPLE_QUEUE_BLOCK 128u

typedef enum {
    MT_SAMPLE_QUEUE_OK = 0,
    MT_SAMPLE_QUEUE_ARGUMENT,
    MT_SAMPLE_QUEUE_CLOSED,
    MT_SAMPLE_QUEUE_COUNT,
    MT_SAMPLE_QUEUE_CODE,
    MT_SAMPLE_QUEUE_INDEX,
    MT_SAMPLE_QUEUE_OVERFLOW,
    MT_SAMPLE_QUEUE_FULL,
    MT_SAMPLE_QUEUE_EMPTY,
    MT_SAMPLE_QUEUE_STALE
} mt_sample_queue_result;

typedef struct {
    uint16_t samples[MT_SAMPLE_QUEUE_SLOTS][MT_SAMPLE_QUEUE_BLOCK];
    uint16_t counts[MT_SAMPLE_QUEUE_SLOTS];
    uint64_t head_index;
    uint64_t next_index;
    size_t head;
    size_t used;
    unsigned sealed;
    mt_sample_queue_result first_fault;
} mt_sample_queue;

typedef struct {
    const uint16_t *samples;
    uint64_t first_index;
    uint16_t count;
} mt_sample_block;

/* Single foreground owner; no IRQ/concurrent access. No hardware side effects.
 * State fields are implementation-owned. Initialize before every other operation
 * on a non-NULL object; do not modify its fields externally. NULL init is a no-op.
 * Init only after the caller has stopped
 * acquisition and invalidated all outstanding views/transport references.
 * first_index is a queue interval origin; a new wire0.2 stream MUST use zero.
 * A nonzero origin is useful for a bounded subinterval/test, not a new stream. */
void mt_sample_queue_init(mt_sample_queue *queue, uint64_t first_index);

/* Copy one certified acquired block. Input cannot overlap queue storage.
 * count=1..128; count<128 requires final_block=1, which seals future input.
 * final_block must be 0 or 1. An empty terminal uses seal, never a zero block.
 * Every supplied-data failure latches the first queue cause and seals input;
 * prior samples/indices remain unchanged. FULL includes a dropped offered block:
 * the caller MUST stop acquisition and retain loss evidence. This module cannot
 * establish the number of later produced samples or hardware quiescence.
 * Calls after sealing return CLOSED without replacing the original first_fault.
 * Validation precedence: NULL queue, sealed, NULL samples, count/final flag,
 * first index, exclusive-end overflow, sample codes, capacity. Thus bad code
 * wins over FULL when both occur. For a valid queue, NULL input is a
 * supplied-data failure. NULL queue simply
 * returns ARGUMENT. Valid pointers must describe count readable input samples. */
mt_sample_queue_result mt_sample_queue_push(mt_sample_queue *queue,
                                           uint64_t first_index,
                                           const uint16_t *samples,
                                           uint16_t count,
                                           unsigned final_block);

/* Stop accepting samples, preserving queued prefix and any earlier fault.
 * This is a software queue barrier only, not a hardware stop operation.
 * NULL seal is a no-op. Existing borrowed views remain valid through sealing. */
void mt_sample_queue_seal(mt_sample_queue *queue);

/* Non-overlapping output; unchanged on failure. Borrowed samples remain valid
 * until a successful pop/init. Push never overwrites the current head. */
mt_sample_queue_result mt_sample_queue_peek(const mt_sample_queue *queue,
                                           mt_sample_block *block);

/* Remove only the expected head after the caller is finished with its samples.
 * The caller must not pop on TX enqueue if serialization still borrows the view.
 * STALE/EMPTY/ARGUMENT do not alter state or latch an acquisition fault.
 * This does not certify USB completion, host receipt, CRC or durable storage. */
mt_sample_queue_result mt_sample_queue_pop(mt_sample_queue *queue,
                                          uint64_t expected_first_index);
#endif
