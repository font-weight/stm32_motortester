#ifndef MT_ADC_TIMER_DMA_H
#define MT_ADC_TIMER_DMA_H

#include <stdbool.h>
#include <stdint.h>

#define MT_ADC_DMA_SAMPLES 256u
#define MT_ADC_DMA_BLOCK_SAMPLES 128u
#define MT_ADC_DMA_RATE_HZ 50000u
#define MT_ADC_DMA_SAMPLE_US 20u
#define MT_ADC_DMA_BLOCK_US 2560u
#define MT_ADC_DMA_WAIT_US 1000u
#define MT_ADC_DMA_WAIT_POLLS 10000u

/* Aligned 32-bit register access only (ES096 2.4.2). No startup, ISR or HAL.
 * Callbacks must return promptly, must not reenter, and must not allocate.
 * now_us is an always-running modulo-2^32 monotonic clock with <=1 us ticks.
 * A full wrap between calls cannot be detected: service is required <2560 us.
 */
typedef struct {
    uint32_t (*read32)(void *user, uint32_t address);
    void (*write32)(void *user, uint32_t address, uint32_t value);
    uint32_t (*now_us)(void *user);
    void *user;
} mt_adc_dma_io_t;

typedef enum {
    MT_ADC_DMA_STOPPED = 0,
    MT_ADC_DMA_RUNNING,
    MT_ADC_DMA_FAULTED
} mt_adc_dma_state_t;

typedef enum {
    MT_ADC_DMA_FAULT_NONE = 0,
    MT_ADC_DMA_FAULT_CLOCK,
    MT_ADC_DMA_FAULT_CALIBRATION,
    MT_ADC_DMA_FAULT_TIMEBASE,
    MT_ADC_DMA_FAULT_TRANSFER,
    MT_ADC_DMA_FAULT_DEADLINE,
    MT_ADC_DMA_FAULT_STOP,
    MT_ADC_DMA_FAULT_SEQUENCE_LIMIT
} mt_adc_dma_fault_t;

typedef enum {
    MT_ADC_DMA_OK = 0,
    MT_ADC_DMA_BLOCK = 1,
    MT_ADC_DMA_EMPTY = 2,
    MT_ADC_DMA_BAD_ARGUMENT = -1,
    MT_ADC_DMA_BUSY = -2,
    MT_ADC_DMA_ERROR = -3,
    MT_ADC_DMA_NOT_RUNNING = -4
} mt_adc_dma_result_t;

typedef struct {
    uint32_t sysclk_hz;
    uint32_t hclk_hz;
    uint32_t pclk1_hz;
    uint32_t pclk2_hz;
    uint32_t timer_hz;
    uint32_t adc_hz;
    uint32_t timer_psc;
    uint32_t timer_arr;
} mt_adc_dma_clock_t;

typedef struct {
    const uint16_t *samples; /* Snapshot owned by driver, NOT the DMA ring. */
    uint32_t first_sample_index; /* First sample after start is index zero. */
    uint32_t count;
    uint32_t nominal_rate_hz;
    /* Timer CEN write is bracketed by these clock reads. First timer update
     * follows one 20 us period; ADC aperture/latency is NOT measured here.
     * Derive nominal sample time from index, not the foreground drain time.
     */
    uint32_t start_before_us;
    uint32_t start_after_us;
    uint32_t first_trigger_offset_us;
} mt_adc_dma_block_t;

/* Statically allocate in DMA-accessible STM32 SRAM and keep alive through a
 * successful stop. Single owner; all calls serialized in foreground. No ISR
 * accesses this context. Do not mutate public members outside the test model.
 * Ring=512 B; stable snapshot=256 B. Both count toward the final RAM model.
 */
typedef struct {
    volatile uint16_t dma_samples[MT_ADC_DMA_SAMPLES];
    uint16_t snapshot[MT_ADC_DMA_BLOCK_SAMPLES];
    mt_adc_dma_io_t io;
    mt_adc_dma_clock_t clock;
    uint32_t hse_hz;
    uint32_t clock_cfgr;
    uint32_t start_before_us;
    uint32_t start_after_us;
    uint32_t last_poll_us;
    uint32_t last_progress_us;
    uint32_t first_sample_index;
    uint32_t last_remaining;
    mt_adc_dma_state_t state;
    mt_adc_dma_fault_t fault;
    bool next_half;
    bool initialized;
    bool owns_peripherals;
} mt_adc_dma_t;

/* Helpers for native STM32 memory-mapped I/O. They ignore user. Native startup
 * supplies now_us and its user argument; tests supply an emulated register bus.
 */
uint32_t mt_adc_dma_mmio_read32(void *user, uint32_t address);
void mt_adc_dma_mmio_write32(void *user, uint32_t address, uint32_t value);

/* init performs NO MMIO. hse_hz is the verified nominal board input frequency,
 * not a default: zero is rejected if current SYSCLK/PLL uses HSE. HSI is the
 * device's nominal 8 MHz. It is not an independent frequency measurement.
 * Call init once, before all other calls, with nonoverlapping valid objects.
 */
mt_adc_dma_result_t mt_adc_dma_init(mt_adc_dma_t *driver,
                                  const mt_adc_dma_io_t *io, uint32_t hse_hz);
/* Existing RCC must already provide 12 MHz ADC, legal APB clocks and an integer
 * 1 MHz timer prescaler divisor (ARR=19 gives 50 kS/s). Does not alter RCC CFGR/CR, startup or oscillator choice.
 * Owns ADC1, PA0, TIM3, DMA1 channel 1; they must be exclusively reserved.
 * A successful stop is required before restart after a fault.
 */
mt_adc_dma_result_t mt_adc_dma_start(mt_adc_dma_t *driver);
/* Call at intervals STRICTLY LESS THAN 2560 us while running. Copy and metadata
 * validation also finish inside that budget. Output unchanged unless BLOCK.
 * A BLOCK view lasts only until the next read/start/stop, regardless of result.
 * Caller must feed the acquisition core before next read; stop on frozen/error.
 */
mt_adc_dma_result_t mt_adc_dma_read_block(mt_adc_dma_t *driver,
                                       mt_adc_dma_block_t *block);
/* Stops TIM3, powers ADC1 down, disables DMA and verifies readback. Idempotent.
 * Success invalidates partial/snapshot data. Failure retains ownership and the
 * caller MUST keep the context alive and must not reuse its ring/peripherals.
 * RCC gates are left enabled (GPIO/DMA clocks can have other users).
 */
mt_adc_dma_result_t mt_adc_dma_stop(mt_adc_dma_t *driver);

#endif
