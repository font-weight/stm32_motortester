/* TEST ONLY. Native model ABI is not the STM32 ABI. No real MMIO or USB bus. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef CFG_MT_USB_BRIDGE
#define CFG_MT_USB_BRIDGE 1
#endif
#define OPT_MCU_STM32F1 1
#ifndef CFG_TUSB_MCU
#define CFG_TUSB_MCU OPT_MCU_STM32F1
#endif
#ifndef MODEL_ENDPOINTS
#define MODEL_ENDPOINTS 3
#endif
#ifndef EXPECT_FIXED
#define EXPECT_FIXED 0
#endif
#define CFG_TUD_ENDPPOINT_MAX MODEL_ENDPOINTS
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUSB_FSDEV_PMA_SIZE 512
#define FSDEV_BTABLE_BASE 0U
#define FSDEV_USE_SBUF_ISO 0
#define TU_ATTR_ALWAYS_INLINE
#define TU_ATTR_ALIGNED(n) __attribute__((aligned(n)))
#define TUSB_DIR_IN_MASK 0x80u
#define tu_memclr(ptr,size) memset((ptr),0,(size))
#define tu_div_ceil(value,divisor) (((value)+(divisor)-1)/(divisor))
typedef enum {TUSB_DIR_OUT=0,TUSB_DIR_IN=1} tusb_dir_t;
enum {TUSB_XFER_CONTROL=0,TUSB_XFER_ISOCHRONOUS=1,TUSB_XFER_BULK=2,TUSB_XFER_INTERRUPT=3};
enum {XFER_RESULT_SUCCESS=0,TUSB_SPEED_FULL=0,DCD_EVENT_RESUME=1,DCD_EVENT_SUSPEND=2};
enum {BTABLE_BUF_TX=0,BTABLE_BUF_RX=1};
typedef uint16_t fsdev_bus_t;
typedef struct {uint8_t data[256]; size_t count;} tu_fifo_t;
/* Only fields consumed by extracted dcd_edpt_open, not an on-wire descriptor. */
typedef struct {uint8_t bEndpointAddress; struct {uint8_t xfer;} bmAttributes; uint16_t wMaxPacketSize;} tusb_desc_endpoint_t;
static uint8_t tu_edpt_number(uint8_t address) {return address & 15u;}
static tusb_dir_t tu_edpt_dir(uint8_t address) {return (address&0x80u)?TUSB_DIR_IN:TUSB_DIR_OUT;}
static uint16_t tu_edpt_packet_size(const tusb_desc_endpoint_t *desc) {return desc->wMaxPacketSize;}
static uint16_t tu_min16(uint16_t a,uint16_t b) {return a<b?a:b;}

static unsigned long checks;
static const char *test_context;
static _Noreturn void model_fatal(const char *condition,const char *file,int line) {
    fprintf(stderr,"FAIL [%s] %s:%d: %s\n",test_context?test_context:"startup",file,line,condition); exit(70);
}
#define CHECK(condition) do {checks++; if(!(condition)) model_fatal(#condition,__FILE__,__LINE__);} while(0)
#define MODEL_ASSERT1(condition) do {if(!(condition)) model_fatal(#condition,__FILE__,__LINE__);}while(0)
#define MODEL_ASSERT2(condition,retval) MODEL_ASSERT1(condition)
#define MODEL_ASSERT_SELECT(_1,_2,NAME,...) NAME
#define TU_ASSERT(...) MODEL_ASSERT_SELECT(__VA_ARGS__,MODEL_ASSERT2,MODEL_ASSERT1,unused)(__VA_ARGS__)
#define TU_BREAKPOINT() model_fatal("unexpected breakpoint",__FILE__,__LINE__)
#include "constants.inc"

/* Logical registers. Actual 16-bit APB spacing is outside this native model. */
typedef struct {struct {volatile uint16_t reg;} ep[FSDEV_EP_COUNT];
    volatile uint16_t CNTR,ISTR,FNR,DADDR,BTABLE;} model_regs_t;
static model_regs_t registers;
#define FSDEV_REG (&registers)
typedef struct {struct {uint16_t addr,count;} ep16[FSDEV_EP_COUNT][2];} model_btable_t;
static model_btable_t btable;
#define FSDEV_BTABLE (&btable)
typedef struct {uint16_t value;} fsdev_pma_buf_t;
static fsdev_pma_buf_t pma[256];
static fsdev_pma_buf_t *model_pma_at(unsigned address) {CHECK(address<512 && address%2==0);return &pma[address/2];}
#define PMA_BUF_AT(address) model_pma_at(address)
static uint32_t model_istr_read(void);
#include "state.inc"
static uint16_t ep_buf_ptr;
static unsigned reset_scope;
static unsigned write_seen[FSDEV_EP_COUNT];
static unsigned pre_open_observations;
static unsigned dirty_pre_open;
static unsigned enabled_write_seen;
static unsigned bus_reset_events,setup_events,signal_events,sof_events;
static uint8_t last_setup[8];
typedef struct {uint8_t ep; uint32_t length;} completion_t;
static completion_t events[64];
static unsigned event_count;
static unsigned fifo_reads,fifo_writes,pma_reads,pma_writes;
static uint16_t istr_flags;
static unsigned istr_reads;
static unsigned overrun_calls;
static uint16_t inject_istr_after_read;
static unsigned inject_ctr_slot;
static uint16_t inject_ctr_bits;
void mt_usb_bridge_pma_overrun(void) { overrun_calls++; }
static bool model_xfer_zero(const xfer_ctl_t *x) {
    return x->buffer==NULL && x->ff==NULL && x->total_len==0 && x->queued_len==0 &&
        x->max_packet_size==0 && x->ep_idx==0 && !x->iso_in_sending;
}

static uint32_t model_istr_read(void) {
    CHECK(++istr_reads<1000);
    uint32_t result=istr_flags;
    for(unsigned i=0;i<FSDEV_EP_COUNT;i++) {
        uint16_t ep=registers.ep[i].reg;
        if(ep&(U_EP_CTR_RX|U_EP_CTR_TX)) {
            result|=U_ISTR_CTR|i|((ep&U_EP_CTR_RX)?U_ISTR_DIR:0);
            break;
        }
    }
    /* Physical flag arrival just after the sampled MMIO read, before return. */
    istr_flags|=inject_istr_after_read;
    inject_istr_after_read=0;
    if(inject_ctr_bits) { registers.ep[inject_ctr_slot].reg|=inject_ctr_bits; inject_ctr_bits=0; }
    return result;
}
static void model_istr_write(uint16_t value) {
    const uint16_t clearable=U_ISTR_PMAOVR|U_ISTR_ERR|U_ISTR_WKUP|U_ISTR_SUSP|U_ISTR_RESET|U_ISTR_SOF|U_ISTR_ESOF;
    istr_flags &= (uint16_t)(value|~clearable);
}
/* Test-only replacement for ep_write; exact callers retain their C bodies. */
static void ep_write(uint32_t id,uint32_t value,bool need_exclusive) {
    (void)need_exclusive; /* no native IRQ/NVIC emulation */
    CHECK(id<FSDEV_EP_COUNT);
    if(reset_scope) {
        write_seen[id]++;
        if(registers.DADDR&U_DADDR_EF) enabled_write_seen++;
        if(EXPECT_FIXED) CHECK((registers.DADDR&U_DADDR_EF)==0);
    }
    const uint16_t old=registers.ep[id].reg;
    const uint16_t rw=U_EP_T_FIELD|U_EP_KIND|U_EPADDR_FIELD;
    const uint16_t toggles=U_EP_DTOG_RX|U_EPRX_STAT|U_EP_DTOG_TX|U_EPTX_STAT;
    const uint16_t ctr=U_EP_CTR_RX|U_EP_CTR_TX;
    registers.ep[id].reg=(uint16_t)((value&rw)|((old^value)&toggles)|(old&value&ctr)|(old&U_EP_SETUP));
}
static void dcd_event_xfer_complete(uint8_t port,uint8_t ep,uint32_t length,unsigned result,bool isr) {
    CHECK(port==0 && result==XFER_RESULT_SUCCESS && isr && event_count<64);
    events[event_count++]=(completion_t){ep,length};
}
static void dcd_event_bus_reset(uint8_t port,unsigned speed,bool isr) {CHECK(port==0&&speed==TUSB_SPEED_FULL&&isr);bus_reset_events++;}
static void dcd_event_setup_received(uint8_t port,const uint8_t *bytes,bool isr) {CHECK(port==0&&isr);memcpy(last_setup,bytes,8);setup_events++;}
static void dcd_event_sof(uint8_t port,uint16_t frame,bool isr) {(void)frame;CHECK(port==0&&isr);sof_events++;}
static void dcd_event_bus_signal(uint8_t port,unsigned signal,bool isr) {(void)signal;CHECK(port==0&&isr);signal_events++;}
static void tu_hwfifo_read(fsdev_pma_buf_t *src,void *dest,uint16_t count,void *unused) {
    (void)unused; CHECK(src>=pma&&src<=pma+256); CHECK(count<=512-(size_t)(src-pma)*2);
    if(count) {CHECK(dest!=NULL);memcpy(dest,src,count);} pma_reads++;
}
static void tu_hwfifo_write(fsdev_pma_buf_t *dest,const void *src,uint16_t count,void *unused) {
    (void)unused; CHECK(dest>=pma&&dest<=pma+256); CHECK(count<=512-(size_t)(dest-pma)*2);
    if(count) {CHECK(src!=NULL);memcpy(dest,src,count);} pma_writes++;
}
static void tu_hwfifo_read_to_fifo(fsdev_pma_buf_t *src,tu_fifo_t *ff,uint16_t count,void *unused) {
    CHECK(ff&&ff->count+count<=sizeof(ff->data));tu_hwfifo_read(src,ff->data+ff->count,count,unused);ff->count+=count;fifo_reads++;
}
static void tu_hwfifo_write_from_fifo(fsdev_pma_buf_t *dest,tu_fifo_t *ff,uint16_t count,void *unused) {
    CHECK(ff&&count<=ff->count);tu_hwfifo_write(dest,ff->data,count,unused);ff->count-=count;memmove(ff->data,ff->data+count,ff->count);fifo_writes++;
}
static void handle_bus_reset(uint8_t rhport);
static void edpt0_open(uint8_t rhport);
static void dcd_transmit_packet(xfer_ctl_t *xfer,uint16_t ep_ix);
static void btable_set_rx_bufsize(uint32_t ep_id,uint8_t buf_id,uint16_t count);
