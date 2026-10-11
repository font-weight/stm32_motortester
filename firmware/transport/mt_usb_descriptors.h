#ifndef MT_USB_DESCRIPTORS_H
#define MT_USB_DESCRIPTORS_H
#include <stdint.h>
#define MT_USB_IDENTITY_MAX_CHARS 31u
typedef struct {
    uint16_t vid, pid, bcd_device, bus_power_ma;
    const char *manufacturer;
    const char *product;
    const char *serial;
} mt_usb_identity;
/* One foreground initialization before tusb_init; no live reinitialization.
 * Caller supplies authorized IDs and a measured/verified bus-power budget.
 * VID must be neither0 nor0xffff; PID0 is allowed. Device revision is BCD.
 * Power is even2..500mA. Strings are copied, never silently truncated:
 * nonempty printable ASCII <=31 chars; serial uses ASCII alphanumerics only.
 * Serial must be stable and unique for each physical unit.
 * No pointer may reference unreadable storage during initialization.
 * Success does not verify USB-IF ownership, power, or actual enumeration. */
int mt_usb_descriptors_init(const mt_usb_identity *identity);
#endif
