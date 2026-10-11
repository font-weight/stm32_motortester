#include "mt_irq_guard.h"
#if !defined(__GNUC__) || !defined(__ARM_ARCH_7M__) || !defined(__thumb__)
#error "IRQ guard requires a GCC-compatible privileged Cortex-M3 Thumb target"
#else
uint32_t mt_irq_guard_enter(void) {
    uint32_t previous;
    /* An IRQ between MRS and CPSID completes before guarded work begins.
     * The compiler barrier prevents moving that work ahead of masking. */
    __asm__ volatile ("mrs %0, primask\n\tcpsid i"
                      : "=r" (previous) : : "memory");
    return previous;
}
void mt_irq_guard_leave(uint32_t previous) {
    /* Restore the captured mask, not unconditional CPSIE. A pending IRQ may
     * run immediately after this instruction. No peripheral fence is implied. */
    __asm__ volatile ("msr primask, %0" : : "r" (previous) : "memory");
}
#endif
