#include "mt_live_device.h"
#include <string.h>

#define RESERVE UINT32_C(0xffffff00)
#define LIMIT_MS UINT64_C(500)
enum { HELLO=1, CAPS=2, STOP=5, START=8, HELLO_ACK=129, CAPS_REPLY=130,
       STOP_ACK=133, START_ACK=136, DATA=152, END=153, ERROR_REPLY=255 };

static uint16_t r16(const uint8_t *p) { return (uint16_t)((uint16_t)p[0] | (uint16_t)((uint16_t)p[1]<<8)); }
static uint32_t r32(const uint8_t *p) { uint32_t v=0; unsigned i; for(i=0;i<4;i++)v|=(uint32_t)p[i]<<(8u*i); return v; }
static uint64_t r64(const uint8_t *p) { return (uint64_t)r32(p)|((uint64_t)r32(p+4)<<32); }
static void w16(uint8_t *p,uint16_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); }
static void w32(uint8_t *p,uint32_t v) { unsigned i; for(i=0;i<4;i++)p[i]=(uint8_t)(v>>(8u*i)); }
static void w64(uint8_t *p,uint64_t v) { unsigned i; for(i=0;i<8;i++)p[i]=(uint8_t)(v>>(8u*i)); }
static void cause(mt_live_device *d,uint16_t reason) { if(!d->first_reason)d->first_reason=reason; }
static void local_cause(mt_live_device *d,unsigned reason) { if(!d->local_error)d->local_error=reason; }
static void clear_wants(mt_live_device *d) {
    d->want_caps=d->want_start=d->want_error=d->want_end=d->want_ack=0;
}
static void abandon_tx(mt_live_device *d) {
    if(d->tx_offset)d->needs_delimiter=1;
    d->tx_length=d->tx_offset=0;
    clear_wants(d);
}
static void raw_barrier(mt_live_device *d) {
    mt_live_snapshot s;
    memset(&s,0,sizeof s);
    d->io.quiesce(d->io.context,&s);
    d->hardware_off=(s.hardware_off==1u);
    /* Raw recovery discards tails (including a legitimate cached old tail),
     * but retains the bounded acquisition diagnostic before later failures. */
    if(s.acquisition_reason>0u && s.acquisition_reason<=6u)cause(d,s.acquisition_reason);
}
static void recover(mt_live_device *d,unsigned reason) {
    local_cause(d,reason);
    if(d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING)
        raw_barrier(d);
    mt_sample_queue_seal(&d->queue);
    abandon_tx(d);
    d->stop_budget=d->hello_budget=d->inherited_budget=0;
    d->state=MT_LIVE_FAULT;
}
static int now(mt_live_device *d,uint64_t *value) {
    uint64_t n=d->io.clock_ms(d->io.context);
    if(n<d->last_time || n>(uint64_t)INT64_MAX-LIMIT_MS) {
        d->clock_failed=1;
        if(d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING)cause(d,3);
        recover(d,MT_LIVE_ERR_CLOCK); return 0;
    }
    d->last_time=n; *value=n; return 1;
}
static uint64_t deadline(const mt_live_device *d) {
    uint64_t n=UINT64_MAX;
    if(d->stop_budget && d->stop_deadline<n)n=d->stop_deadline;
    if(d->hello_budget && d->hello_deadline<n)n=d->hello_deadline;
    if(d->inherited_budget && d->inherited_deadline<n)n=d->inherited_deadline;
    return n;
}
static int expire(mt_live_device *d,uint64_t n) {
    if(n>=deadline(d)) { recover(d,MT_LIVE_ERR_DEADLINE); return 0; }
    return 1;
}
static void error_reply(mt_live_device *d,uint8_t type,uint8_t code,uint32_t request) {
    if(d->want_error || d->want_caps || d->want_start) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
    d->error_type=type; d->error_code=code; d->error_request=request; d->want_error=1;
}

/* Validate all snapshot fields before publishing any tail sample. A known
 * produced endpoint can exceed the retained prefix after a certified loss. */
static int snapshot_valid(const mt_live_device *d,const mt_live_snapshot *s) {
    uint64_t end=d->queue.next_index;
    unsigned b,i;
    if(s->hardware_off>1u || s->produced_known>1u || s->tail_count>2u ||
       s->acquisition_reason>6u)return 0;
    for(b=0;b<s->tail_count;b++) {
        const mt_live_tail *t=&s->tail[b];
        if(!t->count || t->count>128u || (b+1u<s->tail_count && t->count!=128u))return 0;
        if(b==0u) {
            if(t->first_index<end)return 0;
            if(!d->queue.sealed && t->first_index!=end)return 0;
        } else if(t->first_index!=end)return 0;
        if(UINT64_MAX-t->first_index<t->count)return 0;
        end=t->first_index+t->count;
        for(i=0;i<t->count;i++)if(t->codes[i]>4095u)return 0;
    }
    if(s->produced_known && s->produced_end<end)return 0;
    if(!s->produced_known && s->produced_end!=0u)return 0;
    return 1;
}
static void terminate(mt_live_device *d,uint16_t reason,uint64_t n) {
    mt_live_snapshot s;
    unsigned b;
    uint64_t after;
    int valid_snapshot;
    if(reason)cause(d,reason);
    if(d->state==MT_LIVE_DRAINING || d->state==MT_LIVE_TERMINAL)return;
    if(d->state!=MT_LIVE_STARTING && d->state!=MT_LIVE_STREAMING) {
        recover(d,MT_LIVE_ERR_PROTOCOL); return;
    }
    if(!now(d,&n))return;
    d->stop_budget=1; d->stop_deadline=n+LIMIT_MS;
    d->state=MT_LIVE_DRAINING;
    memset(&s,0,sizeof s);
    d->io.quiesce(d->io.context,&s);
    d->hardware_off=(s.hardware_off==1u);
    valid_snapshot=snapshot_valid(d,&s);
    /* Quiesce observed its acquisition cause before the following clock read.
     * Preserve diagnostics now; tail/prefix publication still waits for time. */
    if(!valid_snapshot)cause(d,2);
    else if(s.acquisition_reason)cause(d,s.acquisition_reason);
    if(!now(d,&after) || !expire(d,after))return;
    if(!valid_snapshot) {
        cause(d,2); d->produced_known=0; d->produced_end=0;
    } else {
        if(s.acquisition_reason)cause(d,s.acquisition_reason);
        d->produced_known=s.produced_known; d->produced_end=s.produced_end;
        /* A sealed queue already records a gap; never append across that gap. */
        if(!d->queue.sealed) {
            for(b=0;b<s.tail_count;b++) {
                mt_sample_queue_result q=mt_sample_queue_push(&d->queue,s.tail[b].first_index,
                    s.tail[b].codes,s.tail[b].count,b+1u==s.tail_count);
                if(q!=MT_SAMPLE_QUEUE_OK) { cause(d,q==MT_SAMPLE_QUEUE_FULL?1u:2u); break; }
            }
        }
    }
    mt_sample_queue_seal(&d->queue);
    if(!d->hardware_off) {
        cause(d,5);
        /* A still-unquiesced producer has no certified final endpoint. */
        d->produced_known=0; d->produced_end=0;
    }
    d->want_end=1;
}
static uint16_t flags(const mt_live_device *d) {
    uint16_t f=0;
    if(!d->produced_known)f=3;
    else if(d->produced_end!=d->prefix_end)f=2;
    if(!d->hardware_off)f=(uint16_t)(f|4u);
    return f;
}
static void begin_hello(mt_live_device *d,uint64_t nonce,uint64_t n) {
    uint64_t inherited=deadline(d),after;
    int isolation;
    if(nonce==d->nonce || nonce==d->attempted_nonce)return;
    if(!now(d,&n) || !expire(d,n))return;
    d->attempted_nonce=nonce; d->pending_nonce=nonce;
    if(d->tx_offset && inherited!=UINT64_MAX) {
        d->inherited_budget=1; d->inherited_deadline=inherited;
    } else if(!d->tx_offset)d->inherited_budget=0;
    if(!d->tx_offset)d->tx_length=0;
    clear_wants(d);
    d->stop_budget=0;
    d->hello_budget=1; d->hello_deadline=n+LIMIT_MS;
    d->state=MT_LIVE_HELLO_PENDING;
    raw_barrier(d);
    if(!now(d,&after) || !expire(d,after))return;
    if(!d->hardware_off) { recover(d,MT_LIVE_ERR_CALLBACK); return; }
    isolation=d->io.isolate_tx(d->io.context);
    if(!now(d,&after) || !expire(d,after))return;
    if(isolation!=1) { recover(d,MT_LIVE_ERR_TRANSPORT); return; }
}
static void accept_frame(mt_live_device *d,const uint8_t *frame,size_t length,uint64_t n) {
    mt_wire_message m;
    mt_wire_result result=mt_wire_decode_revision(frame,length,0,2,&m);
    if(result==MT_WIRE_VERSION) {
        d->incompatible_seen=1;
        recover(d,MT_LIVE_ERR_INCOMPATIBLE);
        return;
    }
    if(result!=MT_WIRE_OK)return;
    if(m.type>=128u) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
    if(m.type!=HELLO && m.type!=CAPS && m.type!=START && m.type!=STOP &&
       (d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING || d->state==MT_LIVE_DRAINING)) {
        recover(d,MT_LIVE_ERR_PROTOCOL); return;
    }
    if(m.type==HELLO) {
        if(m.session || m.sequence!=1u || m.request!=1u || m.payload_length!=8u || !r64(m.payload))return;
        begin_hello(d,r64(m.payload),n); return;
    }
    if(m.session!=d->nonce || !d->nonce)return;
    if(d->state==MT_LIVE_NO_SESSION || d->state==MT_LIVE_HELLO_PENDING ||
       d->state==MT_LIVE_FAULT || d->state==MT_LIVE_NEEDS_HELLO)return;
    if(d->have_previous && length==d->previous_length &&
       memcmp(frame,d->previous,length)==0)return;
    if(d->rx_exhausted || m.sequence!=d->rx_next || m.request<=d->last_request || !m.request) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
    if(m.type!=STOP && (m.sequence>=RESERVE || m.request>=RESERVE)) {
        if(d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING)terminate(d,4,n);
        else recover(d,MT_LIVE_ERR_PROTOCOL);
        return;
    }
    memcpy(d->previous,frame,length); d->previous_length=length; d->have_previous=1;
    if(m.sequence==UINT32_MAX)d->rx_exhausted=1;
    else d->rx_next=m.sequence+1u;
    d->last_request=m.request;
    if(d->state==MT_LIVE_DRAINING && m.type!=STOP) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
    if(m.type==CAPS) {
        if(d->state!=MT_LIVE_STOPPED) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
        if(m.payload_length) { error_reply(d,m.type,1,m.request); return; }
        if(d->want_caps || d->want_error) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
        d->want_caps=1; d->caps_request=m.request; return;
    }
    if(m.type==START) {
        mt_live_snapshot s;
        uint64_t after;
        int started;
        if(d->state!=MT_LIVE_STOPPED) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
        if(!d->caps_ready || d->want_caps || d->want_error) {
            error_reply(d,m.type,2,m.request); return;
        }
        if(m.payload_length!=4u || r16(m.payload)!=1u || r16(m.payload+2)!=0u) {
            error_reply(d,m.type,1,m.request); return;
        }
        if(d->tx_next>=RESERVE) { recover(d,MT_LIVE_ERR_PROTOCOL); return; }
        d->generation=m.request;
        d->want_start=1; /* Reserve the response before any hardware call. */
        memset(&s,0,sizeof s); d->io.quiesce(d->io.context,&s);
        d->hardware_off=(s.hardware_off==1u);
        if(!now(d,&after))return;
        d->state=MT_LIVE_STARTING;
        if(d->hardware_off) {
            d->hardware_off=0;
            started=d->io.start(d->io.context,d->generation);
        } else started=0;
        if(!now(d,&after))return;
        if(started!=1) {
            d->want_start=0;
            /* Failed-start cleanup is a failed termination, never a stream END.
             * Bound its barrier and sole reserved error before calling cleanup. */
            d->stop_budget=1; d->stop_deadline=after+LIMIT_MS;
            d->state=MT_LIVE_NEEDS_HELLO;
            raw_barrier(d);
            if(!now(d,&after) || !expire(d,after))return;
            d->error_type=START; d->error_code=4; d->error_request=m.request; d->want_error=1;
            return;
        }
        d->hardware_off=0; mt_sample_queue_init(&d->queue,0);
        d->state=MT_LIVE_STARTING; return;
    }
    if(m.type==STOP) {
        if(m.payload_length!=4u || r32(m.payload)!=d->generation || !d->generation || d->stop_seen ||
           (d->state!=MT_LIVE_STARTING && d->state!=MT_LIVE_STREAMING &&
            d->state!=MT_LIVE_DRAINING && d->state!=MT_LIVE_TERMINAL)) {
            recover(d,MT_LIVE_ERR_PROTOCOL); return;
        }
        d->stop_seen=1; d->stop_request=m.request;
        if(d->end_complete) {
            if(!d->hardware_off || (d->terminal_flags&4u)) { recover(d,MT_LIVE_ERR_CALLBACK); return; }
            if(!now(d,&n))return;
            d->stop_budget=1; d->stop_deadline=n+LIMIT_MS; d->want_ack=1;
        } else if(d->state==MT_LIVE_DRAINING) {
            if(d->tx_type==END && d->tx_length && !d->tx_offset)d->tx_length=0;
        } else terminate(d,0,n);
        return;
    }
    if(d->state==MT_LIVE_STOPPED)error_reply(d,m.type,5,m.request);
    else recover(d,MT_LIVE_ERR_PROTOCOL);
}

int mt_live_device_init(mt_live_device *d,const mt_live_io *io) {
    if(!d || !io || !io->clock_ms || !io->start || !io->quiesce || !io->write ||
       !io->isolate_tx || !io->transaction_bound_ms || io->transaction_bound_ms>=500u)return 0;
    memset(d,0,sizeof *d); d->io=*io; d->state=MT_LIVE_NO_SESSION;
    d->prefix_crc_state=UINT32_C(0xffffffff); mt_sample_queue_init(&d->queue,0);
    return 1;
}
int mt_live_device_receive(mt_live_device *d,const uint8_t *bytes,size_t length) {
    uint64_t n; size_t i;
    if(!d || (!bytes && length) || length>MT_WIRE_FRAME_MAX)return 0;
    if(!now(d,&n))return 0;
    (void)expire(d,n);
    for(i=0;i<length;i++) {
        uint8_t b=bytes[i];
        if(!b) {
            if(!d->rx_discarding && d->rx_used)accept_frame(d,d->rx,d->rx_used,n);
            d->rx_used=0; d->rx_discarding=0;
            if(!now(d,&n))return 0;
            (void)expire(d,n);
        } else if(!d->rx_discarding) {
            if(d->rx_used==sizeof d->rx) { d->rx_used=0; d->rx_discarding=1; }
            else d->rx[d->rx_used++]=b;
        }
    }
    return 1;
}
static uint32_t crc_codes(uint32_t crc,const uint16_t *p,uint16_t count) {
    unsigned i,b,k;
    for(i=0;i<count;i++)for(b=0;b<2;b++) {
        crc^=(uint8_t)(p[i]>>(8u*b));
        for(k=0;k<8;k++)crc=(crc>>1)^((crc&1u)?UINT32_C(0xedb88320):0u);
    }
    return crc;
}
static int build_tx(mt_live_device *d) {
    mt_wire_message m; mt_sample_block block;
    uint8_t type=0; uint32_t request=0;
    if(d->tx_length)return 1;
    memset(&m,0,sizeof m);
    m.session=d->nonce;
    if(d->state==MT_LIVE_HELLO_PENDING) {
        if(!d->hardware_off)return 0;
        if(d->needs_delimiter) {
            d->tx[0]=0; d->tx_length=1; d->tx_type=0; d->tx_offset=0; return 1;
        }
        type=HELLO_ACK; request=1; m.session=d->pending_nonce;
        m.payload_length=16; m.payload[1]=2; m.payload[2]=1; m.payload[3]=12;
        w16(m.payload+4,512); w16(m.payload+6,128); w32(m.payload+8,d->io.firmware_build); w32(m.payload+12,1);
    } else if(d->want_error) {
        type=ERROR_REPLY; request=d->error_request; m.payload_length=8;
        m.payload[0]=d->error_type; m.payload[1]=d->error_code; w32(m.payload+4,d->generation);
    } else if(d->want_caps) {
        type=CAPS_REPLY; request=d->caps_request; m.payload_length=24;
        w32(m.payload,1); w16(m.payload+4,1); m.payload[7]=12;
        w32(m.payload+8,50000); w32(m.payload+12,1); w16(m.payload+16,128); w32(m.payload+20,1);
    } else if(d->want_start) {
        type=START_ACK; request=d->generation; m.payload_length=16;
        w32(m.payload,d->generation); w32(m.payload+4,1);
    } else if((d->state==MT_LIVE_STREAMING || d->state==MT_LIVE_DRAINING) &&
              mt_sample_queue_peek(&d->queue,&block)==MT_SAMPLE_QUEUE_OK) {
        unsigned i;
        type=DATA; m.payload_length=(uint16_t)(20u+2u*block.count);
        w32(m.payload,d->generation); w32(m.payload+4,1); w64(m.payload+8,block.first_index); w16(m.payload+16,block.count);
        for(i=0;i<block.count;i++)w16(m.payload+20u+2u*i,block.samples[i]);
        d->tx_count=block.count; d->tx_first_index=block.first_index;
    } else if(d->want_end) {
        type=END; m.payload_length=40;
        w32(m.payload,d->generation); w32(m.payload+4,1); w64(m.payload+8,d->prefix_end);
        w64(m.payload+16,d->produced_known?d->produced_end:0);
        w32(m.payload+24,d->last_data_sequence); w32(m.payload+28,d->prefix_crc_state^UINT32_C(0xffffffff));
        w16(m.payload+32,d->first_reason); w16(m.payload+34,flags(d)); w32(m.payload+36,d->stop_request);
    } else if(d->want_ack) {
        type=STOP_ACK; request=d->stop_request; m.payload_length=20;
        w32(m.payload,d->generation); w64(m.payload+4,d->prefix_end); w32(m.payload+12,d->end_sequence); w32(m.payload+16,d->terminal_flags);
    } else return 0;
    m.type=type; m.request=request; m.sequence=(type==HELLO_ACK)?1u:d->tx_next;
    if(!m.sequence || (type!=HELLO_ACK && d->tx_exhausted)) { recover(d,MT_LIVE_ERR_PROTOCOL); return 0; }
    d->tx_sequence=m.sequence; d->tx_nonce=m.session; d->tx_type=type; d->tx_offset=0;
    if(mt_wire_encode_revision(&m,0,2,d->tx,sizeof d->tx,&d->tx_length)!=MT_WIRE_OK) {
        recover(d,MT_LIVE_ERR_ARGUMENT); return 0;
    }
    return 1;
}
static void commit_hello(mt_live_device *d) {
        d->nonce=d->pending_nonce; d->state=MT_LIVE_STOPPED;
        d->hello_budget=d->inherited_budget=d->stop_budget=0;
        d->rx_next=2; d->last_request=1; d->tx_next=2; d->have_previous=0;
        d->rx_exhausted=d->tx_exhausted=0;
        d->generation=0; d->caps_ready=0; d->first_reason=0; d->local_error=0; d->clock_failed=0;
        d->incompatible_seen=0;
        d->prefix_end=0; d->prefix_crc_state=UINT32_C(0xffffffff); d->last_data_sequence=0;
        d->produced_known=0; d->produced_end=0; d->stop_request=0; d->stop_seen=0;
        d->end_started=d->end_complete=0; d->terminal_flags=0;
        mt_sample_queue_init(&d->queue,0);
}
static void completed(mt_live_device *d) {
    uint8_t type=d->tx_type;
    if(type==DATA) {
        mt_sample_block b;
        if(mt_sample_queue_peek(&d->queue,&b)!=MT_SAMPLE_QUEUE_OK ||
           b.first_index!=d->tx_first_index || b.count!=d->tx_count) { recover(d,MT_LIVE_ERR_CALLBACK); return; }
        d->prefix_crc_state=crc_codes(d->prefix_crc_state,b.samples,b.count);
        d->prefix_end=b.first_index+b.count; d->last_data_sequence=d->tx_sequence;
        (void)mt_sample_queue_pop(&d->queue,b.first_index);
    }
    if(d->state==MT_LIVE_HELLO_PENDING && type!=HELLO_ACK && type!=0u) {
        d->inherited_budget=0; d->tx_length=d->tx_offset=0; return;
    }
    if(type==HELLO_ACK && d->tx_nonce!=d->pending_nonce) {
        d->inherited_budget=0; d->tx_length=d->tx_offset=0; return;
    }
    switch(type) {
    case 0: d->needs_delimiter=0; break;
    case HELLO_ACK:
        /* Commit only after the final timing observation in step(). */
        break;
    case CAPS_REPLY: d->want_caps=0; d->caps_ready=1; break;
    case START_ACK: d->want_start=0; if(d->state==MT_LIVE_STARTING)d->state=MT_LIVE_STREAMING; break;
    case ERROR_REPLY:
        d->want_error=0;
        if(d->state==MT_LIVE_NEEDS_HELLO && d->error_type==START && d->error_code==4u)
            d->stop_budget=0; /* The failed attempt is closed, not accepted. */
        break;
    case END:
        d->want_end=0; d->end_complete=1; d->end_sequence=d->tx_sequence; d->terminal_flags=flags(d);
        d->state=MT_LIVE_TERMINAL;
        if(d->stop_seen && d->hardware_off && !(d->terminal_flags&4u))d->want_ack=1;
        else d->stop_budget=0;
        break;
    case STOP_ACK: d->want_ack=0; d->stop_budget=0; d->state=MT_LIVE_NEEDS_HELLO; break;
    default: break;
    }
    d->tx_length=d->tx_offset=0;
}
int mt_live_device_step(mt_live_device *d) {
    uint64_t n,bound,after; size_t offer,accepted; unsigned first,hello_complete=0;
    if(!d || !now(d,&n) || !expire(d,n))return 0;
    if(d->state==MT_LIVE_FAULT || d->state==MT_LIVE_NO_SESSION)return 1;
    if(d->tx_next>=RESERVE && (d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING))terminate(d,4,n);
    if(!now(d,&n) || !expire(d,n) || !build_tx(d))return d->state!=MT_LIVE_FAULT;
    if(!now(d,&n) || !expire(d,n))return 0;
    bound=deadline(d);
    if(bound!=UINT64_MAX && (UINT64_MAX-n<d->io.transaction_bound_ms || n+d->io.transaction_bound_ms>=bound)) {
        recover(d,MT_LIVE_ERR_DEADLINE); return 0;
    }
    offer=d->tx_length-d->tx_offset; if(offer>64u)offer=64u;
    first=(d->tx_offset==0u);
    accepted=d->io.write(d->io.context,d->tx+d->tx_offset,offer);
    d->last_write_known=(accepted!=SIZE_MAX && accepted<=offer);
    d->last_write_accepted=d->last_write_known?accepted:0;
    if(!d->last_write_known) { d->needs_delimiter=1; recover(d,MT_LIVE_ERR_TRANSPORT); return 0; }
    if(!now(d,&after) || !expire(d,after)) {
        /* Accepted bytes are irreversible even when their completion is late.
         * In particular a first partial write has not advanced tx_offset yet. */
        if(accepted)d->needs_delimiter=1;
        return 0;
    }
    if(accepted) {
        if(first && d->tx_type!=HELLO_ACK && d->tx_type!=0u) {
            if(d->tx_sequence==UINT32_MAX)d->tx_exhausted=1;
            else d->tx_next=d->tx_sequence+1u;
        }
        if(first && d->tx_type==END)d->end_started=1;
        d->tx_offset+=accepted;
        if(d->tx_offset==d->tx_length) {
            hello_complete=(d->tx_type==HELLO_ACK && d->tx_nonce==d->pending_nonce &&
                            d->state==MT_LIVE_HELLO_PENDING);
            completed(d);
        }
    }
    /* The configured bound includes this completion path and final observation.
     * A violation is a local contract failure, not a retractable remote ACK. */
    if(!now(d,&after))return 0;
    if(bound!=UINT64_MAX && after>=bound) { recover(d,MT_LIVE_ERR_CALLBACK); return 0; }
    /* No fallible operation or callback after this observation. The qualified
     * transaction bound includes these final stores and queue initialization. */
    if(hello_complete && d->state==MT_LIVE_HELLO_PENDING)commit_hello(d);
    return d->state!=MT_LIVE_FAULT;
}
int mt_live_device_offer(mt_live_device *d,uint64_t first_index,const uint16_t *codes,uint16_t count) {
    uint64_t n; mt_sample_queue_result q;
    if(!d || !now(d,&n) || !expire(d,n))return 0;
    if(d->state!=MT_LIVE_STARTING && d->state!=MT_LIVE_STREAMING)return 0;
    q=mt_sample_queue_push(&d->queue,first_index,codes,count,0);
    if(q!=MT_SAMPLE_QUEUE_OK) { terminate(d,q==MT_SAMPLE_QUEUE_FULL?1u:2u,n); return 0; }
    return 1;
}
void mt_live_device_fault(mt_live_device *d,uint16_t reason) {
    uint64_t n;
    if(!d || reason==0u || reason>6u)return;
    /* The caller has already observed this acquisition fault. A later clock
     * failure during recovery must not replace that earlier cause. */
    if(d->state==MT_LIVE_STARTING || d->state==MT_LIVE_STREAMING ||
       d->state==MT_LIVE_DRAINING || d->state==MT_LIVE_TERMINAL)cause(d,reason);
    if(!now(d,&n) || !expire(d,n))return;
    if(d->end_started) { cause(d,reason); recover(d,MT_LIVE_ERR_CALLBACK); return; }
    if(d->tx_type==END && d->tx_length && !d->tx_offset)d->tx_length=0;
    terminate(d,reason,n);
}
void mt_live_device_transport_lost(mt_live_device *d) {
    if(!d)return;
    raw_barrier(d);
    d->state=MT_LIVE_FAULT;
    recover(d,MT_LIVE_ERR_TRANSPORT);
    d->rx_used=0; d->rx_discarding=0; d->needs_delimiter=1;
}
