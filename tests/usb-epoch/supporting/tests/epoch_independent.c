#include "mt_usb_epoch.h"
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t checks;
static uint64_t transitions;
static const char *context;
#define CHECK(x) do { ++checks; if (!(x)) { \
    fprintf(stderr, "FAIL %s:%d (%s): %s\n", __FILE__, __LINE__, context, #x); \
    exit(EXIT_FAILURE); } } while (0)

static uint32_t open_epoch(mt_usb_epoch *s) {
    uint32_t e = mt_usb_epoch_reset(s);
    CHECK(e != 0);
    CHECK(mt_usb_epoch_enqueued(s, e, 1, 1));
    CHECK(mt_usb_epoch_cleaned(s, e));
    CHECK(mt_usb_epoch_reset_done(s, e));
    CHECK(mt_usb_epoch_configured(s, e));
    CHECK(mt_usb_epoch_app_io(s, e));
    return e;
}
static void closed(const mt_usb_epoch *s, uint32_t e) {
    CHECK(!mt_usb_epoch_core_io(s, e));
    CHECK(!mt_usb_epoch_app_io(s, e));
}
static void stale_is_inert(mt_usb_epoch *s, uint32_t old) {
    mt_usb_epoch before = *s;
    CHECK(!mt_usb_epoch_enqueued(s, old, 1, 1));
    CHECK(!mt_usb_epoch_enqueued(s, old, 0, 0));
    CHECK(!mt_usb_epoch_cleaned(s, old));
    CHECK(!mt_usb_epoch_dispatch(s, old, 0));
    CHECK(!mt_usb_epoch_dispatch(s, old, 1));
    CHECK(!mt_usb_epoch_reset_done(s, old));
    CHECK(!mt_usb_epoch_configured(s, old));
    CHECK(!mt_usb_epoch_fail(s, old, MT_USB_EPOCH_SUBMIT_FAILED));
    CHECK(!mt_usb_epoch_fail(s, old, MT_USB_EPOCH_COMPLETION_FAILED));
    CHECK(!mt_usb_epoch_fail(s, old, MT_USB_EPOCH_DECONFIGURED));
    CHECK(!mt_usb_epoch_suspend(s, old));
    CHECK(!mt_usb_epoch_resume(s, old, 1));
    closed(s, old);
    CHECK(memcmp(s, &before, sizeof(before)) == 0);
}
static void directed(void) {
    mt_usb_epoch s, before;
    uint32_t e, old, p, q;
    context = "initial and reset-before-enqueue";
    mt_usb_epoch_init(&s);
    stale_is_inert(&s, 0);
    e = open_epoch(&s);
    old = e;
    e = mt_usb_epoch_reset(&s);
    CHECK(e == old + 1);
    closed(&s, old);
    closed(&s, e);
    CHECK(!mt_usb_epoch_dispatch(&s, e, 1));
    CHECK(!mt_usb_epoch_configured(&s, e));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(mt_usb_epoch_enqueued(&s, e, 0, 1));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(mt_usb_epoch_dispatch(&s, e, 1));
    CHECK(!mt_usb_epoch_dispatch(&s, e, 0));
    CHECK(!mt_usb_epoch_configured(&s, e));
    CHECK(mt_usb_epoch_reset_done(&s, e));
    CHECK(mt_usb_epoch_core_io(&s, e));
    CHECK(!mt_usb_epoch_app_io(&s, e));
    CHECK(mt_usb_epoch_configured(&s, e));
    CHECK(mt_usb_epoch_app_io(&s, e));

    context = "cleanup required before reset dispatch";
    e = mt_usb_epoch_reset(&s);
    CHECK(mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(!mt_usb_epoch_dispatch(&s, e, 1));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(!mt_usb_epoch_configured(&s, e));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(mt_usb_epoch_reset_done(&s, e));
    CHECK(mt_usb_epoch_configured(&s, e));

    context = "duplicates and late accepted outcomes cannot reset state";
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(!mt_usb_epoch_dispatch(&s, e, 1));
    CHECK(mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(!mt_usb_epoch_dispatch(&s, e, 1));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(mt_usb_epoch_configured(&s, e));
    CHECK(mt_usb_epoch_app_io(&s, e));

    context = "lost current enqueue poisons even after configuration";
    CHECK(!mt_usb_epoch_enqueued(&s, e, 0, 0));
    CHECK(s.queue_losses == 1);
    CHECK(s.faults == MT_USB_EPOCH_QUEUE_LOSS);
    closed(&s, e);
    CHECK(!mt_usb_epoch_dispatch(&s, e, 0));
    CHECK(!mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(!mt_usb_epoch_configured(&s, e));
    CHECK(!mt_usb_epoch_suspend(&s, e));
    CHECK(!mt_usb_epoch_resume(&s, e, 1));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    closed(&s, e);
    old = e;
    e = mt_usb_epoch_reset(&s);
    CHECK(!mt_usb_epoch_enqueued(&s, e, 1, 0));
    CHECK(s.queue_losses == 2);
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(!mt_usb_epoch_enqueued(&s, e, 1, 1));
    closed(&s, e);
    e = open_epoch(&s);
    CHECK(s.queue_losses == 2);
    stale_is_inert(&s, old);
    stale_is_inert(&s, e - 1);
    CHECK(mt_usb_epoch_app_io(&s, e));

    context = "reset interleavings invalidate all old receipts";
    old = mt_usb_epoch_reset(&s);
    CHECK(mt_usb_epoch_enqueued(&s, old, 1, 1));
    CHECK(mt_usb_epoch_cleaned(&s, old));
    CHECK(mt_usb_epoch_dispatch(&s, old, 1));
    e = mt_usb_epoch_reset(&s); /* reset while old handler is executing */
    stale_is_inert(&s, old);
    CHECK(mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(mt_usb_epoch_reset_done(&s, e));
    old = e;
    e = mt_usb_epoch_reset(&s); /* reset before config receipt */
    CHECK(!mt_usb_epoch_configured(&s, old));
    closed(&s, e);

    context = "sticky fault categories and deconfiguration";
    e = open_epoch(&s);
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_SUBMIT_FAILED));
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_COMPLETION_FAILED));
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_DECONFIGURED));
    CHECK(s.io_failures == 2);
    CHECK(s.faults == (MT_USB_EPOCH_SUBMIT_FAILED |
                      MT_USB_EPOCH_COMPLETION_FAILED | MT_USB_EPOCH_DECONFIGURED));
    closed(&s, e);
    CHECK(!mt_usb_epoch_configured(&s, e));
    e = open_epoch(&s);
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_DECONFIGURED));
    CHECK(s.io_failures == 2);
    CHECK(!mt_usb_epoch_configured(&s, e));
    closed(&s, e);

    context = "soft serial prevents stale acknowledgment";
    e = open_epoch(&s);
    p = mt_usb_epoch_suspend(&s, e);
    CHECK(p == 1);
    closed(&s, e);
    CHECK(!mt_usb_epoch_dispatch(&s, e, 0)); /* held current events must be deferred */
    CHECK(!mt_usb_epoch_configured(&s, e));
    q = mt_usb_epoch_suspend(&s, e);
    CHECK(q == p + 1);
    CHECK(!mt_usb_epoch_resume(&s, e, p));
    closed(&s, e);
    CHECK(mt_usb_epoch_resume(&s, e, q));
    CHECK(!mt_usb_epoch_resume(&s, e, q));
    CHECK(mt_usb_epoch_app_io(&s, e));
    old = e;
    e = open_epoch(&s);
    CHECK(mt_usb_epoch_suspend(&s, e) == 1);
    CHECK(!mt_usb_epoch_resume(&s, old, 1));
    closed(&s, e);
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_SUBMIT_FAILED));
    CHECK(!mt_usb_epoch_resume(&s, e, 1));
    closed(&s, e);

    context = "disconnect never substitutes for bus reset";
    old = e;
    e = mt_usb_epoch_disconnect(&s);
    CHECK(e == old + 1);
    CHECK(s.faults == MT_USB_EPOCH_DISCONNECTED);
    CHECK(!mt_usb_epoch_enqueued(&s, e, 1, 1));
    CHECK(mt_usb_epoch_cleaned(&s, e));
    CHECK(!mt_usb_epoch_reset_done(&s, e));
    CHECK(!mt_usb_epoch_configured(&s, e));
    closed(&s, e);
    old = e;
    e = open_epoch(&s);
    stale_is_inert(&s, old);

    context = "soft counter saturation fails closed and reset recovers";
    s.soft_serial = UINT32_MAX - 1;
    CHECK(mt_usb_epoch_suspend(&s, e) == UINT32_MAX);
    CHECK(mt_usb_epoch_resume(&s, e, UINT32_MAX));
    CHECK(mt_usb_epoch_app_io(&s, e));
    CHECK(!mt_usb_epoch_suspend(&s, e));
    CHECK(s.soft_serial == UINT32_MAX);
    CHECK(s.faults == MT_USB_EPOCH_SOFT_EXHAUSTED);
    closed(&s, e);
    CHECK(!mt_usb_epoch_resume(&s, e, UINT32_MAX));
    e = open_epoch(&s);
    CHECK(mt_usb_epoch_suspend(&s, e) == 1);
    CHECK(mt_usb_epoch_resume(&s, e, 1));

    context = "diagnostics saturate, including repeated notifications";
    s.queue_losses = UINT32_MAX - 1;
    s.io_failures = UINT32_MAX - 1;
    CHECK(!mt_usb_epoch_enqueued(&s, e, 0, 0));
    CHECK(!mt_usb_epoch_enqueued(&s, e, 0, 0));
    CHECK(s.queue_losses == UINT32_MAX);
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_SUBMIT_FAILED));
    CHECK(mt_usb_epoch_fail(&s, e, MT_USB_EPOCH_COMPLETION_FAILED));
    CHECK(s.io_failures == UINT32_MAX);
    e = open_epoch(&s);
    CHECK(s.queue_losses == UINT32_MAX && s.io_failures == UINT32_MAX);

    context = "hard max token is valid once, then permanent shutdown";
    mt_usb_epoch_init(&s);
    s.current = UINT32_MAX - 1;
    e = open_epoch(&s);
    CHECK(e == UINT32_MAX);
    CHECK(!mt_usb_epoch_reset(&s));
    CHECK(s.current == UINT32_MAX);
    CHECK(s.exhausted == 1);
    CHECK(s.faults == MT_USB_EPOCH_EXHAUSTED);
    closed(&s, e);
    before = s;
    CHECK(!mt_usb_epoch_disconnect(&s));
    CHECK(!mt_usb_epoch_reset(&s));
    stale_is_inert(&s, e);
    CHECK(memcmp(&s, &before, sizeof(s)) == 0);
    mt_usb_epoch_init(&s);
    s.current = UINT32_MAX;
    CHECK(!mt_usb_epoch_disconnect(&s));
    CHECK(s.exhausted);

    context = "invalid parameters are no-ops";
    mt_usb_epoch_init(NULL);
    CHECK(!mt_usb_epoch_reset(NULL));
    CHECK(!mt_usb_epoch_disconnect(NULL));
    stale_is_inert(&(mt_usb_epoch){0}, UINT32_MAX);
    CHECK(!mt_usb_epoch_enqueued(NULL, 1, 1, 1));
    CHECK(!mt_usb_epoch_cleaned(NULL, 1));
    CHECK(!mt_usb_epoch_dispatch(NULL, 1, 1));
    CHECK(!mt_usb_epoch_reset_done(NULL, 1));
    CHECK(!mt_usb_epoch_configured(NULL, 1));
    CHECK(!mt_usb_epoch_core_io(NULL, 1));
    CHECK(!mt_usb_epoch_app_io(NULL, 1));
    CHECK(!mt_usb_epoch_fail(NULL, 1, MT_USB_EPOCH_SUBMIT_FAILED));
    CHECK(!mt_usb_epoch_suspend(NULL, 1));
    CHECK(!mt_usb_epoch_resume(NULL, 1, 1));
    mt_usb_epoch_init(&s);
    e = open_epoch(&s);
    before = s;
    CHECK(!mt_usb_epoch_enqueued(&s, e, 2, 1));
    CHECK(!mt_usb_epoch_enqueued(&s, e, 1, 2));
    CHECK(!mt_usb_epoch_enqueued(&s, e, UINT32_MAX, UINT32_MAX));
    CHECK(!mt_usb_epoch_dispatch(&s, e, 2));
    CHECK(!mt_usb_epoch_resume(&s, e, 0));
    CHECK(!mt_usb_epoch_resume(&s, e, 1));
    for (uint32_t reason = 0; reason < 128; ++reason) {
        if (reason == MT_USB_EPOCH_SUBMIT_FAILED ||
            reason == MT_USB_EPOCH_COMPLETION_FAILED ||
            reason == MT_USB_EPOCH_DECONFIGURED) continue;
        CHECK(!mt_usb_epoch_fail(&s, e, reason));
    }
    CHECK(!mt_usb_epoch_fail(&s, e, UINT32_MAX));
    stale_is_inert(&s, 0);
    stale_is_inert(&s, e + 1);
    CHECK(memcmp(&s, &before, sizeof(s)) == 0);
    puts("directed: PASS (12 contract scenario groups)");
}

/* Reference representation uses received evidence and a phase, rather than
 * copying the implementation's epoch-valued receipts. It is an oracle for
 * SERIAL call sequences only, not a concurrency, queue or hardware model. */
enum phase { COLD, RESET_SEEN, RESET_EXECUTED };
typedef struct {
    uint32_t identity, latest_pause, outstanding_pause, faults;
    uint64_t drops, failures;
    enum phase phase;
    unsigned dead, cleanup, queued, configured;
} reference;
enum action { RESET, DISCONNECT, ENQUEUE_RESET, ENQUEUE_OTHER, LOSE_RESET,
    LOSE_OTHER, CLEAN, DONE, CONFIGURE, SUBMIT_FAIL, COMPLETION_FAIL,
    DECONFIGURE, SUSPEND, RESUME_LAST, RESUME_PREVIOUS, ACTION_COUNT };
static uint64_t action_seen[ACTION_COUNT];
static uint64_t permitted_io, denied_io;
static int live(const reference *m, uint32_t t) {
    return t != 0 && t == m->identity && !m->dead;
}
static int eligible(const reference *m, uint32_t t) {
    return live(m, t) && !m->faults && m->cleanup;
}
static uint32_t model(reference *m, enum action a, uint32_t t) {
    int valid = live(m, t);
    if (a == RESET || a == DISCONNECT) {
        if (m->dead) return 0;
        if (m->identity == UINT32_MAX) {
            m->dead = 1;
            m->faults |= MT_USB_EPOCH_EXHAUSTED;
            return 0;
        }
        ++m->identity;
        m->latest_pause = m->outstanding_pause = 0;
        m->cleanup = m->queued = m->configured = 0;
        m->phase = a == RESET ? RESET_SEEN : COLD;
        m->faults = a == RESET ? 0u : MT_USB_EPOCH_DISCONNECTED;
        return m->identity;
    }
    if (!valid) return 0;
    switch (a) {
    case ENQUEUE_RESET:
    case ENQUEUE_OTHER:
        if (m->faults || (a == ENQUEUE_RESET && m->phase == COLD)) return 0;
        if (a == ENQUEUE_RESET) m->queued = 1;
        return 1;
    case LOSE_RESET:
    case LOSE_OTHER:
        ++m->drops;
        m->faults |= MT_USB_EPOCH_QUEUE_LOSS;
        return 0;
    case CLEAN:
        m->cleanup = 1;
        return 1;
    case DONE:
        if (m->faults || !m->cleanup || !m->queued || m->phase != RESET_SEEN) return 0;
        m->phase = RESET_EXECUTED;
        return 1;
    case CONFIGURE:
        if (m->faults || !m->cleanup || m->phase != RESET_EXECUTED || m->outstanding_pause) return 0;
        m->configured = 1;
        return 1;
    case SUBMIT_FAIL:
    case COMPLETION_FAIL:
    case DECONFIGURE:
        if (a != DECONFIGURE) ++m->failures;
        m->faults |= a == SUBMIT_FAIL ? MT_USB_EPOCH_SUBMIT_FAILED :
                     a == COMPLETION_FAIL ? MT_USB_EPOCH_COMPLETION_FAILED : MT_USB_EPOCH_DECONFIGURED;
        return 1;
    case SUSPEND:
        if (m->faults) return 0;
        if (m->latest_pause == UINT32_MAX) {
            m->faults |= MT_USB_EPOCH_SOFT_EXHAUSTED;
            return 0;
        }
        ++m->latest_pause;
        m->outstanding_pause = m->latest_pause;
        return m->outstanding_pause;
    case RESUME_LAST:
    case RESUME_PREVIOUS: {
        uint32_t serial = a == RESUME_LAST ? m->latest_pause : m->latest_pause - (m->latest_pause != 0);
        if (m->faults || !serial || serial != m->outstanding_pause) return 0;
        m->outstanding_pause = 0;
        return 1;
    }
    case RESET: case DISCONNECT: case ACTION_COUNT: break;
    }
    abort();
}
static uint32_t execute(mt_usb_epoch *s, enum action a, uint32_t t) {
    switch (a) {
    case RESET: return mt_usb_epoch_reset(s);
    case DISCONNECT: return mt_usb_epoch_disconnect(s);
    case ENQUEUE_RESET: return (uint32_t)mt_usb_epoch_enqueued(s, t, 1, 1);
    case ENQUEUE_OTHER: return (uint32_t)mt_usb_epoch_enqueued(s, t, 0, 1);
    case LOSE_RESET: return (uint32_t)mt_usb_epoch_enqueued(s, t, 1, 0);
    case LOSE_OTHER: return (uint32_t)mt_usb_epoch_enqueued(s, t, 0, 0);
    case CLEAN: return (uint32_t)mt_usb_epoch_cleaned(s, t);
    case DONE: return (uint32_t)mt_usb_epoch_reset_done(s, t);
    case CONFIGURE: return (uint32_t)mt_usb_epoch_configured(s, t);
    case SUBMIT_FAIL: return (uint32_t)mt_usb_epoch_fail(s, t, MT_USB_EPOCH_SUBMIT_FAILED);
    case COMPLETION_FAIL: return (uint32_t)mt_usb_epoch_fail(s, t, MT_USB_EPOCH_COMPLETION_FAILED);
    case DECONFIGURE: return (uint32_t)mt_usb_epoch_fail(s, t, MT_USB_EPOCH_DECONFIGURED);
    case SUSPEND: return mt_usb_epoch_suspend(s, t);
    case RESUME_LAST: return (uint32_t)mt_usb_epoch_resume(s, t, s->soft_serial);
    case RESUME_PREVIOUS: return (uint32_t)mt_usb_epoch_resume(s, t, s->soft_serial - (s->soft_serial != 0));
    case ACTION_COUNT: break;
    }
    abort();
}
static uint32_t saturated(uint64_t x) { return x > UINT32_MAX ? UINT32_MAX : (uint32_t)x; }
static void compare(const mt_usb_epoch *s, const reference *m) {
    CHECK(s->current == m->identity);
    CHECK(s->queued_reset == (m->queued ? m->identity : 0));
    CHECK(s->processed_reset == (m->phase == RESET_EXECUTED ? m->identity : 0));
    CHECK(s->cleaned == (m->cleanup ? m->identity : 0));
    CHECK(s->configured == (m->configured ? m->identity : 0));
    CHECK(s->soft_serial == m->latest_pause);
    CHECK(s->pending_soft == m->outstanding_pause);
    CHECK(s->faults == m->faults);
    CHECK(s->queue_losses == saturated(m->drops));
    CHECK(s->io_failures == saturated(m->failures));
    CHECK(s->reset_candidate == (unsigned)(m->phase != COLD));
    CHECK(s->exhausted == m->dead);
    uint32_t tokens[] = {0, m->identity, m->identity - (m->identity != 0),
                        m->identity == UINT32_MAX ? 1 : m->identity + 1, UINT32_MAX};
    for (size_t i = 0; i < sizeof(tokens) / sizeof(tokens[0]); ++i) {
        uint32_t t = tokens[i];
        int dispatch = eligible(m, t) && m->phase == RESET_EXECUTED && !m->outstanding_pause;
        int core = dispatch && !m->outstanding_pause;
        CHECK(mt_usb_epoch_dispatch(s, t, 1) == (eligible(m, t) && m->phase == RESET_SEEN && m->queued));
        CHECK(mt_usb_epoch_dispatch(s, t, 0) == dispatch);
        CHECK(mt_usb_epoch_core_io(s, t) == core);
        CHECK(mt_usb_epoch_app_io(s, t) == (core && m->configured));
        if (core && m->configured) ++permitted_io; else ++denied_io;
    }
}
static void step(mt_usb_epoch *s, reference *m, enum action a, uint32_t token) {
    uint32_t expected = model(m, a, token);
    uint32_t actual = execute(s, a, token);
    ++transitions;
    ++action_seen[a];
    if (actual != expected) fprintf(stderr, "transition=%" PRIu64 " action=%d token=%" PRIu32 " expected=%" PRIu32 " actual=%" PRIu32 "\n", transitions, (int)a, token, expected, actual);
    CHECK(actual == expected);
    compare(s, m);
}
static void enumerate(mt_usb_epoch s, reference m, unsigned remaining) {
    if (!remaining) return;
    for (int a = RESET; a < ACTION_COUNT; ++a) {
        mt_usb_epoch child = s;
        reference reference_child = m;
        step(&child, &reference_child, (enum action)a, s.current);
        enumerate(child, reference_child, remaining - 1);
    }
}
static uint64_t random_state = UINT64_C(0x6d6f746f72757362);
static uint32_t random_u32(void) {
    random_state ^= random_state >> 12;
    random_state ^= random_state << 25;
    random_state ^= random_state >> 27;
    return (uint32_t)((random_state * UINT64_C(2685821657736338717)) >> 32);
}
static void reference_runs(void) {
    mt_usb_epoch s;
    reference m = {0};
    context = "exhaustive model walks, cold and configured origins, depth 5";
    mt_usb_epoch_init(&s);
    compare(&s, &m);
    enumerate(s, m, 5);
    step(&s, &m, RESET, 0);
    step(&s, &m, ENQUEUE_RESET, s.current);
    step(&s, &m, CLEAN, s.current);
    step(&s, &m, DONE, s.current);
    step(&s, &m, CONFIGURE, s.current);
    enumerate(s, m, 5);
    printf("exhaustive: PASS (%" PRIu64 " transitions, all length <= 5 sequences over 15 actions from 2 origins)\n", transitions);
    context = "seeded long random walks with stale, future, zero and current tokens";
    uint64_t start = transitions;
    for (unsigned trial = 0; trial < 1000; ++trial) {
        mt_usb_epoch_init(&s);
        m = (reference){0};
        /* Include saturation origins as explicit boundary injection, not natural histories. */
        if (trial % 4 == 0) s.current = m.identity = UINT32_MAX - 2;
        if (trial % 4 == 1) {
            step(&s, &m, RESET, 0);
            step(&s, &m, ENQUEUE_RESET, s.current);
            step(&s, &m, CLEAN, s.current);
            step(&s, &m, DONE, s.current);
            step(&s, &m, CONFIGURE, s.current);
            s.soft_serial = m.latest_pause = UINT32_MAX - 2;
        }
        if (trial % 4 == 2) {
            s.queue_losses = UINT32_MAX - 2;
            s.io_failures = UINT32_MAX - 2;
            m.drops = m.failures = UINT32_MAX - 2;
        }
        for (unsigned i = 0; i < 1000; ++i) {
            uint32_t t = s.current;
            switch (random_u32() % 8) {
            case 0: t = 0; break;
            case 1: t = t ? t - 1 : UINT32_MAX; break;
            case 2: t = t == UINT32_MAX ? 1 : t + 1; break;
            default: break;
            }
            step(&s, &m, (enum action)(random_u32() % ACTION_COUNT), t);
        }
    }
    printf("random: PASS (%" PRIu64 " transitions; seed 0x6d6f746f72757362; 1000 walks; boundary-injected origins labeled)\n", transitions - start);
    CHECK(permitted_io > 0 && denied_io > 0);
    for (int a = RESET; a < ACTION_COUNT; ++a) CHECK(action_seen[a] > 0);
    printf("reference totals: transitions=%" PRIu64 " permitted_app_queries=%" PRIu64 " denied_app_queries=%" PRIu64 "\n", transitions, permitted_io, denied_io);
}
int main(int argc, char **argv) {
    directed();
    if (argc < 2 || strcmp(argv[1], "--directed") != 0) reference_runs();
    printf("PASS checks=%" PRIu64 " state_size=%zu\n", checks, sizeof(mt_usb_epoch));
    return EXIT_SUCCESS;
}
