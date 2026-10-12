#ifndef MT_BOOT_CPU_H
#define MT_BOOT_CPU_H
/* BOOT001 path only: privileged Thread/MSP, PRIMASK1, BASEPRI/FAULTMASK0.
 * No NMI/HardFault modification of CPU masks or application state. */
int mt_boot_cpu_masked(void);
/* Caller has completed real initialization and final peripheral/time checks.
 * Rechecks CPU state, then DSB/ISB and explicit CPSIE i. Not guard leave(0).
 * Returns 0 without changing masks on a wrong entry state. */
int mt_boot_cpu_admit(void);
/* Early-boot failure only, before any acquisition was started. Preserves all
 * object storage; does not claim peripheral cleanup or successful Stop. */
_Noreturn void mt_boot_cpu_halt(void);
#endif
