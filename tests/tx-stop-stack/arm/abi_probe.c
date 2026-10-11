#include "mt_runtime.h"
#define SIZE(T) const unsigned char size_##T[sizeof(T)] = {0};
#define ALIGN(T) const unsigned char align_##T[_Alignof(T)] = {0};
#define FIELD(T,M) const unsigned char field_##T##_##M[sizeof(((T *)0)->M)] = {0};
#define OFFSET(T,M) const unsigned char offset_##T##_##M[offsetof(T,M)+1] = {0};
SIZE(mt_tx) ALIGN(mt_tx)
SIZE(mt_tx_slot) ALIGN(mt_tx_slot)
SIZE(mt_tx_event) ALIGN(mt_tx_event)
SIZE(mt_wire_message) ALIGN(mt_wire_message)
SIZE(mt_runtime) ALIGN(mt_runtime)
SIZE(mt_tx_result) SIZE(mt_tx_window_phase)
FIELD(mt_tx,slots) OFFSET(mt_tx,slots)
FIELD(mt_tx,frame) OFFSET(mt_tx,frame)
FIELD(mt_tx,count) OFFSET(mt_tx,count)
FIELD(mt_tx,length) OFFSET(mt_tx,length)
FIELD(mt_tx,offset) OFFSET(mt_tx,offset)
FIELD(mt_tx,session) OFFSET(mt_tx,session)
FIELD(mt_tx,next_sequence) OFFSET(mt_tx,next_sequence)
FIELD(mt_tx,head_ms) OFFSET(mt_tx,head_ms)
FIELD(mt_tx,progress_ms) OFFSET(mt_tx,progress_ms)
FIELD(mt_tx,stop_ms) OFFSET(mt_tx,stop_ms)
FIELD(mt_tx,terminal_generation) OFFSET(mt_tx,terminal_generation)
FIELD(mt_tx,terminal_window) OFFSET(mt_tx,terminal_window)
FIELD(mt_tx,terminal_type) OFFSET(mt_tx,terminal_type)
FIELD(mt_tx,stopping) OFFSET(mt_tx,stopping)
FIELD(mt_tx,fault) OFFSET(mt_tx,fault)
FIELD(mt_tx_slot,message) OFFSET(mt_tx_slot,message)
FIELD(mt_tx_slot,reply) OFFSET(mt_tx_slot,reply)
