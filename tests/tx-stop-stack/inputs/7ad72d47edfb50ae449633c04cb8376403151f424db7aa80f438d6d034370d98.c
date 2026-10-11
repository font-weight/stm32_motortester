#include <stdint.h>
#include <string.h>

/* Compile this translation unit freestanding, with builtin and loop-pattern
 * substitution disabled. These are ordinary memory operations, not MMIO,
 * atomic access, a secure erase, or synchronization with DMA/interrupts. */
void *memcpy(void *restrict destination, const void *restrict source, size_t count) {
    /* GCC freestanding also requires exact self-copy; avoid restricted access. */
    if (destination == source) return destination;
    unsigned char *out = destination;
    const unsigned char *in = source;
    for (size_t i = 0; i < count; ++i) out[i] = in[i];
    return destination;
}
void *memmove(void *destination, const void *source, size_t count) {
    unsigned char *out = destination;
    const unsigned char *in = source;
    /* The supported flat-address ARM ABI preserves address ordering in
     * uintptr_t. Avoid relational comparisons of unrelated C pointers. */
    if ((uintptr_t)out > (uintptr_t)in) {
        while (count) { --count; out[count] = in[count]; }
    } else {
        for (size_t i = 0; i < count; ++i) out[i] = in[i];
    }
    return destination;
}
void *memset(void *destination, int value, size_t count) {
    unsigned char *out = destination;
    for (size_t i = 0; i < count; ++i) out[i] = (unsigned char)value;
    return destination;
}
int memcmp(const void *left, const void *right, size_t count) {
    const unsigned char *a = left;
    const unsigned char *b = right;
    for (size_t i = 0; i < count; ++i)
        if (a[i] != b[i]) return (int)a[i] - (int)b[i];
    return 0;
}
