#include "mt_adc_control.h"
unsigned char critic_driver_size[sizeof(mt_adc_dma_t)];
unsigned char critic_binding_size[sizeof(mt_adc_control)];
#ifndef CRITIC_BASE
unsigned char critic_guard_size[sizeof(mt_adc_dma_start_guard)];
#endif
