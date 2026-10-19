/* Independent forward-transfer register model for stop certificates.
 * This is native C state/arithmetic coverage, not an STM32 timing simulation.
 * The production driver is compiled separately, never included as test code.
 */
#include "adc_timer_dma.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "%s:%d: %s [case=%s, iteration=%u]\n", __FILE__, __LINE__, #x, case_name, iteration); \
    exit(1); } } while (0)

static const char *case_name;
static unsigned iteration;

enum {
    RCC_CR=0x40021000, RCC_CFGR=0x40021004,
    RCC_APB2RSTR=0x4002100c, RCC_APB1RSTR=0x40021010,
    RCC_AHB=0x40021014, RCC_APB2=0x40021018, RCC_APB1=0x4002101c,
    ADC_SR=0x40012400, ADC_CR1=0x40012404, ADC_CR2=0x40012408,
    TIM_CR1=0x40000400, TIM_CR2=0x40000404, TIM_SR=0x40000410,
    DMA_ISR=0x40020000, DMA_IFCR=0x40020004, DMA_CCR=0x40020008,
    DMA_N=0x4002000c, DMA_PAR=0x40020010, DMA_MAR=0x40020014,
    DMA_TC=2, DMA_HT=4, DMA_TE=8
};

typedef struct { uint32_t address, value; } reg;
typedef struct {
    reg regs[64], writes[2048];
    unsigned used, write_count, read_count, now_count, n_reads, flag_reads;
    uint32_t now, step, start_us;
    uint64_t transferred;
    mt_adc_dma_t *driver;
    unsigned ignore_stop; /* 1 TIM, 2 ADC, 4 DMA. */
    bool calibration_stuck;
    unsigned stop_extra_at; /* 1 TIM write, 2 ADC write, 3 DMA disable write. */
    unsigned stop_extra_count;
    unsigned change_n_read;
    unsigned change_kind; /* 1 count, 2 TE, 3 timeout, 4 count+sample. */
    uint32_t stop_clock_step;
    bool stop_seen;
    unsigned activate_timer_after_clear; /* Nth TIM read after event clear. */
    unsigned timer_reads_after_clear;
    bool event_clear_seen;
} bus;

static bus hw;
static mt_adc_dma_t driver;

static uint32_t *reg_at(bus *b, uint32_t address) {
    unsigned i;
    CHECK((address & 3u) == 0u);
    for (i=0; i<b->used; ++i) if (b->regs[i].address == address) return &b->regs[i].value;
    CHECK(b->used < 64u);
    b->regs[b->used] = (reg){address, 0u};
    return &b->regs[b->used++].value;
}
static uint16_t code_at(uint64_t index) {
    /* Nonlinear 12-bit test pattern distinguishes halves and rotations. */
    return (uint16_t)(((index * UINT64_C(37)) ^ (index >> 4) ^ UINT64_C(0x5a3)) & UINT64_C(4095));
}
static void transfer(bus *b, unsigned count) {
    unsigned i;
    for (i=0; i<count; ++i) {
        uint32_t position = (uint32_t)(b->transferred % UINT64_C(256));
        CHECK((*reg_at(b,DMA_CCR) & 1u) != 0u);
        b->driver->dma_samples[position] = code_at(b->transferred);
        ++b->transferred;
        position = (position + 1u) % 256u;
        *reg_at(b,DMA_N) = position == 0u ? 256u : 256u-position;
        if (position == 128u) *reg_at(b,DMA_ISR) |= DMA_HT;
        if (position == 0u) *reg_at(b,DMA_ISR) |= DMA_TC;
    }
}
static uint32_t rd(void *user, uint32_t address) {
    bus *b=user;
    uint32_t *v=reg_at(b,address);
    ++b->read_count;
    if (address==TIM_CR1 && b->event_clear_seen && b->activate_timer_after_clear &&
        ++b->timer_reads_after_clear==b->activate_timer_after_clear) *v |= 1u;
    if (address==ADC_CR2 && !b->calibration_stuck) *v &= ~UINT32_C(12);
    if (address==DMA_ISR) ++b->flag_reads;
    if (address==DMA_N) {
        ++b->n_reads;
        if (b->change_n_read != 0u && b->n_reads == b->change_n_read) {
            if (b->change_kind == 1u || b->change_kind == 4u) *v = *v==1u ? 256u : *v-1u;
            if (b->change_kind == 2u) *reg_at(b,DMA_ISR) |= DMA_TE;
            if (b->change_kind == 3u) b->now += MT_ADC_DMA_BLOCK_US;
            if (b->change_kind == 4u) b->driver->dma_samples[(unsigned)(b->transferred%256u)] = 1234u;
            if (b->change_kind == 5u) *reg_at(b,TIM_CR1) |= 1u;
            if (b->change_kind == 6u) *reg_at(b,RCC_AHB) &= ~1u;
        }
    }
    return *v;
}
static void wr(void *user, uint32_t address, uint32_t value) {
    bus *b=user;
    uint32_t *v=reg_at(b,address);
    unsigned stop_step = address==TIM_CR1 && value==0u ? 1u :
                         address==ADC_CR2 && value==0u ? 2u :
                         address==DMA_CCR && (value&1u)==0u && (*v&1u)!=0u ? 3u : 0u;
    CHECK(b->write_count < 2048u);
    b->writes[b->write_count++] = (reg){address,value};
    if (stop_step != 0u && b->driver->state == MT_ADC_DMA_RUNNING) {
        if (stop_step == 1u) {
            b->stop_seen = true;
            if (b->stop_clock_step) b->step=b->stop_clock_step;
        }
        if (stop_step == b->stop_extra_at) {
            transfer(b,b->stop_extra_count);
            b->stop_extra_at=0u;
        }
    }
    if (address==DMA_IFCR) {
        CHECK((value&1u)==0u); /* No CGIF, even on teardown. */
        *reg_at(b,DMA_ISR) &= ~value;
        if (b->activate_timer_after_clear) b->event_clear_seen=true;
    }
    if (address==TIM_CR1 && value==0u && (*v&1u)!=0u && (b->ignore_stop&1u)) return;
    if (address==ADC_CR2 && value==0u && (*v&1u)!=0u && (b->ignore_stop&2u)) return;
    if (address==DMA_CCR && (value&1u)==0u && (*v&1u)!=0u && (b->ignore_stop&4u)) return;
    *v=value;
}
static uint32_t now_us(void *user) {
    bus *b=user;
    uint32_t stamp=b->now;
    b->now+=b->step;
    ++b->now_count;
    return stamp;
}
static void setup(void) {
    mt_adc_dma_io_t io={rd,wr,now_us,&hw};
    memset(&hw,0,sizeof(hw));
    hw.driver=&driver; hw.now=1000u; hw.step=1u;
    *reg_at(&hw,RCC_CR)=(3u<<16)|(3u<<24);
    *reg_at(&hw,RCC_CFGR)=2u|(2u<<2)|(4u<<8)|(2u<<14)|(1u<<16)|(7u<<18);
    CHECK(mt_adc_dma_init(&driver,&io,8000000u)==MT_ADC_DMA_OK);
    CHECK(hw.write_count==0u && hw.read_count==0u);
}
static void enable_gates(void) {
    *reg_at(&hw,RCC_AHB)|=1u;
    *reg_at(&hw,RCC_APB2)|=(1u<<2)|(1u<<9);
    *reg_at(&hw,RCC_APB1)|=1u<<1;
}
static void started(void) {
    setup();
    CHECK(mt_adc_dma_enable_stream(&driver)==MT_ADC_DMA_OK);
    CHECK(driver.stream_mode);
    CHECK(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    CHECK(driver.state==MT_ADC_DMA_RUNNING && driver.owns_peripherals);
    hw.start_us=driver.start_before_us;
    CHECK(mt_adc_dma_enable_stream(&driver)!=MT_ADC_DMA_OK);
}
static void set_transfer_time(unsigned after_us) {
    hw.now=hw.start_us+(uint32_t)(hw.transferred*UINT64_C(20))+after_us;
}
static void consume_block(void) {
    mt_adc_dma_block_t block;
    uint64_t base=hw.transferred;
    unsigned i;
    CHECK(base%128u==0u);
    transfer(&hw,127u); set_transfer_time(19u);
    CHECK(mt_adc_dma_read_block(&driver,&block)==MT_ADC_DMA_EMPTY);
    transfer(&hw,1u); set_transfer_time(2u);
    CHECK(mt_adc_dma_read_block(&driver,&block)==MT_ADC_DMA_BLOCK);
    CHECK(block.first_sample_index==base && block.count==128u);
    CHECK(block.samples==driver.snapshot);
    for (i=0;i<128u;++i) CHECK(block.samples[i]==code_at(base+i));
}
static void pending(unsigned count) {
    mt_adc_dma_block_t unused;
    CHECK(count<256u);
    if (count>=127u) {
        transfer(&hw,127u); set_transfer_time(19u);
        CHECK(mt_adc_dma_read_block(&driver,&unused)==MT_ADC_DMA_EMPTY);
        if (count>127u) { transfer(&hw,count-127u); set_transfer_time(2u); }
    } else {
        transfer(&hw,count); set_transfer_time(2u);
    }
}
static void known(const mt_adc_dma_stop_snapshot_t *out, uint64_t base, unsigned count) {
    unsigned i;
    CHECK(out->hardware_off && out->dma_end_known);
    CHECK(out->first_sample_index==base);
    CHECK(out->dma_end==base+count && out->count==count);
    CHECK(out->first_fault==MT_ADC_DMA_FAULT_NONE);
    CHECK(!driver.owns_peripherals);
    CHECK((*reg_at(&hw,TIM_CR1)&1u)==0u && *reg_at(&hw,ADC_CR2)==0u && (*reg_at(&hw,DMA_CCR)&1u)==0u);
    for(i=0;i<count;++i) CHECK(out->samples[i]==code_at(base+i));
}
static void unknown(const mt_adc_dma_stop_snapshot_t *out, bool off) {
    CHECK(out->hardware_off==off);
    CHECK(!out->dma_end_known && out->dma_end==0u && out->count==0u);
}
static void test_all_tails(void) {
    unsigned phase, count, rotations;
    for(rotations=0;rotations<3u;++rotations) for(phase=0;phase<2u;++phase) for(count=0;count<256u;++count) {
        mt_adc_dma_stop_snapshot_t out, again;
        unsigned i, writes;
        uint64_t base=(uint64_t)(rotations*2u+phase)*128u;
        iteration=rotations*512u+phase*256u+count;
        started();
        for(i=0;i<rotations*2u+phase;++i) consume_block();
        pending(count);
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        known(&out,base,count);
        CHECK((*reg_at(&hw,DMA_ISR)&14u)==0u);
        writes=hw.write_count;
        memset(&again,0xa5,sizeof(again));
        CHECK(mt_adc_dma_stop_snapshot(&driver,&again)==MT_ADC_DMA_OK);
        known(&again,base,count);
        CHECK(hw.write_count==writes);
        CHECK(memcmp(out.samples,again.samples,count*sizeof(uint16_t))==0);
        /* No pointer into the ring escaped into this caller-owned certificate. */
        for(i=0;i<256u;++i) driver.dma_samples[i]=0xffffu;
        for(i=0;i<count;++i) CHECK(out.samples[i]==code_at(base+i));
    }
}
static void test_inverse_domain(void) {
    unsigned phase, flags, n;
    for(phase=0;phase<2u;++phase) for(flags=0;flags<16u;flags+=2u) for(n=0;n<=257u;++n) {
        mt_adc_dma_stop_snapshot_t out;
        unsigned k;
        bool possible=false;
        iteration=phase*2064u+(flags/2u)*258u+n;
        /* Independently enumerate forward progress, not inverse-case formulas. */
        for(k=0;k<256u;++k) {
            unsigned pos=phase*128u;
            unsigned seen=0u, step;
            for(step=0;step<k;++step) {
                pos=(pos+1u)%256u;
                if(pos==128u) seen|=DMA_HT;
                if(pos==0u) seen|=DMA_TC;
            }
            if(flags==seen && n==(pos==0u?256u:256u-pos)) possible=true;
        }
        if(possible) continue; /* Exact positive data/count cases above. */
        started(); if(phase) consume_block();
        *reg_at(&hw,DMA_ISR)=flags; *reg_at(&hw,DMA_N)=n;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
        CHECK(!driver.owns_peripherals);
    }
}
static void test_fault_then_retry(void) {
    mt_adc_dma_block_t block={0};
    mt_adc_dma_stop_snapshot_t out;
    unsigned reason;
    for(reason=0;reason<3u;++reason) {
        iteration=reason;
        started(); pending(17u); hw.ignore_stop=4u; hw.step=0u;
        if(reason==0u) *reg_at(&hw,DMA_ISR)|=DMA_TE;
        if(reason==1u) hw.now=driver.last_poll_us+MT_ADC_DMA_BLOCK_US;
        if(reason==2u) *reg_at(&hw,RCC_CFGR)^=1u<<14;
        CHECK(mt_adc_dma_read_block(&driver,&block)==MT_ADC_DMA_ERROR);
        CHECK(driver.owns_peripherals);
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false);
        CHECK(out.first_fault==(reason==0u?MT_ADC_DMA_FAULT_TRANSFER:reason==1u?MT_ADC_DMA_FAULT_DEADLINE:MT_ADC_DMA_FAULT_CLOCK));
        hw.ignore_stop=0u; hw.step=1u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
        CHECK(out.first_fault==(reason==0u?MT_ADC_DMA_FAULT_TRANSFER:reason==1u?MT_ADC_DMA_FAULT_DEADLINE:MT_ADC_DMA_FAULT_CLOCK));
        CHECK(!driver.owns_peripherals);
        *reg_at(&hw,DMA_ISR)=0u; *reg_at(&hw,DMA_N)=256u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
    }
}
static void test_stop_failures(void) {
    unsigned failure;
    for(failure=1u;failure<=4u;failure*=2u) {
        mt_adc_dma_stop_snapshot_t out;
        unsigned reads;
        iteration=failure;
        started(); pending(21u); hw.ignore_stop=failure; hw.step=0u; reads=hw.read_count;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false);
        CHECK(driver.owns_peripherals && out.first_fault==MT_ADC_DMA_FAULT_STOP);
        CHECK(hw.read_count-reads<=MT_ADC_DMA_WAIT_POLLS+100u);
        CHECK((*reg_at(&hw,DMA_ISR)&14u)==0u); /* No pending flags in this fixture. */
        hw.ignore_stop=0u; hw.step=1u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
        CHECK(out.first_fault==MT_ADC_DMA_FAULT_STOP);
    }
}
static void test_external_history(void) {
    mt_adc_dma_stop_snapshot_t out;
    unsigned reads,writes;
    started(); pending(217u); reads=hw.read_count; writes=hw.write_count;
    mt_adc_dma_invalidate_stream_history(&driver,MT_ADC_DMA_FAULT_TIMEBASE);
    CHECK(hw.read_count==reads && hw.write_count==writes);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_TIMEBASE);
    mt_adc_dma_invalidate_stream_history(&driver,MT_ADC_DMA_FAULT_TRANSFER);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_TIMEBASE);
}
static void test_unowned(void) {
    const uint32_t active[]={TIM_CR1,ADC_CR2,DMA_CCR};
    unsigned i;
    mt_adc_dma_stop_snapshot_t out;
    setup(); enable_gates();
    CHECK(mt_adc_dma_enable_stream(&driver)==MT_ADC_DMA_OK);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    CHECK(out.hardware_off && out.dma_end==0u && out.count==0u);
    CHECK(hw.read_count!=0u && hw.write_count==0u);
    for(i=0;i<3u;++i) {
        iteration=i;
        setup(); enable_gates(); *reg_at(&hw,active[i])=1u;
        CHECK(mt_adc_dma_enable_stream(&driver)==MT_ADC_DMA_OK);
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false);
        CHECK(hw.write_count==0u && *reg_at(&hw,active[i])==1u && !driver.owns_peripherals);
    }
    setup(); CHECK(mt_adc_dma_enable_stream(&driver)==MT_ADC_DMA_OK);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
    unknown(&out,false); CHECK(hw.write_count==0u);
}
static void test_gate_reset_loss(void) {
    const uint32_t addresses[]={RCC_AHB,RCC_APB1,RCC_APB2,RCC_APB1RSTR,RCC_APB2RSTR};
    const uint32_t masks[]={1u,2u,1u<<9,2u,1u<<9};
    unsigned i;
    for(i=0;i<5u;++i) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=i; started(); pending(137u);
        if(i<3u) *reg_at(&hw,addresses[i])&=~masks[i];
        else *reg_at(&hw,addresses[i])|=masks[i];
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false); CHECK(driver.owns_peripherals);
        if(i<3u) *reg_at(&hw,addresses[i])|=masks[i];
        else *reg_at(&hw,addresses[i])&=~masks[i];
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
    }
}
static void test_stop_late_transfers(void) {
    unsigned at,phase;
    for(at=1u;at<=3u;++at) for(phase=0;phase<2u;++phase) {
        mt_adc_dma_stop_snapshot_t out;
        uint64_t base=(uint64_t)phase*128u;
        iteration=at*2u+phase;
        started(); if(phase) consume_block(); pending(126u);
        hw.stop_extra_at=at; hw.stop_extra_count=3u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        CHECK(hw.stop_extra_at==0u);
        known(&out,base,129u);
    }
}
static void test_snapshot_races(void) {
    unsigned kind;
    for(kind=1u;kind<=4u;++kind) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=kind; started(); pending(151u);
        hw.n_reads=0u; hw.change_n_read=2u; hw.change_kind=kind;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        CHECK(hw.n_reads>=2u);
        unknown(&out,true);
        /* The result cannot contain an early partial copied prefix. */
        CHECK(out.count==0u);
    }
}
static void test_bad_codes(void) {
    const unsigned bad_positions[]={0u,127u,128u,254u};
    unsigned i;
    for(i=0;i<4u;++i) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=i; started(); pending(255u);
        driver.dma_samples[bad_positions[i]]=4096u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
    }
}
static void test_history_and_deadline(void) {
    mt_adc_dma_stop_snapshot_t out;
    mt_adc_dma_block_t unused;
    started(); pending(17u);
    hw.now=driver.last_poll_us+MT_ADC_DMA_BLOCK_US;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true);
    started(); pending(17u);
    hw.stop_clock_step=MT_ADC_DMA_BLOCK_US;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true);
    started(); pending(17u); transfer(&hw,512u); hw.now=driver.last_poll_us+11000u;
    CHECK(mt_adc_dma_read_block(&driver,&unused)==MT_ADC_DMA_ERROR);
    *reg_at(&hw,DMA_ISR)=0u; *reg_at(&hw,DMA_N)=239u;
    hw.now=driver.last_poll_us+1u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true);
}
static void test_u64_indices(void) {
    mt_adc_dma_block_t block;
    mt_adc_dma_stop_snapshot_t out;
    uint64_t base;
    unsigned i;
    started();
    base=UINT64_C(0xffffff80);
    driver.first_sample_index=base; driver.next_half=true; hw.transferred=base;
    *reg_at(&hw,DMA_N)=128u;
    /* Model uses near-zero relative timestamps; source cursor is independent. */
    hw.start_us=driver.last_poll_us-(uint32_t)(base*UINT64_C(20));
    consume_block();
    CHECK(driver.first_sample_index==UINT64_C(0x100000000));
    pending(133u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    known(&out,UINT64_C(0x100000000),133u);
    started();
    base=UINT64_MAX-UINT64_C(127);
    driver.first_sample_index=base; driver.next_half=true; hw.transferred=base;
    *reg_at(&hw,DMA_N)=128u; hw.start_us=driver.last_poll_us-(uint32_t)(base*UINT64_C(20));
    pending(127u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    known(&out,base,127u); CHECK(out.dma_end==UINT64_MAX);
    started();
    base=UINT64_MAX-UINT64_C(127);
    driver.first_sample_index=base; driver.next_half=true; hw.transferred=base;
    *reg_at(&hw,DMA_N)=128u;
    /* Feed full final half without incrementing the oracle past UINT64_MAX. */
    for(i=0;i<128u;++i) driver.dma_samples[128u+i]=code_at(base+i);
    *reg_at(&hw,DMA_N)=256u; *reg_at(&hw,DMA_ISR)=DMA_TC;
    block.count=777u;
    CHECK(mt_adc_dma_read_block(&driver,&block)==MT_ADC_DMA_ERROR);
    CHECK(block.count==777u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
}
static void test_new_generation(void) {
    mt_adc_dma_stop_snapshot_t old,out;
    started(); pending(211u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&old)==MT_ADC_DMA_OK);
    known(&old,0u,211u);
    CHECK(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    hw.start_us=driver.start_before_us; hw.transferred=0u;
    pending(3u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    known(&out,0u,3u);
    CHECK(old.count==211u && old.dma_end==211u);
}
static void test_legacy_mode(void) {
    mt_adc_dma_block_t out;
    mt_adc_dma_stop_snapshot_t snap;
    setup(); CHECK(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    CHECK(!driver.stream_mode);
    driver.first_sample_index=UINT64_C(0xffffff80); driver.next_half=true;
    *reg_at(&hw,DMA_ISR)=DMA_TC; *reg_at(&hw,DMA_N)=256u;
    out.count=777u;
    CHECK(mt_adc_dma_read_block(&driver,&out)==MT_ADC_DMA_ERROR);
    CHECK(out.count==777u && driver.fault==MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&snap)==MT_ADC_DMA_BAD_ARGUMENT);
}
static void test_arguments(void) {
    mt_adc_dma_stop_snapshot_t out;
    mt_adc_dma_t zero={0};
    CHECK(mt_adc_dma_enable_stream(NULL)==MT_ADC_DMA_BAD_ARGUMENT);
    CHECK(mt_adc_dma_enable_stream(&zero)==MT_ADC_DMA_BAD_ARGUMENT);
    CHECK(mt_adc_dma_stop_snapshot(NULL,&out)==MT_ADC_DMA_BAD_ARGUMENT);
    CHECK(mt_adc_dma_stop_snapshot(&zero,&out)==MT_ADC_DMA_BAD_ARGUMENT);
    started();
    CHECK(mt_adc_dma_stop_snapshot(&driver,NULL)==MT_ADC_DMA_BAD_ARGUMENT);
    CHECK(driver.state==MT_ADC_DMA_RUNNING && driver.owns_peripherals);
}


static void test_explicit_fault_precedence(void) {
    mt_adc_dma_stop_snapshot_t out;
    started(); pending(151u); *reg_at(&hw,DMA_ISR)|=DMA_TE;
    hw.ignore_stop=4u; hw.step=0u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
    unknown(&out,false);
    CHECK(out.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
    hw.ignore_stop=0u; hw.step=1u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
}
static void test_stop_detected_cause(void) {
    mt_adc_dma_stop_snapshot_t out;
    started(); pending(17u); *reg_at(&hw,RCC_CFGR)^=1u<<14;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_CLOCK);
    started(); pending(17u); hw.now=driver.last_poll_us+MT_ADC_DMA_BLOCK_US;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_DEADLINE);
}
static void test_repeat_revalidation(void) {
    mt_adc_dma_stop_snapshot_t old,out;
    unsigned writes;
    started(); pending(151u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&old)==MT_ADC_DMA_OK);
    known(&old,0u,151u);
    writes=hw.write_count;
    *reg_at(&hw,RCC_AHB)&=~1u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
    unknown(&out,false); CHECK(hw.write_count==writes);
    *reg_at(&hw,RCC_AHB)|=1u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_STOP);
    CHECK(old.count==151u && old.dma_end==151u && old.samples[150]==code_at(150u));
}
static void test_cursor_inconsistency(void) {
    mt_adc_dma_stop_snapshot_t out;
    started(); pending(11u); driver.first_sample_index=1u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK); unknown(&out,true);
    started(); pending(11u); driver.next_half=true;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK); unknown(&out,true);
}
static void test_stop_before_fault_read(void) {
    mt_adc_dma_stop_snapshot_t out;
    mt_adc_dma_block_t ignored;
    started(); pending(151u); *reg_at(&hw,DMA_ISR)|=DMA_TE;
    CHECK(mt_adc_dma_read_block(&driver,&ignored)==MT_ADC_DMA_ERROR);
    CHECK((*reg_at(&hw,DMA_ISR)&14u)==0u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_TRANSFER);
    /* Stopping via the old wrapper must capture first, before event clearing. */
    started(); pending(211u);
    CHECK(mt_adc_dma_stop(&driver)==MT_ADC_DMA_OK);
    CHECK((*reg_at(&hw,DMA_ISR)&14u)==0u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    known(&out,0u,211u);
}


static void test_late_off_revalidation(void) {
    unsigned path;
    for(path=0u;path<2u;++path) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=path; started(); pending(151u);
        if(path) *reg_at(&hw,DMA_ISR)|=DMA_TE;
        hw.n_reads=0u; hw.change_n_read=path?1u:2u; hw.change_kind=path?6u:5u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false); CHECK(driver.owns_peripherals);
        /* The evidence must not be erased on failed hardware readback. */
        CHECK((*reg_at(&hw,DMA_ISR)&DMA_HT)!=0u);
        *reg_at(&hw,TIM_CR1)=0u; *reg_at(&hw,RCC_AHB)|=1u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
    }
}


static void test_clear_and_copy_barriers(void) {
    unsigned read_number;
    for(read_number=1u;read_number<=2u;++read_number) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=read_number; started(); pending(151u);
        hw.activate_timer_after_clear=read_number;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false); CHECK(driver.owns_peripherals);
        CHECK(out.first_fault==MT_ADC_DMA_FAULT_STOP);
        hw.activate_timer_after_clear=0u;
        *reg_at(&hw,TIM_CR1)=0u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true);
    }
}
static void test_failed_start(void) {
    mt_adc_dma_stop_snapshot_t out;
    setup(); CHECK(mt_adc_dma_enable_stream(&driver)==MT_ADC_DMA_OK);
    hw.calibration_stuck=true; hw.ignore_stop=2u;
    CHECK(mt_adc_dma_start(&driver)==MT_ADC_DMA_ERROR);
    CHECK(driver.first_fault==MT_ADC_DMA_FAULT_CALIBRATION);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
    unknown(&out,false); CHECK(out.first_fault==MT_ADC_DMA_FAULT_CALIBRATION);
    hw.calibration_stuck=false; hw.ignore_stop=0u;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_CALIBRATION);
    CHECK(mt_adc_dma_start(&driver)==MT_ADC_DMA_OK);
    hw.start_us=driver.start_before_us; hw.transferred=0u;
    pending(5u);
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    known(&out,0u,5u);
}


static void test_direct_stop_overflow(void) {
    mt_adc_dma_stop_snapshot_t out;
    unsigned i;
    uint64_t base=UINT64_MAX-UINT64_C(127);
    started(); driver.first_sample_index=base; driver.next_half=true;
    for(i=0;i<128u;++i) driver.dma_samples[128u+i]=code_at(base+i);
    *reg_at(&hw,DMA_N)=256u; *reg_at(&hw,DMA_ISR)=DMA_TC;
    CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
    unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_SEQUENCE_LIMIT);
}
static void test_pending_event_precedence(void) {
    unsigned phase,both;
    for(phase=0u;phase<2u;++phase) for(both=0u;both<2u;++both) {
        mt_adc_dma_stop_snapshot_t out;
        iteration=phase*2u+both;
        started(); if(phase) consume_block(); pending(17u);
        *reg_at(&hw,DMA_ISR)=both ? DMA_HT|DMA_TC : phase?DMA_HT:DMA_TC;
        hw.ignore_stop=4u; hw.step=0u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_ERROR);
        unknown(&out,false); CHECK(out.first_fault==MT_ADC_DMA_FAULT_DEADLINE);
        hw.ignore_stop=0u; hw.step=1u;
        CHECK(mt_adc_dma_stop_snapshot(&driver,&out)==MT_ADC_DMA_OK);
        unknown(&out,true); CHECK(out.first_fault==MT_ADC_DMA_FAULT_DEADLINE);
    }
}

typedef struct { const char *name; void (*run)(void); } test;
static const test tests[]={
    {"all_tails",test_all_tails},{"inverse_domain",test_inverse_domain},
    {"fault_then_retry",test_fault_then_retry},{"stop_failures",test_stop_failures},
    {"external_history",test_external_history},{"unowned",test_unowned},
    {"gate_reset_loss",test_gate_reset_loss},{"stop_late_transfers",test_stop_late_transfers},
    {"snapshot_races",test_snapshot_races},{"bad_codes",test_bad_codes},
    {"history_deadline",test_history_and_deadline},{"u64_indices",test_u64_indices},
    {"new_generation",test_new_generation},{"legacy_mode",test_legacy_mode},
    {"arguments",test_arguments},
    {"explicit_fault_precedence",test_explicit_fault_precedence},
    {"stop_detected_cause",test_stop_detected_cause},
    {"repeat_revalidation",test_repeat_revalidation},
    {"cursor_inconsistency",test_cursor_inconsistency},
    {"stop_before_fault_read",test_stop_before_fault_read},
    {"late_off_revalidation",test_late_off_revalidation},
    {"clear_copy_barriers",test_clear_and_copy_barriers},
    {"failed_start",test_failed_start},
    {"direct_stop_overflow",test_direct_stop_overflow},
    {"pending_event_precedence",test_pending_event_precedence}
};
int main(int argc,char **argv) {
    unsigned i;
    bool found=false;
    if(argc>2) return 2;
    if(argc==2 && strcmp(argv[1],"--list")==0) {
        for(i=0;i<sizeof(tests)/sizeof(tests[0]);++i) puts(tests[i].name);
        return 0;
    }
    for(i=0;i<sizeof(tests)/sizeof(tests[0]);++i) {
        if(argc==2 && strcmp(argv[1],tests[i].name)!=0) continue;
        found=true;
        case_name=tests[i].name; iteration=0u;
        tests[i].run(); printf("PASS %s\n",case_name);
    }
    return found ? 0 : 2;
}
