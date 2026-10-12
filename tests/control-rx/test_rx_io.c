#include "../../firmware/app/mt_adc_control.h"
#include "../../firmware/app/mt_control_rx.h"
#include <assert.h>
static mt_control_rx composed_rx;
static mt_control_result io_init_rx(mt_adc_control *b, mt_control *c,
    mt_adc_dma_t *d, uint32_t (*ms)(void *), void *ctx, uint32_t build) {
    mt_control_result result = mt_adc_control_init(b,c,d,ms,ctx,build);
    if (result != MT_CONTROL_OK) return result;
    return mt_control_rx_init(&composed_rx,c);
}
static mt_control_result encoded_receive(mt_control *c,
    const mt_wire_message *m, uint32_t now) {
    uint8_t bytes[MT_WIRE_FRAME_MAX]; size_t n, used = 0;
    assert(composed_rx.control == c);
    assert(mt_wire_encode(m,bytes,sizeof bytes,&n) == MT_WIRE_OK);
    while (used < n) {
        size_t count = n-used, consumed;
        mt_control_result result;
        if (count > 7u) count = 7u;
        result = mt_control_rx_feed(&composed_rx,bytes+used,count,now,&consumed);
        used += consumed;
        if (result != MT_CONTROL_OK) return result;
        assert(consumed == count);
    }
    return MT_CONTROL_OK;
}
#define mt_adc_control_init io_init_rx
#define mt_control_receive encoded_receive
#include "../adc-control/test_adc_control.c"
#undef mt_control_receive
#undef mt_adc_control_init
