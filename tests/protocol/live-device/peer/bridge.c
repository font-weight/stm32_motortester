#include "firmware/protocol/mt_live_device.h"
#include <string.h>

/* Test seam only: every state/wire transition is in the unmodified C endpoint. */
static mt_live_device device;
static mt_live_snapshot snapshot;
static uint64_t clock_value;
static size_t accept_limit, output_length;
static unsigned start_calls, quiesce_calls, isolation_calls, write_calls;
static unsigned max_write, invalid_callback;
static int start_result, isolation_result;
static uint8_t output[1048576];

static uint64_t fake_clock(void *context) { (void)context; return clock_value; }
static int fake_start(void *context, uint32_t generation) {
    (void)context; (void)generation; ++start_calls; return start_result;
}
static void fake_quiesce(void *context, mt_live_snapshot *value) {
    (void)context; ++quiesce_calls; *value=snapshot;
}
static int fake_isolate(void *context) {
    (void)context; ++isolation_calls; return isolation_result;
}
static size_t fake_write(void *context, const uint8_t *bytes, size_t length) {
    size_t accepted=length<accept_limit?length:accept_limit;
    (void)context; ++write_calls;
    if(length>max_write)max_write=(unsigned)length;
    if(length>64u || accepted>sizeof output-output_length) {
        invalid_callback=1; return SIZE_MAX;
    }
    memcpy(output+output_length,bytes,accepted); output_length+=accepted;
    return accepted;
}
int bridge_reset(void) {
    mt_live_io io;
    /* New synthetic fixture: there is no independently running producer, every
     * callback has returned, and writes retain no pointers. Reset the external
     * clock/snapshot/FIFO context before initializing reused device storage.
     * This is test isolation, never a claim that init stops real hardware. */
    memset(&io,0,sizeof io); memset(&snapshot,0,sizeof snapshot);
    snapshot.hardware_off=1; snapshot.produced_known=1;
    clock_value=0; accept_limit=64; output_length=0;
    start_calls=quiesce_calls=isolation_calls=write_calls=max_write=invalid_callback=0;
    start_result=isolation_result=1;
    io.clock_ms=fake_clock; io.start=fake_start; io.quiesce=fake_quiesce;
    io.write=fake_write; io.isolate_tx=fake_isolate;
    io.transaction_bound_ms=1; io.firmware_build=0x0135289bu;
    return mt_live_device_init(&device,&io);
}
void bridge_time(uint64_t value) { clock_value=value; }
void bridge_accept(size_t value) { accept_limit=value; }
void bridge_start_result(int value) { start_result=value; }
void bridge_snapshot(unsigned off,unsigned known,uint64_t end,uint16_t reason) {
    memset(&snapshot,0,sizeof snapshot); snapshot.hardware_off=off;
    snapshot.produced_known=known; snapshot.produced_end=end;
    snapshot.acquisition_reason=reason;
}
int bridge_tail(uint64_t first,const uint16_t *codes,uint16_t count) {
    mt_live_tail *tail;
    if(snapshot.tail_count>=2u || !codes || !count || count>128u)return 0;
    tail=&snapshot.tail[snapshot.tail_count++];tail->first_index=first;tail->count=count;
    memcpy(tail->codes,codes,(size_t)count*sizeof *codes); return 1;
}
int bridge_receive(const uint8_t *bytes,size_t length) {
    return mt_live_device_receive(&device,bytes,length);
}
int bridge_step(void) { return mt_live_device_step(&device); }
int bridge_offer(uint64_t first,const uint16_t *codes,uint16_t count) {
    return mt_live_device_offer(&device,first,codes,count);
}
void bridge_fault(uint16_t reason) { mt_live_device_fault(&device,reason); }
size_t bridge_drain(uint8_t *out,size_t capacity) {
    size_t count=output_length<capacity?output_length:capacity;
    memcpy(out,output,count); memmove(output,output+count,output_length-count);
    output_length-=count;return count;
}
uint64_t bridge_get(unsigned field) {
    switch(field) {
    case 0:return (uint64_t)device.state;
    case 1:return device.generation;
    case 2:return device.prefix_end;
    case 3:return device.queue.next_index;
    case 4:return device.queue.used;
    case 5:return device.first_reason;
    case 6:return device.local_error;
    case 7:return device.hardware_off;
    case 8:return device.tx_type;
    case 9:return device.tx_offset;
    case 10:return device.tx_length;
    case 11:return device.end_started;
    case 12:return device.end_complete;
    case 13:return start_calls;
    case 14:return quiesce_calls;
    case 15:return isolation_calls;
    case 16:return write_calls;
    case 17:return max_write;
    case 18:return invalid_callback;
    case 19:return device.stop_deadline;
    case 20:return device.terminal_flags;
    default:return UINT64_MAX;
    }
}
