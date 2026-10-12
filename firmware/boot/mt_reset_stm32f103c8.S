/* Flash-only STM32F103C8 reset. Requires an actual system reset. */
.syntax unified
.cpu cortex-m3
.thumb

.section .isr_vector,"a",%progbits
.balign 512
.global mt_boot_vectors
.type mt_boot_vectors, %object
mt_boot_vectors:
    .word __StackTop
    .word Reset_Handler
    .word NMI_Handler
    .word HardFault_Handler
    .word MemManage_Handler
    .word BusFault_Handler
    .word UsageFault_Handler
    .word 0
    .word 0
    .word 0
    .word 0
    .word SVC_Handler
    .word DebugMon_Handler
    .word 0
    .word PendSV_Handler
    .word SysTick_Handler
    .word WWDG_IRQHandler
    .word PVD_IRQHandler
    .word TAMPER_IRQHandler
    .word RTC_IRQHandler
    .word FLASH_IRQHandler
    .word RCC_IRQHandler
    .word EXTI0_IRQHandler
    .word EXTI1_IRQHandler
    .word EXTI2_IRQHandler
    .word EXTI3_IRQHandler
    .word EXTI4_IRQHandler
    .word DMA1_Channel1_IRQHandler
    .word DMA1_Channel2_IRQHandler
    .word DMA1_Channel3_IRQHandler
    .word DMA1_Channel4_IRQHandler
    .word DMA1_Channel5_IRQHandler
    .word DMA1_Channel6_IRQHandler
    .word DMA1_Channel7_IRQHandler
    .word ADC1_2_IRQHandler
    .word USB_HP_CAN1_TX_IRQHandler
    .word USB_LP_CAN1_RX0_IRQHandler
    .word CAN1_RX1_IRQHandler
    .word CAN1_SCE_IRQHandler
    .word EXTI9_5_IRQHandler
    .word TIM1_BRK_IRQHandler
    .word TIM1_UP_IRQHandler
    .word TIM1_TRG_COM_IRQHandler
    .word TIM1_CC_IRQHandler
    .word TIM2_IRQHandler
    .word TIM3_IRQHandler
    .word TIM4_IRQHandler
    .word I2C1_EV_IRQHandler
    .word I2C1_ER_IRQHandler
    .word I2C2_EV_IRQHandler
    .word I2C2_ER_IRQHandler
    .word SPI1_IRQHandler
    .word SPI2_IRQHandler
    .word USART1_IRQHandler
    .word USART2_IRQHandler
    .word USART3_IRQHandler
    .word EXTI15_10_IRQHandler
    .word RTC_Alarm_IRQHandler
    .word USBWakeUp_IRQHandler
.size mt_boot_vectors, . - mt_boot_vectors

.section .text.Reset_Handler,"ax",%progbits
.global Reset_Handler
.type Reset_Handler, %function
.thumb_func
Reset_Handler:
    cpsid i
    /* Hardware loaded MSP. Keep IRQs masked until board integration is ready. */
    ldr r0, =0xe000ed08
    ldr r1, =mt_boot_vectors
    str r1, [r0]
    ldr r0, =0xe000ed14
    ldr r1, [r0]
    orr r1, r1, #512
    str r1, [r0]
    dsb sy
    isb sy

    ldr r0, =__data_load
    ldr r1, =__data_start
    ldr r2, =__data_end
.Lcopy:
    cmp r1, r2
    bhs .Lzero_begin
    ldr r3, [r0], #4
    str r3, [r1], #4
    b .Lcopy
.Lzero_begin:
    ldr r1, =__bss_start
    ldr r2, =__bss_end
    movs r3, #0
.Lzero:
    cmp r1, r2
    bhs .Lenter
    str r3, [r1], #4
    b .Lzero
.Lenter:
    dsb sy
    bl main
    /* Returning from main is an integration fault, not a safe device shutdown. */
    cpsid i
.Lreturned:
    b .Lreturned
.size Reset_Handler, . - Reset_Handler
.ltorg

.section .text.Default_Handler,"ax",%progbits
.global Default_Handler
.type Default_Handler, %function
.thumb_func
Default_Handler:
    cpsid i
.Ldefault:
    b .Ldefault
.size Default_Handler, . - Default_Handler

.macro DEFAULT_IRQ name
    .weak \name
    .thumb_set \name, Default_Handler
.endm
DEFAULT_IRQ NMI_Handler
DEFAULT_IRQ HardFault_Handler
DEFAULT_IRQ MemManage_Handler
DEFAULT_IRQ BusFault_Handler
DEFAULT_IRQ UsageFault_Handler
DEFAULT_IRQ SVC_Handler
DEFAULT_IRQ DebugMon_Handler
DEFAULT_IRQ PendSV_Handler
DEFAULT_IRQ SysTick_Handler
DEFAULT_IRQ WWDG_IRQHandler
DEFAULT_IRQ PVD_IRQHandler
DEFAULT_IRQ TAMPER_IRQHandler
DEFAULT_IRQ RTC_IRQHandler
DEFAULT_IRQ FLASH_IRQHandler
DEFAULT_IRQ RCC_IRQHandler
DEFAULT_IRQ EXTI0_IRQHandler
DEFAULT_IRQ EXTI1_IRQHandler
DEFAULT_IRQ EXTI2_IRQHandler
DEFAULT_IRQ EXTI3_IRQHandler
DEFAULT_IRQ EXTI4_IRQHandler
DEFAULT_IRQ DMA1_Channel1_IRQHandler
DEFAULT_IRQ DMA1_Channel2_IRQHandler
DEFAULT_IRQ DMA1_Channel3_IRQHandler
DEFAULT_IRQ DMA1_Channel4_IRQHandler
DEFAULT_IRQ DMA1_Channel5_IRQHandler
DEFAULT_IRQ DMA1_Channel6_IRQHandler
DEFAULT_IRQ DMA1_Channel7_IRQHandler
DEFAULT_IRQ ADC1_2_IRQHandler
DEFAULT_IRQ USB_HP_CAN1_TX_IRQHandler
DEFAULT_IRQ USB_LP_CAN1_RX0_IRQHandler
DEFAULT_IRQ CAN1_RX1_IRQHandler
DEFAULT_IRQ CAN1_SCE_IRQHandler
DEFAULT_IRQ EXTI9_5_IRQHandler
DEFAULT_IRQ TIM1_BRK_IRQHandler
DEFAULT_IRQ TIM1_UP_IRQHandler
DEFAULT_IRQ TIM1_TRG_COM_IRQHandler
DEFAULT_IRQ TIM1_CC_IRQHandler
DEFAULT_IRQ TIM2_IRQHandler
DEFAULT_IRQ TIM3_IRQHandler
DEFAULT_IRQ TIM4_IRQHandler
DEFAULT_IRQ I2C1_EV_IRQHandler
DEFAULT_IRQ I2C1_ER_IRQHandler
DEFAULT_IRQ I2C2_EV_IRQHandler
DEFAULT_IRQ I2C2_ER_IRQHandler
DEFAULT_IRQ SPI1_IRQHandler
DEFAULT_IRQ SPI2_IRQHandler
DEFAULT_IRQ USART1_IRQHandler
DEFAULT_IRQ USART2_IRQHandler
DEFAULT_IRQ USART3_IRQHandler
DEFAULT_IRQ EXTI15_10_IRQHandler
DEFAULT_IRQ RTC_Alarm_IRQHandler
DEFAULT_IRQ USBWakeUp_IRQHandler

.section .note.GNU-stack,"",%progbits
