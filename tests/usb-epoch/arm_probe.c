#include "../../firmware/transport/mt_usb_epoch.h"
unsigned char usb_epoch_context_size[sizeof(mt_usb_epoch)];
_Static_assert(sizeof(void *) == 4, "ARM32 ABI required");
_Static_assert(sizeof(mt_usb_epoch) == 48, "review context budget on ABI change");
