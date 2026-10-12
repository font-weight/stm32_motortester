#include "mt_usb_irq.h"
#include "mt_irq_guard.h"
#include "tusb.h"

/* Guard the complete DCD call, including its nested bridge locks and NVIC
 * re-enables. Preserve an already masked caller rather than forcing IRQs on. */
static void usb_irq_dispatch(void)
{
    const uint32_t previous = mt_irq_guard_enter();
    tud_int_handler(0);
    mt_irq_guard_leave(previous);
}

void USB_HP_CAN1_TX_IRQHandler(void)
{
    usb_irq_dispatch();
}

void USB_LP_CAN1_RX0_IRQHandler(void)
{
    usb_irq_dispatch();
}
