#include "mt_boot_usb.h"
#include <stddef.h>
#define RCC_CR UINT32_C(0x40021000)
#define RCC_CFGR UINT32_C(0x40021004)
#define RCC_APB1RSTR UINT32_C(0x40021010)
#define RCC_APB1ENR UINT32_C(0x4002101c)
#define FLASH_ACR UINT32_C(0x40022000)
#define USB_BIT UINT32_C(0x00800000)
#define CAN_BIT UINT32_C(0x02000000)
#define SHARED_IRQS UINT32_C(0x00780000)
#define WAKE_IRQ UINT32_C(0x00000400)
#define EXTI18 UINT32_C(0x00040000)
#define EXTI_IMR UINT32_C(0x40010400)
#define EXTI_EMR UINT32_C(0x40010404)
#define EXTI_RTSR UINT32_C(0x40010408)
#define EXTI_FTSR UINT32_C(0x4001040c)
#define EXTI_SWIER UINT32_C(0x40010410)
#define EXTI_PR UINT32_C(0x40010414)
#define ISER0 UINT32_C(0xe000e100)
#define ISER1 UINT32_C(0xe000e104)
#define ISPR0 UINT32_C(0xe000e200)
#define ISPR1 UINT32_C(0xe000e204)
#define IABR0 UINT32_C(0xe000e300)
#define IABR1 UINT32_C(0xe000e304)
#define IPR4 UINT32_C(0xe000e410)
#define IPR5 UINT32_C(0xe000e414)
#define HP_PRIORITY UINT32_C(0xf0000000)
#define LP_PRIORITY UINT32_C(0x000000f0)

static uint32_t rd(const mt_clock_startup *s, uint32_t address) {
    return s->io.read32(s->io.context, address);
}
static void wr(const mt_clock_startup *s, uint32_t address, uint32_t value) {
    s->io.write32(s->io.context, address, value);
}
static int same_clock(const mt_clock_startup *s) {
    return (rd(s, RCC_CR) & UINT32_C(0x030f00fb)) ==
            (s->last_cr & UINT32_C(0x030f00fb)) &&
        (rd(s, RCC_CFGR) & UINT32_C(0x077fffff)) ==
            (s->last_cfgr & UINT32_C(0x077fffff)) &&
        (rd(s, FLASH_ACR) & 0x3fu) == 0x32u && (s->last_flash & 0x3fu) == 0x32u;
}
static int quiescent(const mt_clock_startup *s) {
    return !((rd(s, RCC_APB1ENR) | rd(s, RCC_APB1RSTR)) & CAN_BIT) &&
        !((rd(s, ISER0) | rd(s, ISPR0) | rd(s, IABR0)) & SHARED_IRQS) &&
        !((rd(s, ISER1) | rd(s, ISPR1) | rd(s, IABR1)) & WAKE_IRQ) &&
        !((rd(s, EXTI_IMR) | rd(s, EXTI_EMR) | rd(s, EXTI_RTSR) |
            rd(s, EXTI_FTSR) | rd(s, EXTI_SWIER)) & EXTI18);
}
static int priorities_match(const mt_clock_startup *s, uint32_t priority) {
    return (rd(s, IPR4) & HP_PRIORITY) == (priority << 28) &&
        (rd(s, IPR5) & LP_PRIORITY) == (priority << 4);
}
static mt_boot_usb_result finish(mt_boot_usb *b, mt_boot_usb_result result) {
    b->cause = result; b->ready = result == MT_BOOT_USB_OK ? 1u : 0u;
    b->active = 0;
    return result;
}
mt_boot_usb_result mt_boot_usb_prepare(mt_boot_usb *b,
    const mt_clock_startup *s, uint32_t priority) {
    uint32_t gates, resets;
    if (b == NULL || s == NULL || s->io.read32 == NULL ||
        s->io.write32 == NULL || priority > 15u) return MT_BOOT_USB_ARGUMENT;
    if (b->active || b->attempted) return MT_BOOT_USB_BUSY;
    b->attempted = 1; b->active = 1; b->ready = 0;
    if (s->active || s->attempted != 1u || s->applied != 1u ||
        s->cause != MT_CLOCK_OK || s->recovery != MT_CLOCK_RECOVERY_NONE ||
        !same_clock(s)) return finish(b, MT_BOOT_USB_CLOCK);
    gates = rd(s, RCC_APB1ENR); resets = rd(s, RCC_APB1RSTR);
    if ((gates | resets) & USB_BIT) return finish(b, MT_BOOT_USB_NOT_RESET);
    if (!quiescent(s)) return finish(b, MT_BOOT_USB_CONFLICT);
    /* PR has undefined reset contents. Clear only unarmed line18 by W1C.
     * Never RMW a pending register or clear another owner's pending event. */
    wr(s, EXTI_PR, EXTI18);
    if ((rd(s, EXTI_PR) | rd(s, EXTI_SWIER)) & EXTI18)
        return finish(b, MT_BOOT_USB_WAKE_PENDING);
    wr(s, IPR4, (rd(s, IPR4) & ~HP_PRIORITY) | (priority << 28));
    wr(s, IPR5, (rd(s, IPR5) & ~LP_PRIORITY) | (priority << 4));
    if (!priorities_match(s, priority)) return finish(b, MT_BOOT_USB_PRIORITY);
    wr(s, RCC_APB1ENR, gates | USB_BIT);
    if (!(rd(s, RCC_APB1ENR) & USB_BIT)) return finish(b, MT_BOOT_USB_GATE);
    wr(s, RCC_APB1RSTR, resets | USB_BIT);
    if (!(rd(s, RCC_APB1RSTR) & USB_BIT)) return finish(b, MT_BOOT_USB_RESET_ASSERT);
    wr(s, RCC_APB1RSTR, resets & ~USB_BIT);
    if ((rd(s, RCC_APB1RSTR) & USB_BIT) || !(rd(s, RCC_APB1ENR) & USB_BIT))
        return finish(b, MT_BOOT_USB_RESET_RELEASE);
    if (!same_clock(s) || !(rd(s, RCC_APB1ENR) & USB_BIT) ||
        (rd(s, RCC_APB1RSTR) & USB_BIT) || !quiescent(s) ||
        !priorities_match(s, priority) ||
        (rd(s, EXTI_PR) & EXTI18)) return finish(b, MT_BOOT_USB_FINAL_STATE);
    return finish(b, MT_BOOT_USB_OK);
}
