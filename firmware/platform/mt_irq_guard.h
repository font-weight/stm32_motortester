#ifndef MT_IRQ_GUARD_H
#define MT_IRQ_GUARD_H
#include <stdint.h>
/* Privileged Cortex-M3 only. One matching leave per enter, in LIFO order.
 * Pass the returned key unchanged; nested/outer disabled masks stay disabled.
 * NMI/HardFault handlers must not access the guarded state. These callbacks
 * do not configure NVIC, priorities, vector handlers, or peripheral barriers. */
uint32_t mt_irq_guard_enter(void);
void mt_irq_guard_leave(uint32_t previous);
#endif
