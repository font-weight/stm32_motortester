/* Target ABI probe; do not link this array into firmware. */
#include "../../firmware/platform/mt_timebase.h"
unsigned char sizeof_timebase[sizeof(mt_timebase)];
