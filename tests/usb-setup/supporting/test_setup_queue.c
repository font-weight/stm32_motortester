/* USB003 supporting regression: real TinyUSB core/FIFO/CDC; fake DCD only.
 * Serial producer/consumer invocations, OS_NONE, one event per task call.
 * No production epoch/IRQ synchronization behavior is modeled here. */
#include "fake_dcd.h"
#include "tusb.h"
#include "device/dcd.h"
#include "device/usbd_pvt.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(id, expr) do { if (!(expr)) { \
    fprintf(stderr, "TEST_FAIL %s line=%u expression=%s\n", id, \
            (unsigned)__LINE__, #expr); exit(1); } } while (0)
_Static_assert(CFG_TUD_TASK_QUEUE_SZ == 16, "Regression expects the frozen depth 16 config");
_Static_assert(CFG_TUD_TASK_EVENTS_PER_RUN == 1, "Bounded task regression requires exactly one event");
_Static_assert(CFG_TUSB_OS == OPT_OS_NONE, "Only the serialized OS_NONE model is claimed");
static unsigned accepted, accepted_setups, accepted_resets, callbacks;
static unsigned markers[CFG_TUD_TASK_QUEUE_SZ];
static unsigned callback_order[CFG_TUD_TASK_QUEUE_SZ];

void tud_event_hook_cb(uint8_t rhport, uint32_t event_id, bool in_isr) {
    CHECK("HOOK_PORT", rhport == 0);
    (void)in_isr;
    ++accepted;
    if (event_id == DCD_EVENT_SETUP_RECEIVED) ++accepted_setups;
    if (event_id == DCD_EVENT_BUS_RESET) ++accepted_resets;
}
static void marker_callback(void *arg) {
    CHECK("CALLBACK_BOUND", callbacks < CFG_TUD_TASK_QUEUE_SZ);
    callback_order[callbacks++] = *(const unsigned *)arg;
}
static void start(void) {
    fake_start();
    accepted = accepted_setups = accepted_resets = callbacks = 0;
    for (unsigned i = 0; i < CFG_TUD_TASK_QUEUE_SZ; ++i) markers[i] = i;
    memset(callback_order, 0, sizeof(callback_order));
    CHECK("START_QUEUE_EMPTY", !tud_task_event_ready());
    CHECK("START_EP0_IDLE", !fake_ep[0].pending && !fake_ep[0x80].pending);
}
static void defer(unsigned marker) {
    CHECK("MARKER_BOUND", marker < CFG_TUD_TASK_QUEUE_SZ);
    usbd_defer_func(marker_callback, &markers[marker], true);
}
static void setup_device(unsigned length) {
    CHECK("LENGTH_BOUND", length <= 18);
    const uint8_t packet[8] = {0x80, TUSB_REQ_GET_DESCRIPTOR, 0, TUSB_DESC_DEVICE,
                             0, 0, (uint8_t)length, 0};
    dcd_event_setup_received(0, packet, true);
}
static void step(void) {
    CHECK("STEP_HAS_EVENT", tud_task_event_ready());
    tud_task_ext(0, false);
}
static void expect_device_response(unsigned length) {
    CHECK("EP0_RESPONSE_MISSING", fake_ep[0x80].pending);
    CHECK("EP0_RESPONSE_SIZE", fake_ep[0x80].length == length);
    CHECK("EP0_RESPONSE_DATA", !memcmp(fake_ep[0x80].buffer,
          tud_descriptor_device_cb(), length));
    CHECK("EP0_RESPONSE_SINGLE", fake_record_count == 1);
    CHECK("EP0_RESPONSE_ACCEPTED", fake_records[0].accepted &&
          fake_records[0].ep_addr == 0x80 && fake_records[0].length == length);
    CHECK("NO_STALL", !fake_ep[0].stalled && !fake_ep[0x80].stalled);
    CHECK("NO_INVALID_SUBMISSION", fake_invalid_submissions == 0);
}
static void complete_descriptor(void) {
    fake_complete_in(0x80, false);
    CHECK("COMPLETION_DEFERRED", !fake_ep[0].pending);
    step();
    CHECK("STATUS_OUT", fake_ep[0].pending && fake_ep[0].length == 0);
    fake_ep[0].pending = false;
    dcd_event_xfer_complete(0, 0, 0, XFER_RESULT_SUCCESS, true);
    step();
    CHECK("COMPLETED_QUEUE_EMPTY", !tud_task_event_ready());
    CHECK("COMPLETED_EP0_IDLE", !fake_ep[0].pending && !fake_ep[0x80].pending);
}
static void expect_reset_then_descriptor(void) {
    unsigned resets_before = accepted_resets, setups_before = accepted_setups;
    CHECK("BEFORE_RESET_MOUNTED", tud_mounted());
    fake_bus_reset(false);
    setup_device(18); /* New SETUP is already queued when the RESET is dispatched. */
    CHECK("RESET_ENQUEUED", accepted_resets == resets_before + 1);
    CHECK("NEW_SETUP_ENQUEUED", accepted_setups == setups_before + 1);
    CHECK("RESET_NOT_YET_PROCESSED", tud_mounted());
    step();
    CHECK("RESET_PROCESSED", !tud_mounted());
    CHECK("RESET_BUDGET_ONE", tud_task_event_ready() && fake_record_count == 0);
    step();
    CHECK("RESET_DESCRIPTOR_QUEUE_EMPTY", !tud_task_event_ready());
    expect_device_response(18);
    complete_descriptor();
    fake_configuration(1);
    CHECK("CDC_RECONFIGURED", tud_mounted() && tud_cdc_n_ready(0));
}
static void rejected(unsigned count) {
    start();
    for (unsigned i = 0; i < CFG_TUD_TASK_QUEUE_SZ; ++i) defer(i);
    CHECK("FULL_QUEUE_ACCEPTED", accepted == CFG_TUD_TASK_QUEUE_SZ);
    for (unsigned i = 0; i < count; ++i) setup_device(8);
    CHECK("REJECTED_NOT_HOOKED", accepted == CFG_TUD_TASK_QUEUE_SZ && accepted_setups == 0);
    for (unsigned i = 0; i < CFG_TUD_TASK_QUEUE_SZ; ++i) {
        unsigned before = callbacks;
        step();
        CHECK("ONE_EVENT_PER_TASK", callbacks == before + 1);
        CHECK("DEFERRED_ORDER", callback_order[i] == i);
        CHECK("BOUNDED_QUEUE_READY", tud_task_event_ready() == (i + 1 < CFG_TUD_TASK_QUEUE_SZ));
        CHECK("NO_REJECTED_SETUP_RESPONSE", fake_record_count == 0);
    }
    expect_reset_then_descriptor();
    fake_finish();
}
static void accepted_supersession(bool full) {
    start();
    unsigned count = full ? CFG_TUD_TASK_QUEUE_SZ : 2;
    for (unsigned i = 0; i < count; ++i) setup_device(i + 1 == count ? 18 : 8);
    CHECK("ALL_SETUPS_ACCEPTED", accepted_setups == count && accepted == count);
    for (unsigned i = 0; i < count; ++i) {
        step();
        if (i + 1 != count) {
            CHECK("OLD_SETUP_SUPERSEDED", fake_record_count == 0);
            CHECK("SUPERSESSION_BUDGET_ONE", tud_task_event_ready());
        }
    }
    CHECK("SUPERSESSION_QUEUE_EMPTY", !tud_task_event_ready());
    expect_device_response(18);
    complete_descriptor();
    fake_finish();
}
static void mixed(unsigned rejected_setups) {
    start();
    defer(0); setup_device(8); defer(2); setup_device(18);
    for (unsigned i = 4; i < CFG_TUD_TASK_QUEUE_SZ; ++i) defer(i);
    CHECK("MIXED_FILLED", accepted == CFG_TUD_TASK_QUEUE_SZ && accepted_setups == 2);
    for (unsigned i = 0; i < rejected_setups; ++i) {
        setup_device(8);
        defer(15); /* Rejected non-SETUP must not change SETUP accounting. */
        fake_signal(DCD_EVENT_RESUME, false);
    }
    CHECK("MIXED_REJECTIONS_NOT_HOOKED", accepted == CFG_TUD_TASK_QUEUE_SZ && accepted_setups == 2);
    for (unsigned i = 0; i < CFG_TUD_TASK_QUEUE_SZ; ++i) {
        unsigned before = callbacks;
        step();
        if (i == 1 || i == 3) CHECK("MIXED_SETUP_NOT_CALLBACK", callbacks == before);
        else {
            CHECK("MIXED_CALLBACK_BUDGET", callbacks == before + 1);
            CHECK("MIXED_CALLBACK_ORDER", callback_order[callbacks - 1] == i);
        }
        CHECK("MIXED_QUEUE_BUDGET", tud_task_event_ready() == (i + 1 < CFG_TUD_TASK_QUEUE_SZ));
        if (i < 3) CHECK("MIXED_SUPERSESSION", fake_record_count == 0);
        else expect_device_response(18);
    }
    CHECK("MIXED_CALLBACK_TOTAL", callbacks == CFG_TUD_TASK_QUEUE_SZ - 2);
    complete_descriptor();
    fake_finish();
}
static void reset_queued_setups(void) {
    start();
    defer(0); fake_bus_reset(false); setup_device(8); defer(3); setup_device(18); defer(5);
    CHECK("RESET_MIX_ACCEPTED", accepted == 6 && accepted_setups == 2 && accepted_resets == 1);
    for (unsigned i = 0; i < 6; ++i) {
        unsigned before = callbacks;
        step();
        if (i == 0 || i == 3 || i == 5) CHECK("RESET_MIX_CALLBACK_BUDGET", callbacks == before + 1);
        else CHECK("RESET_MIX_OTHER_BUDGET", callbacks == before);
        if (i >= 1) CHECK("RESET_MIX_UNMOUNTED", !tud_mounted());
        if (i < 4) CHECK("RESET_MIX_NO_EARLY_RESPONSE", fake_record_count == 0);
        else expect_device_response(18);
        CHECK("RESET_MIX_QUEUE_BUDGET", tud_task_event_ready() == (i < 5));
    }
    complete_descriptor();
    fake_finish();
}
int main(int argc, char **argv) {
    CHECK("ARG_COUNT", argc == 2);
    printf("INFO scenario=%s HWFIFO=%u queue=%u event_budget=%u\n", argv[1],
           (unsigned)CFG_TUD_EDPT_DEDICATED_HWFIFO, (unsigned)CFG_TUD_TASK_QUEUE_SZ,
           (unsigned)CFG_TUD_TASK_EVENTS_PER_RUN);
    fflush(stdout);
    if (!strncmp(argv[1], "rejected_", 9)) rejected((unsigned)strtoul(argv[1] + 9, NULL, 10));
    else if (!strcmp(argv[1], "supersession")) accepted_supersession(false);
    else if (!strcmp(argv[1], "capacity_supersession")) accepted_supersession(true);
    else if (!strncmp(argv[1], "mixed_", 6)) mixed((unsigned)strtoul(argv[1] + 6, NULL, 10));
    else if (!strcmp(argv[1], "reset_queued_setups")) reset_queued_setups();
    else CHECK("UNKNOWN_SCENARIO", false);
    printf("PASS %s\n", argv[1]);
    return 0;
}
