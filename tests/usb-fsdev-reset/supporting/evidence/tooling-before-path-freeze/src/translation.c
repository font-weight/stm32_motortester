#include "preamble.h"
#include "common.inc"
#include "driver.inc"
/* Call seam only: preserve extracted reset bytes and wrap its EP0-open dependency.
 * The wrapper observes preconditions and forwards once to the exact edpt0_open. */
#define edpt0_open model_open_checked
#include "reset.inc"
#undef edpt0_open
#include "tests.c"
