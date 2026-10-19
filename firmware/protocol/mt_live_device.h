#ifndef MT_LIVE_DEVICE_H
#define MT_LIVE_DEVICE_H
#include "mt_wire.h"
#include "../acquisition/mt_sample_queue.h"

/* Isolated native prototype. No production USB/ADC binding exists. */
typedef enum {
    MT_LIVE_NO_SESSION, MT_LIVE_HELLO_PENDING, MT_LIVE_STOPPED,
    MT_LIVE_STARTING, MT_LIVE_STREAMING, MT_LIVE_DRAINING,
    MT_LIVE_TERMINAL, MT_LIVE_NEEDS_HELLO, MT_LIVE_FAULT
} mt_live_state;

typedef enum {
    MT_LIVE_ERR_NONE=0, MT_LIVE_ERR_PROTOCOL, MT_LIVE_ERR_CLOCK,
    MT_LIVE_ERR_TRANSPORT, MT_LIVE_ERR_DEADLINE, MT_LIVE_ERR_ARGUMENT,
    MT_LIVE_ERR_CALLBACK, MT_LIVE_ERR_INCOMPATIBLE
} mt_live_local_error;

typedef struct {
    uint64_t first_index;
    uint16_t count;
    uint16_t codes[128];
} mt_live_tail;

typedef struct {
    unsigned hardware_off;       /* Exactly 0 or 1; not inferred from queue state. */
    unsigned produced_known;     /* Exactly 0 or 1. */
    uint64_t produced_end;
    uint16_t acquisition_reason; /* 0 none, or protocol reason 1..6. */
    unsigned tail_count;         /* 0..2 certified pending blocks. */
    mt_live_tail tail[2];
} mt_live_snapshot;

typedef struct {
    void *context;
    uint64_t (*clock_ms)(void *context);
    /* Return exactly 1 only after enabling the fixed 50kS/s profile for this
     * generation, with source indices beginning at zero. Return 0 on failure;
     * a failed start MAY have partially enabled hardware, so cleanup follows.
     * Do not call offer/receive/step reentrantly from this callback. */
    int (*start)(void *context, uint32_t generation);
    /* Always fill a self-contained snapshot; retain no pointer to it. Returned
     * codes/metadata must not change after return. hardware_off=1 certifies the
     * actual timer/ADC/DMA ownership barrier, not merely a requested stop.
     * produced_known=1 certifies the final exclusive uint64 source endpoint;
     * it cannot be inferred from queue.next_index. Unknown means end=0.
     * A failed hardware barrier forces an unknown final endpoint in the core.
     * tail_count=0..2, blocks are internally contiguous, codes0..4095, counts
     * 1..128 and only the last block may be short. Include a pending full DMA
     * half as well as a final partial if both exist. Before a queue gap, the
     * first tail starts at the next offered index. After a queue fault, later
     * certified tail is discarded; it must never bridge the lost block.
     * A known produced end must cover the prior accepted prefix and all tail.
     * acquisition_reason preserves the first observed acquisition cause before
     * cleanup; hardware_off separately reports cleanup/quiescence success. */
    void (*quiesce)(void *context, mt_live_snapshot *snapshot);
    size_t (*write)(void *context, const uint8_t *bytes, size_t length);
    /* Return 1 only after proving no outstanding old writer can append bytes
     * later or retain a caller buffer, and accepted FIFO bytes remain ordered
     * before any subsequent delimiter/ACK. It need not empty the FIFO and does
     * not prove hardware stopped. Return0 if isolation cannot be certified. */
    int (*isolate_tx)(void *context);
    /* Qualified full write + completion/state-commit bound, including preemption.
     * Synthetic seam only until independently proven on the actual target.
     * write copies <=64 bytes synchronously into an ordered FIFO, retains no
     * pointer, and returns exact accepted count; SIZE_MAX means unknown count.
     * All callbacks must be bounded and non-reentrant. Clock is monotonic,
     * non-wrapping, <=INT64_MAX. Init/start do not imply hardware qualification. */
    uint32_t transaction_bound_ms;
    uint32_t firmware_build;
} mt_live_io;

typedef struct {
    mt_live_io io;
    mt_sample_queue queue;
    mt_live_state state;
    uint64_t last_time, nonce, attempted_nonce, pending_nonce;
    uint64_t stop_deadline, hello_deadline, inherited_deadline;
    unsigned stop_budget, hello_budget, inherited_budget;
    unsigned hardware_off, clock_failed, needs_delimiter;
    unsigned caps_ready, have_previous, rx_discarding;
    uint32_t rx_next, last_request, tx_next, generation;
    unsigned rx_exhausted, tx_exhausted;
    uint16_t first_reason, terminal_flags;
    unsigned local_error; /* First mt_live_local_error; never overwrite a cause. */
    unsigned incompatible_seen; /* Also observable when an earlier cause exists. */
    uint64_t produced_end, prefix_end;
    unsigned produced_known;
    uint32_t prefix_crc_state, last_data_sequence;
    uint32_t stop_request, end_sequence;
    unsigned end_started, end_complete, stop_seen;
    unsigned want_caps, want_start, want_error, want_end, want_ack;
    uint32_t caps_request, error_request;
    uint8_t error_type, error_code;
    uint8_t rx[MT_WIRE_FRAME_MAX - 1u];
    size_t rx_used;
    uint64_t rx_first_time;
    uint8_t previous[MT_WIRE_FRAME_MAX - 1u];
    size_t previous_length;
    uint8_t tx[MT_WIRE_FRAME_MAX];
    size_t tx_length, tx_offset;
    uint8_t tx_type;
    uint64_t tx_nonce, tx_first_index;
    uint16_t tx_count;
    uint32_t tx_sequence;
    size_t last_write_accepted;
    unsigned last_write_known;
} mt_live_device;

/* All operations are single-owner foreground calls on an initialized object.
 * Caller input/output cannot alias device storage. No callbacks may re-enter.
 * Init has no hardware side effects; it does not prove startup quiescence.
 * Use fresh unowned storage, or first externally establish acquisition reset/
 * quiescence, writer isolation and invalidation of every borrowed reference.
 * Never reset an active device object or live acquisition/writer by init alone.
 * The callbacks may modify only their external context and explicit outputs,
 * not these implementation-owned device fields. */
int mt_live_device_init(mt_live_device *device, const mt_live_io *io);
/* A bounded chunk, at most544 bytes. Return1 means input processing completed,
 * not that a command was accepted; inspect read-only state/diagnostics. Return0
 * reports invalid call arguments or a clock failure. Invalid envelope never
 * executes a command. A CRC-valid version mismatch records incompatibility and
 * recovers, with no invented wire reply or automatic legacy fallback. */
int mt_live_device_receive(mt_live_device *device, const uint8_t *bytes, size_t length);
/* One bounded TX operation plus timer service; call independently of RX traffic.
 * Return0 denotes a fault/rejected operation, not proof that no bytes escaped.
 * Return1 may mean no work in NO_SESSION or an already-latched FAULT; it is not
 * a healthy-session/progress indication. Inspect the read-only state/diagnostics. */
int mt_live_device_step(mt_live_device *device);
/* Certified full acquired block in source order; return1 only when accepted. */
int mt_live_device_offer(mt_live_device *device, uint64_t first_index,
                         const uint16_t *codes, uint16_t count);
void mt_live_device_fault(mt_live_device *device, uint16_t reason);
void mt_live_device_transport_lost(mt_live_device *device);
#endif
