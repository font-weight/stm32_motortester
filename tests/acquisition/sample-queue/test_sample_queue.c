#include "mt_sample_queue.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Literals deliberately do not inherit queue capacity from implementation. */
enum { MODEL_SLOTS = 16, MODEL_CODES = 128 };
_Static_assert(MT_SAMPLE_QUEUE_SLOTS == 16u, "expected sixteen slots");
_Static_assert(MT_SAMPLE_QUEUE_BLOCK == 128u, "expected 128 codes per slot");

typedef struct {
    uint64_t first;
    uint16_t count;
    uint16_t codes[MODEL_CODES];
} reference_block;

typedef struct {
    reference_block blocks[MODEL_SLOTS];
    size_t length;
    uint64_t origin;
    uint64_t end;
    unsigned closed;
    mt_sample_queue_result fault;
} reference_fifo;

typedef struct {
    mt_sample_queue queue;
    reference_fifo ref;
} fixture;

static const char *scenario = "startup";
static uint64_t operations;
static uint64_t checks;
static uint64_t rng_state = UINT64_C(0x162128c011f00d59);
static unsigned trace_number;
static unsigned step_number;
static uint16_t large_input[UINT16_MAX];

#define CHECK(condition) do { \
    ++checks; \
    if (!(condition)) { \
        fprintf(stderr, "%s trace=%u step=%u operation=%" PRIu64 \
                " line=%d: %s\n", scenario, trace_number, step_number, \
                operations, __LINE__, #condition); \
        exit(EXIT_FAILURE); \
    } \
} while (0)

static uint32_t random32(void) {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return (uint32_t)(rng_state >> 16);
}

static void pattern(uint16_t codes[MODEL_CODES], uint32_t salt) {
    size_t i;
    for (i = 0u; i < MODEL_CODES; ++i) {
        codes[i] = (uint16_t)((salt + (uint32_t)i * 67u) & 4095u);
    }
    codes[0] = 0u;
    codes[127] = 4095u;
}

static void snapshot(mt_sample_queue *copy, const mt_sample_queue *source) {
    memcpy(copy, source, sizeof *copy);
}

static void check_state(const fixture *f) {
    mt_sample_block view;
    mt_sample_block untouched;
    mt_sample_queue copy;
    mt_sample_queue_result result;
    uint16_t marker = 37u;
    size_t i;
    memset(&view, 0xa5, sizeof view);
    view.samples = &marker;
    view.first_index = UINT64_C(0xabc00abc00abc);
    view.count = 507u;
    memcpy(&untouched, &view, sizeof view);
    snapshot(&copy, &f->queue);
    result = mt_sample_queue_peek(&f->queue, &view);
    CHECK(memcmp(&copy, &f->queue, sizeof copy) == 0);
    CHECK(f->queue.used == f->ref.length);
    CHECK(f->queue.used <= 16u);
    CHECK(f->queue.head < 16u);
    CHECK(f->queue.next_index == f->ref.end);
    CHECK(f->queue.sealed == f->ref.closed);
    CHECK(f->queue.first_fault == f->ref.fault);
    if (f->ref.length == 0u) {
        CHECK(result == MT_SAMPLE_QUEUE_EMPTY);
        CHECK(memcmp(&view, &untouched, sizeof view) == 0);
        CHECK(f->queue.head_index == f->ref.end);
        return;
    }
    CHECK(result == MT_SAMPLE_QUEUE_OK);
    CHECK(view.first_index == f->ref.blocks[0].first);
    CHECK(f->queue.head_index == f->ref.blocks[0].first);
    CHECK(view.count == f->ref.blocks[0].count);
    CHECK(view.samples != NULL);
    for (i = 0u; i < view.count; ++i) {
        CHECK(view.samples[i] == f->ref.blocks[0].codes[i]);
    }
}

static void reset(fixture *f, uint64_t origin) {
    /* This fixture has no concurrent producer or surviving borrowed views. */
    memset(f, 0xa5, sizeof *f);
    memset(&f->ref, 0, sizeof f->ref);
    f->ref.origin = origin;
    f->ref.end = origin;
    mt_sample_queue_init(&f->queue, origin);
    ++operations;
    check_state(f);
}

static void push(fixture *f, uint64_t first, const uint16_t *codes,
                 uint16_t count, unsigned final,
                 mt_sample_queue_result expected) {
    mt_sample_queue before;
    mt_sample_queue normalized;
    mt_sample_block borrowed;
    uint16_t borrowed_copy[MODEL_CODES];
    mt_sample_queue_result result;
    unsigned had_view = f->ref.length != 0u;
    size_t i;
    if (had_view) {
        CHECK(mt_sample_queue_peek(&f->queue, &borrowed) == MT_SAMPLE_QUEUE_OK);
        memcpy(borrowed_copy, borrowed.samples,
               (size_t)borrowed.count * sizeof borrowed_copy[0]);
    }
    snapshot(&before, &f->queue);
    result = mt_sample_queue_push(&f->queue, first, codes, count, final);
    ++operations;
    CHECK(result == expected);
    if (expected == MT_SAMPLE_QUEUE_OK) {
        reference_block *block;
        CHECK(f->ref.length < 16u);
        CHECK(first == f->ref.end);
        CHECK(count >= 1u && count <= 128u);
        block = &f->ref.blocks[f->ref.length++];
        block->first = first;
        block->count = count;
        for (i = 0u; i < count; ++i) block->codes[i] = codes[i];
        f->ref.end += count;
        if (final != 0u) f->ref.closed = 1u;
    } else if (expected != MT_SAMPLE_QUEUE_CLOSED) {
        CHECK(f->ref.closed == 0u);
        f->ref.closed = 1u;
        f->ref.fault = expected;
        /* Ignore exactly the two permitted fault-state changes. */
        snapshot(&normalized, &f->queue);
        normalized.sealed = before.sealed;
        normalized.first_fault = before.first_fault;
        CHECK(memcmp(&normalized, &before, sizeof before) == 0);
    } else {
        CHECK(memcmp(&before, &f->queue, sizeof before) == 0);
    }
    if (had_view) {
        mt_sample_block after;
        CHECK(mt_sample_queue_peek(&f->queue, &after) == MT_SAMPLE_QUEUE_OK);
        CHECK(after.samples == borrowed.samples);
        CHECK(after.first_index == borrowed.first_index);
        CHECK(after.count == borrowed.count);
        CHECK(memcmp(borrowed.samples, borrowed_copy,
                     (size_t)borrowed.count * sizeof borrowed_copy[0]) == 0);
    }
    check_state(f);
}

static void pop(fixture *f, uint64_t first, mt_sample_queue_result expected) {
    mt_sample_queue before;
    size_t i;
    snapshot(&before, &f->queue);
    CHECK(mt_sample_queue_pop(&f->queue, first) == expected);
    ++operations;
    if (expected == MT_SAMPLE_QUEUE_OK) {
        CHECK(f->ref.length != 0u);
        CHECK(first == f->ref.blocks[0].first);
        /* Independent representation: shift whole blocks, never a ring. */
        for (i = 1u; i < f->ref.length; ++i) {
            f->ref.blocks[i - 1u] = f->ref.blocks[i];
        }
        --f->ref.length;
    } else {
        CHECK(memcmp(&before, &f->queue, sizeof before) == 0);
    }
    check_state(f);
}

static void seal(fixture *f) {
    mt_sample_queue before;
    mt_sample_queue normalized;
    snapshot(&before, &f->queue);
    mt_sample_queue_seal(&f->queue);
    ++operations;
    snapshot(&normalized, &f->queue);
    normalized.sealed = before.sealed;
    CHECK(memcmp(&before, &normalized, sizeof before) == 0);
    f->ref.closed = 1u;
    check_state(f);
}

static void drain(fixture *f) {
    while (f->ref.length != 0u) {
        pop(f, f->ref.blocks[0].first, MT_SAMPLE_QUEUE_OK);
    }
    pop(f, f->ref.end, MT_SAMPLE_QUEUE_EMPTY);
}

static void add_prefix(fixture *f, unsigned count) {
    uint16_t codes[MODEL_CODES];
    unsigned i;
    for (i = 0u; i < count; ++i) {
        pattern(codes, random32());
        push(f, f->ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_OK);
        /* A successful push must own its data, rather than borrow the caller. */
        memset(codes, 0xff, sizeof codes);
        check_state(f);
    }
}

static void verify_closed_and_drain(fixture *f) {
    uint16_t codes[MODEL_CODES];
    mt_sample_queue_result cause = f->ref.fault;
    pattern(codes, 19u);
    push(f, f->ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_CLOSED);
    push(f, UINT64_MAX, NULL, 0u, UINT_MAX, MT_SAMPLE_QUEUE_CLOSED);
    seal(f);
    seal(f);
    drain(f);
    CHECK(f->queue.first_fault == cause);
    push(f, f->ref.end, codes, 128u, 1u, MT_SAMPLE_QUEUE_CLOSED);
}

static void test_null_empty_and_seal(void) {
    fixture f;
    mt_sample_block block;
    mt_sample_block saved;
    mt_sample_queue before;
    uint16_t marker = 7u;
    scenario = "null-empty-seal";
    mt_sample_queue_init(NULL, 0u);
    mt_sample_queue_seal(NULL);
    reset(&f, 0u);
    memset(&block, 0x5a, sizeof block);
    block.samples = &marker;
    memcpy(&saved, &block, sizeof block);
    CHECK(mt_sample_queue_push(NULL, 0u, &marker, 1u, 1u) == MT_SAMPLE_QUEUE_ARGUMENT);
    CHECK(mt_sample_queue_peek(NULL, &block) == MT_SAMPLE_QUEUE_ARGUMENT);
    CHECK(memcmp(&block, &saved, sizeof block) == 0);
    CHECK(mt_sample_queue_peek(NULL, NULL) == MT_SAMPLE_QUEUE_ARGUMENT);
    CHECK(mt_sample_queue_pop(NULL, 0u) == MT_SAMPLE_QUEUE_ARGUMENT);
    snapshot(&before, &f.queue);
    CHECK(mt_sample_queue_peek(&f.queue, NULL) == MT_SAMPLE_QUEUE_ARGUMENT);
    CHECK(memcmp(&before, &f.queue, sizeof before) == 0);
    pop(&f, 0u, MT_SAMPLE_QUEUE_EMPTY);
    pop(&f, UINT64_MAX, MT_SAMPLE_QUEUE_EMPTY);
    seal(&f);
    verify_closed_and_drain(&f);
    reset(&f, UINT64_MAX);
    seal(&f);
    verify_closed_and_drain(&f);
}

static void test_capacity_wrap_and_copy(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    unsigned cycle;
    unsigned i;
    scenario = "capacity-wrap-copy";
    reset(&f, UINT64_C(987654321));
    for (cycle = 0u; cycle < 100u; ++cycle) {
        add_prefix(&f, 16u - (unsigned)f.ref.length);
        for (i = 0u; i < 13u; ++i) {
            uint64_t first = f.ref.blocks[0].first;
            pop(&f, first + 1u, MT_SAMPLE_QUEUE_STALE);
            pop(&f, first, MT_SAMPLE_QUEUE_OK);
            if (f.ref.length != 0u) pop(&f, first, MT_SAMPLE_QUEUE_STALE);
        }
    }
    add_prefix(&f, 16u - (unsigned)f.ref.length);
    pattern(codes, 23u);
    push(&f, f.ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_FULL);
    verify_closed_and_drain(&f);
    reset(&f, 0u);
    add_prefix(&f, 16u);
    seal(&f);
    verify_closed_and_drain(&f);
}

static void test_every_final_length(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    unsigned count;
    scenario = "all-final-lengths";
    for (count = 1u; count <= 128u; ++count) {
        reset(&f, 41u);
        add_prefix(&f, count % 16u);
        pattern(codes, count);
        push(&f, f.ref.end, codes, (uint16_t)count, 1u, MT_SAMPLE_QUEUE_OK);
        memset(codes, 0xff, sizeof codes);
        check_state(&f);
        verify_closed_and_drain(&f);
    }
    reset(&f, 0u);
    add_prefix(&f, 15u);
    pattern(codes, 128u);
    push(&f, f.ref.end, codes, 128u, 1u, MT_SAMPLE_QUEUE_OK);
    verify_closed_and_drain(&f);
    reset(&f, 0u);
    add_prefix(&f, 16u);
    push(&f, f.ref.end, codes, 1u, 1u, MT_SAMPLE_QUEUE_FULL);
    verify_closed_and_drain(&f);
}

static void test_invalid_counts_and_flags(void) {
    fixture f;
    unsigned count;
    const unsigned flags[] = { 2u, 3u, UINT_MAX };
    size_t i;
    scenario = "invalid-counts-flags";
    for (count = 0u; count < 128u; ++count) {
        reset(&f, 0u);
        add_prefix(&f, 3u);
        push(&f, f.ref.end, large_input, (uint16_t)count, 0u, MT_SAMPLE_QUEUE_COUNT);
        verify_closed_and_drain(&f);
    }
    for (count = 129u; count <= UINT16_MAX; ++count) {
        reset(&f, 83u);
        /* Every representable oversized count, with at least a prior block. */
        add_prefix(&f, 1u);
        push(&f, f.ref.end, large_input, (uint16_t)count,
             count % 2u, MT_SAMPLE_QUEUE_COUNT);
        verify_closed_and_drain(&f);
    }
    reset(&f, 0u);
    add_prefix(&f, 2u);
    push(&f, f.ref.end, large_input, 0u, 1u, MT_SAMPLE_QUEUE_COUNT);
    verify_closed_and_drain(&f);
    for (i = 0u; i < sizeof flags / sizeof flags[0]; ++i) {
        reset(&f, 29u);
        add_prefix(&f, 4u);
        push(&f, f.ref.end, large_input, 128u, flags[i], MT_SAMPLE_QUEUE_COUNT);
        verify_closed_and_drain(&f);
    }
}

static void test_code_errors_and_null_input(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    const uint16_t invalid[] = {4096u, UINT16_MAX};
    size_t position;
    size_t value;
    unsigned fill;
    scenario = "invalid-codes-all-positions";
    for (fill = 0u; fill <= 16u; ++fill) {
        for (value = 0u; value < sizeof invalid / sizeof invalid[0]; ++value) {
            for (position = 0u; position < 128u; ++position) {
                reset(&f, 17u);
                add_prefix(&f, fill);
                pattern(codes, (uint32_t)position);
                codes[position] = invalid[value];
                /* Includes CODE before FULL and invalid very last sample. */
                push(&f, f.ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_CODE);
                verify_closed_and_drain(&f);
            }
        }
        reset(&f, 0u);
        add_prefix(&f, fill);
        push(&f, f.ref.end, NULL, 128u, 0u, MT_SAMPLE_QUEUE_ARGUMENT);
        verify_closed_and_drain(&f);
    }
}

static void test_indices_and_maximum(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    uint16_t one[] = {4095u};
    unsigned i;
    const uint64_t wrong[] = {0u, 127u, 255u, 257u, UINT64_MAX};
    scenario = "indices-u64-exclusive-end";
    pattern(codes, 71u);
    for (i = 0u; i < sizeof wrong / sizeof wrong[0]; ++i) {
        reset(&f, 0u);
        add_prefix(&f, 2u);
        push(&f, wrong[i], codes, 128u, 0u, MT_SAMPLE_QUEUE_INDEX);
        verify_closed_and_drain(&f);
    }
    /* Literal expected outcomes, independent of the implementation formula. */
    reset(&f, UINT64_C(18446744073709551487)); /* max - 128 */
    push(&f, UINT64_C(18446744073709551487), codes, 128u, 0u, MT_SAMPLE_QUEUE_OK);
    CHECK(f.queue.next_index == UINT64_C(18446744073709551615));
    push(&f, UINT64_C(18446744073709551615), one, 1u, 1u, MT_SAMPLE_QUEUE_OVERFLOW);
    verify_closed_and_drain(&f);

    reset(&f, UINT64_C(18446744073709551488)); /* max - 127 */
    push(&f, UINT64_C(18446744073709551488), codes, 128u, 0u, MT_SAMPLE_QUEUE_OVERFLOW);
    verify_closed_and_drain(&f);

    reset(&f, UINT64_C(18446744073709551614)); /* final sample max - 1 */
    push(&f, UINT64_C(18446744073709551614), one, 1u, 1u, MT_SAMPLE_QUEUE_OK);
    CHECK(f.queue.next_index == UINT64_C(18446744073709551615));
    verify_closed_and_drain(&f);

    reset(&f, UINT64_C(18446744073709551615)); /* sample max cannot have an end */
    push(&f, UINT64_C(18446744073709551615), one, 1u, 1u, MT_SAMPLE_QUEUE_OVERFLOW);
    verify_closed_and_drain(&f);

    reset(&f, UINT64_C(18446744073709551486)); /* max - 129 */
    push(&f, UINT64_C(18446744073709551486), codes, 128u, 0u, MT_SAMPLE_QUEUE_OK);
    pop(&f, UINT64_C(18446744073709551486), MT_SAMPLE_QUEUE_OK);
    push(&f, UINT64_C(18446744073709551614), one, 1u, 1u, MT_SAMPLE_QUEUE_OK);
    verify_closed_and_drain(&f);
}

static void test_precedence(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    scenario = "observed-validation-precedence";
    pattern(codes, 99u);
    codes[127] = 4096u;
    reset(&f, 0u);
    add_prefix(&f, 16u);
    push(&f, 0u, NULL, 0u, UINT_MAX, MT_SAMPLE_QUEUE_ARGUMENT);
    verify_closed_and_drain(&f);
    reset(&f, 0u);
    add_prefix(&f, 16u);
    push(&f, 0u, codes, 0u, UINT_MAX, MT_SAMPLE_QUEUE_COUNT);
    verify_closed_and_drain(&f);
    reset(&f, 0u);
    add_prefix(&f, 16u);
    push(&f, 0u, codes, 128u, 0u, MT_SAMPLE_QUEUE_INDEX);
    verify_closed_and_drain(&f);
    reset(&f, UINT64_C(18446744073709551488));
    push(&f, UINT64_C(18446744073709551488), codes, 128u, 0u, MT_SAMPLE_QUEUE_OVERFLOW);
    verify_closed_and_drain(&f);
    reset(&f, 0u);
    add_prefix(&f, 16u);
    push(&f, f.ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_CODE);
    verify_closed_and_drain(&f);
    reset(&f, UINT64_C(18446744073709549567)); /* max - 2048 */
    add_prefix(&f, 16u);
    push(&f, UINT64_MAX, codes, 128u, 0u, MT_SAMPLE_QUEUE_OVERFLOW);
    verify_closed_and_drain(&f);
}

static void test_random_healthy_traces(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    unsigned trace;
    unsigned step;
    scenario = "random-linear-fifo-healthy";
    for (trace = 0u; trace < 512u; ++trace) {
        trace_number = trace;
        reset(&f, (uint64_t)random32() << 16);
        for (step = 0u; step < 1024u; ++step) {
            uint32_t choice = random32();
            step_number = step;
            if (f.ref.length == 0u || (f.ref.length < 16u && choice % 100u < 53u)) {
                pattern(codes, random32());
                push(&f, f.ref.end, codes, 128u, 0u, MT_SAMPLE_QUEUE_OK);
                memset(codes, 0xff, sizeof codes);
                check_state(&f);
            } else if (choice % 100u < 95u) {
                pop(&f, f.ref.blocks[0].first, MT_SAMPLE_QUEUE_OK);
            } else {
                pop(&f, f.ref.blocks[0].first ^ UINT64_C(1), MT_SAMPLE_QUEUE_STALE);
            }
        }
        seal(&f);
        verify_closed_and_drain(&f);
    }
}

static void test_random_fault_traces(void) {
    fixture f;
    uint16_t codes[MODEL_CODES];
    unsigned trace;
    unsigned step;
    scenario = "random-linear-fifo-faults";
    for (trace = 0u; trace < 2048u; ++trace) {
        trace_number = trace;
        reset(&f, (uint64_t)random32());
        for (step = 0u; step < 128u; ++step) {
            uint32_t choice = random32() % 100u;
            uint64_t first = f.ref.end;
            step_number = step;
            pattern(codes, random32());
            if (choice < 65u) {
                if (f.ref.closed != 0u) {
                    push(&f, first, codes, 128u, 0u, MT_SAMPLE_QUEUE_CLOSED);
                } else if (choice < 47u) {
                    push(&f, first, codes, 128u, 0u,
                         f.ref.length == 16u ? MT_SAMPLE_QUEUE_FULL : MT_SAMPLE_QUEUE_OK);
                } else if (choice < 50u) {
                    codes[random32() % 128u] = 4096u;
                    push(&f, first, codes, 128u, 0u, MT_SAMPLE_QUEUE_CODE);
                } else if (choice < 53u) {
                    push(&f, first + 1u, codes, 128u, 0u, MT_SAMPLE_QUEUE_INDEX);
                } else if (choice < 56u) {
                    push(&f, first, codes, 127u, 0u, MT_SAMPLE_QUEUE_COUNT);
                } else if (choice < 59u) {
                    push(&f, first, NULL, 128u, 0u, MT_SAMPLE_QUEUE_ARGUMENT);
                } else {
                    uint16_t count = (uint16_t)(1u + random32() % 128u);
                    push(&f, first, codes, count, 1u,
                         f.ref.length == 16u ? MT_SAMPLE_QUEUE_FULL : MT_SAMPLE_QUEUE_OK);
                }
                memset(codes, 0xff, sizeof codes);
                check_state(&f);
            } else if (choice < 88u) {
                pop(&f, f.ref.length != 0u ? f.ref.blocks[0].first : first,
                    f.ref.length != 0u ? MT_SAMPLE_QUEUE_OK : MT_SAMPLE_QUEUE_EMPTY);
            } else if (choice < 95u) {
                pop(&f, f.ref.length != 0u ? f.ref.blocks[0].first + 1u : first,
                    f.ref.length != 0u ? MT_SAMPLE_QUEUE_STALE : MT_SAMPLE_QUEUE_EMPTY);
            } else {
                seal(&f);
            }
        }
        seal(&f);
        verify_closed_and_drain(&f);
    }
}

int main(void) {
    test_null_empty_and_seal();
    test_capacity_wrap_and_copy();
    test_every_final_length();
    test_invalid_counts_and_flags();
    test_code_errors_and_null_input();
    test_indices_and_maximum();
    test_precedence();
    test_random_healthy_traces();
    test_random_fault_traces();
    printf("PASS: 9 scenario groups, 2560 deterministic random traces, "
           "%" PRIu64 " operations, %" PRIu64 " checks\n", operations, checks);
    printf("Native sizeof(mt_sample_queue)=%zu bytes; raw codes=4096 bytes; "
           "seed=0x162128c011f00d59\n", sizeof(mt_sample_queue));
    return EXIT_SUCCESS;
}
