import ctypes as C, random, struct, zlib, json
from pathlib import Path
root=Path(__file__).parent
lib=C.CDLL(str(root/'libmt_stream.so'))
class Message(C.Structure):
    _fields_=[('type',C.c_uint8),('session',C.c_uint64),('sequence',C.c_uint32),('request',C.c_uint32),('payload_length',C.c_uint16),('payload',C.c_uint8*512)]
class Stream(C.Structure):
    _fields_=[('bytes',C.c_uint8*543),('used',C.c_size_t),('first_ms',C.c_uint32),('last_ms',C.c_uint32),('discarding',C.c_uint),('last_decode',C.c_int)]
class Guard(C.Structure):
    _fields_=[('before',C.c_uint8*16),('stream',Stream),('after',C.c_uint8*16)]
lib.mt_stream_init.argtypes=[C.POINTER(Stream)]
lib.mt_stream_feed.argtypes=[C.POINTER(Stream),C.c_uint8,C.c_uint32,C.POINTER(Message)]
lib.mt_stream_poll.argtypes=[C.POINTER(Stream),C.c_uint32]
def cobs(raw):
    parts=[]
    for part in raw.split(b'\0'):
        while len(part)>=254:parts.append(b'\xff'+part[:254]);part=part[254:]
        parts.append(bytes([len(part)+1])+part)
    return b''.join(parts)
def make(payload=b'',typ=127,session=0x0807060504030201,sequence=1,request=2):
    raw=struct.pack('<BBBBHHQII',0,1,typ,0,len(payload),0,session,sequence,request)+payload
    return cobs(raw+struct.pack('<I',zlib.crc32(raw)))+b'\0'
def decode(frame):
    if not 1<=len(frame)<=543:return 4,None
    decoded=[];index=0
    while index<len(frame):
        step=frame[index]
        if step==0:return 3,None
        block=frame[index+1:index+step]
        if len(block)!=step-1 or 0 in block:return 3,None
        decoded.extend(block);index+=step
        if step<255 and index<len(frame):decoded.append(0)
        if len(decoded)>540:return 4,None
    raw=bytes(decoded)
    if len(raw)<28:return 4,None
    fields=struct.unpack_from('<BBBBHHQII',raw);major,minor,typ,res,n,res2,session,seq,req=fields
    if n>512 or len(raw)!=28+n:return 4,None
    if zlib.crc32(raw[:-4])!=struct.unpack('<I',raw[-4:])[0]:return 5,None
    if (major,minor)!=(0,1):return 6,None
    if res or res2:return 7,None
    return 0,(typ,session,seq,req,n,raw[24:-4])
class Reference:
    def __init__(self):self.data=bytearray();self.first=self.last=0;self.discard=False
    def poll(self,now):
        if self.data and not self.discard and (((now-self.last)&0xffffffff)>=1000 or ((now-self.first)&0xffffffff)>=3000):
            self.data.clear();self.discard=True;return 4
        return 0
    def feed(self,b,now):
        event=self.poll(now)
        if self.discard:
            if b==0:self.discard=False
            return event,None,None
        if b==0:
            if not self.data:return 0,None,None
            code,result=decode(bytes(self.data));self.data.clear();return (1 if code==0 else 2),code,result
        if len(self.data)==543:self.data.clear();self.discard=True;return 3,None,None
        if not self.data:self.first=now
        self.last=now;self.data.append(b);return 0,None,None
hist={};steps=0
class Driver:
    def __init__(self):
        self.g=Guard();self.g.before[:]=b'\xa5'*16;self.g.after[:]=b'\x5a'*16;self.m=Message();C.memset(C.byref(self.m),0xcc,C.sizeof(self.m));lib.mt_stream_init(C.byref(self.g.stream));self.r=Reference()
    def check(self,b,now):
        global steps
        now&=0xffffffff;before=bytes(self.m)
        if b is None:
            want=self.r.poll(now);got=lib.mt_stream_poll(C.byref(self.g.stream),now);code=result=None
        else:
            want,code,result=self.r.feed(b,now);got=lib.mt_stream_feed(C.byref(self.g.stream),b,now,C.byref(self.m))
        assert want==got,(want,got,b,now)
        if got==1:assert (self.m.type,self.m.session,self.m.sequence,self.m.request,self.m.payload_length,bytes(self.m.payload[:self.m.payload_length]))==result
        else:assert bytes(self.m)==before,('modified output',got)
        if code is not None:assert self.g.stream.last_decode==code,(self.g.stream.last_decode,code)
        assert self.g.stream.used==len(self.r.data) and bool(self.g.stream.discarding)==self.r.discard
        assert bytes(self.g.before)==b'\xa5'*16 and bytes(self.g.after)==b'\x5a'*16
        hist[str(got)]=hist.get(str(got),0)+1;steps+=1
rng=random.Random(20261010)
# Every split of an independently built maximum-payload frame; coalescing retains state.
frame=make(bytes(i%256 for i in range(512)))
for split in range(len(frame)+1):
    d=Driver()
    for i,b in enumerate(frame):d.check(b,10 if i<split else 11)
    for b in frame:d.check(b,12)
# Interbyte and total exact boundaries, plus rollover.
for start in (0,123,0xffffffff-499):
    for gap in (999,1000,1001):
        d=Driver();d.check(1,start);d.check(None,start+gap);d.check(0,start+gap)
        for b in make():d.check(b,start+gap+1)
for total in (2999,3000,3001):
    d=Driver()
    for t in (0,900,1800,2700):d.check(1,t)
    d.check(0,total)
    for b in make():d.check(b,total+1)
# Corrupted CRC plus independently repaired version/reserved violations.
for off,new in ((0,1),(1,2),(3,1),(6,1)):
    raw=bytearray(struct.pack('<BBBBHHQII',0,1,127,0,0,0,1,1,2));raw[off]=new
    frame=cobs(bytes(raw)+struct.pack('<I',zlib.crc32(raw)))+b'\0';d=Driver()
    for b in frame+make():d.check(b,0)
# Random mixed streams, idle polling, timed boundaries, overflow and recovery.
for trial in range(2000):
    d=Driver();now=(0xffffff00+trial)&0xffffffff
    for _ in range(12):
        choice=rng.randrange(5)
        if choice==0:segment=make(rng.randbytes(rng.randrange(513)),sequence=rng.getrandbits(32))
        elif choice==1:segment=rng.randbytes(rng.randrange(700))
        elif choice==2:segment=b'\x01'*rng.choice([542,543,544,600])+b'\0'+make()
        elif choice==3:segment=b'\0'*rng.randrange(10)
        else:
            now=(now+rng.choice([999,1000,1001,2999,3000,3001]))&0xffffffff;d.check(None,now);segment=make()
        for b in segment:
            now=(now+rng.choice([0,0,0,1]))&0xffffffff;d.check(b,now)
print(json.dumps({'status':'PASS','event_checks':steps,'event_histogram':hist,'random_streams':2000,'seed':20261010,'sizeof_stream_host':C.sizeof(Stream),'scope':'byte stream only; no session/USB/ARM/Windows'},indent=2))
