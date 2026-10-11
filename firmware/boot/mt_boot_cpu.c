#include "mt_boot_cpu.h"
#include <stdint.h>
int mt_boot_cpu_masked(void) {
    uint32_t primask, basepri, faultmask, ipsr, control;
    __asm__ volatile("mrs %0, primask\n\tmrs %1, basepri\n\tmrs %2, faultmask\n\t"
                     "mrs %3, ipsr\n\tmrs %4, control"
        : "=r"(primask), "=r"(basepri), "=r"(faultmask), "=r"(ipsr), "=r"(control)
        : : "memory");
    return primask == 1u && basepri == 0u && faultmask == 0u && ipsr == 0u &&
        (control & 3u) == 0u;
}
int mt_boot_cpu_admit(void) {
    if (!mt_boot_cpu_masked()) return 0;
    __asm__ volatile("dsb\n\tisb\n\tcpsie i\n\tisb" : : : "memory");
    return 1;
}
_Noreturn void mt_boot_cpu_halt(void) {
    __asm__ volatile("cpsid i" : : : "memory");
    for (;;) __asm__ volatile("nop" : : : "memory");
}
