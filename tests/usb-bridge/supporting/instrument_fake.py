"""Explicit test-only instrumentation of the unchanged prior fake DCD input."""
def generate(cdc, build, bridge):
    src=(cdc/'fake_dcd.c').read_text()
    def replace(old,new):
        nonlocal src
        assert src.count(old)==1, (old,src.count(old)); src=src.replace(old,new)
    replace('#include "fake_dcd.h"','#include "fake_dcd.h"\n#include "test_integration.h"')
    replace('void tud_mount_cb(void) { ++fake_mounts; }','void tud_mount_cb(void) { ++fake_mounts; test_mount_hook(); }')
    replace('void dcd_sof_enable(uint8_t rhport, bool en) { (void)rhport; (void)en; }','void dcd_sof_enable(uint8_t rhport, bool en) { (void)rhport; (void)en; test_dcd_call(OP_SOF); }')
    dcd=(bridge/'build/dcd_stm32_fsdev.c').read_text()
    begin=dcd.index('void dcd_set_address(')
    end=dcd.index('\n}\n',begin)+2
    address=dcd[begin:end]
    assert 'mt_usb_bridge_address_result(dcd_edpt_xfer(' in address
    address=address.replace('{', '{\n  test_dcd_call(OP_ADDRESS);',1)
    replace('void dcd_set_address(uint8_t rhport, uint8_t address) { (void)address; assert(dcd_edpt_xfer(rhport, 0x80, NULL, 0, false)); }',address+'\nvoid dcd_edpt0_status_complete(uint8_t rhport, tusb_control_request_t const *request) { (void)rhport; (void)request; test_dcd_call(OP_STATUS); }')

    replace('void dcd_edpt_close_all(uint8_t rhport) {','void dcd_edpt_close_all(uint8_t rhport) {\n    test_dcd_call(OP_CLOSE);')
    replace('bool dcd_edpt_open(uint8_t rhport, const tusb_desc_endpoint_t *desc) {','bool dcd_edpt_open(uint8_t rhport, const tusb_desc_endpoint_t *desc) {\n    test_dcd_call(OP_OPEN); if (test_reject_open == desc->bEndpointAddress) { test_reject_open = -1; return false; }')
    replace('void dcd_edpt_stall(uint8_t rhport, uint8_t addr) {','void dcd_edpt_stall(uint8_t rhport, uint8_t addr) { test_dcd_call(OP_STALL);')
    replace('void dcd_edpt_clear_stall(uint8_t rhport, uint8_t addr) {','void dcd_edpt_clear_stall(uint8_t rhport, uint8_t addr) { test_dcd_call(OP_CLEAR);')
    replace('bool dcd_edpt_xfer(uint8_t rhport, uint8_t addr, uint8_t *buffer, uint16_t length, bool in_isr) {','bool dcd_edpt_xfer(uint8_t rhport, uint8_t addr, uint8_t *buffer, uint16_t length, bool in_isr) {\n    test_dcd_call(OP_XFER);')
    replace('bool accepted = valid;','bool accepted = valid;\n    if (test_reject_xfer == addr) { accepted = false; test_reject_xfer = -1; }')
    replace('bool dcd_edpt_xfer_fifo(uint8_t rhport, uint8_t addr, tu_fifo_t *fifo, uint16_t length, bool in_isr) {','bool dcd_edpt_xfer_fifo(uint8_t rhport, uint8_t addr, tu_fifo_t *fifo, uint16_t length, bool in_isr) {\n    test_dcd_call(OP_FIFO);')
    replace('void fake_pump(void) {\n    unsigned limit = 0;\n    while (tud_task_event_ready()) { assert(++limit < 1000); tud_task_ext(0, false); }\n}','void fake_pump(void) { test_pump(); }')
    (build/'fake_dcd_instrumented.c').write_text(src)
