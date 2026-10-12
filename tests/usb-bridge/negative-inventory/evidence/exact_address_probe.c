#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#define TUSB_DIR_IN_MASK 0x80
static unsigned actual, wrapped;
bool dcd_edpt_xfer(uint8_t,uint8_t,uint8_t*,uint16_t,bool);
void dcd_set_address(uint8_t rhport, uint8_t dev_addr) {
  (void)dev_addr;

  // Respond with status
  dcd_edpt_xfer(rhport, TUSB_DIR_IN_MASK | 0x00, NULL, 0, false);

  // DCD can only set address after status for this request is complete.
  // do it at dcd_edpt0_status_complete()
}
bool dcd_edpt_xfer(uint8_t p,uint8_t e,uint8_t*b,uint16_t n,bool i) { (void)p;(void)e;(void)b;(void)n;(void)i; ++actual; return false; }
bool __wrap_dcd_edpt_xfer(uint8_t p,uint8_t e,uint8_t*b,uint16_t n,bool i) { (void)p;(void)e;(void)b;(void)n;(void)i; ++wrapped; return false; }
int main(void) { dcd_set_address(0,7); assert(actual==1 && wrapped==0); printf("exact FSDEV SET_ADDRESS: internal xfer=false, void returned, actual=%u, wrapped=%u\n",actual,wrapped); }
