#include "mt_board_inputs.h"
#include "mt_boot_cpu.h"
#include "mt_boot_time.h"
#include "mt_boot_usb.h"
#include "mt_irq_guard.h"
#include "mt_runtime.h"
#include "mt_usb_time.h"
#include "tusb.h"
#include <stddef.h>

/* Persistent DMA-accessible storage survives every boot/runtime failure. */
static mt_clock_startup startup;
static mt_timebase timebase;
static mt_boot_time time_start;
static mt_boot_usb usb_start;
static mt_runtime runtime;
/* Debugger diagnostics, not a wire response or a physical acceptance receipt. */
volatile unsigned mt_main_boot_failure;
volatile unsigned mt_main_irq_admitted;
volatile unsigned mt_main_usb_init_result;
volatile mt_runtime_result mt_main_last_runtime;

enum { BOOT_CPU = 1, BOOT_INPUT, BOOT_DESC, BOOT_CLOCK, BOOT_TIME,
       BOOT_TIME_BIND, BOOT_RUNTIME, BOOT_USB_PREP, BOOT_USB_INIT,
       BOOT_FINAL, BOOT_ADMIT };
static _Noreturn void failed(unsigned reason) {
    mt_main_boot_failure = reason;
    mt_boot_cpu_halt();
}
static uint32_t rd(uint32_t address) {
    return mt_clock_mmio_read32(NULL, address);
}
static int final_usb_ok(void) {
    uint32_t priority = mt_board.usb_irq_priority;
    if (startup.attempted != 1u || startup.applied != 1u || startup.active ||
        startup.cause != MT_CLOCK_OK || startup.recovery != MT_CLOCK_RECOVERY_NONE ||
        (startup.last_flash & 0x3fu) != 0x32u ||
        time_start.attempted != 1u || time_start.active || time_start.ready != 1u ||
        time_start.cause != MT_BOOT_TIME_OK || usb_start.attempted != 1u ||
        usb_start.active || usb_start.ready != 1u || usb_start.cause != MT_BOOT_USB_OK)
        return 0;
    /* USB19/20 may legitimately be pending after host RESET. Never clear it.
     * CAN21/22 and wake42 must remain unused. No DADDR/EP0 pristine-state test. */
    return (rd(0x40021000u) & 0x030f00fbu) == (startup.last_cr & 0x030f00fbu) &&
        (rd(0x40021004u) & 0x077fffffu) == (startup.last_cfgr & 0x077fffffu) &&
        (rd(0x40022000u) & 0x3fu) == 0x32u &&
        (rd(0x4002101cu) & 0x02800000u) == 0x00800000u &&
        !(rd(0x40021010u) & 0x02800000u) &&
        rd(0xe000e100u) == 0x00180000u &&
        !(rd(0xe000e104u) & 0x7ffu) &&
        !(rd(0xe000e200u) & 0x00600000u) &&
        !(rd(0xe000e300u) & 0x00780000u) &&
        !((rd(0xe000e204u) | rd(0xe000e304u)) & 0x400u) &&
        (rd(0xe000e410u) & 0xf0000000u) == (priority << 28) &&
        (rd(0xe000e414u) & 0xf0u) == (priority << 4) &&
        !((rd(0x40010400u) | rd(0x40010404u) | rd(0x40010408u) |
            rd(0x4001040cu) | rd(0x40010410u) | rd(0x40010414u)) & 0x40000u) &&
        /* RM0008 permits aligned word reads of these 16-bit USB registers.
         * Mask reserved upper bits; this does not authorize arbitrary PMA I/O. */
        (rd(0x40005c40u) & 0xff1fu) == 0xdd00u &&
        (rd(0x40005c50u) & 0xfff8u) == 0u;
}
int main(void) {
    mt_clock_plan plan;
    const mt_clock_io clock_io = {mt_clock_mmio_read32, mt_clock_mmio_write32, NULL};
    const mt_runtime_mmio runtime_io = {mt_clock_mmio_read32, mt_clock_mmio_write32, NULL};
    const tusb_rhport_init_t rhport = { .role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_FULL };
    if (!mt_boot_cpu_masked()) failed(BOOT_CPU);
    if (mt_board.verified_fixed_pullup != 1u || mt_board.require_dtr > 1u ||
        mt_board.usb_irq_priority > 15u || !mt_board.clock_poll_budget ||
        mt_board.clock_poll_budget > MT_CLOCK_MAX_POLLS ||
        mt_clock_plan_72(mt_board.verified_hse_hz, mt_board.hse_mode, &plan) != MT_CLOCK_OK)
        failed(BOOT_INPUT);
    if (!mt_usb_descriptors_init(&mt_board.usb)) failed(BOOT_DESC);
    if (mt_clock_apply(&startup, &clock_io, mt_board.verified_hse_hz,
            mt_board.hse_mode, mt_board.clock_poll_budget) != MT_CLOCK_OK) failed(BOOT_CLOCK);
    if (mt_boot_time_start(&time_start, &startup, &timebase) != MT_BOOT_TIME_OK)
        failed(BOOT_TIME);
    if (!mt_usb_time_bind(&timebase)) failed(BOOT_TIME_BIND);
    if (mt_runtime_init(&runtime, &timebase, &runtime_io, mt_board.verified_hse_hz,
            mt_irq_guard_enter, mt_irq_guard_leave, mt_board.firmware_build,
            mt_board.require_dtr) != MT_RUNTIME_OK) failed(BOOT_RUNTIME);
    if (mt_boot_usb_prepare(&usb_start, &startup, mt_board.usb_irq_priority) != MT_BOOT_USB_OK)
        failed(BOOT_USB_PREP);
    mt_main_usb_init_result = tusb_init(0, &rhport) ? 1u : 0u;
    if (!mt_main_usb_init_result || !tud_inited()) failed(BOOT_USB_INIT);
    if (!final_usb_ok() || !mt_timebase_update(&timebase) || !mt_usb_time_valid())
        failed(BOOT_FINAL);
    if (!mt_boot_cpu_admit()) failed(BOOT_ADMIT);
    mt_main_irq_admitted = 1;
    for (;;) {
        /* TIME_FAULT/RECOVERY must keep stepping for actual Stop retries.
         * Never free/reset DMA storage, restart TIME or call separate tud_task. */
        mt_main_last_runtime = mt_runtime_step(&runtime);
    }
}
