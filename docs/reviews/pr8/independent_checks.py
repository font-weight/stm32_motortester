import ctypes as c, json, random, struct, zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
lib=c.CDLL(str(ROOT/'review-artifacts/libmt.so'))
class Msg(c.Structure):
    _fields_=[('type',c.c_uint8),('session',c.c_uint64),('sequence',c.c_uint32),('request',c.c_uint32),('payload_length',c.c_uint16),('payload',c.c_uint8*512)]
class Config(c.Structure):
    _fields_=[('profile',c.c_uint16),('mode',c.c_uint8),('source',c.c_uint8),('edge',c.c_uint8),('level_code',c.c_uint16),('pretrigger_samples',c.c_uint16),('auto_timeout_ms',c.c_uint32)]
class Release(c.Structure):
    _fields_=[('generation',c.c_uint32),('window_id',c.c_uint32),('samples_crc32',c.c_uint32)]
class Value(c.Union):
    _fields_=[('nonce',c.c_uint64),('config',Config),('config_id',c.c_uint32),('generation',c.c_uint32),('release',Release)]
class Command(c.Structure):
    _fields_=[('type',c.c_uint8),('value',Value)]
class Support(c.Structure):
    _fields_=[('mode_bits',c.c_uint8),('source_bits',c.c_uint8),('edge_bits',c.c_uint8),('pretrigger',c.c_uint8)]
class Stream(c.Structure):
    _fields_=[('bytes',c.c_uint8*543),('used',c.c_size_t),('first_ms',c.c_uint32),('last_ms',c.c_uint32),('discarding',c.c_uint),('last_decode',c.c_int)]
lib.mt_wire_encode.argtypes=[c.POINTER(Msg),c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)]
lib.mt_wire_decode.argtypes=[c.c_void_p,c.c_size_t,c.POINTER(Msg)]
lib.mt_wire_crc32.argtypes=[c.c_void_p,c.c_size_t];lib.mt_wire_crc32.restype=c.c_uint32
lib.mt_command_parse.argtypes=[c.POINTER(Msg),c.POINTER(Command)]
lib.mt_command_config_valid.argtypes=[c.POINTER(Config),c.POINTER(Support)]
lib.mt_stream_init.argtypes=[c.POINTER(Stream)]
lib.mt_stream_feed.argtypes=[c.POINTER(Stream),c.c_uint8,c.c_uint32,c.POINTER(Msg)]
lib.mt_stream_poll.argtypes=[c.POINTER(Stream),c.c_uint32]
rng=random.Random(20261010)

def cobs(data):
    result=bytearray();block=bytearray()
    for b in data:
        if b==0:
            result.extend(bytes([len(block)+1])+block);block.clear()
        else:
            block.append(b)
            if len(block)==254:
                result.extend(b'\xff'+block);block.clear()
    result.extend(bytes([len(block)+1])+block)
    return bytes(result)

def raw(m):
    data=struct.pack('<BBBBHHQII',0,1,m.type,0,m.payload_length,0,m.session,m.sequence,m.request)+bytes(m.payload[:m.payload_length])
    return data+struct.pack('<I',zlib.crc32(data))

def msg(payload=b'',t=127):
    m=Msg();m.type=t;m.session=rng.getrandbits(64);m.sequence=rng.getrandbits(32);m.request=rng.getrandbits(32);m.payload_length=len(payload)
    for i,b in enumerate(payload):m.payload[i]=b
    return m

def dec(frame,expected=0):
    out=Msg();c.memset(c.byref(out),0xa5,c.sizeof(out));saved=bytes(out)
    rc=lib.mt_wire_decode(frame,len(frame),c.byref(out))
    assert rc==expected,(rc,expected,frame.hex())
    if rc:assert bytes(out)==saved
    return out

assert lib.mt_wire_crc32(b'123456789',9)==0xcbf43926
assert lib.mt_wire_crc32(None,0)==0
vector=json.loads((ROOT/'protocol/vectors-v0.1.json').read_text())['vectors'][0]
assert cobs(bytes.fromhex(vector['decoded_hex']))+b'\x00'==bytes.fromhex(vector['frame_hex'])
assert zlib.crc32(bytes.fromhex(vector['decoded_hex'])[:-4])==struct.unpack('<I',bytes.fromhex(vector['decoded_hex'])[-4:])[0]
dec(bytes.fromhex(vector['frame_hex'])[:-1])
roundtrips=0
for n in range(513):
    for payload in [bytes(n),b'\x01'*n,b'\xff'*n,bytes(i%256 for i in range(n)),rng.randbytes(n)]:
        m=msg(payload);out=(c.c_uint8*544)();written=c.c_size_t(0);assert lib.mt_wire_encode(c.byref(m),out,544,c.byref(written))==0
        expected=cobs(raw(m))+b'\x00';assert bytes(out[:written.value])==expected;assert written.value<=544
        decoded=dec(expected[:-1]);assert (decoded.type,decoded.session,decoded.sequence,decoded.request,decoded.payload_length,bytes(decoded.payload[:n]))==(m.type,m.session,m.sequence,m.request,n,payload)
        roundtrips+=1
for capacity in range(544):
    m=msg(b'abc');out=(c.c_uint8*544)(*([0xa5]*544));written=c.c_size_t(123)
    assert lib.mt_wire_encode(c.byref(m),out,capacity,c.byref(written))==2
    assert bytes(out)==b'\xa5'*544 and written.value==123
m=msg(b'abc');r=bytearray(raw(m))
# Independently recompute CRC for semantic header failures.
for offset,value,expected in [(0,1,6),(1,2,6),(3,1,7),(6,1,7),(7,1,7),(4,4,4)]:
    bad=bytearray(r);bad[offset]=value;bad[-4:]=struct.pack('<I',zlib.crc32(bad[:-4]));dec(cobs(bad),expected)
bad=bytearray(r);bad[24]^=1;dec(cobs(bad),5)
for frame,expected in [(b'',4),(b'\0',3),(b'\2',3),(b'\2\0',3),(b'\1'*544,4),(b'\1'*543,4)]:dec(frame,expected)
# Every truncation of a valid frame is rejected and leaves the output untouched.
f=cobs(raw(msg(rng.randbytes(512))))
for end in range(len(f)):
    o=Msg();c.memset(c.byref(o),0xa5,c.sizeof(o));before=bytes(o)
    assert lib.mt_wire_decode(f[:end],end,c.byref(o))!=0 and bytes(o)==before
# Arbitrary decode inputs: check failure atomicity and successful canonical validity.
for _ in range(20000):
    f=rng.randbytes(rng.randrange(700));o=Msg();c.memset(c.byref(o),0xa5,c.sizeof(o));before=bytes(o)
    rc=lib.mt_wire_decode(f,len(f),c.byref(o))
    if rc:assert bytes(o)==before
    else:assert zlib.crc32(raw(o)[:-4])==struct.unpack('<I',raw(o)[-4:])[0]
# Host-command payload layouts independently produced from wire tables.
values=[(1,struct.pack('<Q',0x102030405060708)),(2,b''),(3,struct.pack('<HBBBBHHHI',1,0,1,1,0,4094,1024,0,60000)),(4,struct.pack('<I',0xfedcba98)),(5,struct.pack('<I',0x76543210)),(6,b''),(7,struct.pack('<III',0xffffffff,0x89abcdef,0x76543210))]
for typ,payload in values:
    m=msg(payload,typ);out=Command();assert lib.mt_command_parse(c.byref(m),c.byref(out))==0 and out.type==typ
    if typ==1:assert out.value.nonce==0x102030405060708
    if typ==3:assert tuple(getattr(out.value.config,k) for k in ['profile','mode','source','edge','level_code','pretrigger_samples','auto_timeout_ms'])==(1,0,1,1,4094,1024,60000)
    if typ==4:assert out.value.config_id==0xfedcba98
    if typ==5:assert out.value.generation==0x76543210
    if typ==7:assert (out.value.release.generation,out.value.release.window_id,out.value.release.samples_crc32)==(0xffffffff,0x89abcdef,0x76543210)
    for size in range(513):
        if size==len(payload):continue
        m.payload_length=size;c.memset(c.byref(out),0xa5,c.sizeof(out));before=bytes(out)
        assert lib.mt_command_parse(c.byref(m),c.byref(out))==4 and bytes(out)==before
# Every declared/missing support bit versus every permissible combination.
config_cases=0
for modes in range(8):
 for sources in range(4):
  for edges in range(4):
   for pre in range(2):
    support=Support(modes,sources,edges,pre)
    for mode in range(3):
     for source in range(2):
      for edge in range(2):
       for samples in [0,512,1024]:
        cfg=Config(1,mode,source,edge,1,samples,100 if mode==0 else 0)
        expected=bool(modes&(1<<mode) and sources&(1<<source) and edges&(1<<edge) and (not samples or pre))
        assert bool(lib.mt_command_config_valid(c.byref(cfg),c.byref(support)))==expected
        config_cases+=1
print(json.dumps({'exact_sha':'0f285510c1b9b6bd21bb7b60ae6098f7584ae71a','status':'PASS for listed checks only','independent_crc_and_vector':True,'codec_roundtrips':roundtrips,'capacity_refusals':544,'random_decode_inputs':20000,'host_layout_vectors':len(values),'wrong_command_lengths':3584,'config_support_cases':config_cases,'host_sizes':{x.__name__:c.sizeof(x) for x in [Msg,Config,Command,Support,Stream]}},indent=2))
