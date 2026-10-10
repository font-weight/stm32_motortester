#include "fake_dcd.h"
#include "tusb.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    printf("INFO TinyUSB native dedicated-HWFIFO=%u\n", (unsigned)CFG_TUD_EDPT_DEDICATED_HWFIFO);
    uint8_t tx[64], rx[64]; for (size_t i = 0; i < sizeof(tx); ++i) tx[i] = (uint8_t)i;
    fake_start(); assert(!tud_cdc_n_connected(0));
    fake_line_state(3); assert(tud_cdc_n_connected(0) && fake_last_dtr && fake_last_rts && fake_line_calls == 1);
    fake_line_state(2); assert(!tud_cdc_n_connected(0) && tud_cdc_n_ready(0));
    assert(tud_cdc_n_write(0, tx, 64) == 64); assert(fake_ep[FAKE_CDC_IN].pending);
    assert(tud_cdc_n_write(0, tx, 64) == 64); assert(tud_cdc_n_write(0, tx, 64) == 0);
    assert(fake_host_size == 0); fake_drain(); assert(fake_host_size == 128);
    assert(memcmp(fake_host, tx, 64) == 0 && memcmp(fake_host + 64, tx, 64) == 0);
    fake_out(tx, 64, true); assert(tud_cdc_n_available(0) == 64);
    assert(tud_cdc_n_read(0, rx, 64) == 64 && memcmp(rx, tx, 64) == 0);
    unsigned unmounts = fake_umounts; fake_bus_reset(true); assert(!tud_mounted() && fake_umounts == unmounts);
    fake_configuration(1); assert(tud_cdc_n_ready(0)); fake_finish();
    puts("PASS real TinyUSB core EP0 device/config descriptors, SET_ADDRESS, SET_CONFIGURATION/CDC line-state + bulk IN/OUT FIFO backpressure; fake DCD only");
    return 0;
}
