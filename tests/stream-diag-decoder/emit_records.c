#include "mt_stream_diag.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define DEMCR UINT32_C(0xe000edfc)
#define CTRL UINT32_C(0xe0001000)
#define CYCLE UINT32_C(0xe0001004)
struct bus { uint32_t demcr,ctrl,cycle; };
static uint32_t rd(void *p,uint32_t a){struct bus *b=p;if(a==DEMCR)return b->demcr;if(a==CTRL)return b->ctrl;if(a==CYCLE){b->cycle+=3u;return b->cycle;}return 0u;}
static void wr(void *p,uint32_t a,uint32_t v){struct bus *b=p;if(a==DEMCR)b->demcr=v;else if(a==CTRL)b->ctrl=v;}
int main(int argc,char **argv){struct bus b={0,0,UINT32_MAX-30u};uint32_t begin;mt_stream_diag_record record;if(argc!=2)return 1;
 if(!mt_stream_diag_init(rd,wr,&b,UINT32_C(0xc19a372b)))return 2;
 mt_stream_diag_begin(UINT32_C(0x87654321),UINT64_C(0xfedcba9876543210));
 if(strcmp(argv[1],"replacement_zero_counts")==0)mt_stream_diag_replaced(1u,3u,0u);
 else {
  if(strcmp(argv[1],"invalid_dwt")==0)b.ctrl=0u;
  if(strcmp(argv[1],"first_fault")==0)mt_stream_diag_note(UINT32_MAX,2u,4u,0u,6u,0u);
  if(strcmp(argv[1],"saturated")==0)mt_stream_diagnostics.poll_count=UINT32_MAX;
  if(strcmp(argv[1],"observed")==0 || strcmp(argv[1],"saturated")==0){mt_stream_diag_poll(20u);mt_stream_diag_poll(22u);mt_stream_diag_copy(4u);begin=mt_stream_diag_cycle();mt_stream_diag_tx(begin);}
  begin=mt_stream_diag_cycle();mt_stream_diag_tx(begin);begin=mt_stream_diag_cycle();mt_stream_diag_observe(begin,0u,7u,3u,0u,0u,1u,4u,0u,0u,0u,1u);
 }
 record=mt_stream_diagnostics;
 return fwrite(&record,1,sizeof(record),stdout)==sizeof(record)?0:3;
}
