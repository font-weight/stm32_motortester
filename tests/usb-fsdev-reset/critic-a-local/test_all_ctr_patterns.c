/* Critic-A supplemental test: every retained RX/TX combination across eight
 * physical slots. It reuses the disclosed supporting register-access model;
 * this is neither a second independent peripheral model nor MMIO execution. */
#define main supporting_original_main
#include "translation.c"
#undef main
int main(void) {
    CHECK(EXPECT_FIXED == 1);
    for (uint32_t mask = 0; mask < UINT32_C(65536); ++mask) {
        test_context = "all 65536 retained CTR combinations";
        fresh_model();
        seed_ownership();
        for (unsigned slot = 0; slot < FSDEV_EP_COUNT; ++slot) {
            uint16_t retained = 0;
            if (mask & (UINT32_C(1) << (2U * slot))) retained |= U_EP_CTR_RX;
            if (mask & (UINT32_C(1) << (2U * slot + 1U))) retained |= U_EP_CTR_TX;
            registers.ep[slot].reg = (uint16_t)(retained | U_EP_CONTROL |
                U_EP_DTOG_RX | U_EP_DTOG_TX | U_EPRX_STAT | U_EPTX_STAT | slot);
        }
        bus_reset_endpoint_registers();
        run_reset();
        CHECK(bus_reset_events == 1);
        CHECK(event_count == 0 && pma_reads == 0 && pma_writes == 0);
        istr_reads = 0;
        dcd_int_handler(0);
        CHECK(event_count == 0 && pma_reads == 0 && pma_writes == 0);
        CHECK(!(model_istr_read() & (U_ISTR_CTR | U_ISTR_RESET)));
    }
    printf("PASS Critic-A all CTR patterns: logical_endpoints=%u patterns=65536 assertions=%lu\n",
           MODEL_ENDPOINTS, checks);
    return 0;
}
