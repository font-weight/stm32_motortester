"""Critic-B independent Python-vs-pinned-C byte/stream checks, no session semantics."""
from pathlib import Path
import ctypes as C, hashlib, json, random, struct, subprocess, sys
host=Path(sys.argv[1]).resolve(); repo=Path(sys.argv[2]).resolve(); here=Path(__file__).resolve().parent
sys.path.insert(0,str(host/'host'))
from motortester.wire import Frame, WireError, StreamDecoder, encode_frame, decode_frame
PIN='8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7'
cdir=here/'c-reference';cdir.mkdir(exist_ok=True)
hashes={}
for name in ('mt_wire.c','mt_wire.h','mt_stream.c','mt_stream.h'):
    data=subprocess.check_output(['git','show',f'{PIN}:firmware/protocol/{name}'],cwd=repo)
    (cdir/name).write_bytes(data);hashes[name]=hashlib.sha256(data).hexdigest()
subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-shared','-fPIC',str(cdir/'mt_wire.c'),str(cdir/'mt_stream.c'),'-o',str(here/'reference.so')],check=True)
class Message(C.Structure):
    _fields_=[('type',C.c_uint8),('session',C.c_uint64),('sequence',C.c_uint32),('request',C.c_uint32),('payload_length',C.c_uint16),('payload',C.c_uint8*512)]
class Stream(C.Structure):
    _fields_=[('bytes',C.c_uint8*543),('used',C.c_size_t),('first_ms',C.c_uint32),('last_ms',C.c_uint32),('discarding',C.c_uint),('last_decode',C.c_int)]
lib=C.CDLL(str(here/'reference.so'));P=C.POINTER(C.c_uint8)
lib.mt_wire_decode.argtypes=[P,C.c_size_t,C.POINTER(Message)];lib.mt_wire_decode.restype=C.c_int
lib.mt_wire_encode.argtypes=[C.POINTER(Message),P,C.c_size_t,C.POINTER(C.c_size_t)];lib.mt_wire_encode.restype=C.c_int
lib.mt_stream_init.argtypes=[C.POINTER(Stream)];lib.mt_stream_init.restype=None
lib.mt_stream_feed.argtypes=[C.POINTER(Stream),C.c_uint8,C.c_uint32,C.POINTER(Message)];lib.mt_stream_feed.restype=C.c_int
lib.mt_stream_poll.argtypes=[C.POINTER(Stream),C.c_uint32];lib.mt_stream_poll.restype=C.c_int
CODES={3:'FRAMING',4:'LENGTH',5:'CRC',6:'VERSION',7:'RESERVED'}
counts={};rng=random.Random(20261010)
def note(k,n=1):counts[k]=counts.get(k,0)+n
def cbuf(x):return (C.c_uint8*max(1,len(x)))(*x)
def values(m):return (m.type,m.session,m.sequence,m.request,bytes(m.payload[:m.payload_length]))
def fvalues(f):return f.message_type,f.session,f.sequence,f.request_id,f.payload
def crc_bitwise(raw):
    c=0xffffffff
    for b in raw:
        c^=b
        for _ in range(8):c=(c>>1)^(0xedb88320 if c&1 else 0)
    return c^0xffffffff
# Reference uses index backpatching, rather than production's split-runs algorithm.
def cobs(raw):
    result=bytearray([0]);start=0;code=1
    for b in raw:
        if b:
            result.append(b);code+=1
            if code<255:continue
        result[start]=code;start=len(result);result.append(0);code=1
    result[start]=code
    return bytes(result)
def raw(f):
    body=struct.pack('<BBBBHHQII',0,1,f.message_type,0,len(f.payload),0,f.session,f.sequence,f.request_id)+f.payload
    return body+struct.pack('<I',crc_bitwise(body))
def packet(f):return cobs(raw(f))+b'\0'
def decode_both(w):
    m=Message();C.memset(C.byref(m),0xa5,C.sizeof(m));before=bytes(m)
    code=lib.mt_wire_decode(cbuf(w),len(w),C.byref(m))
    try:
        f=decode_frame(w)
    except WireError as e:
        assert code!=0,(w.hex(),e.code)
        assert bytes(m)==before
        if 1<=len(w)<=543:assert e.code.value==CODES[code],(e.code,code)
        note('negative_decode');return
    assert code==0 and fvalues(f)==values(m)
    note('positive_decode')
assert crc_bitwise(b'123456789')==0xcbf43926
for n in range(513):
    for p in (bytes(n),b'\xff'*n,bytes(i%256 for i in range(n)),rng.randbytes(n)):
        f=Frame(rng.randrange(256),rng.getrandbits(64),rng.getrandbits(32),rng.getrandbits(32),p)
        expected=packet(f);assert encode_frame(f)==expected
        m=Message(type=f.message_type,session=f.session,sequence=f.sequence,request=f.request_id,payload_length=n);m.payload[:n]=p
        out=(C.c_uint8*544)();written=C.c_size_t()
        assert lib.mt_wire_encode(C.byref(m),out,544,C.byref(written))==0
        assert bytes(out[:written.value])==expected
        decode_both(expected[:-1]);note('three_way_encode')
f=Frame(145,123,456,0,bytes(range(64)));baseline=raw(f)
for index in range(len(baseline)):
    for bit in range(8):
        mutant=bytearray(baseline);mutant[index]^=1<<bit;decode_both(cobs(mutant))
for _ in range(50000):decode_both(rng.randbytes(rng.randrange(545)))
# Native byte-by-byte stream is adapted only to aggregate the documented host API output.
class Compare:
    def __init__(self):self.h=StreamDecoder();self.c=Stream();lib.mt_stream_init(C.byref(self.c));self.m=Message();self.now=0
    def feed(self,data,now):
        expected=[];diag={}
        def event(e):
            if e==1:expected.append(values(self.m));note('stream_frames')
            elif e:
                code=CODES[self.c.last_decode] if e==2 else {3:'OVERFLOW',4:'TIMEOUT'}[e]
                diag[code]=diag.get(code,0)+1;note('stream_'+code)
        event(lib.mt_stream_poll(C.byref(self.c),now))
        for b in data:event(lib.mt_stream_feed(C.byref(self.c),b,now,C.byref(self.m)))
        got=self.h.feed(data,now)
        assert [fvalues(f) for f in got.frames]==expected
        assert [(d.code.value,d.count) for d in got.diagnostics]==list(diag.items())
        assert self.h._used==self.c.used and self.h._discarding==bool(self.c.discarding)
        assert bytes(self.h._buffer[:self.h._used])==bytes(self.c.bytes[:self.c.used])
        note('stream_calls');note('stream_bytes',len(data))
    def reset(self):self.h.reset();lib.mt_stream_init(C.byref(self.c));note('resets')
packets=[packet(Frame(127,123,i,0,p)) for i,p in enumerate((b'',b'\0a',b'\xff'*512))]
for w in packets:
    for cut in range(len(w)+1):
        x=Compare();x.feed(w[:cut],10);x.feed(w[cut:],11)
for gap in (999,1000,1001):
    x=Compare();x.feed(packets[-1][:-1],123);x.feed(b'\0'+packets[0],123+gap)
for total in (2999,3000,3001):
    x=Compare();w=packets[-1]
    for i,t in enumerate((0,900,1800,2700)):x.feed(w[i:i+1],t)
    x.feed(w[4:],total);x.feed(packets[0],total)
x=Compare();now=0
for _ in range(5000):
    now+=rng.choice([0,0,1,999,1000,1001,2999,3000,3001])
    choice=rng.randrange(8)
    if choice==0:data=b''.join(rng.choice(packets) for _ in range(rng.randrange(1,6)))
    elif choice==1:data=rng.randbytes(rng.randrange(4097))
    elif choice==2:data=b'\1'*rng.choice([542,543,544,600])+b'\0'+packets[0]
    elif choice==3:data=b'\0'*rng.randrange(4097)
    elif choice==4:data=packets[-1][:rng.randrange(len(packets[-1]))]
    elif choice==5:data=b'\xff\0'*2048
    elif choice==6:data=b''
    else:x.reset();now=0;data=packets[0]
    x.feed(data,now)
print(json.dumps({'status':'PASS','host_source':str(host),'host_wire_sha256':hashlib.sha256((host/'host/motortester/wire.py').read_bytes()).hexdigest(),'c_source_sha':PIN,'c_sha256':hashes,'seed':20261010,'counts':counts,'scope':'opaque byte codec and stream only; no session, Windows, USB or hardware'},indent=2))
