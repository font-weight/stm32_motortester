#include "acquisition.h"

#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static unsigned test_count;

static mt_acq_config_t config_for(uint8_t mode, uint16_t pre, uint8_t edge)
{
    mt_acq_config_t config = {0};
    config.profile_id = MT_ACQ_PROFILE_ID;
    config.mode = mode;
    config.edge = edge;
    config.level_code = 2048u;
    config.pretrigger_samples = pre;
    config.auto_timeout_ms = mode == (uint8_t)MT_ACQ_AUTO ? 100u : 0u;
    return config;
}

static void setup(mt_acq_t *acq, const mt_acq_config_t *config, uint32_t now)
{
    assert(mt_acq_init(acq) == MT_ACQ_OK);
    assert(mt_acq_configure(acq, config) == MT_ACQ_OK);
    assert(mt_acq_arm(acq, now) == MT_ACQ_OK);
}

static void finish(mt_acq_t *acq, uint16_t code, uint32_t now)
{
    unsigned remaining = acq->post_remaining;
    assert(acq->state == MT_ACQ_CAPTURING);
    assert(remaining != 0u);
    for (unsigned i = 0; i < remaining; ++i) {
        assert(mt_acq_push(acq, code, now) ==
               (i + 1u == remaining ? MT_ACQ_WINDOW_READY : MT_ACQ_OK));
    }
    assert(acq->state == MT_ACQ_FROZEN);
}

static void test_arguments_and_configs(void)
{
    mt_acq_t acq;
    mt_acq_config_t config = config_for(MT_ACQ_NORMAL, 512u, MT_ACQ_RISING);
    mt_acq_config_t invalid[13];
    mt_acq_window_t window;
    uint16_t code = 123u;
    unsigned char before[sizeof(acq)];

    assert(mt_acq_init(NULL) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_configure(NULL, &config) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_arm(NULL, 0u) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_push(NULL, 1u, 0u) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_window(NULL, &window) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_read(NULL, 0u, &code) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_release(NULL, 0u) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_stop(NULL) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_fail(NULL, MT_ACQ_FAULT_DMA) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_init(&acq) == MT_ACQ_OK);
    assert(acq.state == MT_ACQ_STOPPED && !acq.configured);
    assert(mt_acq_arm(&acq, 0u) == MT_ACQ_INVALID_CONFIG);
    assert(mt_acq_configure(&acq, NULL) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_window(&acq, NULL) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_read(&acq, 0u, NULL) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_read(&acq, 2048u, &code) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_read(&acq, UINT16_MAX, &code) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_read(&acq, 0u, &code) == MT_ACQ_NOT_READY && code == 123u);
    assert(mt_acq_configure(&acq, &config) == MT_ACQ_OK);
    for (unsigned i = 0; i < 13u; ++i) {
        invalid[i] = config;
    }
    invalid[0].profile_id = 0u;
    invalid[1].profile_id = 2u;
    invalid[2].source = 1u;
    invalid[3].mode = 3u;
    invalid[4].edge = 2u;
    invalid[5].level_code = 0u;
    invalid[6].level_code = 4095u;
    invalid[7].pretrigger_samples = 511u;
    invalid[8].pretrigger_samples = 2048u;
    invalid[9].auto_timeout_ms = 100u;
    invalid[10].mode = MT_ACQ_AUTO;
    invalid[10].auto_timeout_ms = 99u;
    invalid[11].mode = MT_ACQ_AUTO;
    invalid[11].auto_timeout_ms = 60001u;
    invalid[12].mode = MT_ACQ_SINGLE;
    invalid[12].auto_timeout_ms = 1u;
    memcpy(before, &acq, sizeof(acq));
    for (unsigned i = 0; i < 13u; ++i) {
        assert(mt_acq_configure(&acq, &invalid[i]) == MT_ACQ_INVALID_CONFIG);
        assert(memcmp(before, &acq, sizeof(acq)) == 0);
    }
    config.mode = MT_ACQ_AUTO;
    config.auto_timeout_ms = 60000u;
    config.level_code = 1u;
    assert(mt_acq_configure(&acq, &config) == MT_ACQ_OK);
    config.auto_timeout_ms = 100u;
    config.level_code = 4094u;
    assert(mt_acq_configure(&acq, &config) == MT_ACQ_OK);
    ++test_count;
}

static void test_crossing_and_readiness(void)
{
    const uint16_t pre_values[] = {0u, 512u, 1024u};
    for (unsigned edge = 0; edge < 2u; ++edge) {
        const uint16_t before_level = edge == 0u ? 2047u : 2049u;
        for (unsigned p = 0; p < 3u; ++p) {
            mt_acq_t acq;
            const mt_acq_config_t config = config_for(MT_ACQ_NORMAL, pre_values[p],
                                                     (uint8_t)edge);
            setup(&acq, &config, 0u);
            if (pre_values[p] != 0u) {
                for (unsigned i = 0; i + 1u < pre_values[p]; ++i) {
                    assert(mt_acq_push(&acq, before_level, 1u) == MT_ACQ_OK);
                    assert(acq.state == MT_ACQ_PREPARING);
                }
                /* The readiness boundary sample crosses, but still belongs to
                 * preparation. It must not trigger or be re-evaluated later. */
                assert(mt_acq_push(&acq, 2048u, 1u) == MT_ACQ_OK);
                assert(acq.state == MT_ACQ_WAITING);
            } else {
                /* First sample on the far side cannot invent a crossing. */
                assert(mt_acq_push(&acq, 2048u, 1u) == MT_ACQ_OK);
                assert(acq.state == MT_ACQ_WAITING);
            }
            assert(mt_acq_push(&acq, 2048u, 1u) == MT_ACQ_OK);
            assert(mt_acq_push(&acq, before_level, 1u) == MT_ACQ_OK);
            assert(mt_acq_push(&acq, 2048u, 1u) == MT_ACQ_TRIGGERED);
            assert(acq.window.trigger_index == pre_values[p]);
            assert(acq.post_remaining == MT_ACQ_WINDOW_SAMPLES - pre_values[p] - 1u);
            finish(&acq, 2048u, 1u);
            uint16_t code = 0u;
            assert(mt_acq_read(&acq, pre_values[p], &code) == MT_ACQ_OK);
            assert(code == 2048u);
            ++test_count;
        }
    }
}

/* Independent chronological oracle: a linear input stream, with the expected
 * 2048-code interval sliced around its known unique crossing. Ring wrap and
 * waiting duration vary; every frozen code is checked, not just endpoints. */
static void test_chronological_oracle(void)
{
    uint16_t stream[12000];
    const uint16_t pre_values[] = {0u, 512u, 1024u};
    uint32_t rng = UINT32_C(0x381492ab);

    for (unsigned run = 0; run < 120u; ++run) {
        mt_acq_t acq;
        uint16_t pre = pre_values[run % 3u];
        uint8_t edge = (uint8_t)((run / 3u) % 2u);
        uint8_t mode = (uint8_t)((run / 6u) % 3u);
        mt_acq_config_t config = config_for(mode, pre, edge);
        config.auto_timeout_ms = mode == (uint8_t)MT_ACQ_AUTO ? 60000u : 0u;
        rng = rng * UINT32_C(1664525) + UINT32_C(1013904223);
        unsigned trigger_at = (unsigned)pre + 1u + (unsigned)(rng % 8000u);
        unsigned total = trigger_at + MT_ACQ_WINDOW_SAMPLES - pre;
        assert(total <= sizeof(stream) / sizeof(stream[0]));
        for (unsigned i = 0; i < total; ++i) {
            rng = rng * UINT32_C(1664525) + UINT32_C(1013904223);
            uint16_t magnitude = (uint16_t)(1u + (rng % 2046u));
            if ((i < trigger_at) == (edge == 0u)) {
                stream[i] = magnitude;
            } else {
                stream[i] = (uint16_t)(2048u + magnitude);
            }
        }
        setup(&acq, &config, 0u);
        for (unsigned i = 0; i < total; ++i) {
            mt_acq_result_t expected = MT_ACQ_OK;
            if (i == trigger_at) {
                expected = MT_ACQ_TRIGGERED;
            } else if (i + 1u == total) {
                expected = MT_ACQ_WINDOW_READY;
            }
            assert(mt_acq_push(&acq, stream[i], i / 50u) == expected);
        }
        mt_acq_window_t window;
        assert(mt_acq_window(&acq, &window) == MT_ACQ_OK);
        assert(window.sample_count == MT_ACQ_WINDOW_SAMPLES);
        assert(window.pretrigger_samples == pre && window.trigger_index == pre);
        assert(window.trigger_kind == MT_ACQ_TRIGGER_ANALOG);
        assert(window.quality_flags == 0u);
        for (uint16_t i = 0; i < MT_ACQ_WINDOW_SAMPLES; ++i) {
            uint16_t actual = UINT16_MAX;
            assert(mt_acq_read(&acq, i, &actual) == MT_ACQ_OK);
            assert(actual == stream[trigger_at - pre + i]);
        }
        ++test_count;
    }
}

static void test_auto_timeout(void)
{
    const uint16_t pre_values[] = {0u, 512u, 1024u};
    for (unsigned p = 0; p < 3u; ++p) {
        for (unsigned wrap = 0; wrap < 2u; ++wrap) {
            mt_acq_t acq;
            mt_acq_config_t config = config_for(MT_ACQ_AUTO, pre_values[p], MT_ACQ_RISING);
            uint32_t armed = wrap == 0u ? 700u : UINT32_MAX - 80u;
            uint32_t ready = armed;
            setup(&acq, &config, armed);
            for (unsigned i = 0; i < pre_values[p]; ++i) {
                /* Even a preparation longer than timeout cannot force a window. */
                ready = armed + i;
                assert(mt_acq_push(&acq, 100u, ready) == MT_ACQ_OK);
            }
            assert(acq.state == MT_ACQ_WAITING);
            assert(mt_acq_push(&acq, 100u, ready + 99u) == MT_ACQ_OK);
            assert(mt_acq_push(&acq, 100u, ready + 100u) == MT_ACQ_TRIGGERED);
            assert(acq.window.trigger_kind == MT_ACQ_TRIGGER_AUTO_TIMEOUT);
            assert(acq.window.trigger_index == MT_ACQ_NO_TRIGGER_INDEX);
            finish(&acq, 100u, ready + 100u);
            assert(acq.window.quality_flags == MT_ACQ_QUALITY_AUTO_TIMEOUT);
            ++test_count;
        }
    }
    mt_acq_t acq;
    mt_acq_config_t config = config_for(MT_ACQ_AUTO, 0u, MT_ACQ_RISING);
    setup(&acq, &config, 0u);
    assert(mt_acq_push(&acq, 100u, 99u) == MT_ACQ_OK);
    assert(mt_acq_push(&acq, 2048u, 100u) == MT_ACQ_TRIGGERED);
    assert(acq.window.trigger_kind == MT_ACQ_TRIGGER_ANALOG);
    finish(&acq, 2048u, 100u);
    assert(acq.window.quality_flags == 0u);
    setup(&acq, &config, 0u);
    /* Auto can time out on the first valid sample, without inventing an edge. */
    assert(mt_acq_push(&acq, 3000u, 100u) == MT_ACQ_TRIGGERED);
    assert(acq.window.trigger_kind == MT_ACQ_TRIGGER_AUTO_TIMEOUT);
    ++test_count;
}

static void test_modes_and_immutability(void)
{
    for (unsigned mode = 0; mode < 3u; ++mode) {
        mt_acq_t acq;
        mt_acq_config_t config = config_for((uint8_t)mode, 512u, MT_ACQ_RISING);
        unsigned char before[sizeof(acq)];
        uint32_t now = 0u;
        setup(&acq, &config, now);
        assert(mt_acq_configure(&acq, &config) == MT_ACQ_BUSY);
        assert(mt_acq_arm(&acq, now) == MT_ACQ_BUSY);
        for (unsigned i = 0; i < 512u; ++i) {
            assert(mt_acq_push(&acq, 100u, now) == MT_ACQ_OK);
        }
        assert(mt_acq_configure(&acq, &config) == MT_ACQ_BUSY);
        if (mode != (unsigned)MT_ACQ_AUTO) {
            now = 100000u;
            assert(mt_acq_push(&acq, 100u, now) == MT_ACQ_OK);
            assert(acq.state == MT_ACQ_WAITING);
        }
        assert(mt_acq_push(&acq, 2048u, now) == MT_ACQ_TRIGGERED);
        assert(mt_acq_configure(&acq, &config) == MT_ACQ_BUSY);
        assert(mt_acq_release(&acq, now) == MT_ACQ_NOT_READY);
        finish(&acq, 2100u, now);
        memcpy(before, &acq, sizeof(acq));
        for (unsigned i = 0; i < 3000u; ++i) {
            assert(mt_acq_push(&acq, UINT16_MAX, 0u) == MT_ACQ_IGNORED);
        }
        assert(mt_acq_configure(&acq, &config) == MT_ACQ_BUSY);
        assert(mt_acq_arm(&acq, now) == MT_ACQ_BUSY);
        assert(memcmp(before, &acq, sizeof(acq)) == 0);
        assert(mt_acq_release(&acq, now + 1u) == MT_ACQ_OK);
        assert(acq.state == (mode == (unsigned)MT_ACQ_SINGLE ?
                             MT_ACQ_STOPPED : MT_ACQ_PREPARING));
        assert(mt_acq_release(&acq, now + 1u) == MT_ACQ_NOT_READY);
        assert(acq.retained_count == 0u && !acq.have_previous);
        ++test_count;
    }
}

static void test_rail_membership(void)
{
    const uint16_t pre_values[] = {0u, 512u, 1024u};
    for (unsigned p = 0; p < 3u; ++p) {
        for (unsigned keep = 0; keep < 3u; ++keep) {
            mt_acq_t acq;
            mt_acq_config_t config = config_for(MT_ACQ_NORMAL, pre_values[p], MT_ACQ_RISING);
            setup(&acq, &config, 0u);
            assert(mt_acq_push(&acq, 0u, 0u) == MT_ACQ_OK);
            for (unsigned i = 0; i < 3000u; ++i) {
                assert(mt_acq_push(&acq, 100u, 0u) == MT_ACQ_OK);
            }
            /* keep=0: old rail must expire; keep=1/2: rails inside final window. */
            uint16_t trigger = keep == 1u ? 4095u : 2048u;
            assert(mt_acq_push(&acq, trigger, 0u) == MT_ACQ_TRIGGERED);
            finish(&acq, keep == 2u ? 0u : 2000u, 0u);
            assert(acq.window.quality_flags == (keep == 0u ? 0u : MT_ACQ_QUALITY_RAIL_CODE));
            ++test_count;
        }
    }
}

static void test_stop_and_faults(void)
{
    for (unsigned stage = 0; stage < 6u; ++stage) {
        mt_acq_t acq;
        mt_acq_config_t config = config_for(MT_ACQ_NORMAL, 512u, MT_ACQ_RISING);
        unsigned char before[sizeof(acq)];
        mt_acq_window_t window;
        unsigned char old_window[sizeof(window)];
        assert(mt_acq_init(&acq) == MT_ACQ_OK);
        assert(mt_acq_configure(&acq, &config) == MT_ACQ_OK);
        if (stage >= 1u) {
            assert(mt_acq_arm(&acq, 0u) == MT_ACQ_OK);
        }
        if (stage >= 2u) {
            for (unsigned i = 0; i < 512u; ++i) {
                assert(mt_acq_push(&acq, 100u, 0u) == MT_ACQ_OK);
            }
        }
        if (stage >= 3u) {
            assert(mt_acq_push(&acq, 2048u, 0u) == MT_ACQ_TRIGGERED);
        }
        if (stage >= 4u) {
            finish(&acq, 2048u, 0u);
        }
        if (stage >= 5u) {
            assert(mt_acq_fail(&acq, MT_ACQ_FAULT_DMA) == MT_ACQ_OK);
        }
        assert(mt_acq_stop(&acq) == MT_ACQ_OK);
        assert(acq.state == MT_ACQ_STOPPED);
        assert(acq.fault == MT_ACQ_FAULT_NONE);
        memcpy(before, &acq, sizeof(acq));
        assert(mt_acq_stop(&acq) == MT_ACQ_OK);
        assert(mt_acq_push(&acq, 3000u, 0u) == MT_ACQ_IGNORED);
        assert(mt_acq_release(&acq, 0u) == MT_ACQ_NOT_READY);
        assert(memcmp(before, &acq, sizeof(acq)) == 0);
        memset(&window, 0xa5, sizeof(window));
        memcpy(old_window, &window, sizeof(window));
        assert(mt_acq_window(&acq, &window) == MT_ACQ_NOT_READY);
        assert(memcmp(old_window, &window, sizeof(window)) == 0);
        assert(mt_acq_arm(&acq, 20u) == MT_ACQ_OK);
        assert(!acq.have_previous && acq.retained_count == 0u);
        ++test_count;
    }
    mt_acq_t acq;
    mt_acq_config_t config = config_for(MT_ACQ_NORMAL, 0u, MT_ACQ_RISING);
    setup(&acq, &config, 100u);
    assert(mt_acq_fail(&acq, MT_ACQ_FAULT_NONE) == MT_ACQ_INVALID_ARGUMENT);
    assert(mt_acq_fail(&acq, (mt_acq_fault_t)99) == MT_ACQ_INVALID_ARGUMENT);
    assert(acq.state == MT_ACQ_WAITING);
    assert(mt_acq_push(&acq, 4096u, 100u) == MT_ACQ_INPUT_FAULT);
    assert(acq.state == MT_ACQ_ERROR && acq.fault == MT_ACQ_FAULT_SAMPLE_RANGE);
    assert(mt_acq_arm(&acq, 100u) == MT_ACQ_BUSY);
    assert(mt_acq_configure(&acq, &config) == MT_ACQ_BUSY);
    assert(mt_acq_push(&acq, 100u, 100u) == MT_ACQ_IGNORED);
    assert(mt_acq_stop(&acq) == MT_ACQ_OK);
    assert(mt_acq_arm(&acq, 100u) == MT_ACQ_OK);
    assert(mt_acq_push(&acq, 100u, 99u) == MT_ACQ_INPUT_FAULT);
    assert(acq.fault == MT_ACQ_FAULT_TIME_ORDER);
    setup(&acq, &config, UINT32_MAX - 1u);
    assert(mt_acq_push(&acq, 100u, 0u) == MT_ACQ_OK);
    assert(mt_acq_push(&acq, 3000u, 0u) == MT_ACQ_TRIGGERED);
    assert(mt_acq_push(&acq, UINT16_MAX, 0u) == MT_ACQ_INPUT_FAULT);
    assert(acq.state == MT_ACQ_ERROR);
    setup(&acq, &config, 0u);
    assert(mt_acq_push(&acq, 100u, UINT32_C(0x80000000)) == MT_ACQ_INPUT_FAULT);
    assert(acq.fault == MT_ACQ_FAULT_TIME_ORDER);
    assert(mt_acq_fail(&acq, MT_ACQ_FAULT_DEADLINE) == MT_ACQ_OK);
    assert(acq.fault == MT_ACQ_FAULT_DEADLINE);
    ++test_count;
}

int main(void)
{
    test_arguments_and_configs();
    test_crossing_and_readiness();
    test_chronological_oracle();
    test_auto_timeout();
    test_modes_and_immutability();
    test_rail_membership();
    test_stop_and_faults();
    assert(sizeof(mt_acq_t) <= 4300u);
    printf("acquisition: %u scenarios passed; sizeof(mt_acq_t)=%zu bytes\n",
           test_count, sizeof(mt_acq_t));
    return 0;
}
