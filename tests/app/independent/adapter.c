#include "firmware/app/mt_capture_link.h"
#include <string.h>
static mt_capture_link link;
static mt_tx tx;
int reset_all(void) {
 mt_wire_message ack = {0};
 mt_tx_init(&tx); mt_capture_link_init(&link, &tx);
 ack.type=129; ack.request=1; ack.payload_length=16;
 int result=(int)mt_tx_hello(&tx, 0x1234, &ack, 0);
 mt_capture_link_init(&link,&tx);return result;
}
int configure(unsigned mode, unsigned pre, unsigned edge) {
 mt_acq_config_t c={0}; c.profile_id=1; c.mode=(uint8_t)mode;
 c.pretrigger_samples=(uint16_t)pre; c.edge=(uint8_t)edge; c.level_code=2048;
 c.auto_timeout_ms=mode==0u?100u:0u;
 return (int)mt_capture_link_configure(&link,&c,7);
}
int arm(unsigned generation, unsigned now) {return (int)mt_capture_link_arm(&link,7,generation,now);}
int sample(unsigned code,unsigned now) {return (int)mt_capture_link_sample(&link,(uint16_t)code,now);}
int poll_link(unsigned now,mt_tx_write_fn fn,void *context) {return (int)mt_capture_link_poll(&link,now,fn,context);}
int stop_link(unsigned generation,unsigned request,unsigned now) {return (int)mt_capture_link_stop(&link,generation,request,now);}
int release_link(unsigned generation,unsigned window,unsigned crc,unsigned request,unsigned now) {return (int)mt_capture_link_release(&link,generation,window,crc,request,now);}
int hello(unsigned nonce,unsigned now) {
 mt_wire_message ack={0}; ack.type=129;ack.request=1;ack.payload_length=16;
 return (int)mt_tx_hello(&tx,nonce,&ack,now);
}
int reply(unsigned request,unsigned now) {
 mt_wire_message r={0};r.type=255;r.request=request;r.payload_length=8;
 return (int)mt_tx_enqueue(&tx,&r,now);
}
unsigned get(unsigned field) {
 switch(field) {
 case 0:return (unsigned)link.phase; case 1:return link.armed;
 case 2:return link.active_window;case 3:return link.last_window;
 case 4:return link.window_crc;case 5:return link.have_tombstone;
 case 6:return (unsigned)tx.count;case 7:return tx.count?tx.slots[0].message.type:0u;
 case 8:return (unsigned)tx.offset;case 9:return link.offset;
 case 10:return link.release_since_ms;case 11:return (unsigned)link.acquisition.state;
 case 12:return link.generation;case 13:return tx.next_sequence;
 case 14:return (unsigned)tx.session;case 15:return link.tomb_window;
 case 16:return (unsigned)tx.stopping;case 17:return link.crc_running;
 default:return 0;
 }
}
void copy_samples(void *p) {memcpy(p,link.acquisition.samples,sizeof link.acquisition.samples);}
void force_last(unsigned n) {link.last_window=n;}
void force_sequence(unsigned n) {tx.next_sequence=n;}
void link_reinitialize(void) {mt_capture_link_init(&link,&tx);}
int has_dropped_counter(void) {
#ifdef TEST_HAS_DROPPED
 return 1;
#else
 return 0;
#endif
}
void force_dropped(unsigned n) {
#ifdef TEST_HAS_DROPPED
 link.dropped_windows=n;
#else
 (void)n;
#endif
}
