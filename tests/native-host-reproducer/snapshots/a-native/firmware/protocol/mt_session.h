#ifndef MT_SESSION_H
#define MT_SESSION_H
#include "mt_command.h"

typedef enum {
    MT_SESSION_ACCEPT = 0,
    MT_SESSION_NEW_HELLO,
    MT_SESSION_DUPLICATE,
    MT_SESSION_WRONG_SESSION,
    MT_SESSION_NEED_HELLO,
    MT_SESSION_INVALID_HELLO,
    MT_SESSION_RECOVERY,
    MT_SESSION_EXHAUSTED,
    MT_SESSION_ARGUMENT
} mt_session_event;

/* Single-owner foreground RX state, bounded storage, no heap.
 * previous contains only meaningful decoded fields and used payload bytes.
 * nonce is retained through recovery so an identical HELLO cannot restart it. */
typedef struct {
    uint64_t nonce;
    uint32_t next_sequence;
    uint32_t last_request;
    unsigned active;
    unsigned have_previous;
    mt_wire_message previous;
} mt_session;

/* Boot/physical transport reset: forget all state, including nonce.
 * The host must still generate a fresh nonce on every handshake attempt. */
void mt_session_init(mt_session *session);

/* Disable ordinary command acceptance, preserving the current nonce.
 * Caller must stop sampling and clear/drain TX as the wire contract requires.
 * This function alone has no hardware side effects. NULL is ignored. */
void mt_session_require_hello(mt_session *session);

/* Call ONLY for a message already accepted by mt_wire_decode.
 * Input must not overlap session storage. NULL or impossible payload length
 * returns ARGUMENT without changing state.
 *
 * NEW_HELLO commits RX identity only; caller must safely abort old acquisition,
 * flush/drain old-session TX, reset configuration/counters, then send HELLO_ACK.
 * ACCEPT consumes header sequence/request even when ordinary payload is invalid;
 * caller then parses payload and replies ERROR, without executing invalid data.
 * DUPLICATE is ignored, never automatically re-executed or re-ACKed.
 * RECOVERY/EXHAUSTED disables acceptance; caller must perform safe recovery.
 * No event in this API arms ADC, allocates a window, or sends any response.
 *
 * Draft rule: sequence contiguous from2; request strictly increases >=2, gaps
 * permitted. Neither identifier may use UINT32_MAX. Shared wire review pending. */
mt_session_event mt_session_receive(mt_session *session,
                                    const mt_wire_message *message);
#endif
