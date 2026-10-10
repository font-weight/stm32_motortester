#include "adc_timer_dma.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

/* Independent, minimal register-bus model, not a hardware/ADC simulator. */
enum {
    R_CR=0x40021000, R_CFGR=0x40021004, R_AHB=0x40021014,
    R_APB2=0x40021018, R_APB1=0x4002101c, GPIO=0x40010800,
    A_SR=0x40012400, A_CR1=0x40012404, A_CR2=0x40012408,
    A_SMPR1=0x4001240c, A_SMPR2=0x40012410,
    A_SQR1=0x4001242c, A_SQR2=0x40012430, A_SQR3=0x40012434,
    A_DR=0x4001244c,
    T_CR1=0x40000400, T_CR2=0x40000404, T_SMCR=0x40000408,
    T_DIER=0x4000040c, T_SR=0x40000410, T_EGR=0x40000414,
    T_CCER=0x40000420, T_CNT=0x40000424, T_PSC=0x40000428,
    T_ARR=0x4000042c,
    D_ISR=0x40020000, D_IFCR=0x40020004, D_CCR=0x40020008,
    D_N=0x4002000c, D_PAR=0x40020010, D_MAR=0x40020014
};

typedef struct { uint32_t address, value; } reg_t;
typedef struct {
    reg_t regs[40];
    unsigned count;
    reg_t writes[256];
    unsigned write_count;
    uint32_t tick, tick_step;
    bool reset_stuck, cal_stuck, stop_stuck, freeze_on_reset;
    unsigned reads, time_reads, n_reads, inject_n_read;
    unsigned injection;
    bool ignore_clock_gate;
} fake_t;

static fake_t hw;
static mt_adc_dma_t driver;

static uint32_t *slot(fake_t *f, uint32_t address)
{
    unsigned i;
    assert(address % 4u == 0u);
    for (i=0; i<f->count; ++i) {
        if (f->regs[i].address == address) { return &f->regs[i].value; }
    }
    assert(f->count < 40u);
    f->regs[f->count] = (reg_t){address, 0u};
    return &f->regs[f->count++].value;
}

static uint32_t read32(void *user, uint32_t address)
{
    fake_t *f=user;
    uint32_t *v=slot(f,address);
    ++f->reads;
    if (address == A_CR2) {
        if (!f->reset_stuck) { *v &= ~8u; }
        if (!f->cal_stuck) { *v &= ~4u; }
    }
    if (address == D_N && ++f->n_reads == f->inject_n_read) {
        if (f->injection == 1u) { *v=256u; *slot(f,D_ISR)|=2u; }
        if (f->injection == 2u) { *slot(f,D_ISR)|=8u; }
        if (f->injection == 3u) { f->tick+=MT_ADC_DMA_BLOCK_US; }
        if (f->injection == 4u) { *v=0u; }
    }
    return *v;
}

static void write32(void *user, uint32_t address, uint32_t value)
{
    fake_t *f=user;
    uint32_t *v=slot(f,address);
    assert(f->write_count < 256u);
    f->writes[f->write_count++]=(reg_t){address,value};
    if (address == D_IFCR) {
        assert((value & 1u) == 0u); /* ES096: no channel-global flag clear. */
        *slot(f,D_ISR) &= ~value;
    }
    if (address == D_CCR && f->stop_stuck && (*v & 1u) != 0u) { value|=1u; }
    if (address == A_CR2 && (value & 8u) != 0u && f->freeze_on_reset) { f->tick_step=0u; }
    if (address == R_APB2 && f->ignore_clock_gate) { return; }
    *v=value;
}

static uint32_t now_us(void *user)
{
    fake_t *f=user;
    uint32_t t=f->tick;
    ++f->time_reads;
    f->tick+=f->tick_step;
    return t;
}

static void setup(void)
{
    mt_adc_dma_io_t io={read32,write32,now_us,&hw};
    memset(&hw,0,sizeof(hw));
    hw.tick=1000u;
    hw.tick_step=1u;
    /* Nominal HSE 8 MHz -> PLL x9 -> 72 MHz, APB1 /2, ADC /6. */
    *slot(&hw,R_CR)=(3u<<16)|(3u<<24);
    *slot(&hw,R_CFGR)=2u|(2u<<2)|(4u<<8)|(2u<<14)|(1u<<16)|(7u<<18);
    *slot(&hw,GPIO)=0x44444444u;
    *slot(&hw,R_AHB)=0x14u; /* Preserve other peripheral and FLITF clocks. */
    *slot(&hw,R_APB1)=0x00800000u;
    *slot(&hw,R_APB2)=0x00004000u;
    assert(mt_adc_dma_init(&driver,&io,8000000u)==MT_ADC_DMA_OK);
    assert(hw.write_count==0u);
}

static void started(void)
{
    setup();
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    assert(driver.state==MT_ADC_DMA_RUNNING);
}

static unsigned find_write(uint32_t address,uint32_t mask,uint32_t value)
{
    unsigned i;
    for (i=0;i<hw.write_count;++i) {
        if (hw.writes[i].address==address && (hw.writes[i].value&mask)==value) { return i; }
    }
    assert(!"write missing");
    return 0;
}

static void empty_at(uint32_t elapsed,uint32_t remaining)
{
    mt_adc_dma_block_t block={0};
    hw.tick=driver.start_before_us+elapsed;
    *slot(&hw,D_N)=remaining;
    assert(mt_adc_dma_read_block(&driver,&block)==MT_ADC_DMA_EMPTY);
}

static void first_half_pending(void)
{
    unsigned i;
    empty_at(1000u,210u);
    empty_at(2000u,160u);
    hw.tick=driver.start_before_us+2570u;
    *slot(&hw,D_N)=128u;
    *slot(&hw,D_ISR)=4u;
    for (i=0;i<MT_ADC_DMA_SAMPLES;++i) { driver.dma_samples[i]=(uint16_t)(i+100u); }
}

static void stopped_fault(mt_adc_dma_fault_t reason)
{
    assert(driver.state==MT_ADC_DMA_FAULTED);
    assert(driver.fault==reason);
    assert(!driver.owns_peripherals);
    assert((*slot(&hw,T_CR1)&1u)==0u);
    assert(*slot(&hw,A_CR2)==0u);
    assert((*slot(&hw,D_CCR)&1u)==0u);
}

static void test_configuration_and_sequence(void)
{
    unsigned gates,ug,cal,dma,timer;
    started();
    assert(driver.clock.sysclk_hz==72000000u);
    assert(driver.clock.hclk_hz==72000000u);
    assert(driver.clock.pclk1_hz==36000000u);
    assert(driver.clock.pclk2_hz==72000000u);
    assert(driver.clock.timer_hz==72000000u);
    assert(driver.clock.adc_hz==12000000u);
    assert(driver.clock.timer_arr==19u);
    assert(driver.clock.timer_psc==71u);
    assert(*slot(&hw,T_PSC)==driver.clock.timer_psc && *slot(&hw,T_ARR)==19u);
    assert(*slot(&hw,T_CR2)==32u && *slot(&hw,T_SMCR)==0u);
    assert(*slot(&hw,T_DIER)==0u && *slot(&hw,T_CCER)==0u);
    assert(*slot(&hw,A_CR1)==0u && *slot(&hw,A_SMPR2)==6u);
    assert(*slot(&hw,A_SQR1)==0u && *slot(&hw,A_SQR3)==0u);
    assert(*slot(&hw,A_CR2)==((4u<<17)|(1u<<20)|(1u<<8)|1u));
    assert(*slot(&hw,D_CCR)==0x25a1u);
    assert(*slot(&hw,D_N)==256u && *slot(&hw,D_PAR)==A_DR);
    assert(*slot(&hw,D_MAR)==(uint32_t)(uintptr_t)driver.dma_samples);
    assert(*slot(&hw,GPIO)==0x44444440u);
    assert(*slot(&hw,R_AHB)==0x15u);
    assert(*slot(&hw,R_APB1)==0x00800002u);
    assert(*slot(&hw,R_APB2)==0x00004204u);
    gates=find_write(R_APB2,1u<<9,1u<<9);
    ug=find_write(T_EGR,~0u,1u);
    cal=find_write(A_CR2,4u,4u);
    dma=find_write(D_CCR,1u,1u);
    timer=find_write(T_CR1,1u,1u);
    assert(gates<ug && ug<cal && cal<dma && dma<timer);
    assert(find_write(A_CR2,1u<<20,1u<<20)>ug);
    assert(driver.start_after_us-driver.start_before_us==1u);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_BUSY);
}

static void test_clock_validation(void)
{
    setup(); driver.hse_hz=0u;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    assert(driver.fault==MT_ADC_DMA_FAULT_CLOCK && hw.write_count==0u);
    setup(); *slot(&hw,R_CR)&=~(1u<<17);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    setup(); *slot(&hw,R_CFGR)&=~(3u<<14); /* ADC=36 MHz. */
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    setup(); *slot(&hw,R_CFGR)&=~(7u<<8); /* APB1=72 MHz illegal. */
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    setup(); *slot(&hw,R_CFGR)^=1u; /* Pending clock-source switch. */
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    setup(); *slot(&hw,R_CR)&=~(1u<<25);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    setup(); hw.ignore_clock_gate=true;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    assert(driver.fault==MT_ADC_DMA_FAULT_CLOCK);
    /* HSI /2 *12=48 MHz, APB1 /2, ADC /4; no HSE assumption. */
    setup(); driver.hse_hz=0;
    *slot(&hw,R_CR)=3u|(3u<<24);
    *slot(&hw,R_CFGR)=2u|8u|(4u<<8)|(1u<<14)|(10u<<18);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    assert(driver.clock.timer_hz==48000000u && driver.clock.timer_arr==19u);
    assert(driver.clock.timer_psc==47u && *slot(&hw,T_PSC)==47u);
    /* AHB /2: SYSCLK 48, HCLK24, APB1=24 (no doubling), ADC /2=12. */
    setup(); driver.hse_hz=0;
    *slot(&hw,R_CR)=3u|(3u<<24);
    *slot(&hw,R_CFGR)=2u|8u|(8u<<4)|(10u<<18);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    assert(driver.clock.hclk_hz==24000000u && driver.clock.timer_arr==19u);
    assert(driver.clock.timer_psc==23u && *slot(&hw,T_PSC)==23u);
}

static void test_busy_owner(void)
{
    const uint32_t regs[]={D_CCR,T_CR1,A_CR2};
    unsigned i;
    for (i=0;i<3u;++i) {
        setup(); *slot(&hw,regs[i])=1u;
        assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_BUSY);
        assert(hw.write_count==0u && !driver.owns_peripherals);
    }
}

static void test_calibration_and_timebase_timeouts(void)
{
    setup(); hw.tick_step=0u;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_TIMEBASE);
    assert(hw.time_reads<MT_ADC_DMA_WAIT_POLLS+20u);
    setup(); hw.reset_stuck=true;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_CALIBRATION);
    setup(); hw.cal_stuck=true;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_CALIBRATION);
    setup(); hw.reset_stuck=true; hw.freeze_on_reset=true;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_CALIBRATION);
    assert(hw.reads<MT_ADC_DMA_WAIT_POLLS+100u);
}

static void test_owned_blocks(void)
{
    mt_adc_dma_block_t b={0};
    unsigned i;
    started(); first_half_pending();
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
    assert(b.samples==driver.snapshot && b.count==128u);
    assert(b.first_sample_index==0u && b.nominal_rate_hz==50000u);
    assert(b.start_before_us==driver.start_before_us && b.first_trigger_offset_us==20u);
    for (i=0;i<128u;++i) { assert(b.samples[i]==i+100u); }
    driver.dma_samples[0]=4095u;
    assert(b.samples[0]==100u); /* Live DMA writes cannot corrupt snapshot. */
    assert((*slot(&hw,D_ISR)&4u)==0u);
    empty_at(4000u,64u);
    hw.tick=driver.start_before_us+5130u;
    *slot(&hw,D_N)=256u; *slot(&hw,D_ISR)=2u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
    assert(b.first_sample_index==128u);
    for (i=0;i<128u;++i) { assert(b.samples[i]==i+228u); }
    empty_at(6500u,180u);
    hw.tick=driver.start_before_us+7690u;
    *slot(&hw,D_N)=128u; *slot(&hw,D_ISR)=4u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
    assert(b.first_sample_index==256u && b.samples[0]==4095u);
}

static void test_loss_detection(void)
{
    mt_adc_dma_block_t b={0}, unchanged;
    b.count=777u; unchanged=b;
    started(); hw.tick=driver.last_poll_us+MT_ADC_DMA_BLOCK_US;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
    assert(memcmp(&b,&unchanged,sizeof(b))==0);
    started(); *slot(&hw,D_ISR)=6u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
    started(); *slot(&hw,D_ISR)=2u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
    started(); *slot(&hw,D_ISR)=8u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_TRANSFER);
    started(); first_half_pending(); *slot(&hw,D_N)=200u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
    started(); first_half_pending(); *slot(&hw,D_N)=0u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
    started(); *slot(&hw,D_N)=257u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_TRANSFER);
    /* Main loop runs, but ADC never makes progress. */
    started(); empty_at(1000u,256u); empty_at(2000u,256u);
    hw.tick=driver.start_before_us+2560u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_DEADLINE);
}

static void test_copy_races(void)
{
    mt_adc_dma_block_t b={0};
    unsigned injection;
    for (injection=1;injection<=4;++injection) {
        started(); first_half_pending();
        hw.n_reads=0u; hw.inject_n_read=2u; hw.injection=injection;
        b.count=777u;
        assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
        assert(b.count==777u);
        stopped_fault(injection==2u?MT_ADC_DMA_FAULT_TRANSFER:MT_ADC_DMA_FAULT_DEADLINE);
    }
}

static void test_stop_restart_and_failed_stop(void)
{
    mt_adc_dma_block_t b={0};
    unsigned base;
    started(); base=hw.write_count;
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
    assert(hw.writes[base].address==T_CR1 && hw.writes[base].value==0u);
    assert(hw.writes[base+1u].address==A_CR2 && hw.writes[base+1u].value==0u);
    assert(hw.writes[base+2u].address==D_CCR && (hw.writes[base+2u].value&1u)==0u);
    assert(driver.state==MT_ADC_DMA_STOPPED && !driver.owns_peripherals);
    base=hw.write_count;
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK && hw.write_count==base);
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_NOT_RUNNING);
    *slot(&hw,D_ISR)=6u;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    assert(*slot(&hw,D_ISR)==0u && driver.first_sample_index==0u);
    hw.stop_stuck=true; hw.tick_step=0u;
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_ERROR);
    assert(driver.owns_peripherals && driver.fault==MT_ADC_DMA_FAULT_STOP);
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_BUSY);
    hw.stop_stuck=false;
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK && !driver.owns_peripherals);
}

static void test_runtime_clock_change_and_wrap(void)
{
    mt_adc_dma_block_t b={0};
    started(); *slot(&hw,R_CFGR)^=1u<<14;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_CLOCK);
    started(); *slot(&hw,R_CR)&=~(1u<<17);
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_CLOCK);
    /* A lost gate cannot be read back safely: ownership must be retained. */
    started(); *slot(&hw,R_AHB)&=~1u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    assert(driver.owns_peripherals && driver.fault==MT_ADC_DMA_FAULT_STOP);
    *slot(&hw,R_AHB)|=1u;
    assert(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
    setup(); hw.tick=UINT32_MAX-100u;
    assert(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    first_half_pending();
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
    assert(b.first_sample_index==0u && b.start_before_us>UINT32_MAX-100u);
    started(); first_half_pending(); driver.first_sample_index=UINT32_MAX-127u;
    assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
    stopped_fault(MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
}


static void test_all_half_boundaries(void)
{
    mt_adc_dma_block_t b={0};
    uint32_t n;
    for (n=0u;n<=257u;++n) {
        started(); first_half_pending(); *slot(&hw,D_N)=n;
        if (n>0u && n<=128u) {
            assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
        } else {
            assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
        }
        started(); first_half_pending();
        assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
        *slot(&hw,D_N)=n; *slot(&hw,D_ISR)=2u;
        if (n>128u && n<=256u) {
            assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_BLOCK);
        } else {
            assert(mt_adc_dma_read_block(&driver,&b)==MT_ADC_DMA_ERROR);
        }
    }
}

static void test_arguments(void)
{
    mt_adc_dma_block_t b={0};
    mt_adc_dma_t zero={0};
    mt_adc_dma_io_t bad={0};
    assert(mt_adc_dma_init(NULL,&bad,0u)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_init(&zero,NULL,0u)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_init(&zero,&bad,0u)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_start(NULL)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_start(&zero)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_stop(&zero)==MT_ADC_DMA_BAD_ARGUMENT);
    assert(mt_adc_dma_read_block(&zero,&b)==MT_ADC_DMA_BAD_ARGUMENT);
    setup(); assert(mt_adc_dma_read_block(&driver,NULL)==MT_ADC_DMA_BAD_ARGUMENT);
}

int main(void)
{
    test_all_half_boundaries();
    test_arguments();
    test_configuration_and_sequence();
    test_clock_validation();
    test_busy_owner();
    test_calibration_and_timebase_timeouts();
    test_owned_blocks();
    test_loss_detection();
    test_copy_races();
    test_stop_restart_and_failed_stop();
    test_runtime_clock_change_and_wrap();
    puts("ADC/TIM3/DMA register-model tests passed (not a hardware measurement)");
    return 0;
}
