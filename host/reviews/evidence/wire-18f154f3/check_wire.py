import ctypes as C, json, random, struct, zlib
from pathlib import Path
root=Path(__file__).parent
lib=C.CDLL(str(root/'libmt_wire.so'))
class Message(C.Structure):
    _fields_=[('type',C.c_uint8),('session',C.c_uint64),('sequence',C.c_uint32),('request',C.c_uint32),('payload_length',C.c_uint16),('payload',C.c_uint8*512)]
lib.mt_wire_encode.argtypes=[C.POINTER(Message),C.POINTER(C.c_uint8),C.c_size_t,C.POINTER(C.c_size_t)]
lib.mt_wire_decode.argtypes=[C.POINTER(C.c_uint8),C.c_size_t,C.POINTER(Message)]
lib.mt_wire_crc32.argtypes=[C.POINTER(C.c_uint8),C.c_size_t]
lib.mt_wire_crc32.restype=C.c_uint32
counts={}
def note(k): counts[k]=counts.get(k,0)+1
def cbuf(b): return (C.c_uint8*len(b)).from_buffer_copy(b)
def cobs(raw):
    # Independent block decomposition, not the C encoder's rolling mark/code loop.
    blocks=[]
    for part in raw.split(b'\0'):
        while len(part)>=254:
            blocks.append(b'\xff'+part[:254]); part=part[254:]
        blocks.append(bytes([len(part)+1])+part)
    return b''.join(blocks)
def rawmsg(m):
    body=struct.pack('<BBBBHHQII',0,1,m.type,0,m.payload_length,0,m.session,m.sequence,m.request)+bytes(m.payload[:m.payload_length])
    return body+struct.pack('<I',zlib.crc32(body))
def message(payload,typ=127,session=0x0807060504030201,seq=1,req=2):
    m=Message(type=typ,session=session,sequence=seq,request=req,payload_length=len(payload));m.payload[:len(payload)]=payload;return m
def decode(frame,expected=None):
    m=Message(); C.memset(C.byref(m),0xa5,C.sizeof(m)); before=bytes(m)
    guard=(C.c_uint8*(len(frame)+32))(*([0xa5]*16+list(frame)+[0xa5]*16))
    ptr=C.cast(C.byref(guard,16),C.POINTER(C.c_uint8)); code=lib.mt_wire_decode(ptr,len(frame),C.byref(m))
    assert bytes(guard)==b'\xa5'*16+frame+b'\xa5'*16
    if code: assert bytes(m)==before,('output modified',code)
    if expected is not None: assert code==expected,(code,expected,frame.hex())
    return code,m
assert lib.mt_wire_crc32(cbuf(b'123456789'),9)==zlib.crc32(b'123456789')==0xcbf43926
assert lib.mt_wire_crc32(None,0)==0
v=json.loads((root/'vectors-v0.1.json').read_text())['vectors'][0]
m=message(bytes.fromhex(v['expected']['payload_hex']))
assert rawmsg(m).hex()==v['decoded_hex']
assert (cobs(rawmsg(m))+b'\0').hex()==v['frame_hex'];note('published_vector')
rng=random.Random(20261010)
for n in range(513):
    for payload in (bytes(n),b'\xff'*n,bytes(i%256 for i in range(n)),rng.randbytes(n)):
        m=message(payload,typ=rng.randrange(256),session=rng.getrandbits(64),seq=rng.getrandbits(32),req=rng.getrandbits(32)); before=bytes(m)
        guard=(C.c_uint8*576)(*([0xa5]*576));written=C.c_size_t(999)
        ptr=C.cast(C.byref(guard,16),C.POINTER(C.c_uint8))
        assert lib.mt_wire_encode(C.byref(m),ptr,544,C.byref(written))==0
        got=bytes(guard)[16:16+written.value];expected=cobs(rawmsg(m))+b'\0'
        assert got==expected,(n,got.hex(),expected.hex())
        assert bytes(guard)[:16]==bytes(guard)[560:]==b'\xa5'*16
        assert bytes(m)==before and 29<=written.value<=544
        _,opened=decode(got[:-1],0)
        assert (opened.type,opened.session,opened.sequence,opened.request,opened.payload_length,bytes(opened.payload[:n]))==(m.type,m.session,m.sequence,m.request,n,payload)
        note('encode_decode_oracle')
# All undersized output capacities must leave destination and written unchanged.
m=message(b'abc')
for cap in range(544):
    out=(C.c_uint8*544)(*([0xa5]*544));written=C.c_size_t(999)
    assert lib.mt_wire_encode(C.byref(m),out,cap,C.byref(written))==2
    assert bytes(out)==b'\xa5'*544 and written.value==999;note('capacity_rejection')
m.payload_length=513;out=(C.c_uint8*544)(*([0xa5]*544));written=C.c_size_t(999)
assert lib.mt_wire_encode(C.byref(m),out,544,C.byref(written))==4
assert bytes(out)==b'\xa5'*544 and written.value==999;note('oversized_encode')
raw=rawmsg(message(bytes(range(64))))
def framed_mutation(offset,value,repair=True):
    x=bytearray(raw);x[offset]=value
    if repair:x[-4:]=struct.pack('<I',zlib.crc32(x[:-4]))
    return cobs(bytes(x))
for offset,value,code in [(0,1,6),(1,2,6),(3,1,7),(6,1,7),(7,1,7),(4,63,4),(4,65,4),(5,3,4)]:
    decode(framed_mutation(offset,value),code);note('structured_rejection')
for offset in range(len(raw)):
    for bit in range(8):
        x=bytearray(raw);x[offset]^=1<<bit
        assert decode(cobs(bytes(x)))[0]!=0;note('single_bit_rejection')
frame=cobs(raw)
for i in range(len(frame)):
    assert decode(frame[:i])[0]!=0;note('truncation_rejection')
for frame,code in [(b'',4),(b'\0',3),(b'\x02',3),(b'\x02\0',3),(b'\xff'*543,3),(b'\x01'*544,4),(b'\x01'*545,4)]:
    decode(frame,code);note('framing_length_rejection')
for _ in range(100000):
    b=rng.randbytes(rng.randrange(0,545))
    code,_=decode(b)
    assert code!=0;note('random_rejection')
print(json.dumps({'status':'PASS','counts':counts,'seed':20261010,'compiler':'GCC 14.2.0','host':'Linux x86_64','scope':'byte codec only; no USB, stream state, handshake, Windows or hardware'},indent=2))
