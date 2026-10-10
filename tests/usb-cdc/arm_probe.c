#include "../../firmware/transport/mt_usb_cdc.h"
unsigned char usb_cdc_context_size[sizeof(mt_usb_cdc)];
_Static_assert(sizeof(void *) == 4, "ARM32 ABI required");
_Static_assert(MT_CONTROL_RX_CHUNK_MAX == 64u, "bounded carry");
