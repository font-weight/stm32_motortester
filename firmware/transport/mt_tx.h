#ifndef MT_TX_H
#define MT_TX_H
#include "../protocol/mt_wire.h"

#define MT_TX_CAPACITY 6u
#define MT_TX_NORMAL_LIMIT 3u
#define MT_TX_BULK_LIMIT 2u
#define MT_TX_CHUNK_MAX 64u
#define MT_TX_STALL_MS UINT32_C(1000)
#define MT_TX_FRAME_MS UINT32_C(3000)
#define MT_TX_STOP_MS UINT32_C(1000)

typedef enum {
    MT_TX_OK = 0, MT_TX_ARGUMENT, MT_TX_NOT_NEGOTIATED, MT_TX_FULL,
    MT_TX_DRAINING, MT_TX_FAULT, MT_TX_SEQUENCE, MT_TX_TIMEOUT,
    MT_TX_IO, MT_TX_IDLE, MT_TX_BLOCKED, MT_TX_PROGRESS
} mt_tx_result;

enum { MT_TX_STARTED = 1u, MT_TX_FINISHED = 2u, MT_TX_STOP_FINISHED = 4u };
typedef struct {
    unsigned flags;
    uint8_t type;
    uint64_t session;
    uint32_t sequence;
    uint32_t request;
    uint32_t generation;
    uint32_t window_id;
    size_t accepted;
} mt_tx_event;

/* Must copy exactly the returned prefix to an irrevocable, ordered USB TX
 * FIFO before returning. 0 means no bytes accepted; negative means failure
 * with NO bytes accepted. Return must be <= length. Never retain bytes pointer.
 * No recursive mt_tx calls; no ISR/concurrent call. This is NOT USB completion
 * or proof that the host received anything. A software application queue that
 * may later discard/reorder bytes does not meet this contract. */
typedef int (*mt_tx_write_fn)(void *context, const uint8_t *bytes, size_t length);

typedef struct {
    mt_wire_message message;
    unsigned reply;
} mt_tx_slot;

/* Single-owner foreground object, no heap. Treat fields as private. */
typedef struct {
    mt_tx_slot slots[MT_TX_CAPACITY];
    uint8_t frame[MT_WIRE_FRAME_MAX];
    size_t count, length, offset;
    uint64_t session;
    uint32_t next_sequence;
    uint32_t head_ms, progress_ms, stop_ms;
    uint32_t terminal_generation, terminal_window;
    uint8_t terminal_type;
    unsigned stopping;
    mt_tx_result fault;
} mt_tx;

/* Physical USB reset/deconfiguration/disconnect: call this and separately stop
 * acquisition, clear RX parser/session state. Queued/partial TX becomes invalid.
 * Initialization alone does not negotiate a session. NULL is ignored. */
void mt_tx_init(mt_tx *tx);

/* Caller must first validate a NEW, nonzero HELLO nonce with mt_command and the
 * session guard. Repeated current nonce is rejected here. Retain an old partial
 * frame; discard all unstarted frames, then append HELLO_ACK for the new nonce.
 * ack must have type129, request1, payload16, session0, sequence0.
 * On invalid arguments nothing changes. Recoverable TX faults are cleared;
 * caller must ensure the backend is usable. Physical reset uses init instead. */
mt_tx_result mt_tx_hello(mt_tx *tx, uint64_t nonce,
                        const mt_wire_message *ack, uint32_t now_ms);

/* Enqueue a device message by VALUE. Caller supplies type/request/payload only:
 * session/sequence must be 0. HELLO_ACK/STOP_ACK use the dedicated operations.
 * Regular admission: <=3 frames; bulk/events stop at2, reserving one reply slot.
 * FULL changes nothing. Retry bulk only while retaining its original buffer.
 * A reply must be admitted BEFORE its command's state mutation; inability to
 * admit a mandatory reply requires fail-closed recovery, never silent loss.
 * No sequence is consumed until poll receives its first positive acceptance. */
mt_tx_result mt_tx_enqueue(mt_tx *tx, const mt_wire_message *message,
                          uint32_t now_ms);

/* Returns 146 END / 147 ABORT after first-byte acceptance for this session and
 * window, else0. Remembers only the latest committed terminal. The dispatcher
 * owns the longer-lived END CRC tombstone and generation/window validity. */
uint8_t mt_tx_terminal_committed(const mt_tx *tx, uint32_t generation,
                                 uint32_t window_id);

typedef enum {
    MT_TX_NO_WINDOW = 0, MT_TX_WINDOW_NO_TX, MT_TX_WINDOW_QUEUED,
    MT_TX_WINDOW_STARTED, MT_TX_WINDOW_END_COMMITTED,
    MT_TX_WINDOW_ABORT_COMMITTED
} mt_tx_window_phase;
/* A diagnostic view of TX, not proof that a window was allocated. Caller owns
 * that proof. A nonzero id absent from TX returns WINDOW_NO_TX. */
mt_tx_window_phase mt_tx_window_status(const mt_tx *tx, uint32_t generation,
                                      uint32_t active_window);

/* Caller first validates STOP/generation and synchronously disables sampling.
 * Prune all unstarted bulk/events; preserve partial head and all earlier replies.
 * Append (ABORT if active_window!=0 and terminal not committed), STOP_ACK,
 * STATUS(STOPPED), atomically. Three reserved slots guarantee admission under
 * normal queue limits. Existing STOP drain returns DRAINING unchanged.
 * A queued matching ABORT is retained, including its original reason.
 * abort_message may be NULL if no terminal is needed or one is already queued. ACK type133/request
 * nonzero/payload12; status type134/request0/payload24; abort type147/request0/
 * payload12. IDs must match generation/active_window as documented in README.
 * Regular enqueue is blocked through final STOP_ACK byte acceptance. The
 * trailing STATUS remains ordered but does not extend this Stop barrier.
 * STOP_FINISHED accompanies ACK FINISHED; only host reception closes host Stop.
 * FINISHED means FIFO acceptance, NOT USB transfer completion. */
mt_tx_result mt_tx_stop(mt_tx *tx, uint32_t generation, uint32_t active_window,
                       const mt_wire_message *abort_message,
                       const mt_wire_message *ack,
                       const mt_wire_message *stopped_status, uint32_t now_ms);

/* Confirmed acquisition failure on a live transport. Same bounded terminal
 * pruning/order as Stop, but appends only optional ABORT then ERROR STATUS;
 * no ACK or Stop barrier. A retained original reply head keeps its timer;
 * a new head after pruning starts its own timer. Earlier replies/partial frame
 * and the original queued ABORT reason survive. A committed END is rejected.
 * status is type134/request0/payload24, state5, reason1/2, matching generation
 * and active window (or caller-owned last window when no active window).
 * Caller proves the current window allocation and hardware quiescence. */
mt_tx_result mt_tx_acquisition_fault(mt_tx *tx, uint32_t generation,
                       uint32_t active_window,
                       const mt_wire_message *abort_message,
                       const mt_wire_message *error_status, uint32_t now_ms);

/* At most one write of <=64 bytes; no busy wait. event changes only on PROGRESS.
 * Call in monotonic wrapping uint32 milliseconds, gaps <2^31 ms, including when
 * USB is blocked. Check >=1000 ms since last progress, >=3000 ms per head frame,
 * >=1000 ms total Stop drain. Timeout/I/O/sequence failure latch, preserving
 * diagnostics, and forbid further output until a new HELLO or physical reset.
 * Caller must stop acquisition and invalidate incomplete windows on any fault.
 * All input/output objects must be valid and non-overlapping. */
mt_tx_result mt_tx_poll(mt_tx *tx, uint32_t now_ms, mt_tx_write_fn write,
                       void *context, mt_tx_event *event);
#endif
