/* ABI probe only, never linked into firmware. */
#include "../../firmware/app/mt_control_rx.h"
unsigned char sizeof_control_rx[sizeof(mt_control_rx)];
unsigned char sizeof_control[sizeof(mt_control)];
unsigned char sizeof_stream[sizeof(mt_stream)];
unsigned char sizeof_message[sizeof(mt_wire_message)];
