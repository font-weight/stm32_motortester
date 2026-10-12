#ifndef MT_CAPTURE_LINK_H
#define MT_CAPTURE_LINK_H
#include "../acquisition/acquisition.h"
#include "../transport/mt_tx.h"

typedef enum {
    MT_LINK_OK = 0, MT_LINK_TRIGGERED, MT_LINK_WINDOW_READY,
    MT_LINK_BUSY, MT_LINK_ARGUMENT, MT_LINK_STALE, MT_LINK_BAD_RELEASE,
    MT_LINK_TX_BLOCKED, MT_LINK_RECOVERY
} mt_link_result;
typedef enum {
    MT_LINK_IDLE = 0, MT_LINK_BEGIN, MT_LINK_DATA, MT_LINK_END,
    MT_LINK_END_QUEUED, MT_LINK_WAIT_RELEASE, MT_LINK_STOPPING, MT_LINK_FAILED,
    MT_LINK_RELEASE_EXPIRED, MT_LINK_ACQ_ERROR
} mt_link_phase;

/* One foreground owner. tx is external, initialized/negotiated by dispatcher.
 * Call mt_tx_poll ONLY through mt_capture_link_poll while this link is active.
 * Physical ADC/timer/DMA Stop is a caller responsibility before link Stop/reset.
 * After a validated NEW HELLO and synchronous hardware Stop, reinitialize this
 * link. An old partial TX frame may drain, but its session events are ignored.
 * Initialize after mt_tx_hello; using a link across a nonce change fails closed. */
typedef struct {
    mt_acq_t acquisition;
    mt_tx *tx;
    uint64_t session;
    uint32_t config_id, generation, last_window, active_window;
    uint32_t dropped_windows;
    uint32_t crc_running, window_crc, release_since_ms;
    uint32_t tomb_generation, tomb_window, tomb_crc;
    uint16_t offset;
    uint8_t error_reason, cancellation_counted;
    unsigned armed, have_tombstone;
    mt_link_phase phase;
} mt_capture_link;

void mt_capture_link_init(mt_capture_link *link, mt_tx *tx);
/* Dispatcher first validates session/request, all config/id ranges and current
 * state. Only after this preflight may it admit the SUCCESS CONFIG/ARM reply,
 * then call with no intervening mutation (single foreground owner). Failure
 * to admit a required reply requires recovery without executing the command.
 * These calls do not generate CONFIG_ACK or ARM_ACK.
 * Configuration identity and generation are supplied by that dispatcher. */
mt_link_result mt_capture_link_configure(mt_capture_link *link,
                                         const mt_acq_config_t *config,
                                         uint32_t config_id);
mt_link_result mt_capture_link_arm(mt_capture_link *link, uint32_t config_id,
                                   uint32_t generation, uint32_t now_ms);
/* On WINDOW_READY the driver must stop further acquisition before continuing.
 * On RECOVERY stop peripherals and recover transport/session; no full window
 * may be inferred from a failed/incomplete capture. */
mt_link_result mt_capture_link_sample(mt_capture_link *link, uint16_t code,
                                      uint32_t sample_ms);
/* Only an armed, actively acquiring IDLE phase can report reason1 (DMA) or
 * reason2 (deadline). Caller has confirmed hardware Stop and live transport.
 * Cancel an allocated window exactly once, queue ABORT before ERROR STATUS,
 * retain IDs/raw storage and disable rearm. Other phases fail closed. */
mt_link_result mt_capture_link_acquisition_fault(mt_capture_link *link,
                                                uint8_t reason, uint32_t now_ms);
/* Advance only the completed-END hold deadline. Expiry stops continuation but
 * retains IDs/tombstone and prevents buffer reuse; TX remains serviceable.
 * Acquisition-window read API readiness is not retained in the ERROR state.
 * Return 1 only for the transition. The controller reports ERROR/reason 3. */
int mt_capture_link_expire_release(mt_capture_link *link, uint32_t now_ms);
/* At most one bulk enqueue and one <=64-byte backend write per call.
 * now_ms clock/gaps follow mt_tx_poll. Never busy-wait on TX_BLOCKED. */
mt_link_result mt_capture_link_poll(mt_capture_link *link, uint32_t now_ms,
                                    mt_tx_write_fn write, void *context);
/* Caller MUST first validate header, target generation and current state,
 * WITHOUT touching hardware on a stale/invalid target. For a valid Stop,
 * synchronously disable hardware successfully before this call. Hardware Stop
 * failure requires recovery without calling this API or claiming STOP_ACK.
 * Generates Stop terminal/ACK/status using the TX reserve, but never a phantom
 * window. request is this STOP request ID; generation must be current (0 before
 * first ARM is allowed). A second outstanding Stop is BUSY. */
mt_link_result mt_capture_link_stop(mt_capture_link *link, uint32_t generation,
                                    uint32_t request, uint32_t now_ms);
/* Generates RELEASE_ACK only after exact current/tombstone ID+CRC validation.
 * A tombstone replay never re-arms. No file/disk operation delays this ACK.
 * BAD_RELEASE/STALE/BUSY mapping to ERROR belongs to the command dispatcher. */
mt_link_result mt_capture_link_release(mt_capture_link *link, uint32_t generation,
                                       uint32_t window, uint32_t crc,
                                       uint32_t request, uint32_t now_ms);
#endif
