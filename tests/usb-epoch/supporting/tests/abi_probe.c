#include "mt_usb_epoch.h"
#include <stddef.h>
_Static_assert(sizeof(uint32_t) == 4, "32-bit tokens");
_Static_assert(sizeof(unsigned) == 4, "scoped ARM ABI uses 32-bit unsigned");
_Static_assert(sizeof(mt_usb_epoch) == 48, "scoped ARM metadata size");
_Static_assert(_Alignof(mt_usb_epoch) == 4, "scoped ARM metadata alignment");
_Static_assert(offsetof(mt_usb_epoch, current) == 0, "current offset");
_Static_assert(offsetof(mt_usb_epoch, queued_reset) == 4, "queued reset offset");
_Static_assert(offsetof(mt_usb_epoch, processed_reset) == 8, "processed reset offset");
_Static_assert(offsetof(mt_usb_epoch, cleaned) == 12, "cleaned offset");
_Static_assert(offsetof(mt_usb_epoch, configured) == 16, "configured offset");
_Static_assert(offsetof(mt_usb_epoch, soft_serial) == 20, "soft serial offset");
_Static_assert(offsetof(mt_usb_epoch, pending_soft) == 24, "pending soft offset");
_Static_assert(offsetof(mt_usb_epoch, faults) == 28, "faults offset");
_Static_assert(offsetof(mt_usb_epoch, queue_losses) == 32, "queue losses offset");
_Static_assert(offsetof(mt_usb_epoch, io_failures) == 36, "I/O failures offset");
_Static_assert(offsetof(mt_usb_epoch, reset_candidate) == 40, "reset candidate offset");
_Static_assert(offsetof(mt_usb_epoch, exhausted) == 44, "exhausted offset");
