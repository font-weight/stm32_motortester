#include "mt_usb_descriptors.h"
#include "tusb.h"
#include <stddef.h>
#if CFG_TUD_ENABLED != 1 || CFG_TUD_MAX_SPEED != OPT_MODE_FULL_SPEED || \
    CFG_TUD_CDC != 1 || CFG_TUD_ENDPOINT0_SIZE != 64 || \
    CFG_TUD_INTERFACE_MAX != 2 || CFG_TUD_ENDPPOINT_MAX != 3 || \
    CFG_TUD_CDC_RX_EPSIZE != 64 || CFG_TUD_CDC_TX_EPSIZE != 64
#error "Descriptors require the one-CDC full-speed endpoint profile"
#endif
static uint8_t device[18] = {
    18, TUSB_DESC_DEVICE, 0, 2, TUSB_CLASS_MISC,
    MISC_SUBCLASS_COMMON, MISC_PROTOCOL_IAD, 64,
    0, 0, 0, 0, 0, 0, 1, 2, 3, 1
};
static uint8_t configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, 2, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN, 0, 0),
    TUD_CDC_DESCRIPTOR(0, 0, 0x81, 8, 0x02, 0x82, 64)
};
static const uint16_t languages[] = {0x0304, 0x0409};
static uint16_t strings[3][MT_USB_IDENTITY_MAX_CHARS + 1u];
static unsigned initialized;
_Static_assert(sizeof configuration == 75, "Unexpected CDC descriptor layout");
static unsigned ascii_length(const char *text, unsigned serial) {
    unsigned n;
    if (!text) return 0;
    for (n = 0; n <= MT_USB_IDENTITY_MAX_CHARS; ++n) {
        unsigned char c = (unsigned char)text[n];
        if (!c) return n;
        if (c < 32u || c > 126u) return 0;
        if (serial && !((c >= '0' && c <= '9') ||
            (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'))) return 0;
    }
    return 0;
}
static void put16(uint8_t *bytes, uint16_t value) {
    bytes[0] = (uint8_t)value; bytes[1] = (uint8_t)(value >> 8);
}
int mt_usb_descriptors_init(const mt_usb_identity *id) {
    const char *texts[3];
    unsigned lengths[3];
    if (!id || initialized || tud_inited() || !id->vid || id->vid == UINT16_MAX ||
        id->bus_power_ma < 2u || id->bus_power_ma > 500u || (id->bus_power_ma & 1u))
        return 0;
    for (unsigned shift = 0; shift < 16u; shift += 4u)
        if (((id->bcd_device >> shift) & 15u) > 9u) return 0;
    texts[0] = id->manufacturer; texts[1] = id->product; texts[2] = id->serial;
    for (unsigned i = 0; i < 3u; ++i) {
        lengths[i] = ascii_length(texts[i], i == 2u);
        if (!lengths[i]) return 0;
    }
    /* Validate every field before publishing any descriptor. Separate immutable
     * buffers keep prior control-transfer pointers stable across callbacks. */
    put16(device + 8, id->vid); put16(device + 10, id->pid);
    put16(device + 12, id->bcd_device);
    configuration[8] = (uint8_t)(id->bus_power_ma / 2u);
    for (unsigned i = 0; i < 3u; ++i) {
        strings[i][0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2u + 2u * lengths[i]));
        for (unsigned j = 0; j < lengths[i]; ++j)
            strings[i][j + 1u] = (uint8_t)texts[i][j];
    }
    initialized = 1;
    return 1;
}
uint8_t const *tud_descriptor_device_cb(void) {
    return initialized ? device : NULL;
}
uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    return initialized && index == 0u ? configuration : NULL;
}
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t language) {
    if (!initialized) return NULL;
    if (!index) return languages;
    return index <= 3u && language == 0x0409u ? strings[index - 1u] : NULL;
}
