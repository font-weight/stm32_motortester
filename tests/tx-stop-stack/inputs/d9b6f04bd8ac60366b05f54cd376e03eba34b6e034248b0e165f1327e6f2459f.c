/* Independent old/new translation units; public-API scenarios first.
 * Object padding and unused slots/payload are deliberately not success oracles.
 * Failure atomicity uses all object bytes. No hardware or timing claim. */
#include "mt_tx.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DECLARE(P) \
void P##_init(mt_tx *); \
mt_tx_result P##_hello(mt_tx *, uint64_t, const mt_wire_message *, uint32_t); \
mt_tx_result P##_enqueue(mt_tx *, const mt_wire_message *, uint32_t); \
mt_tx_result P##_stop(mt_tx *,uint32_t,uint32_t,const mt_wire_message *,const mt_wire_message *,const mt_wire_message *,uint32_t); \
mt_tx_result P##_poll(mt_tx *,uint32_t,mt_tx_write_fn,void *,mt_tx_event *); \
uint8_t P##_terminal_committed(const mt_tx *,uint32_t,uint32_t); \
mt_tx_window_phase P##_window_status(const mt_tx *,uint32_t,uint32_t)
DECLARE(old_tx);
DECLARE(new_tx);

typedef struct { uint8_t bytes[32768]; size_t used, step, calls; int failure; } sink;
typedef struct { mt_tx old, new; sink a, b; uint32_t now; } pair;
static unsigned long scenarios, operations;
static uint32_t seed = UINT32_C(0x19700270);
static uint32_t random32(void) { seed ^= seed<<13; seed ^= seed>>17; seed ^= seed<<5; return seed; }
static void put32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4u;++i) p[i]=(uint8_t)(v>>(i*8u)); }
static mt_wire_message message(uint8_t type,uint32_t request) {
    mt_wire_message m; memset(&m,0xa5,sizeof m);
    m.type=type; m.request=request; m.session=0; m.sequence=0;
    switch(type) {
    case 129:m.payload_length=16;break;
    case 130:m.payload_length=30;break;
    case 131:m.payload_length=20;break;
    case 132:case 135:case 255:m.payload_length=8;break;
    case 133:case 147:m.payload_length=12;break;
    case 134:m.payload_length=24;break;
    case 144:m.payload_length=40;break;
    case 145:m.payload_length=512;break;
    case 146:m.payload_length=16;break;
    default:assert(0);m.payload_length=0;
    }
    memset(m.payload,0,m.payload_length);
    if(type>=144u&&type<=147u) {put32(m.payload,7);put32(m.payload+4,3);}
    return m;
}
static int write_sink(void *ctx,const uint8_t *bytes,size_t count) {
    sink *s=ctx; ++s->calls; assert(count<=MT_TX_CHUNK_MAX);
    if(s->failure) return s->failure;
    if(count>s->step)count=s->step;
    assert(s->used+count<=sizeof s->bytes);
    memcpy(s->bytes+s->used,bytes,count);s->used+=count;return (int)count;
}
static void compare_message(const mt_wire_message *a,const mt_wire_message *b) {
    assert(a->type==b->type&&a->session==b->session&&a->sequence==b->sequence);
    assert(a->request==b->request&&a->payload_length==b->payload_length);
    assert(a->payload_length<=MT_WIRE_PAYLOAD_MAX);
    assert(memcmp(a->payload,b->payload,a->payload_length)==0);
}
static void compare(const pair *p) {
    const mt_tx *a=&p->old,*b=&p->new;
#define SAME(f) assert(a->f==b->f)
    SAME(count);SAME(length);SAME(offset);SAME(session);SAME(next_sequence);
    SAME(head_ms);SAME(progress_ms);SAME(stop_ms);SAME(terminal_generation);
    SAME(terminal_window);SAME(terminal_type);SAME(stopping);SAME(fault);
#undef SAME
    assert(a->count<=MT_TX_CAPACITY&&a->length<=MT_WIRE_FRAME_MAX);
    for(size_t i=0;i<a->count;++i) {compare_message(&a->slots[i].message,&b->slots[i].message);assert(a->slots[i].reply==b->slots[i].reply);}
    if(a->length)assert(memcmp(a->frame,b->frame,a->length)==0);
    assert(p->a.used==p->b.used&&p->a.calls==p->b.calls);
    assert(memcmp(p->a.bytes,p->b.bytes,p->a.used)==0);
    for(uint32_t g=6;g<9u;++g)for(uint32_t w=0;w<5u;++w) {
        assert(old_tx_terminal_committed(a,g,w)==new_tx_terminal_committed(b,g,w));
        assert(old_tx_window_status(a,g,w)==new_tx_window_status(b,g,w));
    }
}
static mt_tx_result poll_pair(pair *p,size_t step) {
    mt_tx_event a,b,old_a,old_b; memset(&a,0xd6,sizeof a);memset(&b,0xd6,sizeof b);
    memcpy(&old_a,&a,sizeof a);memcpy(&old_b,&b,sizeof b);
    p->a.step=step;p->b.step=step;
    mt_tx_result x=old_tx_poll(&p->old,p->now,write_sink,&p->a,&a);
    mt_tx_result y=new_tx_poll(&p->new,p->now,write_sink,&p->b,&b);
    assert(x==y);++operations;
    if(x==MT_TX_PROGRESS) {
#define EVENT(f) assert(a.f==b.f)
        EVENT(flags);EVENT(type);EVENT(session);EVENT(sequence);EVENT(request);
        EVENT(generation);EVENT(window_id);EVENT(accepted);
#undef EVENT
    } else {assert(memcmp(&a,&old_a,sizeof a)==0);assert(memcmp(&b,&old_b,sizeof b)==0);}
    compare(p);return x;
}
static void drain(pair *p,size_t step) {unsigned budget=10000;while(p->old.count){assert(budget--);assert(poll_pair(p,step)==MT_TX_PROGRESS);}assert(poll_pair(p,step)==MT_TX_IDLE);}
static mt_tx_result enqueue(pair *p,const mt_wire_message *m) {
    mt_tx a,b;memcpy(&a,&p->old,sizeof a);memcpy(&b,&p->new,sizeof b);
    mt_tx_result x=old_tx_enqueue(&p->old,m,p->now),y=new_tx_enqueue(&p->new,m,p->now);
    assert(x==y);++operations;
    if(x!=MT_TX_OK){assert(memcmp(&a,&p->old,sizeof a)==0);assert(memcmp(&b,&p->new,sizeof b)==0);}
    compare(p);return x;
}
static void connect_pair(pair *p,uint32_t now) {
    mt_wire_message h=message(129,1);memset(p,0,sizeof *p);p->now=now;
    old_tx_init(&p->old);new_tx_init(&p->new);
    assert(old_tx_hello(&p->old,UINT64_C(0x1122334455667788),&h,now)==MT_TX_OK);
    assert(new_tx_hello(&p->new,UINT64_C(0x1122334455667788),&h,now)==MT_TX_OK);
    drain(p,64);p->a.used=p->b.used=0;++scenarios;
}
static void stop_messages(uint32_t gen,uint32_t win,mt_wire_message *a,mt_wire_message *k,mt_wire_message *s) {
    *a=message(147,0);*k=message(133,9);*s=message(134,0);
    put32(a->payload,gen);put32(a->payload+4,win);a->payload[8]=1;
    put32(k->payload,gen);put32(k->payload+4,win);
    put32(s->payload+4,gen);put32(s->payload+8,win);
}
static mt_tx_result stop_raw(pair *p,uint32_t gen,uint32_t win,const mt_wire_message *a,const mt_wire_message *k,const mt_wire_message *s) {
    mt_tx oa,ob; mt_wire_message aa,kk,ss;
    memcpy(&oa,&p->old,sizeof oa);memcpy(&ob,&p->new,sizeof ob);
    if(a)memcpy(&aa,a,sizeof aa);memcpy(&kk,k,sizeof kk);memcpy(&ss,s,sizeof ss);
    mt_tx_result x=old_tx_stop(&p->old,gen,win,a,k,s,p->now);
    mt_tx_result y=new_tx_stop(&p->new,gen,win,a,k,s,p->now);
    assert(x==y);++operations;
    if(x!=MT_TX_OK){assert(memcmp(&oa,&p->old,sizeof oa)==0);assert(memcmp(&ob,&p->new,sizeof ob)==0);}
    if(a)assert(memcmp(&aa,a,sizeof aa)==0);assert(memcmp(&kk,k,sizeof kk)==0);assert(memcmp(&ss,s,sizeof ss)==0);
    compare(p);return x;
}
static mt_tx_result stop_pair(pair *p,uint32_t win,int null_abort) {
    mt_wire_message a,k,s;stop_messages(7,win,&a,&k,&s);return stop_raw(p,7,win,null_abort?NULL:&a,&k,&s);
}
static size_t decode(pair *p,mt_wire_message *out,size_t capacity) {
    size_t start=0,count=0;
    for(size_t i=0;i<p->a.used;++i)if(!p->a.bytes[i]){assert(count<capacity);assert(mt_wire_decode(p->a.bytes+start,i-start,&out[count++])==MT_WIRE_OK);start=i+1;}
    assert(start==p->a.used);return count;
}
static void retained_abort_reason_and_reply_order(void) {
    for(unsigned reason=0;reason<256u;++reason)for(unsigned null_abort=0;null_abort<2u;++null_abort)for(unsigned first_reply=0;first_reply<2u;++first_reply) {
        pair p;mt_wire_message a=message(147,0),r=message(132,4),out[6];connect_pair(&p,12);
        for(unsigned i=8;i<12u;++i)a.payload[i]=(uint8_t)(reason+i-8u);
        if(first_reply)assert(enqueue(&p,&r)==MT_TX_OK);
        assert(enqueue(&p,&a)==MT_TX_OK);r.request=5;assert(enqueue(&p,&r)==MT_TX_OK);
        assert(stop_pair(&p,3,(int)null_abort)==MT_TX_OK);drain(&p,1u+reason%64u);
        size_t n=decode(&p,out,6),idx=first_reply?2u:1u;
        assert(n==idx+3u&&out[idx].type==147u&&memcmp(out[idx].payload,a.payload,12)==0);
        for(size_t i=0;i<idx;++i)assert(out[i].type==132u);
        assert(out[idx+1].type==133u&&out[idx+2].type==134u);
    }
}
static void partial_head_compaction(void) {
    const uint8_t types[]={144,145,134,132};
    for(size_t t=0;t<sizeof types;++t)for(size_t prefix=1;prefix<=64u;++prefix) {
        pair p;mt_wire_message h=message(types[t],types[t]==132u?4u:0u),a=message(147,0),r=message(135,6),out[6];
        connect_pair(&p,0);a.payload[8]=7;assert(enqueue(&p,&h)==MT_TX_OK);
        assert(poll_pair(&p,prefix)==MT_TX_PROGRESS);
        int partial=p.old.offset!=0u;assert(enqueue(&p,&a)==MT_TX_OK);assert(enqueue(&p,&r)==MT_TX_OK);
        assert(stop_pair(&p,3,1)==MT_TX_OK);assert(p.old.count==(partial?5u:4u));drain(&p,64);
        size_t n=decode(&p,out,6);assert(n==5u&&out[0].type==types[t]&&out[1].type==135u&&out[2].type==147u&&out[2].payload[8]==7u);
    }
}
static void zero_acceptance_then_stop(void) {
    const uint8_t types[]={144,145,146,147,134};
    for(size_t i=0;i<sizeof types;++i) {
        pair p;mt_wire_message m=message(types[i],0),out[4];connect_pair(&p,0);m.payload[8]=2;
        assert(enqueue(&p,&m)==MT_TX_OK);assert(poll_pair(&p,0)==MT_TX_BLOCKED);
        assert(p.old.offset==0u&&p.old.length!=0u&&p.old.next_sequence==2u);
        assert(stop_pair(&p,3,types[i]==147u)==MT_TX_OK);drain(&p,64);
        assert(decode(&p,out,4)==3u&&out[0].type==147u&&out[0].sequence==2u);
        assert(out[0].payload[8]==(types[i]==147u?2u:1u));
    }
}
static void committed_terminal_no_duplicate(void) {
    for(uint8_t type=146;type<=147u;++type)for(size_t prefix=1;prefix<=64u;++prefix) {
        pair p;mt_wire_message m=message(type,0),out[4];connect_pair(&p,0);
        assert(enqueue(&p,&m)==MT_TX_OK);assert(poll_pair(&p,prefix)==MT_TX_PROGRESS);
        assert(stop_pair(&p,3,1)==MT_TX_OK);drain(&p,64);
        assert(decode(&p,out,4)==3u&&out[0].type==type&&out[1].type==133u);
    }
}
static void argument_rejections_atomic(void) {
    for(unsigned retained=0;retained<2u;++retained)for(unsigned code=0;code<22u;++code) {
        pair p;mt_wire_message a,k,s;uint32_t g=7,w=3;connect_pair(&p,0);stop_messages(g,w,&a,&k,&s);
        if(retained){mt_wire_message queued=a;queued.payload[8]=2;assert(enqueue(&p,&queued)==MT_TX_OK);}
        switch(code){
        case 0:k.type=132;break;case 1:k.request=0;break;case 2:k.session=1;break;case 3:k.sequence=1;break;case 4:k.payload_length=11;break;
        case 5:s.type=133;break;case 6:s.request=1;break;case 7:s.session=1;break;case 8:s.sequence=1;break;case 9:s.payload_length=23;break;
        case 10:put32(k.payload,8);break;case 11:s.payload[0]=1;break;case 12:s.payload[1]=1;break;case 13:put32(s.payload+4,8);break;
        case 14:put32(s.payload+8,4);break;case 15:put32(k.payload+4,4);put32(s.payload+8,4);break;case 16:g=0;break;
        case 17:a.type=146;break;case 18:a.request=1;break;case 19:a.payload_length=11;break;case 20:put32(a.payload,8);break;case 21:put32(a.payload+4,4);break;
        default:assert(0);
        }
        mt_tx_result expected=retained&&code>=17u?MT_TX_OK:MT_TX_ARGUMENT;
        assert(stop_raw(&p,g,w,&a,&k,&s)==expected);
    }
    pair p;connect_pair(&p,0);assert(stop_pair(&p,3,1)==MT_TX_ARGUMENT);
    assert(stop_pair(&p,0,1)==MT_TX_OK);assert(stop_pair(&p,0,1)==MT_TX_DRAINING);
}
static void admission_full_and_reserved_capacity(void) {
    pair p;mt_wire_message d=message(145,0),r=message(132,4),out[7];connect_pair(&p,0);
    assert(enqueue(&p,&d)==MT_TX_OK);assert(poll_pair(&p,1)==MT_TX_PROGRESS);
    assert(enqueue(&p,&r)==MT_TX_OK);r.request=5;assert(enqueue(&p,&r)==MT_TX_OK);
    assert(enqueue(&p,&r)==MT_TX_FULL);assert(enqueue(&p,&d)==MT_TX_FULL);
    assert(stop_pair(&p,3,0)==MT_TX_OK);assert(p.old.count==6u);assert(enqueue(&p,&r)==MT_TX_DRAINING);
    drain(&p,64);assert(decode(&p,out,7)==6u);assert(out[0].type==145u&&out[1].request==4u&&out[2].request==5u&&out[3].type==147u);
}
static void wrap_timeout_fault_and_recovery(void) {
    pair p;mt_wire_message a=message(147,0),h=message(129,1);connect_pair(&p,UINT32_MAX-499u);
    assert(enqueue(&p,&a)==MT_TX_OK);assert(stop_pair(&p,3,1)==MT_TX_OK);
    p.now+=999u;assert(poll_pair(&p,0)==MT_TX_BLOCKED);++p.now;assert(poll_pair(&p,64)==MT_TX_TIMEOUT);
    assert(stop_pair(&p,3,1)==MT_TX_FAULT);assert(poll_pair(&p,64)==MT_TX_FAULT);
    assert(old_tx_hello(&p.old,99,&h,p.now)==MT_TX_OK);assert(new_tx_hello(&p.new,99,&h,p.now)==MT_TX_OK);compare(&p);drain(&p,64);
    memset(&p,0,sizeof p);old_tx_init(&p.old);new_tx_init(&p.new);assert(stop_pair(&p,0,1)==MT_TX_NOT_NEGOTIATED);
}
static void public_queue_randomized(void) {
    const uint8_t types[]={130,131,132,134,135,144,145,146,147,255};
    for(unsigned trial=0;trial<10000u;++trial) {
        pair p;connect_pair(&p,random32());
        for(unsigned i=0;i<12u;++i){uint8_t type=types[random32()%10u];uint32_t request=(type<144u&&type!=134u)||type==255u?random32()%100u+2u:0u;mt_wire_message m=message(type,request);
            if(type==147u)for(unsigned j=8;j<12u;++j)m.payload[j]=(uint8_t)random32();
            (void)enqueue(&p,&m);if((random32()&1u)&&p.old.count)(void)poll_pair(&p,random32()%65u);
        }
        uint32_t win=(random32()%4u)==0u?0u:3u;
        assert(stop_pair(&p,win,0)==MT_TX_OK);drain(&p,1u+random32()%64u);
    }
}
static void private_state_defensive_controls(void) {
    /* These are intentionally corrupted/future queue layouts, not supported
     * public API states or additional promises about callers. */
    for(unsigned mode=0;mode<3u;++mode){pair p;mt_wire_message a=message(147,0);connect_pair(&p,0);assert(enqueue(&p,&a)==MT_TX_OK);
        if(mode==0){p.old.slots[0].message.payload_length=11;p.new.slots[0].message.payload_length=11;}
        if(mode==1){p.old.slots[0].message.request=4;p.new.slots[0].message.request=4;}
        if(mode==2){for(size_t i=1;i<5u;++i){p.old.slots[i]=p.old.slots[0];p.new.slots[i]=p.new.slots[0];p.old.slots[i].reply=p.new.slots[i].reply=1;}p.old.count=p.new.count=5;}
        assert(stop_pair(&p,3,1)==(mode==2?MT_TX_FULL:MT_TX_ARGUMENT));
    }
}
typedef void (*test_fn)(void);
static const struct {const char *name;test_fn fn;} tests[]={
 {"retained_abort_reason_and_reply_order",retained_abort_reason_and_reply_order},
 {"partial_head_compaction",partial_head_compaction},{"zero_acceptance_then_stop",zero_acceptance_then_stop},
 {"committed_terminal_no_duplicate",committed_terminal_no_duplicate},{"argument_rejections_atomic",argument_rejections_atomic},
 {"admission_full_and_reserved_capacity",admission_full_and_reserved_capacity},{"wrap_timeout_fault_and_recovery",wrap_timeout_fault_and_recovery},
 {"public_queue_randomized",public_queue_randomized},{"private_state_defensive_controls",private_state_defensive_controls}
};
int main(int argc,char **argv) {
    if(argc==2&&!strcmp(argv[1],"--list")){for(size_t i=0;i<sizeof tests/sizeof tests[0];++i)puts(tests[i].name);return 0;}
    for(size_t i=0;i<sizeof tests/sizeof tests[0];++i)if(argc==2&&!strcmp(argv[1],tests[i].name)){tests[i].fn();printf("{\"case\":\"%s\",\"scenarios\":%lu,\"operations\":%lu,\"pass\":true}\n",tests[i].name,scenarios,operations);return 0;}
    return 2;
}
