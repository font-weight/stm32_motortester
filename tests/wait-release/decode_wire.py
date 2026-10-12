#!/usr/bin/env python3
"""Independent spec-driven decoder: stdlib struct/zlib; no firmware/host codec."""
import argparse,json,struct,zlib
from pathlib import Path

def cobs(src):
    out=bytearray(); at=0
    while at<len(src):
        code=src[at];at+=1
        assert code and at+code-1<=len(src)
        piece=src[at:at+code-1];assert 0 not in piece
        out.extend(piece);at+=code-1
        if code!=255 and at<len(src):out.append(0)
    return bytes(out)

def decode(path):
    data=path.read_bytes();parts=data.split(b'\0');tail=parts.pop()
    frames=[];seq={};windows={};terminals=set()
    for part in parts:
        assert part
        b=cobs(part);assert 28<=len(b)<=540
        major,minor,kind,reserved,size,reserved2,session,sequence,request=struct.unpack_from('<BBBBHHQII',b)
        assert (major,minor,reserved,reserved2)==(0,1,0,0)
        assert size<=512 and len(b)==28+size
        assert zlib.crc32(b[:-4])==struct.unpack_from('<I',b,len(b)-4)[0]
        assert sequence==seq.get(session,0)+1;seq[session]=sequence
        p=b[24:-4]
        f={'type':kind,'session':session,'sequence':sequence,'request':request,'payload_hex':p.hex()}
        if kind==134:
            assert size==24
            state,reason,reserved3,gen,win,observed,busy,dropped=struct.unpack('<BBHIIIII',p)
            assert reserved3==0
            assert state!=5 or reason==3
            if state!=5:assert reason==0
            f.update(state=state,reason=reason,generation=gen,window=win,dropped=dropped)
        if kind==255:f.update(rejected_type=p[0],error=p[1])
        if kind in (144,145,146,147):
            gen,win=struct.unpack_from('<II',p);key=(session,gen,win);f.update(generation=gen,window=win)
            assert win and gen
            if kind==144:
                assert size==40 and key not in windows
                assert struct.unpack_from('<H',p,14)[0]==2048
                windows[key]=bytearray()
            if kind==145:
                offset,count=struct.unpack_from('<HH',p,8)
                assert size==12+2*count and 1<=count<=250
                assert key in windows and offset*2==len(windows[key])
                windows[key].extend(p[12:])
            if kind==146:
                assert size==16 and key not in terminals
                samples,reserved4,crc=struct.unpack_from('<HHI',p,8)
                assert samples==2048 and reserved4==0 and len(windows[key])==4096
                assert zlib.crc32(windows[key])==crc
                assert windows[key]==struct.pack('<2048H',3000,*([2500]*2047))
                terminals.add(key);f.update(samples=samples,sample_crc=f'{crc:08x}')
            if kind==147:assert key not in terminals;terminals.add(key)
        frames.append(f)
    return {'path':str(path),'bytes':len(data),'complete_frames':len(frames),'incomplete_tail_bytes':len(tail),'frames':frames}

parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--output',required=True)
a=parser.parse_args();files=sorted(Path(a.directory).glob('**/*.bin'))
results=[decode(p) for p in files];Path(a.output).write_text(json.dumps(results,indent=2)+'\n')
print(f'PASS: {len(files)} captured streams; independent COBS/header/frameCRC/sequence/sampleCRC checks; {sum(x["incomplete_tail_bytes"]>0 for x in results)} intentional partial tails')
