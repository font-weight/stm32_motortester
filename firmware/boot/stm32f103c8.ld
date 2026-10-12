/* Flash-only image; require --no-gc-sections --orphan-handling=error.
 * Pass --defsym=__boot_stack_bytes=N explicitly. */
OUTPUT_FORMAT("elf32-littlearm")
OUTPUT_ARCH(arm)
ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
    RAM (rw)   : ORIGIN = 0x20000000, LENGTH = 20K
}

__StackTop = ORIGIN(RAM) + LENGTH(RAM);
__StackLimit = __StackTop - __boot_stack_bytes;

SECTIONS
{
    .isr_vector ORIGIN(FLASH) :
    {
        KEEP(*(.isr_vector .isr_vector.*))
    } > FLASH

    .text : ALIGN(4)
    {
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH

    .data : ALIGN(4)
    {
        __data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT> FLASH
    __data_load = LOADADDR(.data);

    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start = .;
        *(.bss .bss.*)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM
    __static_end = .;

    /* KEEP makes unsupported runtime features fail even with section GC. */
    .unsupported :
    {
        KEEP(*(.preinit_array* .init_array* .fini_array* .ctors* .dtors*))
        KEEP(*(.init .fini .tdata* .tbss* .noinit*))
        KEEP(*(COMMON))
    } > RAM
    .ARM.extab : { KEEP(*(.ARM.extab* .gnu.linkonce.armextab.*)) } > FLASH
    .ARM.exidx : { KEEP(*(.ARM.exidx* .gnu.linkonce.armexidx.*)) } > FLASH
    .unsupported_stubs :
    {
        KEEP(*(.glue_7 .glue_7t .vfp11_veneer .v4_bx .iplt))
    } > FLASH
    .rel.iplt : { KEEP(*(.rel.iplt)) } > FLASH
    .igot.plt : { KEEP(*(.igot.plt)) } > RAM

    /* Non-allocating metadata only; unknown sections require a link error. */
    .ARM.attributes 0 : { *(.ARM.attributes) }
    /DISCARD/ : { *(.comment .note.GNU-stack) }
}

ASSERT(DEFINED(Reset_Handler), "missing Reset_Handler")
ASSERT(DEFINED(main), "missing main")
ASSERT(ADDR(.isr_vector) == ORIGIN(FLASH), "vector base must be Flash origin")
ASSERT((ADDR(.isr_vector) & 511) == 0, "vector base must align to 512 bytes")
ASSERT(SIZEOF(.isr_vector) == 236, "expected exactly 59 vector words")
ASSERT(DEFINED(__boot_stack_bytes), "explicit __boot_stack_bytes required")
ASSERT(__boot_stack_bytes > 0 && __boot_stack_bytes <= LENGTH(RAM), "invalid stack reservation")
ASSERT((__boot_stack_bytes & 7) == 0, "stack reservation must align to 8 bytes")
ASSERT((__StackTop & 7) == 0, "initial MSP must align to 8 bytes")
ASSERT(__static_end <= __StackLimit, "static storage overlaps reserved stack")
ASSERT(((__data_start | __data_end | __data_load | __bss_start | __bss_end) & 3) == 0, "reset ranges must align to words")
ASSERT(__data_start <= __data_end && __bss_start <= __bss_end, "reversed reset range")
ASSERT(__data_start >= ORIGIN(RAM) && __bss_end <= __StackTop, "reset range outside SRAM")
ASSERT(__data_load >= ORIGIN(FLASH) && __data_load + SIZEOF(.data) <= ORIGIN(FLASH) + LENGTH(FLASH), "data load image outside Flash")
ASSERT(SIZEOF(.unsupported) == 0, "constructors, TLS, noinit and COMMON unsupported")
ASSERT(SIZEOF(.ARM.extab) == 0 && SIZEOF(.ARM.exidx) == 0, "unwind tables unsupported")
ASSERT(SIZEOF(.unsupported_stubs) == 0 && SIZEOF(.rel.iplt) == 0 && SIZEOF(.igot.plt) == 0, "interworking and dynamic stubs unsupported")
