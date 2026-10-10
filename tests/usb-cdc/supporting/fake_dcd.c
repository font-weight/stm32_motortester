/* TEST ONLY fake DCD. Real unmodified TinyUSB core, CDC, and FIFO are linked.
 * No physical USB, production IRQ or epoch-bridge behavior is represented. */
#include "fake_dcd.h"
#include "tusb.h"
#include "device/dcd.h"
#include <assert.h>
#include <string.h>

fake_endpoint fake_ep[256];
static uint8_t fake_packet[256][64];
uint8_t fake_submitted[FAKE_LOG_CAPACITY], fake_host[FAKE_LOG_CAPACITY];
size_t fake_submitted_size, fake_host_size;
fake_submission fake_records[FAKE_LOG_RECORDS];
size_t fake_record_count;
unsigned fake_epoch, fake_invalid_submissions, fake_mounts, fake_umounts;
unsigned fake_line_calls;
bool fake_last_dtr, fake_last_rts, fake_reject_next_in;
void (*fake_on_in_submit)(void);
void (*fake_on_out_submit)(void);

/* Zero VID/PID are deliberate test-only sentinels, not assigned product IDs. */
static const tusb_desc_device_t device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t), .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200, .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON, .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = 64, .idVendor = 0, .idProduct = 0,
    .bcdDevice = 0x0000, .bNumConfigurations = 1
};
static const uint8_t config_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, 2, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN, 0, 100),
    TUD_CDC_DESCRIPTOR(0, 0, 0x81, 8, FAKE_CDC_OUT, FAKE_CDC_IN, 64)
};
uint8_t const *tud_descriptor_device_cb(void) { return (const uint8_t *)&device_descriptor; }
uint8_t const *tud_descriptor_configuration_cb(uint8_t index) { assert(index == 0); return config_descriptor; }
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) { (void)index; (void)langid; return NULL; }
uint32_t tusb_time_millis_api(void) { return 0; }
void tud_mount_cb(void) { ++fake_mounts; }
void tud_umount_cb(void) { ++fake_umounts; }
void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts) {
    assert(itf == 0); ++fake_line_calls; fake_last_dtr = dtr; fake_last_rts = rts;
}
bool dcd_init(uint8_t rhport, const tusb_rhport_init_t *init) { assert(rhport == 0); assert(init); return true; }
bool dcd_deinit(uint8_t rhport) { assert(rhport == 0); memset(fake_ep, 0, sizeof(fake_ep)); return true; }
void dcd_int_handler(uint8_t rhport) { (void)rhport; }
void dcd_int_enable(uint8_t rhport) { (void)rhport; }
void dcd_int_disable(uint8_t rhport) { (void)rhport; }
void dcd_remote_wakeup(uint8_t rhport) { (void)rhport; }
void dcd_connect(uint8_t rhport) { (void)rhport; }
void dcd_disconnect(uint8_t rhport) { (void)rhport; }
void dcd_sof_enable(uint8_t rhport, bool en) { (void)rhport; (void)en; }
void dcd_set_address(uint8_t rhport, uint8_t address) { (void)address; assert(dcd_edpt_xfer(rhport, 0x80, NULL, 0, false)); }
void dcd_edpt_close_all(uint8_t rhport) {
    assert(rhport == 0);
    for (unsigned i = 0; i < 256; ++i) if ((i & 0x7f) != 0) memset(&fake_ep[i], 0, sizeof(fake_ep[i]));
}
bool dcd_edpt_open(uint8_t rhport, const tusb_desc_endpoint_t *desc) {
    assert(rhport == 0); fake_endpoint *ep = &fake_ep[desc->bEndpointAddress];
    assert(!ep->open); ep->open = true; ep->epoch = fake_epoch; return true;
}
void dcd_edpt_close(uint8_t rhport, uint8_t addr) { assert(rhport == 0); memset(&fake_ep[addr], 0, sizeof(fake_ep[addr])); }
void dcd_edpt_stall(uint8_t rhport, uint8_t addr) { assert(rhport == 0); fake_ep[addr].stalled = true; fake_ep[addr].pending = false; }
void dcd_edpt_clear_stall(uint8_t rhport, uint8_t addr) { assert(rhport == 0); fake_ep[addr].stalled = false; }
bool dcd_edpt_xfer(uint8_t rhport, uint8_t addr, uint8_t *buffer, uint16_t length, bool in_isr) {
    (void)in_isr; assert(rhport == 0); assert(fake_record_count < FAKE_LOG_RECORDS);
    fake_endpoint *ep = &fake_ep[addr];
    bool valid = ep->open && !ep->pending && !ep->stalled && ep->epoch == fake_epoch;
    if (!valid) ++fake_invalid_submissions;
    bool accepted = valid;
    if (addr == FAKE_CDC_IN && fake_reject_next_in) { accepted = false; fake_reject_next_in = false; }
    fake_submission *r = &fake_records[fake_record_count++];
    *r = (fake_submission){fake_epoch, addr, length, fake_submitted_size, accepted};
    if ((addr & 0x80) && length) {
        assert(buffer); assert(fake_submitted_size + length <= FAKE_LOG_CAPACITY);
        memcpy(fake_submitted + fake_submitted_size, buffer, length); fake_submitted_size += length;
    }
    if (accepted) { ep->pending = true; ep->buffer = buffer; ep->length = length; }
    if (addr == FAKE_CDC_IN && fake_on_in_submit) {
        void (*hook)(void) = fake_on_in_submit; fake_on_in_submit = NULL; hook();
    }
    if (addr == FAKE_CDC_OUT && fake_on_out_submit) {
        void (*hook)(void) = fake_on_out_submit; fake_on_out_submit = NULL; hook();
    }
    return accepted;
}
bool dcd_edpt_xfer_fifo(uint8_t rhport, uint8_t addr, tu_fifo_t *fifo, uint16_t length, bool in_isr) {
    assert(length <= 64); assert(fifo);
    if (addr & 0x80) assert(tu_fifo_read_n(fifo, fake_packet[addr], length) == length);
    bool ok = dcd_edpt_xfer(rhport, addr, fake_packet[addr], length, in_isr);
    if (ok) fake_ep[addr].fifo = fifo;
    return ok;
}
void fake_clear_logs(void) { fake_submitted_size = fake_host_size = fake_record_count = 0; }
void fake_pump(void) {
    unsigned limit = 0;
    while (tud_task_event_ready()) { assert(++limit < 1000); tud_task_ext(0, false); }
}
void fake_bus_reset(bool service) {
    ++fake_epoch; memset(fake_ep, 0, sizeof(fake_ep));
    fake_ep[0].open = fake_ep[0x80].open = true;
    fake_ep[0].epoch = fake_ep[0x80].epoch = fake_epoch;
    dcd_event_bus_reset(0, TUSB_SPEED_FULL, true);
    if (service) fake_pump();
}
void fake_complete_in(uint8_t addr, bool service) {
    fake_endpoint *ep = &fake_ep[addr]; assert((addr & 0x80) && ep->pending && ep->epoch == fake_epoch);
    uint16_t n = ep->length;
    if (addr == FAKE_CDC_IN && n) {
        assert(fake_host_size + n <= FAKE_LOG_CAPACITY);
        memcpy(fake_host + fake_host_size, ep->buffer, n); fake_host_size += n;
    }
    ep->pending = false;
    dcd_event_xfer_complete(0, addr, n, XFER_RESULT_SUCCESS, true);
    if (service) fake_pump();
}
static void setup(uint8_t type, uint8_t request, uint16_t value, uint16_t index, uint16_t length) {
    uint8_t packet[8] = {type, request, (uint8_t)value, (uint8_t)(value >> 8),
        (uint8_t)index, (uint8_t)(index >> 8), (uint8_t)length, (uint8_t)(length >> 8)};
    assert(!fake_ep[0].pending && !fake_ep[0x80].pending);
    fake_ep[0].stalled = fake_ep[0x80].stalled = false;
    dcd_event_setup_received(0, packet, true); fake_pump();
}
static size_t control_read(uint16_t value, uint8_t *out, size_t capacity) {
    setup(0x80, TUSB_REQ_GET_DESCRIPTOR, value, 0, (uint16_t)capacity);
    size_t n = 0;
    while (fake_ep[0x80].pending) {
        size_t take = fake_ep[0x80].length; assert(n+take <= capacity);
        if (take) memcpy(out+n, fake_ep[0x80].buffer, take);
        n += take; fake_complete_in(0x80, true);
    }
    assert(fake_ep[0].pending && fake_ep[0].length == 0);
    fake_ep[0].pending = false; dcd_event_xfer_complete(0, 0, 0, XFER_RESULT_SUCCESS, true); fake_pump();
    return n;
}
static void enumerate_descriptors(void) {
    uint8_t out[256];
    assert(control_read(TUSB_DESC_DEVICE << 8, out, 18) == sizeof(device_descriptor));
    assert(!memcmp(out, &device_descriptor, sizeof(device_descriptor)));
    setup(0, TUSB_REQ_SET_ADDRESS, 7, 0, 0); assert(fake_ep[0x80].pending); fake_complete_in(0x80, true);
    assert(control_read(TUSB_DESC_CONFIGURATION << 8, out, 9) == 9);
    assert(!memcmp(out, config_descriptor, 9));
    assert(control_read(TUSB_DESC_CONFIGURATION << 8, out, sizeof(config_descriptor)) == sizeof(config_descriptor));
    assert(!memcmp(out, config_descriptor, sizeof(config_descriptor)));
}
void fake_configuration(uint8_t value) {
    setup(0, TUSB_REQ_SET_CONFIGURATION, value, 0, 0);
    assert(fake_ep[0x80].pending && fake_ep[0x80].length == 0); fake_complete_in(0x80, true);
}
void fake_line_state(uint16_t value) {
    setup(0x21, CDC_REQUEST_SET_CONTROL_LINE_STATE, value, 0, 0);
    assert(fake_ep[0x80].pending && fake_ep[0x80].length == 0); fake_complete_in(0x80, true);
}
void fake_out(const uint8_t *bytes, size_t length, bool service) {
    fake_endpoint *ep = &fake_ep[FAKE_CDC_OUT]; assert(ep->pending && ep->epoch == fake_epoch);
    assert(length <= ep->length); assert(bytes || !length);
    if (length) {
        if (ep->fifo) assert(tu_fifo_write_n(ep->fifo, bytes, (uint16_t)length) == length);
        else memcpy(ep->buffer, bytes, length);
    }
    ep->pending = false; dcd_event_xfer_complete(0, FAKE_CDC_OUT, (uint32_t)length, XFER_RESULT_SUCCESS, true);
    if (service) fake_pump();
}
void fake_signal(unsigned event, bool service) {
    dcd_event_bus_signal(0, (dcd_eventid_t)event, true); if (service) fake_pump();
}
void fake_drain(void) {
    unsigned limit = 0;
    for (;;) {
        assert(++limit < 10000); fake_pump();
        (void)tud_cdc_n_write_flush(0);
        if (!fake_ep[FAKE_CDC_IN].pending) break;
        fake_complete_in(FAKE_CDC_IN, true);
    }
}
void fake_start(void) {
    if (tud_inited()) assert(tud_deinit(0));
    memset(fake_ep, 0, sizeof(fake_ep));
    fake_epoch = fake_invalid_submissions = fake_mounts = fake_umounts = fake_line_calls = 0;
    fake_last_dtr = fake_last_rts = fake_reject_next_in = false; fake_on_in_submit = fake_on_out_submit = NULL; fake_clear_logs();
    const tusb_rhport_init_t init = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_FULL};
    assert(tusb_rhport_init(0, &init)); fake_bus_reset(true); enumerate_descriptors(); fake_configuration(1);
    assert(tud_mounted() && tud_cdc_n_ready(0));
    assert(fake_ep[FAKE_CDC_OUT].pending && fake_ep[FAKE_CDC_IN].open);
#if CFG_TUD_EDPT_DEDICATED_HWFIFO
    assert(fake_ep[FAKE_CDC_OUT].fifo != NULL);
#else
    assert(fake_ep[FAKE_CDC_OUT].fifo == NULL);
#endif
    fake_clear_logs();
}
void fake_finish(void) { assert(fake_invalid_submissions == 0); assert(tud_deinit(0)); }
bool dcd_edpt_iso_alloc(uint8_t rhport, uint8_t addr, uint16_t size) { (void)rhport; (void)addr; (void)size; assert(!"unexpected ISO endpoint"); return false; }
bool dcd_edpt_iso_activate(uint8_t rhport, const tusb_desc_endpoint_t *desc) { (void)rhport; (void)desc; assert(!"unexpected ISO endpoint"); return false; }
