#include "mt_sample_queue.h"
#include <string.h>

_Static_assert(MT_SAMPLE_QUEUE_SLOTS * MT_SAMPLE_QUEUE_BLOCK * sizeof(uint16_t)
               == 4096u, "raw sample storage budget");

static mt_sample_queue_result fail(mt_sample_queue *q,
                                   mt_sample_queue_result reason) {
    if (q->first_fault == MT_SAMPLE_QUEUE_OK) q->first_fault = reason;
    q->sealed = 1u;
    return reason;
}

void mt_sample_queue_init(mt_sample_queue *q, uint64_t first_index) {
    if (!q) return;
    memset(q, 0, sizeof *q);
    q->head_index = first_index;
    q->next_index = first_index;
}

mt_sample_queue_result mt_sample_queue_push(mt_sample_queue *q,
                                           uint64_t first_index,
                                           const uint16_t *samples,
                                           uint16_t count,
                                           unsigned final_block) {
    size_t i, tail;
    if (!q) return MT_SAMPLE_QUEUE_ARGUMENT;
    if (q->sealed) return MT_SAMPLE_QUEUE_CLOSED;
    if (!samples) return fail(q, MT_SAMPLE_QUEUE_ARGUMENT);
    if (final_block > 1u || count == 0u || count > MT_SAMPLE_QUEUE_BLOCK ||
        (count < MT_SAMPLE_QUEUE_BLOCK && final_block == 0u))
        return fail(q, MT_SAMPLE_QUEUE_COUNT);
    if (first_index != q->next_index) return fail(q, MT_SAMPLE_QUEUE_INDEX);
    if (UINT64_MAX - first_index < count)
        return fail(q, MT_SAMPLE_QUEUE_OVERFLOW);
    for (i = 0; i < count; ++i)
        if (samples[i] > 4095u) return fail(q, MT_SAMPLE_QUEUE_CODE);
    if (q->used == MT_SAMPLE_QUEUE_SLOTS) return fail(q, MT_SAMPLE_QUEUE_FULL);

    /* No fallible operation after validation: publish only a complete block. */
    tail = (q->head + q->used) % MT_SAMPLE_QUEUE_SLOTS;
    memcpy(q->samples[tail], samples, (size_t)count * sizeof samples[0]);
    q->counts[tail] = count;
    ++q->used;
    q->next_index += count;
    if (final_block) q->sealed = 1u;
    return MT_SAMPLE_QUEUE_OK;
}

void mt_sample_queue_seal(mt_sample_queue *q) {
    if (q) q->sealed = 1u;
}

mt_sample_queue_result mt_sample_queue_peek(const mt_sample_queue *q,
                                           mt_sample_block *block) {
    if (!q || !block) return MT_SAMPLE_QUEUE_ARGUMENT;
    if (q->used == 0u) return MT_SAMPLE_QUEUE_EMPTY;
    block->samples = q->samples[q->head];
    block->first_index = q->head_index;
    block->count = q->counts[q->head];
    return MT_SAMPLE_QUEUE_OK;
}

mt_sample_queue_result mt_sample_queue_pop(mt_sample_queue *q,
                                          uint64_t expected_first_index) {
    if (!q) return MT_SAMPLE_QUEUE_ARGUMENT;
    if (q->used == 0u) return MT_SAMPLE_QUEUE_EMPTY;
    if (expected_first_index != q->head_index) return MT_SAMPLE_QUEUE_STALE;
    q->head_index += q->counts[q->head];
    q->head = (q->head + 1u) % MT_SAMPLE_QUEUE_SLOTS;
    --q->used;
    return MT_SAMPLE_QUEUE_OK;
}
