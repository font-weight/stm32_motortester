/* Reuse the existing register fixture and run its full TIME regression first.
 * The additional oracle uses an independent uint64 elapsed time, only in tests.
 * No native test establishes real callback duration or target clock accuracy. */
#define main historical_timebase_main
#include "../timebase/test_timebase.c"
#undef main

static unsigned snapshot_reads, snapshot_cnt_reads;
static uint32_t mutate_address, mutate_value, delay_after_counter;

static uint32_t snapshot_read32(void *context, uint32_t address) {
    uint32_t value;
    ++snapshot_reads;
    value = read32(context, address);
    if (address == CNT) {
        ++snapshot_cnt_reads;
        if (mutate_address) *reg(mutate_address) = mutate_value;
        /* Model preemption after the actual CNT sample, before return and
         * validation. Both outputs must still describe that one old sample. */
        advance(delay_after_counter);
    }
    return value;
}

static void prepare_snapshot(void) {
    setup();
    snapshot_reads = snapshot_cnt_reads = 0u;
    mutate_address = mutate_value = delay_after_counter = 0u;
    clock_state.io.read32 = snapshot_read32;
}

static void seed_time(uint64_t elapsed) {
    *reg(CNT) = (uint32_t)(elapsed & UINT64_C(65535));
    clock_state.last_counter = (uint16_t)elapsed;
    clock_state.us = (uint32_t)elapsed;
    clock_state.ms = (uint32_t)(elapsed / UINT64_C(1000));
    clock_state.remainder_us = (uint32_t)(elapsed % UINT64_C(1000));
}

static void assert_pair(uint64_t expected) {
    uint32_t us = UINT32_C(0x13579bdf), ms = UINT32_C(0x2468ace0);
    unsigned reads = snapshot_reads, counters = snapshot_cnt_reads;
    unsigned writes = hw.count;
    assert(mt_timebase_snapshot(&clock_state, &us, &ms));
    assert(us == (uint32_t)expected);
    assert(ms == (uint32_t)(expected / UINT64_C(1000)));
    assert(clock_state.remainder_us == (uint32_t)(expected % UINT64_C(1000)));
    assert(clock_state.last_counter == (uint16_t)expected);
    assert(snapshot_cnt_reads - counters == 1u);
    assert(snapshot_reads - reads == 27u);
    assert(hw.count == writes && mt_timebase_healthy(&clock_state));
}

static void test_coherent_rollover(void) {
    static const uint64_t seeds[] = {
        UINT64_C(0), UINT64_C(999), UINT64_C(65530),
        UINT64_C(4294967290), UINT64_C(4294967295),
        UINT64_C(4294967295999), UINT64_C(4294967296001)
    };
    static const uint32_t deltas[] = {0u, 1u, 5u, 10u, 999u, 1000u, 32000u};
    for (unsigned i = 0; i < sizeof seeds / sizeof seeds[0]; ++i) {
        uint64_t expected = seeds[i];
        prepare_snapshot(); seed_time(expected);
        for (unsigned j = 0; j < sizeof deltas / sizeof deltas[0]; ++j) {
            advance(deltas[j]); expected += deltas[j];
            assert_pair(expected);
        }
    }
    /* Long deterministic sequence crosses the 16-bit hardware counter often
     * and the us32 counter, while ms32 remains independent of us32 / 1000. */
    prepare_snapshot();
    uint64_t expected = UINT64_C(4294900000);
    seed_time(expected);
    for (unsigned i = 0; i < 10000u; ++i) {
        uint32_t delta = (i * 997u) % 32001u;
        advance(delta); expected += delta; assert_pair(expected);
    }
    puts("PASS coherent pair: one validated CNT sample, 27 reads, independent us/ms and CNT rollover");
}

static void assert_snapshot_failure(mt_timebase *clock) {
    const uint32_t before_us = UINT32_C(0x13579bdf);
    const uint32_t before_ms = UINT32_C(0x2468ace0);
    uint32_t us = before_us, ms = before_ms;
    assert(!mt_timebase_snapshot(clock, &us, &ms));
    assert(us == before_us && ms == before_ms);
}

static void test_snapshot_arguments_and_stale_states(void) {
    mt_timebase empty = {0};
    uint32_t us = 123u, ms = 456u;
    prepare_snapshot();
    assert_snapshot_failure(NULL);
    assert_snapshot_failure(&empty);
    assert(!mt_timebase_snapshot(&clock_state, NULL, &ms) && ms == 456u);
    assert(!mt_timebase_snapshot(&clock_state, &us, NULL) && us == 123u);
    assert(!mt_timebase_snapshot(&clock_state, &us, &us) && us == 123u);
    assert(!mt_timebase_snapshot(&clock_state, NULL, NULL));
    assert(snapshot_reads == 0u && snapshot_cnt_reads == 0u);
    clock_state.io.read32 = NULL; assert_snapshot_failure(&clock_state);
    clock_state.io.read32 = snapshot_read32;
    clock_state.io.write32 = NULL; assert_snapshot_failure(&clock_state);
    clock_state.io.write32 = write32;
    assert(mt_timebase_stop(&clock_state));
    unsigned reads = snapshot_reads;
    assert_snapshot_failure(&clock_state);
    assert(snapshot_reads == reads);

    prepare_snapshot(); advance(32001u);
    assert_snapshot_failure(&clock_state);
    assert(clock_state.fault == MT_TIMEBASE_SERVICE_FAULT);
    reads = snapshot_reads;
    advance(1u); assert_snapshot_failure(&clock_state);
    assert(snapshot_reads == reads && snapshot_cnt_reads == 1u);
    puts("PASS snapshot invalid arguments, stopped/sticky fault state and unchanged failure outputs");
}

static void test_validation_before_and_after_counter(void) {
    static const struct { uint32_t address, value; mt_timebase_fault fault; } cases[] = {
        {ENR, 0u, MT_TIMEBASE_CLOCK_FAULT},
        {RST, 1u, MT_TIMEBASE_CLOCK_FAULT},
        {CR, UINT32_C(0x03030001), MT_TIMEBASE_CLOCK_FAULT},
        {CFGR, UINT32_C(0x001d840b), MT_TIMEBASE_CLOCK_FAULT},
        {CR1, 0u, MT_TIMEBASE_TIMER_FAULT},
        {BASE + 4u, 1u, MT_TIMEBASE_TIMER_FAULT},
        {BASE + 8u, 1u, MT_TIMEBASE_TIMER_FAULT},
        {DIER, 1u, MT_TIMEBASE_TIMER_FAULT},
        {BASE + 32u, 1u, MT_TIMEBASE_TIMER_FAULT},
        {BASE + 24u, 1u, MT_TIMEBASE_TIMER_FAULT},
        {BASE + 28u, 1u, MT_TIMEBASE_TIMER_FAULT},
        {PSC, 70u, MT_TIMEBASE_TIMER_FAULT},
        {ARR, 65534u, MT_TIMEBASE_TIMER_FAULT}
    };
    for (unsigned post = 0; post < 2u; ++post) {
        for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
            prepare_snapshot(); seed_time(UINT64_C(999)); advance(33u);
            uint32_t old = *reg(cases[i].address);
            if (post) {
                mutate_address = cases[i].address; mutate_value = cases[i].value;
            } else *reg(cases[i].address) = cases[i].value;
            unsigned writes = hw.count;
            assert_snapshot_failure(&clock_state);
            assert(snapshot_cnt_reads == post && hw.count == writes);
            assert(clock_state.state == MT_TIMEBASE_FAULTED);
            assert(clock_state.fault == cases[i].fault);
            assert(clock_state.us == 999u && clock_state.ms == 0u);
            assert(clock_state.remainder_us == 999u && clock_state.last_counter == 999u);
            /* Restoring the hardware must not revive a stale snapshot. */
            *reg(cases[i].address) = old;
            unsigned reads = snapshot_reads;
            assert_snapshot_failure(&clock_state);
            assert(snapshot_reads == reads);
        }
    }
    puts("PASS all 13 pre/post-CNT configuration failures reject without publishing time");
}

static void test_snapshot_delta_boundary(void) {
    for (unsigned wrap = 0; wrap < 2u; ++wrap) {
        uint64_t initial = wrap ? UINT64_C(65530) : UINT64_C(1);
        prepare_snapshot(); seed_time(initial); advance(32000u);
        assert_pair(initial + UINT64_C(32000));
        advance(32001u);
        assert_snapshot_failure(&clock_state);
        assert(clock_state.fault == MT_TIMEBASE_SERVICE_FAULT);
        assert(clock_state.us == (uint32_t)(initial + UINT64_C(32000)));
        assert(clock_state.ms == (uint32_t)((initial + UINT64_C(32000)) / UINT64_C(1000)));
        assert(snapshot_cnt_reads == 2u);
    }
    puts("PASS 32000-us accepted and 32001-us rejected, including CNT rollover");
}

static void test_preemption_after_counter_sample(void) {
    static const uint64_t seeds[] = {
        UINT64_C(999), UINT64_C(65530), UINT64_C(4294967295),
        UINT64_C(4294967295999)
    };
    for (unsigned i = 0; i < sizeof seeds / sizeof seeds[0]; ++i) {
        prepare_snapshot(); seed_time(seeds[i]);
        delay_after_counter = 5000u;
        assert_pair(seeds[i]);
        assert(*reg(CNT) == (uint32_t)((seeds[i] + UINT64_C(5000)) & UINT64_C(65535)));
        delay_after_counter = 0u;
        assert_pair(seeds[i] + UINT64_C(5000));
    }
    puts("PASS 5-ms modeled preemption preserves one coherent earlier pair; next sample accounts for elapsed time");
}

#ifndef MT_COHERENT_CLOCK_MAIN
#define MT_COHERENT_CLOCK_MAIN main
#endif
int MT_COHERENT_CLOCK_MAIN(void) {
    assert(historical_timebase_main() == 0);
    test_coherent_rollover();
    test_snapshot_arguments_and_stale_states();
    test_validation_before_and_after_counter();
    test_snapshot_delta_boundary();
    test_preemption_after_counter_sample();
    return 0;
}
