/* Reuse both existing suites and their register fixture; no second production
 * implementation is embedded here. Expected bus operations are literal oracles. */
#define MT_COHERENT_CLOCK_MAIN coherent_timebase_main
#include "../coherent-clock/test_snapshot.c"
#undef MT_COHERENT_CLOCK_MAIN

typedef struct { char kind; uint32_t address, value; } bus_event;
static bus_event actual[96], expected[96];
static unsigned actual_count, expected_count;
static FILE *transcript;
static uint32_t corrupt_after_start_address, corrupt_after_start_value;
static unsigned reset_after_stop;

/* Exact validation order and the fixture's healthy register values. */
static const struct { uint32_t address, healthy, broken; } config[] = {
    {ENR, 0x1021u, 0u}, {RST, 0u, 1u},
    {CR, 0x03030000u, 0x03030001u},
    {CFGR, 0x001d840au, 0x001d840bu},
    {CR1, 1u, 0u}, {BASE + 4u, 0u, 1u}, {BASE + 8u, 0u, 1u},
    {DIER, 0u, 1u}, {BASE + 32u, 0u, 1u},
    {BASE + 24u, 0u, 1u}, {BASE + 28u, 0u, 1u},
    {PSC, 71u, 70u}, {ARR, 65535u, 65534u}
};
static const bus_event initial[] = {
    {'R', CR, 0x03030000u}, {'R', CFGR, 0x001d840au},
    {'R', ENR, 0x1021u}, {'R', RST, 0u},
    {'R', CR, 0x03030000u}, {'R', CFGR, 0x001d840au},
    {'R', CR1, 0u}, {'R', DIER, 0u},
    {'W', CR1, 0u}, {'W', BASE + 4u, 0u}, {'W', BASE + 8u, 0u},
    {'W', DIER, 0u}, {'W', BASE + 32u, 0u},
    {'W', BASE + 24u, 0u}, {'W', BASE + 28u, 0u},
    {'W', PSC, 71u}, {'W', ARR, 65535u}, {'W', EGR, 1u},
    {'W', BASE + 16u, 0u}, {'W', CNT, 0u}, {'W', CR1, 1u}
};
static void add_actual(char kind, uint32_t address, uint32_t value) {
    assert(actual_count < sizeof actual / sizeof actual[0]);
    actual[actual_count++] = (bus_event){kind, address, value};
}
static void expect(char kind, uint32_t address, uint32_t value) {
    assert(expected_count < sizeof expected / sizeof expected[0]);
    expected[expected_count++] = (bus_event){kind, address, value};
}
static uint32_t trace_read32(void *context, uint32_t address) {
    uint32_t value = snapshot_read32(context, address);
    add_actual('R', address, value);
    return value;
}
static void trace_write32(void *context, uint32_t address, uint32_t value) {
    add_actual('W', address, value);
    write32(context, address, value);
    if (address == CR1 && value == 1u && corrupt_after_start_address)
        *reg(corrupt_after_start_address) = corrupt_after_start_value;
    if (address == CR1 && value == 0u && reset_after_stop) *reg(RST) = 1u;
}
static void clear_trace(void) { actual_count = expected_count = 0u; }
static void prepare_trace(void) {
    prepare_snapshot();
    clock_state.io.read32 = trace_read32;
    clock_state.io.write32 = trace_write32;
    corrupt_after_start_address = corrupt_after_start_value = reset_after_stop = 0u;
    clear_trace();
}
static void prepare_initial(void) {
    prepare_trace();
    memset(&hw, 0, sizeof hw);
    memset(&clock_state, 0, sizeof clock_state);
    hw.cr = 0x03030000u; hw.cfgr = 0x001d840au; hw.enr = 0x1021u;
}
static void expect_config(unsigned count) {
    assert(count <= 13u);
    for (unsigned i = 0; i < count; ++i)
        expect('R', config[i].address, config[i].healthy);
}
static void expect_update(uint32_t counter) {
    expect_config(13u); expect('R', CNT, counter); expect_config(13u);
}
static void expect_initial(unsigned count) {
    assert(count <= sizeof initial / sizeof initial[0]);
    for (unsigned i = 0; i < count; ++i)
        expect(initial[i].kind, initial[i].address, initial[i].value);
}
static void unchanged_payload(const mt_timebase *old) {
    assert(clock_state.io.read32 == old->io.read32);
    assert(clock_state.io.write32 == old->io.write32);
    assert(clock_state.io.context == old->io.context);
    assert(clock_state.clock_cr == old->clock_cr && clock_state.clock_cfgr == old->clock_cfgr);
    assert(clock_state.us == old->us && clock_state.ms == old->ms);
    assert(clock_state.remainder_us == old->remainder_us);
    assert(clock_state.last_counter == old->last_counter);
    assert(clock_state.initialized == old->initialized);
    assert(clock_state.owns_timer == old->owns_timer);
}
static void check(const char *name, int result, uint32_t us, uint32_t ms) {
    assert(actual_count == expected_count);
    fprintf(transcript, "%s result=%d us=%u ms=%u events=%u\n", name, result,
            (unsigned)us, (unsigned)ms, actual_count);
    for (unsigned i = 0; i < actual_count; ++i) {
        assert(actual[i].kind == expected[i].kind);
        assert(actual[i].address == expected[i].address);
        assert(actual[i].value == expected[i].value);
        fprintf(transcript, "%c %08x %08x\n", actual[i].kind,
                (unsigned)actual[i].address, (unsigned)actual[i].value);
    }
    assert(clock_state.io.read32 == (clock_state.initialized ? trace_read32 : NULL));
    assert(clock_state.io.write32 == (clock_state.initialized ? trace_write32 : NULL));
    assert(clock_state.io.context == NULL);
    fprintf(transcript, "state=%u fault=%u initialized=%u owns=%u cr=%08x cfgr=%08x us=%u ms=%u rem=%u cnt=%u read_cb=%u write_cb=%u null_context=%u\n",
            (unsigned)clock_state.state, (unsigned)clock_state.fault,
            clock_state.initialized, clock_state.owns_timer,
            (unsigned)clock_state.clock_cr, (unsigned)clock_state.clock_cfgr,
            (unsigned)clock_state.us, (unsigned)clock_state.ms,
            (unsigned)clock_state.remainder_us, (unsigned)clock_state.last_counter,
            (unsigned)(clock_state.io.read32 == trace_read32),
            (unsigned)(clock_state.io.write32 == trace_write32),
            (unsigned)(clock_state.io.context == NULL));
}
static void test_exact_update_traces(void) {
    uint32_t us = 0u, ms = 0u;
    prepare_trace(); seed_time(UINT64_C(4294967295)); advance(17u);
    assert(mt_timebase_snapshot(&clock_state, &us, &ms) == 1);
    assert(us == 16u && ms == 4294967u && clock_state.remainder_us == 312u);
    assert(clock_state.last_counter == 16u && mt_timebase_healthy(&clock_state));
    expect_update(16u); check("snapshot-success-us32-wrap", 1, us, ms);

    for (unsigned post = 0; post < 2u; ++post) {
        for (unsigned i = 0; i < 13u; ++i) {
            char name[64];
            prepare_trace(); seed_time(UINT64_C(999)); advance(33u);
            mt_timebase old = clock_state;
            if (post) { mutate_address = config[i].address; mutate_value = config[i].broken; }
            else *reg(config[i].address) = config[i].broken;
            us = 0x13579bdfu; ms = 0x2468ace0u;
            assert(!mt_timebase_snapshot(&clock_state, &us, &ms));
            assert(us == 0x13579bdfu && ms == 0x2468ace0u);
            unchanged_payload(&old);
            assert(clock_state.state == MT_TIMEBASE_FAULTED);
            assert(clock_state.fault == (i < 4u ? MT_TIMEBASE_CLOCK_FAULT : MT_TIMEBASE_TIMER_FAULT));
            if (post) { expect_config(13u); expect('R', CNT, 1032u); }
            expect_config(i + 1u); expected[expected_count - 1u].value = config[i].broken;
            (void)snprintf(name, sizeof name, "snapshot-%s-failure-%02u", post ? "post" : "pre", i);
            check(name, 0, us, ms);
            *reg(config[i].address) = config[i].healthy;
            clear_trace();
            assert(!mt_timebase_snapshot(&clock_state, &us, &ms));
            assert(mt_timebase_now_us(&clock_state) == 999u);
            assert(mt_timebase_now_ms(&clock_state) == 0u);
            unchanged_payload(&old);
            assert(clock_state.state == MT_TIMEBASE_FAULTED);
            check("sticky-fault-after-restore", 0, us, ms);
        }
    }
    /* Fresh hardware changes after a previously accepted snapshot are not cached. */
    prepare_trace(); seed_time(UINT64_C(999));
    assert(mt_timebase_snapshot(&clock_state, &us, &ms) == 1);
    assert(us == 999u && ms == 0u);
    expect_update(999u); check("consecutive-call-healthy", 1, us, ms);
    mt_timebase accepted = clock_state;
    clear_trace(); *reg(PSC) = 70u; advance(33u);
    assert(!mt_timebase_snapshot(&clock_state, &us, &ms));
    assert(us == 999u && ms == 0u); unchanged_payload(&accepted);
    assert(clock_state.state == MT_TIMEBASE_FAULTED && clock_state.fault == MT_TIMEBASE_TIMER_FAULT);
    expect_config(12u); expected[expected_count - 1u].value = 70u;
    check("consecutive-call-new-fault", 0, us, ms);
    /* Configuration faults retain priority over a simultaneous oversized delta. */
    prepare_trace(); seed_time(UINT64_C(999)); advance(32001u);
    mutate_address = PSC; mutate_value = 70u;
    assert_snapshot_failure(&clock_state);
    assert(clock_state.fault == MT_TIMEBASE_TIMER_FAULT);
    expect_config(13u); expect('R', CNT, 33000u); expect_config(12u);
    expected[expected_count - 1u].value = 70u;
    check("timer-before-service-fault", 0, 0x13579bdfu, 0x2468ace0u);
    prepare_trace(); seed_time(UINT64_C(999)); advance(32001u);
    mt_timebase old = clock_state;
    assert_snapshot_failure(&clock_state); unchanged_payload(&old);
    assert(clock_state.state == MT_TIMEBASE_FAULTED && clock_state.fault == MT_TIMEBASE_SERVICE_FAULT);
    expect_update(33000u); check("service-fault-after-full-validation", 0, 0x13579bdfu, 0x2468ace0u);
    puts("PASS exact 27-read success and all 26 pre/post failure prefixes; unchanged outputs/state and fault priority");
}
static void test_exact_init_traces(void) {
    mt_timebase_io io = {trace_read32, trace_write32, NULL};
    prepare_initial();
    assert(mt_timebase_init(&clock_state, &io, 72000000u) == 1);
    expect_initial(21u); expect_config(13u); expect_update(0u);
    assert(actual_count == 61u && hw.count == 13u);
    assert(clock_state.owns_timer && mt_timebase_healthy(&clock_state));
    assert(clock_state.us == 0u && clock_state.ms == 0u);
    assert(clock_state.remainder_us == 0u && clock_state.last_counter == 0u);
    check("init-success", 1, 0u, 0u);
    mt_timebase old = clock_state;
    clear_trace(); assert(!mt_timebase_init(&clock_state, &io, 72000000u));
    unchanged_payload(&old); assert(mt_timebase_healthy(&clock_state));
    check("init-owned-refused", 0, 0u, 0u);
    prepare_initial(); assert(!mt_timebase_init(&clock_state, &io, 48000000u));
    assert(!clock_state.initialized && !clock_state.owns_timer);
    check("init-invalid-frequency", 0, 0u, 0u);
    /* Refusal before ownership: invalid RCC signature, gate/reset, busy CEN/DIER. */
    static const struct { uint32_t address, value; unsigned count, bad; mt_timebase_fault fault; } pre[] = {
        {CR, 0u, 2u, 0u, MT_TIMEBASE_CLOCK_FAULT},
        {CFGR, 0u, 2u, 1u, MT_TIMEBASE_CLOCK_FAULT},
        {ENR, 0u, 3u, 2u, MT_TIMEBASE_CLOCK_FAULT},
        {RST, 1u, 4u, 3u, MT_TIMEBASE_CLOCK_FAULT},
        {CR1, 1u, 7u, 6u, MT_TIMEBASE_TIMER_FAULT},
        {DIER, 1u, 8u, 7u, MT_TIMEBASE_TIMER_FAULT}
    };
    for (unsigned i = 0; i < sizeof pre / sizeof pre[0]; ++i) {
        char name[48]; prepare_initial(); *reg(pre[i].address) = pre[i].value;
        assert(!mt_timebase_init(&clock_state, &io, 72000000u));
        assert(clock_state.state == MT_TIMEBASE_FAULTED && clock_state.fault == pre[i].fault);
        assert(clock_state.initialized && !clock_state.owns_timer && hw.count == 0u);
        expect_initial(pre[i].count); expected[pre[i].bad].value = pre[i].value;
        (void)snprintf(name, sizeof name, "init-preflight-failure-%02u", i);
        check(name, 0, 0u, 0u);
    }
    /* Each configuration readback fails immediately after CEN is written.
     * The same prefix and fault class must hold, with ownership retained. */
    for (unsigned i = 0; i < 13u; ++i) {
        char name[48]; prepare_initial();
        corrupt_after_start_address = config[i].address;
        corrupt_after_start_value = config[i].broken;
        assert(!mt_timebase_init(&clock_state, &io, 72000000u));
        assert(clock_state.state == MT_TIMEBASE_FAULTED && clock_state.owns_timer);
        assert(clock_state.fault == (i < 4u ? MT_TIMEBASE_CLOCK_FAULT : MT_TIMEBASE_TIMER_FAULT));
        assert(clock_state.us == 0u && clock_state.ms == 0u && hw.count == 13u);
        expect_initial(21u); expect_config(i + 1u);
        expected[expected_count - 1u].value = config[i].broken;
        (void)snprintf(name, sizeof name, "init-readback-failure-%02u", i);
        check(name, 0, 0u, 0u);
    }
    puts("PASS init exact 48-read/13-write order, early failures and ownership retention");
}
static void expect_stop(void) {
    expect('R', ENR, 0x1021u); expect('R', RST, 0u);
    expect('W', CR1, 0u); expect('R', CR1, 0u);
    expect('R', ENR, 0x1021u); expect('R', RST, 0u);
}
static void test_exact_stop_traces(void) {
    prepare_trace(); seed_time(UINT64_C(65539));
    mt_timebase old = clock_state;
    assert(mt_timebase_stop(&clock_state) == 1); expect_stop();
    assert(clock_state.state == MT_TIMEBASE_STOPPED && !clock_state.owns_timer);
    assert(clock_state.fault == MT_TIMEBASE_NO_FAULT);
    old.owns_timer = 0u; unchanged_payload(&old);
    check("stop-success", 1, clock_state.us, clock_state.ms);
    clear_trace(); assert(mt_timebase_stop(&clock_state) == 1);
    assert_snapshot_failure(&clock_state);
    check("stop-repeat-and-snapshot-refusal", 1, clock_state.us, clock_state.ms);
    /* Every short circuit, including readback failure before the postcheck. */
    for (unsigned i = 0; i < 5u; ++i) {
        char name[48]; prepare_trace(); seed_time(UINT64_C(999)); old = clock_state;
        if (i == 0u) hw.enr &= ~1u;
        if (i == 1u) hw.rst = 1u;
        if (i == 2u) hw.no_stop = 1u;
        if (i == 3u) hw.gate_loss_on_stop = 1u;
        if (i == 4u) reset_after_stop = 1u;
        assert(!mt_timebase_stop(&clock_state)); unchanged_payload(&old);
        assert(clock_state.state == MT_TIMEBASE_FAULTED && clock_state.fault == MT_TIMEBASE_STOP_FAULT);
        expect_stop();
        if (i == 0u) { expected_count = 1u; expected[0].value = 0x1020u; }
        if (i == 1u) { expected_count = 2u; expected[1].value = 1u; }
        if (i == 2u) { expected_count = 4u; expected[3].value = 1u; }
        if (i == 3u) { expected_count = 5u; expected[4].value = 0x1020u; }
        if (i == 4u) expected[5].value = 1u;
        (void)snprintf(name, sizeof name, "stop-failure-%02u", i);
        check(name, 0, clock_state.us, clock_state.ms);
        hw.enr = 0x1021u; hw.rst = 0u; hw.no_stop = hw.gate_loss_on_stop = reset_after_stop = 0u;
        clear_trace(); assert(mt_timebase_stop(&clock_state) == 1); expect_stop();
        assert(clock_state.state == MT_TIMEBASE_STOPPED && !clock_state.owns_timer);
        assert(clock_state.fault == MT_TIMEBASE_STOP_FAULT && !mt_timebase_healthy(&clock_state));
        old.owns_timer = 0u; unchanged_payload(&old);
        check("stop-retry-retains-fault", 1, clock_state.us, clock_state.ms);
    }
    puts("PASS Stop exact read/write order, all five short circuits, ownership, retry and retained faults");
}
int main(int argc, char **argv) {
    assert(argc == 2);
    transcript = fopen(argv[1], "w"); assert(transcript);
    assert(coherent_timebase_main() == 0);
    test_exact_update_traces(); test_exact_init_traces(); test_exact_stop_traces();
    assert(fclose(transcript) == 0);
    return 0;
}
