#include "mt_live_clock.h"
#include <stddef.h>

int mt_live_clock_init(mt_live_clock *clock, mt_timebase *source)
{
    uint32_t us, ms;
    if (clock == NULL) return 0;
    *clock = (mt_live_clock){.source=source, .failed=1u};
    if (source == NULL || !mt_timebase_snapshot(source, &us, &ms)) return 0;
    clock->last_ms = ms;
    clock->last_us = us;
    clock->milliseconds = ms;
    clock->failed = 0u;
    return 1;
}

uint64_t mt_live_clock_now(void *context)
{
    mt_live_clock *clock = context;
    uint32_t us, ms, delta;
    if (clock == NULL || clock->failed || clock->source == NULL) return UINT64_MAX;
    if (!mt_timebase_snapshot(clock->source, &us, &ms)) {
        clock->failed = 1u;
        return UINT64_MAX;
    }
    delta = ms - clock->last_ms;
    /* At most32 whole milliseconds can pass under the wrapper's own32ms
     * service obligation. This also rejects ordinary source resets/backsteps. */
    if ((uint32_t)(us - clock->last_us) > MT_TIMEBASE_MAX_DELTA ||
        delta > MT_TIMEBASE_MAX_DELTA / 1000u ||
        clock->milliseconds > (uint64_t)INT64_MAX - delta) {
        clock->failed = 1u;
        return UINT64_MAX;
    }
    clock->milliseconds += delta;
    clock->last_ms = ms;
    clock->last_us = us;
    return clock->milliseconds;
}
