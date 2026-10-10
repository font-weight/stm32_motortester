#include "acquisition.h"

#include <stddef.h>
#include <string.h>

#define MT_ACQ_RING_MASK (MT_ACQ_WINDOW_SAMPLES - 1u)
#define MT_ACQ_HALF_CLOCK_RANGE UINT32_C(0x80000000)

_Static_assert((MT_ACQ_WINDOW_SAMPLES & MT_ACQ_RING_MASK) == 0u,
               "Ring size must be a power of two");
_Static_assert(MT_ACQ_WINDOW_SAMPLES < UINT16_MAX,
               "Ring indexes must fit uint16_t");

static bool is_rail(uint16_t code)
{
    return code == 0u || code == 4095u;
}

static bool valid_config(const mt_acq_config_t *config)
{
    if (config->profile_id != MT_ACQ_PROFILE_ID || config->source != 0u ||
        config->mode > (uint8_t)MT_ACQ_SINGLE ||
        config->edge > (uint8_t)MT_ACQ_FALLING ||
        config->level_code < 1u || config->level_code > 4094u) {
        return false;
    }
    if (config->pretrigger_samples != 0u &&
        config->pretrigger_samples != 512u &&
        config->pretrigger_samples != 1024u) {
        return false;
    }
    if (config->mode == (uint8_t)MT_ACQ_AUTO) {
        return config->auto_timeout_ms >= 100u &&
               config->auto_timeout_ms <= 60000u;
    }
    return config->auto_timeout_ms == 0u;
}

static void reset_capture(mt_acq_t *acq)
{
    acq->window.sample_count = 0u;
    acq->window.pretrigger_samples = 0u;
    acq->window.trigger_index = MT_ACQ_NO_TRIGGER_INDEX;
    acq->window.quality_flags = 0u;
    acq->window.trigger_kind = MT_ACQ_TRIGGER_NONE;
    acq->waiting_since_ms = 0u;
    acq->last_sample_ms = 0u;
    acq->write_index = 0u;
    acq->retained_count = 0u;
    acq->first_index = 0u;
    acq->post_remaining = 0u;
    acq->previous_code = 0u;
    acq->rail_count = 0u;
    acq->fault = MT_ACQ_FAULT_NONE;
    acq->have_previous = false;
}

static void begin_capture(mt_acq_t *acq, uint32_t now_ms)
{
    reset_capture(acq);
    acq->waiting_since_ms = now_ms;
    acq->last_sample_ms = now_ms;
    acq->state = acq->config.pretrigger_samples == 0u ?
                 MT_ACQ_WAITING : MT_ACQ_PREPARING;
}

/* Rail membership follows the retained ring, so an old rail code which was
 * overwritten cannot contaminate the final window's quality flag. O(1)/sample.
 */
static void append_sample(mt_acq_t *acq, uint16_t code)
{
    if (acq->retained_count == MT_ACQ_WINDOW_SAMPLES) {
        if (is_rail(acq->samples[acq->write_index])) {
            --acq->rail_count;
        }
    } else {
        ++acq->retained_count;
    }
    acq->samples[acq->write_index] = code;
    if (is_rail(code)) {
        ++acq->rail_count;
    }
    acq->write_index = (uint16_t)((acq->write_index + 1u) & MT_ACQ_RING_MASK);
    acq->previous_code = code;
    acq->have_previous = true;
}

static bool crossing(const mt_acq_t *acq, uint16_t code)
{
    if (!acq->have_previous) {
        return false;
    }
    if (acq->config.edge == (uint8_t)MT_ACQ_RISING) {
        return acq->previous_code < acq->config.level_code &&
               code >= acq->config.level_code;
    }
    return acq->previous_code > acq->config.level_code &&
           code <= acq->config.level_code;
}

mt_acq_result_t mt_acq_init(mt_acq_t *acq)
{
    if (acq == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    memset(acq, 0, sizeof(*acq));
    reset_capture(acq);
    acq->state = MT_ACQ_STOPPED;
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_configure(mt_acq_t *acq, const mt_acq_config_t *config)
{
    if (acq == NULL || config == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_STOPPED) {
        return MT_ACQ_BUSY;
    }
    if (!valid_config(config)) {
        return MT_ACQ_INVALID_CONFIG;
    }
    acq->config = *config;
    acq->configured = true;
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_arm(mt_acq_t *acq, uint32_t now_ms)
{
    if (acq == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_STOPPED) {
        return MT_ACQ_BUSY;
    }
    if (!acq->configured) {
        return MT_ACQ_INVALID_CONFIG;
    }
    begin_capture(acq, now_ms);
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_push(mt_acq_t *acq, uint16_t code, uint32_t sample_ms)
{
    mt_acq_trigger_t trigger = MT_ACQ_TRIGGER_NONE;

    if (acq == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_PREPARING && acq->state != MT_ACQ_WAITING &&
        acq->state != MT_ACQ_CAPTURING) {
        return MT_ACQ_IGNORED;
    }
    if (code > 4095u) {
        (void)mt_acq_fail(acq, MT_ACQ_FAULT_SAMPLE_RANGE);
        return MT_ACQ_INPUT_FAULT;
    }
    if ((uint32_t)(sample_ms - acq->last_sample_ms) >= MT_ACQ_HALF_CLOCK_RANGE) {
        (void)mt_acq_fail(acq, MT_ACQ_FAULT_TIME_ORDER);
        return MT_ACQ_INPUT_FAULT;
    }
    acq->last_sample_ms = sample_ms;

    if (acq->state == MT_ACQ_WAITING) {
        if (crossing(acq, code)) {
            trigger = MT_ACQ_TRIGGER_ANALOG;
        } else if (acq->config.mode == (uint8_t)MT_ACQ_AUTO &&
                   (uint32_t)(sample_ms - acq->waiting_since_ms) >=
                   acq->config.auto_timeout_ms) {
            trigger = MT_ACQ_TRIGGER_AUTO_TIMEOUT;
        }
        if (trigger != MT_ACQ_TRIGGER_NONE) {
            acq->first_index = (uint16_t)((acq->write_index +
                              MT_ACQ_WINDOW_SAMPLES -
                              acq->config.pretrigger_samples) & MT_ACQ_RING_MASK);
            acq->post_remaining = (uint16_t)(MT_ACQ_WINDOW_SAMPLES -
                                            acq->config.pretrigger_samples - 1u);
            acq->window.pretrigger_samples = acq->config.pretrigger_samples;
            acq->window.trigger_kind = trigger;
            acq->window.trigger_index = trigger == MT_ACQ_TRIGGER_ANALOG ?
                                       acq->config.pretrigger_samples :
                                       MT_ACQ_NO_TRIGGER_INDEX;
            acq->window.quality_flags = trigger == MT_ACQ_TRIGGER_AUTO_TIMEOUT ?
                                       MT_ACQ_QUALITY_AUTO_TIMEOUT : 0u;
            append_sample(acq, code);
            acq->state = MT_ACQ_CAPTURING;
            return MT_ACQ_TRIGGERED;
        }
    }

    append_sample(acq, code);
    if (acq->state == MT_ACQ_PREPARING &&
        acq->retained_count == acq->config.pretrigger_samples) {
        acq->waiting_since_ms = sample_ms;
        acq->state = MT_ACQ_WAITING;
    } else if (acq->state == MT_ACQ_CAPTURING) {
        --acq->post_remaining;
        if (acq->post_remaining == 0u) {
            acq->window.sample_count = MT_ACQ_WINDOW_SAMPLES;
            if (acq->rail_count != 0u) {
                acq->window.quality_flags |= MT_ACQ_QUALITY_RAIL_CODE;
            }
            acq->state = MT_ACQ_FROZEN;
            return MT_ACQ_WINDOW_READY;
        }
    }
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_window(const mt_acq_t *acq, mt_acq_window_t *window)
{
    if (acq == NULL || window == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_FROZEN) {
        return MT_ACQ_NOT_READY;
    }
    *window = acq->window;
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_read(const mt_acq_t *acq, uint16_t index, uint16_t *code)
{
    if (acq == NULL || code == NULL || index >= MT_ACQ_WINDOW_SAMPLES) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_FROZEN) {
        return MT_ACQ_NOT_READY;
    }
    *code = acq->samples[(acq->first_index + index) & MT_ACQ_RING_MASK];
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_release(mt_acq_t *acq, uint32_t now_ms)
{
    if (acq == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    if (acq->state != MT_ACQ_FROZEN) {
        return MT_ACQ_NOT_READY;
    }
    if (acq->config.mode == (uint8_t)MT_ACQ_SINGLE) {
        return mt_acq_stop(acq);
    }
    begin_capture(acq, now_ms);
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_stop(mt_acq_t *acq)
{
    if (acq == NULL) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    reset_capture(acq);
    acq->state = MT_ACQ_STOPPED;
    return MT_ACQ_OK;
}

mt_acq_result_t mt_acq_fail(mt_acq_t *acq, mt_acq_fault_t fault)
{
    if (acq == NULL || fault <= MT_ACQ_FAULT_NONE || fault > MT_ACQ_FAULT_DEADLINE) {
        return MT_ACQ_INVALID_ARGUMENT;
    }
    reset_capture(acq);
    acq->fault = fault;
    acq->state = MT_ACQ_ERROR;
    return MT_ACQ_OK;
}
