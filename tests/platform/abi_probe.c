#include "adc_timer_dma.h"

_Static_assert(MT_ADC_DMA_SAMPLES == 2u * MT_ADC_DMA_BLOCK_SAMPLES, "two DMA halves");
_Static_assert(sizeof(mt_adc_dma_state_t) == 4u, "requires -fno-short-enums");
_Static_assert(sizeof(mt_adc_dma_fault_t) == 4u, "requires -fno-short-enums");

/* Symbol sizes are type sizes. This file is a probe, not linked firmware. */
const unsigned char mt_adc_dma_context_bytes[sizeof(mt_adc_dma_t)] = {0};
const unsigned char mt_adc_dma_block_bytes[sizeof(mt_adc_dma_block_t)] = {0};
const unsigned char mt_adc_dma_io_bytes[sizeof(mt_adc_dma_io_t)] = {0};
