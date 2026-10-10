from pathlib import Path
import re,difflib,sys
root=Path(__file__).resolve().parent
source=Path(sys.argv[1]).resolve()
output=Path(sys.argv[2]).resolve()
src=source/'tests/control/independent/test_control_independent.c'
s=original=src.read_text()
s=s.replace('#include "firmware/app/mt_control.h"','#include "firmware/app/mt_control_rx.h"')
s=s.replace('    mt_control c;','    mt_control c;\n    mt_control_rx rx;')
s=s.replace('CHECK(mt_control_init(&f.c,&io)==MT_CONTROL_OK);','CHECK(mt_control_init(&f.c,&io)==MT_CONTROL_OK);CHECK(mt_control_rx_init(&f.rx,&f.c)==MT_CONTROL_OK);')
start=s.index('static mt_control_result receive(');end=s.index('\nstatic mt_control_result hello(',start)
s=s[:start]+'''/* Adapter-only alteration: the same encoded commands traverse RX in chunks
 * of up to 64 bytes. feed reports OK for codec-valid ignored commands. */
static mt_control_result receive(mt_wire_message *m) {
    uint8_t frame[MT_WIRE_FRAME_MAX];size_t n=0,off=0;
    CHECK(mt_wire_encode(m,frame,sizeof frame,&n)==MT_WIRE_OK);
    CHECK(n>1&&frame[n-1]==0);
    while(off<n) {
        size_t take=n-off,used=999;mt_control_result result;
        if(take>64)take=64;
        result=mt_control_rx_feed(&f.rx,frame+off,take,f.now,&used);
        CHECK(used<=take);off+=used;
        if(result!=MT_CONTROL_OK)return result;
        CHECK(used==take);
    }
    return MT_CONTROL_OK;
}'''+s[end:]
s,count=re.subn(r'((?:receive|hello)\([^;]*?\))==MT_CONTROL_IGNORED',r'\1==MT_CONTROL_OK',s)
assert count==9,count
out=output;out.mkdir(parents=True,exist_ok=True)
(out/'test_control_independent.c').write_text(s)
(out/'adapter.diff').write_text(''.join(difflib.unified_diff(original.splitlines(keepends=True),s.splitlines(keepends=True),fromfile='original',tofile='rx-adapted')))
print('Changed 9 ignored-input expectations only; all controller state/TX assertions retained')
