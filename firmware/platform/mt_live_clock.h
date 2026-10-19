#ifndef MT_LIVE_CLOCK_H
#define MT_LIVE_CLOCK_H
#include "mt_timebase.h"
#include <stdint.h>

/* Single foreground owner; the source remains alive and cannot be reset while
 * this wrapper or its consumers are live. Service this wrapper at least every
 * 32000 us, even when other clients update TIME. Finite source counters cannot
 * distinguish a whole missed wrap; this is an explicit scheduling obligation.
 */
typedef struct {
    mt_timebase *source;
    uint64_t milliseconds;
    uint32_t last_ms;
    uint32_t last_us;
    unsigned failed;
} mt_live_clock;
/* Fresh storage only, or after all old timestamp consumers have been stopped
 * and invalidated. No source reinit/ownership change. One validated observation.
 * Failed init leaves the wrapper failed; never retry by silently resetting it
 * while an endpoint still has deadlines from the previous clock epoch. */
int mt_live_clock_init(mt_live_clock *clock, mt_timebase *source);
/* mt_live_io clock callback. Always performs a fresh validated TIME snapshot.
 * Returns a monotonic uint64 <=INT64_MAX, or UINT64_MAX as an explicit invalid
 * sentinel rejected by mt_live_device. Fault is sticky; never return stale time.
 * No endpoint/driver calls or reentrancy from this callback. */
uint64_t mt_live_clock_now(void *clock);
#endif
