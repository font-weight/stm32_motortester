/* Observational negative controls; unchanged pinned TinyUSB and fake DCD. */
#include "fake_dcd.h"
#include "tusb.h"
#include "device/dcd.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static bool reset_in_tx, reset_in_rx, reject_ep0;
static unsigned tx_callbacks, rx_callbacks, status_callbacks;
static unsigned rejected_status_mounts, close_count, close_umounts, sof_off;
static bool close_was_ready;
static unsigned wrapped_xfer, wrapped_fifo, wrapped_status;

bool __real_dcd_edpt_xfer(uint8_t, uint8_t, uint8_t *, uint16_t, bool);
bool __real_dcd_edpt_xfer_fifo(uint8_t, uint8_t, tu_fifo_t *, uint16_t, bool);
void __real_dcd_edpt0_status_complete(uint8_t, const tusb_control_request_t *);
void __real_dcd_edpt_close_all(uint8_t);
void __real_dcd_sof_enable(uint8_t, bool);
bool __wrap_dcd_edpt_xfer(uint8_t port, uint8_t ep, uint8_t *buf, uint16_t len, bool isr) {
    ++wrapped_xfer;
    if (reject_ep0 && ep == 0x80 && len == 0) {
        reject_ep0 = false;
        rejected_status_mounts = fake_mounts;
        return false;
    }
    return __real_dcd_edpt_xfer(port, ep, buf, len, isr);
}
bool __wrap_dcd_edpt_xfer_fifo(uint8_t p, uint8_t e, tu_fifo_t *ff, uint16_t n, bool isr) {
    ++wrapped_fifo; return __real_dcd_edpt_xfer_fifo(p, e, ff, n, isr);
}
void __wrap_dcd_edpt0_status_complete(uint8_t p, const tusb_control_request_t *r) {
    ++wrapped_status; __real_dcd_edpt0_status_complete(p, r);
}
void __wrap_dcd_edpt_close_all(uint8_t port) {
    ++close_count; close_umounts = fake_umounts; close_was_ready = tud_cdc_n_ready(0);
    __real_dcd_edpt_close_all(port);
}
void __wrap_dcd_sof_enable(uint8_t port, bool en) {
    if (!en) ++sof_off;
    __real_dcd_sof_enable(port, en);
}
void tud_cdc_tx_complete_cb(uint8_t itf) {
    assert(itf == 0); ++tx_callbacks;
    if (reset_in_tx) { reset_in_tx = false; fake_bus_reset(false); }
}
void tud_cdc_rx_cb(uint8_t itf) {
    assert(itf == 0); ++rx_callbacks;
    if (reset_in_rx) {
        uint8_t byte; reset_in_rx = false; fake_bus_reset(false);
        assert(tud_cdc_n_read(0, &byte, 1) == 1);
    }
}
void dcd_edpt0_status_complete(uint8_t port, const tusb_control_request_t *request) {
    assert(port == 0 && request); ++status_callbacks;
}
static void raw_setup(uint8_t type, uint8_t request, uint16_t value, uint16_t index, uint16_t len) {
    uint8_t b[8] = {type, request, (uint8_t)value, (uint8_t)(value >> 8),
        (uint8_t)index, (uint8_t)(index >> 8), (uint8_t)len, (uint8_t)(len >> 8)};
    dcd_event_setup_received(0, b, true); fake_pump();
}
int main(int argc, char **argv) {
    assert(argc == 2); fake_start();
    uint8_t data[64]; memset(data, 0x41, sizeof data);
    if (!strcmp(argv[1], "weak_fifo_bypass")) {
        wrapped_xfer = wrapped_fifo = 0;
        assert(tud_cdc_n_write(0, data, 64) == 64);
        assert(fake_record_count == 1);
#if CFG_TUD_EDPT_DEDICATED_HWFIFO
        assert(wrapped_xfer == 0 && wrapped_fifo == 0);
#else
        assert(wrapped_xfer == 1 && wrapped_fifo == 0);
#endif
        printf("actual submissions=1, wrapped xfer=%u, wrapped fifo=%u\n", wrapped_xfer, wrapped_fifo);
    } else if (!strcmp(argv[1], "active_tx_reset")) {
        assert(tud_cdc_n_write(0, data, 64) == 64);
        reset_in_tx = true;
        fake_complete_in(FAKE_CDC_IN, true);
        assert(tx_callbacks == 1 && fake_invalid_submissions == 1);
        assert(fake_records[1].ep_addr == FAKE_CDC_IN && fake_records[1].length == 0);
        printf("active TX callback: reset advanced epoch; automatic old ZLP reached DCD, invalid=%u\n", fake_invalid_submissions);
    } else if (!strcmp(argv[1], "active_rx_reset")) {
        reset_in_rx = true; fake_out(data, 1, true);
        assert(rx_callbacks == 1 && fake_invalid_submissions == 2);
        printf("active RX callback: reset published; nested read + automatic rearm reached DCD, invalid=%u\n", fake_invalid_submissions);
    } else if (!strcmp(argv[1], "failed_cdc_completion")) {
        unsigned accepted = tud_cdc_n_write(0, data, 64); assert(accepted == 64);
        fake_ep[FAKE_CDC_IN].pending = false;
        dcd_event_xfer_complete(0, FAKE_CDC_IN, 64, XFER_RESULT_FAILED, true); fake_pump();
        assert(tx_callbacks == 1 && fake_record_count == 2 && fake_records[1].length == 0);
        assert(fake_host_size == 0 && tud_cdc_n_ready(0));
        printf("FAILED CDC completion dispatched: callback=%u, automatic ZLP=1, copied=%u, host=%zu, ready=1\n", tx_callbacks, accepted, fake_host_size);
    } else if (!strcmp(argv[1], "failed_ep0_completion")) {
        unsigned before = status_callbacks; wrapped_status = 0;
        raw_setup(0, TUSB_REQ_SET_ADDRESS, 9, 0, 0);
        assert(fake_ep[0x80].pending); fake_ep[0x80].pending = false;
        dcd_event_xfer_complete(0, 0x80, 0, XFER_RESULT_FAILED, true); fake_pump();
        assert(status_callbacks == before + 1 && wrapped_status == 0);
        printf("FAILED EP0 status invokes DCD status-complete hook: delta=1, wrapper=0\n");
    } else if (!strcmp(argv[1], "config_status_false")) {
        fake_bus_reset(true); reject_ep0 = true;
        raw_setup(0, TUSB_REQ_SET_CONFIGURATION, 1, 0, 0);
        assert(rejected_status_mounts == 2 && tud_cdc_n_ready(0));
        assert(!fake_ep[0x80].pending && !fake_ep[0x80].stalled);
        printf("config status rejected: mount occurred first (%u), ready=1, no EP0 pending, no stall\n", rejected_status_mounts);
    } else if (!strcmp(argv[1], "config_teardown_order")) {
        raw_setup(0, TUSB_REQ_SET_CONFIGURATION, 0, 0, 0);
        assert(close_count == 1 && sof_off == 1 && close_umounts == 0 && close_was_ready);
        assert(fake_umounts == 1 && fake_ep[0x80].pending);
        printf("config0: SOF-off and close_all before umount; close saw ready=1; EP0 status pending afterward\n");
    } else assert(!"unknown scenario");
    assert(tud_deinit(0));
    return 0;
}
