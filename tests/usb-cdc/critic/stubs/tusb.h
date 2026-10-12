#ifndef CRITIC_STUB_TUSB_H
#define CRITIC_STUB_TUSB_H
#include <stdint.h>
#include <stdbool.h>
#define CFG_TUD_CDC 1
#define CFG_TUD_CDC_TX_OVERWRITABLE_IF_NOT_CONNECTED 0
#define CFG_TUD_CDC_RX_PERSISTENT 0
#define CFG_TUD_CDC_TX_PERSISTENT 0
bool tud_cdc_n_ready(uint8_t itf);
bool tud_cdc_n_connected(uint8_t itf);
uint32_t tud_cdc_n_write(uint8_t itf,const void *bytes,uint32_t length);
uint32_t tud_cdc_n_read(uint8_t itf,void *bytes,uint32_t length);
uint32_t tud_cdc_n_write_flush(uint8_t itf);
void tud_cdc_n_read_flush(uint8_t itf);
bool tud_cdc_n_write_clear(uint8_t itf);
#endif
