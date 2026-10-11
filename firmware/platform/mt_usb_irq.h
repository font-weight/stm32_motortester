#ifndef MT_USB_IRQ_H
#define MT_USB_IRQ_H

/* USB-only shared USB/CAN1 vectors: CAN1 interrupts must remain disabled and
 * must not contribute a pending TX/FIFO0 source. Configure equal HP/LP
 * priorities before USB. No other ISR, NMI or HardFault may access USB state. */
void USB_HP_CAN1_TX_IRQHandler(void);
void USB_LP_CAN1_RX0_IRQHandler(void);

#endif
