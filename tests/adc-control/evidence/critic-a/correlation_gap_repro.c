#define main adapter_model_main
#include "independent_adapter.c"
#undef main
/* The millisecond sample is read, then a bounded 5ms preemption occurs before
 * this callback returns. Both callback values still derive from one clock. */
static uint32_t delayed_ms(void *context) {
 (void)context; uint32_t captured=millis_value;
 micros_value+=5000u;millis_value+=5u;return captured;
}
int main(void) {
 prepare(0,20,0);micros_value=1000;millis_value=1;start_delay=100;
 b.now_ms=delayed_ms;unsigned before=starts;
 int accepted=c.io.start(c.io.context,1);
 uint32_t reported=b.epoch_ms+b.first_sample_delay_us/1000u;
 uint32_t actual=(d.start_before_us+20u)/1000u;
 printf("accepted=%d, driver starts=%u, first nominal ms=%u, actual model first ms=%u\n",accepted,starts-before,reported,actual);
 /* Bounded correlation contract must reject before peripheral start. */
 return accepted || starts!=before;
}
