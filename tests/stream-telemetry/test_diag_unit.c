/* Native software-only tests. Production mt_stream_diag.c is a separate TU.
 * Fake MMIO is a scripted model, not Cortex-M3 hardware or measured time. */
#include "mt_stream_diag.h"
#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEMCR UINT32_C(0xe000edfc)
#define DWT_CTRL UINT32_C(0xe0001000)
#define DWT_CYCCNT UINT32_C(0xe0001004)
#define TRACE_ENABLE (UINT32_C(1) << 24)
#define NO_CYCCNT (UINT32_C(1) << 25)
#define COUNT_ENABLE UINT32_C(1)
#define FLAG_UNAVAILABLE UINT32_C(1)
#define FLAG_SATURATED UINT32_C(2)
#define FLAG_FAULT UINT32_C(4)
#define FLAG_REPLACED UINT32_C(8)
#define FLAG_INVALID UINT32_C(16)
#define BUILD UINT32_C(0x28dab682)
#define NONCE UINT64_C(0xfedcba9876543210)
#define CHECK(expr) do { if (!(expr)) { \
    (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr); \
    exit(EXIT_FAILURE); \
} } while (0)

struct write_event { uint32_t address, value; };
struct fake_dwt {
    uint32_t demcr, control, next_cycle, stride;
    unsigned reject_trace, reject_enable;
    unsigned reads, cycle_reads, writes, unexpected;
    struct write_event written[8];
    uint32_t script[40];
    size_t script_length, script_used;
};

static struct fake_dwt fresh_bus(void)
{
    struct fake_dwt bus = {0};
    bus.demcr = UINT32_C(0x00a00550);
    bus.control = UINT32_C(0x00001402);
    bus.next_cycle = UINT32_C(100);
    bus.stride = 1u;
    return bus;
}

static uint32_t fake_read(void *context, uint32_t address)
{
    struct fake_dwt *bus = context;
    ++bus->reads;
    switch (address) {
    case DEMCR: return bus->demcr;
    case DWT_CTRL: return bus->control;
    case DWT_CYCCNT: {
        uint32_t value;
        ++bus->cycle_reads;
        if (bus->script_used < bus->script_length)
            return bus->script[bus->script_used++];
        value = bus->next_cycle;
        bus->next_cycle += bus->stride;
        return value;
    }
    default:
        ++bus->unexpected;
        CHECK(0 && "Unexpected MMIO read");
        return 0u;
    }
}

static void fake_write(void *context, uint32_t address, uint32_t value)
{
    struct fake_dwt *bus = context;
    CHECK(bus->writes < sizeof(bus->written) / sizeof(bus->written[0]));
    bus->written[bus->writes++] = (struct write_event){address, value};
    switch (address) {
    case DEMCR:
        bus->demcr = bus->reject_trace ? (value & ~TRACE_ENABLE) : value;
        break;
    case DWT_CTRL:
        bus->control = bus->reject_enable ? (value & ~COUNT_ENABLE) : value;
        break;
    default:
        ++bus->unexpected;
        /* A CYCCNT reset or unrelated control write is always a test failure. */
        CHECK(0 && "Unexpected MMIO write, including forbidden CYCCNT reset");
        break;
    }
}

static mt_stream_diag_record snapshot(void)
{
    return mt_stream_diagnostics;
}

static void unchanged(const mt_stream_diag_record *expected)
{
    mt_stream_diag_record now = snapshot();
    CHECK(memcmp(expected, &now, sizeof(now)) == 0);
}

static void init_ok(struct fake_dwt *bus)
{
    CHECK(mt_stream_diag_init(fake_read, fake_write, bus, BUILD) == 1);
    CHECK(mt_stream_diagnostics.magic == UINT32_C(0x4d544447));
    CHECK(mt_stream_diagnostics.version == 1u);
    CHECK(mt_stream_diagnostics.bytes == 136u);
    CHECK(mt_stream_diagnostics.firmware_build == BUILD);
    CHECK(mt_stream_diagnostics.phase == 1u);
    CHECK(mt_stream_diagnostics.flags == 0u);
    CHECK(bus->cycle_reads == 2u);
    CHECK(bus->unexpected == 0u);
}

static void start_ok(struct fake_dwt *bus)
{
    init_ok(bus);
    mt_stream_diag_begin(7u, NONCE);
    CHECK(mt_stream_diagnostics.phase == 2u);
    CHECK(mt_stream_diagnostics.generation == 7u);
    CHECK(mt_stream_diagnostics.nonce_low == UINT32_C(0x76543210));
    CHECK(mt_stream_diagnostics.nonce_high == UINT32_C(0xfedcba98));
    CHECK(mt_stream_diagnostics.start_cycle == 102u);
}

static void observe_at(struct fake_dwt *bus, uint32_t end, uint32_t begin,
                       unsigned freeze)
{
    bus->next_cycle = end;
    mt_stream_diag_observe(begin, 1u, 4u, 11u, 2u, 1u, 0u,
                           3u, 0u, 0u, 0u, freeze);
}

static void inactive_hooks(void)
{
    mt_stream_diag_begin(90u, UINT64_MAX);
    mt_stream_diag_poll(UINT32_MAX);
    mt_stream_diag_copy(UINT32_MAX);
    mt_stream_diag_tx(UINT32_MAX);
    mt_stream_diag_note(9u, 9u, 9u, 9u, 9u, 9u);
    mt_stream_diag_observe(9u, 9u, 9u, 9u, 9u, 9u, 9u,
                           9u, 9u, 9u, 9u, 1u);
    mt_stream_diag_replaced(9u, 9u, 9u);
}

static void test_uninitialized(void)
{
    mt_stream_diag_record before = snapshot();
    CHECK(before.phase == 0u);
    CHECK(mt_stream_diag_cycle() == 0u);
    inactive_hooks();
    unchanged(&before);
}

static void test_null_callbacks(void)
{
    struct fake_dwt bus = fresh_bus();
    mt_stream_diag_record before = snapshot();
    CHECK(mt_stream_diag_init(NULL, fake_write, &bus, BUILD) == 0);
    CHECK(mt_stream_diag_init(fake_read, NULL, &bus, BUILD) == 0);
    unchanged(&before);
    CHECK(bus.reads == 0u && bus.writes == 0u);
    init_ok(&bus); /* Invalid callback arguments do not consume the attempt. */
}

static void test_enable_preserves(void)
{
    struct fake_dwt bus = fresh_bus();
    uint32_t old_demcr = bus.demcr, old_control = bus.control;
    init_ok(&bus);
    CHECK(bus.writes == 2u);
    CHECK(bus.written[0].address == DEMCR);
    CHECK(bus.written[0].value == (old_demcr | TRACE_ENABLE));
    CHECK(bus.written[1].address == DWT_CTRL);
    CHECK(bus.written[1].value == (old_control | COUNT_ENABLE));
    CHECK(bus.demcr == (old_demcr | TRACE_ENABLE));
    CHECK(bus.control == (old_control | COUNT_ENABLE));
    CHECK(bus.next_cycle == 102u); /* No zeroing of the existing counter. */
}

static void test_already_enabled(void)
{
    struct fake_dwt bus = fresh_bus();
    bus.demcr |= TRACE_ENABLE;
    bus.control |= COUNT_ENABLE;
    bus.next_cycle = UINT32_MAX;
    init_ok(&bus); /* The bounded init probe also accepts modulo rollover. */
    CHECK(bus.next_cycle == 1u);
    CHECK(bus.writes == 2u);
    CHECK(bus.written[0].value == bus.demcr);
    CHECK(bus.written[1].value == bus.control);
}

static void failed_init(unsigned mode)
{
    struct fake_dwt bus = fresh_bus();
    mt_stream_diag_record before;
    unsigned reads, writes;
    if (mode == 0u) bus.control |= NO_CYCCNT;
    if (mode == 1u) bus.reject_trace = 1u;
    if (mode == 2u) bus.reject_enable = 1u;
    if (mode == 3u) bus.stride = 0u;
    if (mode == 4u) {
        bus.script[0] = 100u;
        bus.script[1] = 100u;
        bus.script[2] = 101u;
        bus.script_length = 3u;
    }
    CHECK(mt_stream_diag_init(fake_read, fake_write, &bus, BUILD) == 0);
    CHECK(mt_stream_diagnostics.phase == 4u);
    CHECK(mt_stream_diagnostics.magic == UINT32_C(0x4d544447));
    CHECK(mt_stream_diagnostics.version == 1u);
    CHECK(mt_stream_diagnostics.bytes == 136u);
    CHECK(mt_stream_diagnostics.firmware_build == BUILD);
    CHECK(mt_stream_diagnostics.flags ==
          (FLAG_UNAVAILABLE | (mode >= 3u ? FLAG_INVALID : 0u)));
    CHECK(bus.writes == (mode <= 1u ? 1u : 2u));
    CHECK(bus.cycle_reads == (mode == 3u ? 33u : mode == 4u ? 3u : 0u));
    CHECK(bus.unexpected == 0u);
    before = snapshot(); reads = bus.reads; writes = bus.writes;
    inactive_hooks();
    CHECK(mt_stream_diag_init(fake_read, fake_write, &bus, BUILD + 1u) == 0);
    unchanged(&before);
    CHECK(bus.reads == reads && bus.writes == writes);
}
static void test_unsupported(void) { failed_init(0u); }
static void test_trace_rejected(void) { failed_init(1u); }
static void test_enable_rejected(void) { failed_init(2u); }
static void test_probe_stuck(void) { failed_init(3u); }
static void test_probe_delayed(void) { failed_init(4u); }

static void test_armed_and_first_begin(void)
{
    struct fake_dwt bus = fresh_bus();
    mt_stream_diag_record before;
    unsigned reads, writes;
    init_ok(&bus);
    before = snapshot(); reads = bus.reads; writes = bus.writes;
    mt_stream_diag_begin(0u, NONCE);
    mt_stream_diag_poll(99u); mt_stream_diag_copy(99u); mt_stream_diag_tx(99u);
    mt_stream_diag_note(99u, 99u, 99u, 99u, 99u, 99u);
    mt_stream_diag_observe(99u, 99u, 99u, 99u, 99u, 99u, 99u,
                           99u, 99u, 99u, 99u, 1u);
    mt_stream_diag_replaced(99u, 99u, 99u);
    CHECK(mt_stream_diag_init(fake_read, fake_write, &bus, BUILD + 1u) == 0);
    unchanged(&before);
    CHECK(bus.reads == reads && bus.writes == writes);
    mt_stream_diag_begin(UINT32_MAX, NONCE);
    CHECK(mt_stream_diagnostics.phase == 2u);
    CHECK(mt_stream_diagnostics.generation == UINT32_MAX);
    CHECK(mt_stream_diagnostics.nonce_low == UINT32_C(0x76543210));
    CHECK(mt_stream_diagnostics.nonce_high == UINT32_C(0xfedcba98));
    CHECK(mt_stream_diagnostics.start_cycle == 102u);
    before = snapshot(); reads = bus.reads;
    mt_stream_diag_begin(1u, 0u);
    CHECK(mt_stream_diag_init(fake_read, fake_write, &bus, BUILD + 1u) == 0);
    unchanged(&before);
    CHECK(bus.reads == reads && bus.writes == writes);
}

static void invalid_source(unsigned mode)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    if (mode == 0u) bus.demcr &= ~TRACE_ENABLE;
    if (mode == 1u) bus.control &= ~COUNT_ENABLE;
    if (mode == 2u) bus.control |= NO_CYCCNT;
    if (mode == 3u) bus.next_cycle = 102u;
    (void)mt_stream_diag_cycle();
    CHECK(mt_stream_diagnostics.flags == FLAG_INVALID);
    CHECK(mt_stream_diagnostics.phase == 2u);
    bus.demcr |= TRACE_ENABLE;
    bus.control = (bus.control | COUNT_ENABLE) & ~NO_CYCCNT;
    bus.next_cycle = 200u;
    CHECK(mt_stream_diag_cycle() == 200u);
    CHECK(mt_stream_diagnostics.flags == FLAG_INVALID);
    mt_stream_diag_copy(10u);
    CHECK(mt_stream_diagnostics.copy_count == 1u);
    CHECK(mt_stream_diagnostics.flags == FLAG_INVALID);
}
static void test_invalid_trace(void) { invalid_source(0u); }
static void test_invalid_enable(void) { invalid_source(1u); }
static void test_invalid_support(void) { invalid_source(2u); }
static void test_invalid_stationary(void) { invalid_source(3u); }

static void test_poll_wrap_maxima(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    bus.next_cycle = UINT32_MAX - 4u;
    mt_stream_diag_poll(20u);
    CHECK(mt_stream_diagnostics.poll_count == 1u);
    CHECK(mt_stream_diagnostics.poll_gap_max_cycles == 0u);
    CHECK(mt_stream_diagnostics.driver_observed_poll_gap_us == 20u);
    bus.next_cycle = 3u; mt_stream_diag_poll(9u);
    CHECK(mt_stream_diagnostics.poll_gap_max_cycles == 8u);
    CHECK(mt_stream_diagnostics.driver_observed_poll_gap_us == 20u);
    bus.next_cycle = 9u; mt_stream_diag_poll(25u);
    CHECK(mt_stream_diagnostics.poll_count == 3u);
    CHECK(mt_stream_diagnostics.poll_gap_max_cycles == 8u);
    CHECK(mt_stream_diagnostics.driver_observed_poll_gap_us == 25u);
    CHECK(mt_stream_diagnostics.flags == 0u);
}

static void test_step_wrap_maxima(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    observe_at(&bus, UINT32_MAX - 5u, UINT32_MAX - 15u, 0u);
    CHECK(mt_stream_diagnostics.step_body_max_cycles == 10u);
    CHECK(mt_stream_diagnostics.step_gap_max_cycles == 0u);
    observe_at(&bus, 13u, 4u, 0u);
    CHECK(mt_stream_diagnostics.step_body_max_cycles == 10u);
    CHECK(mt_stream_diagnostics.step_gap_max_cycles == 20u);
    observe_at(&bus, 40u, 25u, 0u);
    CHECK(mt_stream_diagnostics.step_count == 3u);
    CHECK(mt_stream_diagnostics.step_body_max_cycles == 15u);
    CHECK(mt_stream_diagnostics.step_gap_max_cycles == 21u);
    CHECK(mt_stream_diagnostics.flags == 0u);
    CHECK(mt_stream_diagnostics.last_cycle == 40u);
}

static void test_step_body_wrap(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    observe_at(&bus, 8u, UINT32_MAX - 7u, 0u);
    CHECK(mt_stream_diagnostics.step_body_max_cycles == 16u);
    CHECK(mt_stream_diagnostics.step_count == 1u);
    CHECK(mt_stream_diagnostics.flags == 0u);
}

static void test_tx_wrap_maxima(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    bus.next_cycle = 5u; mt_stream_diag_tx(UINT32_MAX - 7u);
    CHECK(mt_stream_diagnostics.tx_max_cycles == 13u);
    bus.next_cycle = 15u; mt_stream_diag_tx(8u);
    CHECK(mt_stream_diagnostics.tx_max_cycles == 13u);
    bus.next_cycle = 40u; mt_stream_diag_tx(20u);
    CHECK(mt_stream_diagnostics.tx_count == 3u);
    CHECK(mt_stream_diagnostics.tx_max_cycles == 20u);
    CHECK(mt_stream_diagnostics.flags == 0u);
}

static void test_copy_maxima(void)
{
    struct fake_dwt bus = fresh_bus();
    unsigned reads;
    start_ok(&bus); reads = bus.reads;
    mt_stream_diag_copy(7u); mt_stream_diag_copy(3u); mt_stream_diag_copy(9u);
    CHECK(mt_stream_diagnostics.copy_count == 3u);
    CHECK(mt_stream_diagnostics.copy_max_us == 9u);
    CHECK(bus.reads == reads); /* Existing driver delta is used directly. */
}

static void saturation(unsigned which)
{
    struct fake_dwt bus = fresh_bus();
    volatile uint32_t *counter;
    unsigned iteration;
    start_ok(&bus);
    if (which == 0u) counter = &mt_stream_diagnostics.step_count;
    else if (which == 1u) counter = &mt_stream_diagnostics.tx_count;
    else if (which == 2u) counter = &mt_stream_diagnostics.poll_count;
    else counter = &mt_stream_diagnostics.copy_count;
    /* Explicit public-record seeding replaces ~2^32 calls; no private state
     * is reset or reached. This is a boundary test, not an endurance claim. */
    *counter = UINT32_MAX - 1u;
    for (iteration = 0u; iteration < 3u; ++iteration) {
        if (which == 0u) observe_at(&bus, 200u + iteration, 199u + iteration, 0u);
        else if (which == 1u) mt_stream_diag_tx(100u);
        else if (which == 2u) mt_stream_diag_poll(iteration);
        else mt_stream_diag_copy(iteration);
        CHECK(*counter == UINT32_MAX);
        CHECK(mt_stream_diagnostics.flags == (iteration == 0u ? 0u : FLAG_SATURATED));
    }
}
static void test_saturate_step(void) { saturation(0u); }
static void test_saturate_tx(void) { saturation(1u); }
static void test_saturate_poll(void) { saturation(2u); }
static void test_saturate_copy(void) { saturation(3u); }

static void first_fault(unsigned which)
{
    struct fake_dwt bus = fresh_bus();
    uint32_t result = which == 0u ? 3u : UINT32_MAX;
    uint32_t local = which == 1u ? 17u : 0u;
    uint32_t reason = which == 2u ? 19u : 0u;
    uint32_t adc = which == 3u ? 23u : 0u;
    unsigned reads;
    start_ok(&bus);
    mt_stream_diag_note(0u, 1u, 0u, 0u, 0u, 0u);
    mt_stream_diag_note(1u, 1u, 0u, 0u, 0u, 0u);
    mt_stream_diag_note(2u, 1u, 0u, 0u, 0u, 0u);
    mt_stream_diag_note(UINT32_MAX, 1u, 0u, 0u, 0u, 0u);
    CHECK(mt_stream_diagnostics.flags == 0u);
    reads = bus.reads;
    mt_stream_diag_note(result, 41u, 5u, local, reason, adc);
    CHECK(bus.reads == reads);
    CHECK(mt_stream_diagnostics.flags == FLAG_FAULT);
    mt_stream_diag_note(9u, 80u, 9u, 7u, 8u, 9u);
    bus.next_cycle = 200u;
    mt_stream_diag_observe(190u, 5u, 8u, 90u, 4u, 0u, 1u,
                           2u, 71u, 72u, 73u, 0u);
    CHECK(mt_stream_diagnostics.first_runtime_result == result);
    CHECK(mt_stream_diagnostics.first_local_error == local);
    CHECK(mt_stream_diagnostics.first_acquisition_reason == reason);
    CHECK(mt_stream_diagnostics.first_adc_fault == adc);
    CHECK(mt_stream_diagnostics.first_epoch == 41u);
    CHECK(mt_stream_diagnostics.final_runtime_result == 5u);
    CHECK(mt_stream_diagnostics.final_epoch == 90u);
}
static void test_fault_result(void) { first_fault(0u); }
static void test_fault_local(void) { first_fault(1u); }
static void test_fault_reason(void) { first_fault(2u); }
static void test_fault_adc(void) { first_fault(3u); }

static void test_pre_tx_peak(void)
{
    struct fake_dwt bus = fresh_bus();
    unsigned reads;
    start_ok(&bus); reads = bus.reads;
    mt_stream_diag_note(UINT32_MAX, 44u, 2048u, 12u, 13u, 14u);
    CHECK(mt_stream_diagnostics.queue_high_water == 2048u);
    CHECK(mt_stream_diagnostics.first_runtime_result == UINT32_MAX);
    CHECK(mt_stream_diagnostics.step_count == 0u);
    CHECK(bus.reads == reads);
    bus.next_cycle = 170u; mt_stream_diag_tx(150u);
    bus.next_cycle = 180u;
    mt_stream_diag_observe(140u, 4u, 6u, 44u, 2u, 1u, 0u,
                           0u, 0u, 0u, 0u, 0u);
    CHECK(mt_stream_diagnostics.queue_high_water == 2048u);
    CHECK(mt_stream_diagnostics.first_runtime_result == UINT32_MAX);
    CHECK(mt_stream_diagnostics.first_local_error == 12u);
    CHECK(mt_stream_diagnostics.first_acquisition_reason == 13u);
    CHECK(mt_stream_diagnostics.first_adc_fault == 14u);
    CHECK(mt_stream_diagnostics.first_epoch == 44u);
    CHECK(mt_stream_diagnostics.tx_count == 1u);
    CHECK(mt_stream_diagnostics.step_count == 1u);
    CHECK(mt_stream_diagnostics.final_runtime_result == 4u);
    mt_stream_diag_note(UINT32_MAX, 44u, 2049u, 0u, 0u, 0u);
    CHECK(mt_stream_diagnostics.queue_high_water == 2049u);
}

static void check_frozen(struct fake_dwt *bus)
{
    mt_stream_diag_record before = snapshot();
    unsigned reads = bus->reads, writes = bus->writes;
    CHECK(before.phase == 3u);
    bus->demcr = 0u; bus->control = NO_CYCCNT; bus->next_cycle = UINT32_MAX;
    CHECK(mt_stream_diag_cycle() == before.freeze_cycle);
    inactive_hooks();
    CHECK(mt_stream_diag_init(fake_read, fake_write, bus, BUILD + 1u) == 0);
    CHECK(mt_stream_diag_cycle() == before.freeze_cycle);
    unchanged(&before);
    CHECK(bus->reads == reads && bus->writes == writes);
}

static void test_observe_freeze(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    bus.next_cycle = 140u;
    mt_stream_diag_observe(120u, 2u, 6u, 43u, 2u, 0u, 1u,
                           7u, 0u, 0u, 0u, 0u);
    CHECK(mt_stream_diagnostics.phase == 2u);
    CHECK(mt_stream_diagnostics.freeze_cycle == 0u);
    bus.next_cycle = 170u;
    mt_stream_diag_observe(150u, 2u, 7u, 44u, 3u, 0u, 1u,
                           2u, 0u, 0u, 0u, 1u);
    CHECK(mt_stream_diagnostics.phase == 3u);
    CHECK(mt_stream_diagnostics.step_count == 2u);
    CHECK(mt_stream_diagnostics.step_body_max_cycles == 20u);
    CHECK(mt_stream_diagnostics.step_gap_max_cycles == 30u);
    CHECK(mt_stream_diagnostics.final_runtime_result == 2u);
    CHECK(mt_stream_diagnostics.final_core_state == 7u);
    CHECK(mt_stream_diagnostics.final_epoch == 44u);
    CHECK(mt_stream_diagnostics.final_adc_state == 3u);
    CHECK(mt_stream_diagnostics.final_adc_owned == 0u);
    CHECK(mt_stream_diagnostics.final_hardware_off == 1u);
    CHECK(mt_stream_diagnostics.last_cycle == 170u);
    CHECK(mt_stream_diagnostics.freeze_cycle == 170u);
    CHECK(mt_stream_diagnostics.queue_high_water == 7u);
    check_frozen(&bus);
}

static void test_freeze_caller_predicate(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    /* Intentionally inconsistent inputs identify the module seam: it trusts
     * freeze_eligible. Actual hardware quiescence must be tested in runtime. */
    observe_at(&bus, 140u, 120u, 1u);
    CHECK(mt_stream_diagnostics.phase == 3u);
    CHECK(mt_stream_diagnostics.final_adc_owned == 1u);
    CHECK(mt_stream_diagnostics.final_hardware_off == 0u);
    check_frozen(&bus);
}

static void test_replacement_freeze(void)
{
    struct fake_dwt bus = fresh_bus();
    start_ok(&bus);
    mt_stream_diag_note(UINT32_MAX, 4u, 64u, 31u, 32u, 33u);
    bus.next_cycle = 190u;
    mt_stream_diag_replaced(8u, 45u, 3u);
    CHECK(mt_stream_diagnostics.phase == 3u);
    CHECK(mt_stream_diagnostics.flags == (FLAG_FAULT | FLAG_REPLACED));
    CHECK(mt_stream_diagnostics.final_runtime_result == UINT32_MAX);
    CHECK(mt_stream_diagnostics.final_core_state == 8u);
    CHECK(mt_stream_diagnostics.final_epoch == 45u);
    CHECK(mt_stream_diagnostics.final_adc_state == 3u);
    CHECK(mt_stream_diagnostics.final_adc_owned == 0u);
    CHECK(mt_stream_diagnostics.final_hardware_off == 1u);
    CHECK(mt_stream_diagnostics.first_local_error == 31u);
    CHECK(mt_stream_diagnostics.first_acquisition_reason == 32u);
    CHECK(mt_stream_diagnostics.first_adc_fault == 33u);
    CHECK(mt_stream_diagnostics.first_epoch == 4u);
    CHECK(mt_stream_diagnostics.queue_high_water == 64u);
    CHECK(mt_stream_diagnostics.step_count == 0u);
    CHECK(mt_stream_diagnostics.last_cycle == 190u);
    CHECK(mt_stream_diagnostics.freeze_cycle == 190u);
    CHECK(mt_stream_diagnostics.generation == 7u);
    check_frozen(&bus);
}

static void test_unavailable_cycle(void)
{
    struct fake_dwt bus = fresh_bus();
    bus.control |= NO_CYCCNT;
    CHECK(mt_stream_diag_init(fake_read, fake_write, &bus, BUILD) == 0);
    CHECK(mt_stream_diagnostics.flags == FLAG_UNAVAILABLE);
    CHECK(mt_stream_diag_cycle() == 100u);
    CHECK(mt_stream_diagnostics.phase == 4u);
    CHECK(mt_stream_diagnostics.flags == (FLAG_UNAVAILABLE | FLAG_INVALID));
    /* Unavailable blocks measurement hooks but is not a frozen bus snapshot. */
}

struct test_case { const char *name; void (*run)(void); };
static const struct test_case cases[] = {
    {"uninitialized", test_uninitialized},
    {"null_callbacks", test_null_callbacks},
    {"enable_preserves", test_enable_preserves},
    {"already_enabled_wrap_probe", test_already_enabled},
    {"unsupported", test_unsupported},
    {"trace_enable_rejected", test_trace_rejected},
    {"counter_enable_rejected", test_enable_rejected},
    {"probe_stuck", test_probe_stuck},
    {"probe_delayed_is_invalid", test_probe_delayed},
    {"armed_first_begin_single_init", test_armed_and_first_begin},
    {"invalid_trace_sticky", test_invalid_trace},
    {"invalid_enable_sticky", test_invalid_enable},
    {"invalid_support_sticky", test_invalid_support},
    {"invalid_stationary_sticky", test_invalid_stationary},
    {"poll_wrap_maxima", test_poll_wrap_maxima},
    {"step_wrap_maxima", test_step_wrap_maxima},
    {"step_body_wrap", test_step_body_wrap},
    {"tx_wrap_maxima", test_tx_wrap_maxima},
    {"copy_maxima_no_cycle_read", test_copy_maxima},
    {"saturate_step", test_saturate_step},
    {"saturate_tx", test_saturate_tx},
    {"saturate_poll", test_saturate_poll},
    {"saturate_copy", test_saturate_copy},
    {"first_fault_result", test_fault_result},
    {"first_fault_local", test_fault_local},
    {"first_fault_reason", test_fault_reason},
    {"first_fault_adc", test_fault_adc},
    {"pre_tx_queue_peak_and_fault", test_pre_tx_peak},
    {"observe_freeze_immutable", test_observe_freeze},
    {"freeze_caller_predicate", test_freeze_caller_predicate},
    {"replacement_freeze_immutable", test_replacement_freeze},
    {"unavailable_cycle_is_not_frozen", test_unavailable_cycle}
};

int main(int argc, char **argv)
{
    size_t i;
    CHECK(argc == 2);
    if (strcmp(argv[1], "--list") == 0) {
        for (i = 0u; i < sizeof(cases) / sizeof(cases[0]); ++i)
            (void)puts(cases[i].name);
        return EXIT_SUCCESS;
    }
    for (i = 0u; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        if (strcmp(argv[1], cases[i].name) == 0) {
            cases[i].run();
            (void)printf("{\"case\":\"%s\",\"pass\":true}\n", cases[i].name);
            return EXIT_SUCCESS;
        }
    }
    (void)fprintf(stderr, "Unknown case: %s\n", argv[1]);
    return EXIT_FAILURE;
}
