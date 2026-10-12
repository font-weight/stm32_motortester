#ifndef TEST_FAKE_DCD_H
#define TEST_FAKE_DCD_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define FAKE_LOG_CAPACITY 131072u
#define FAKE_LOG_RECORDS 8192u
#define FAKE_CDC_OUT 0x02u
#define FAKE_CDC_IN 0x82u
typedef struct {
    unsigned epoch;
    uint8_t ep_addr;
    uint16_t length;
    size_t offset;
    bool accepted;
} fake_submission;
typedef struct {
    bool open, pending, stalled;
    uint8_t *buffer;
    void *fifo;
    uint16_t length;
    unsigned epoch;
} fake_endpoint;
extern fake_endpoint fake_ep[256];
extern uint8_t fake_submitted[FAKE_LOG_CAPACITY];
extern uint8_t fake_host[FAKE_LOG_CAPACITY];
extern size_t fake_submitted_size, fake_host_size;
extern fake_submission fake_records[FAKE_LOG_RECORDS];
extern size_t fake_record_count;
extern unsigned fake_epoch, fake_invalid_submissions, fake_mounts, fake_umounts;
extern unsigned fake_line_calls;
extern bool fake_last_dtr, fake_last_rts, fake_reject_next_in;
extern void (*fake_on_in_submit)(void);
extern void (*fake_on_out_submit)(void);
void fake_start(void);
void fake_finish(void);
void fake_bus_reset(bool service);
void fake_pump(void);
void fake_configuration(uint8_t value);
void fake_line_state(uint16_t value);
void fake_signal(unsigned event, bool service);
void fake_complete_in(uint8_t ep_addr, bool service);
void fake_out(const uint8_t *bytes, size_t length, bool service);
void fake_drain(void);
void fake_clear_logs(void);
#endif
