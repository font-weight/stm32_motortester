"""Independent black-box RX session oracle; source path is argv[1]."""
import ctypes as C, json, random, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); here=Path(__file__).resolve().parent
subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-shared','-fPIC',str(root/'firmware/protocol/mt_command.c'),str(root/'firmware/protocol/mt_session.c'),'-o',str(here/'session.so')],check=True)
class Msg(C.Structure):
    _fields_=[('type',C.c_uint8),('session',C.c_uint64),('sequence',C.c_uint32),('request',C.c_uint32),('payload_length',C.c_uint16),('payload',C.c_uint8*512)]
class Session(C.Structure):
    _fields_=[('nonce',C.c_uint64),('next_sequence',C.c_uint32),('last_request',C.c_uint32),('active',C.c_uint),('have_previous',C.c_uint),('previous',Msg)]
lib=C.CDLL(str(here/'session.so'))
lib.mt_session_receive.argtypes=[C.POINTER(Session),C.POINTER(Msg)];lib.mt_session_receive.restype=C.c_int
lib.mt_session_init.argtypes=[C.POINTER(Session)];lib.mt_session_init.restype=None
lib.mt_session_require_hello.argtypes=[C.POINTER(Session)];lib.mt_session_require_hello.restype=None
names=['ACCEPT','NEW_HELLO','DUPLICATE','WRONG_SESSION','NEED_HELLO','INVALID_HELLO','RECOVERY','EXHAUSTED','ARGUMENT']
DEVICE=set(range(129,136))|set(range(144,148))|{255}; MAX=(1<<32)-1
rng=random.Random(20261010); count=0; hist={n:0 for n in names}
def msg(t,session,seq,req,p=b''):
    m=Msg();C.memset(C.addressof(m),0xa7,C.sizeof(m));m.type=t;m.session=session;m.sequence=seq;m.request=req;m.payload_length=len(p)
    for i,v in enumerate(p):m.payload[i]=v
    return m
def hello(n):return msg(1,0,1,1,n.to_bytes(8,'little'))
def identity(m):return (m.type,m.session,m.sequence,m.request,bytes(m.payload[:m.payload_length]))
class Oracle:
    def __init__(self):self.nonce=0;self.reset()
    def reset(self):self.active=0;self.next=0;self.last=0;self.prev=None
    def recv(self,m):
        if m.payload_length>512:return 8
        if m.type in DEVICE:self.reset();return 6 # precedence explicitly stated in section Session/order
        if m.type==1:
            n=int.from_bytes(bytes(m.payload[:8]),'little')
            if (m.session,m.sequence,m.request,m.payload_length)!=(0,1,1,8) or n==0:return 5
            if n==self.nonce:return 2
            self.nonce=n;self.reset();self.active=1;self.next=2;self.last=1;return 1
        if not self.active:return 4
        if m.session!=self.nonce:return 3
        ident=identity(m)
        if self.prev is not None and m.sequence==self.prev[2]:
            if ident==self.prev:return 2
            self.reset();return 6
        if MAX in (m.sequence,m.request):self.reset();return 7
        if m.sequence!=self.next or not (2<=m.request>self.last):self.reset();return 6
        self.prev=ident;self.next=m.sequence+1;self.last=m.request;return 0
s=Session();o=Oracle();lib.mt_session_init(C.byref(s))
def check(m):
    global count
    pre=bytes(s); event=o.recv(m); got=lib.mt_session_receive(C.byref(s),C.byref(m));count+=1;hist[names[got]]+=1
    assert got==event,(count,identity(m),names[got],names[event])
    assert (s.nonce,s.active,s.next_sequence,s.last_request,s.have_previous)==(o.nonce,o.active,o.next,o.last,int(o.prev is not None))
    if o.prev is not None:assert identity(s.previous)==o.prev
    if got in (2,3,4,5,8):assert bytes(s)==pre,('mutation on ignored input',got)
    return got
# Every type at all payload lengths, with a fresh session: payload validity belongs to parser.
for t in range(256):
    for length in (0,1,4,7,8,12,16,511,512):
        check(hello(1000+count));m=msg(t,o.nonce,2,2,bytes((i+t)&255 for i in range(length)))
        expected=6 if t in DEVICE else (5 if t==1 else 0)
        assert check(m)==expected
        if expected==0:
            assert check(m)==2
            if length<512:m.payload[511]^=255;assert check(m)==2
# Malformed HELLO headers and nonces preserve a live session and previous frame.
check(hello(555));check(msg(2,555,2,2))
for attr,val in [('session',555),('sequence',0),('sequence',2),('request',0),('request',2),('payload_length',7),('payload_length',9)]:
    m=hello(777);setattr(m,attr,val);assert check(m)==5
assert check(hello(0))==5
# Wrong-session ordinary messages are ignored, regardless of their sequence/request.
for t in (0,2,3,4,5,6,7,127,136,143,254):
    for seq in (0,1,2,9,MAX):
        assert check(msg(t,999,seq,MAX))==3
# Confirm the direction-first exception to the blanket wrong-session sentence.
assert check(msg(129,999,2,2))==6
check(hello(888))
# Explicit exhaustion boundary with a reachable abstract late-session state.
s.next_sequence=o.next=MAX-1;s.last_request=o.last=MAX-2
m=msg(6,o.nonce,MAX-1,MAX-1);assert check(m)==0;assert check(m)==2
assert check(msg(6,o.nonce,MAX,MAX-1))==7
assert check(hello(o.nonce))==2;assert not s.active
check(hello(o.nonce+1));assert check(msg(6,o.nonce,2,MAX))==7
# Oversize API guard leaves all state unchanged.
check(hello(890));m=msg(2,890,2,2);m.payload_length=513;assert check(m)==8
# Deterministic random mixed traces across malformed, valid, duplicate and recovery actions.
for _ in range(100000):
    action=rng.randrange(12)
    if action==0:m=hello(rng.randrange(1,1<<63))
    elif action==1:m=hello(o.nonce or 1)
    elif action in (2,3,4):
        m=msg(rng.choice([2,3,4,5,6,7,0,127,136,143,254]),o.nonce,o.next,min(MAX,o.last+rng.randrange(1,8)),rng.randbytes(rng.randrange(513)))
    elif action in (5,6) and o.prev:
        t,n,seq,req,p=o.prev;m=msg(t,n,seq,req,p)
        if action==6:m.request=(m.request+1)&MAX
    elif action==7:m=msg(rng.choice([2,3,127]),o.nonce^1,rng.randrange(MAX+1),rng.randrange(MAX+1))
    elif action==8:m=msg(rng.choice(tuple(DEVICE)),o.nonce,rng.randrange(8),rng.randrange(8))
    elif action==9:m=msg(3,o.nonce,o.next,MAX)
    elif action==10:m=hello(0)
    else:m=msg(rng.randrange(256),o.nonce,rng.randrange(10),rng.randrange(10),rng.randbytes(rng.randrange(513)))
    check(m)
lib.mt_session_init(None);lib.mt_session_require_hello(None)
assert lib.mt_session_receive(None,C.byref(hello(1)))==8
before=bytes(s);assert lib.mt_session_receive(C.byref(s),None)==8;assert before==bytes(s)
print(json.dumps({'result':'PASS','source':str(root),'seed':20261010,'comparisons':count,'events':hist,'sizeof_session_x64':C.sizeof(Session),'sizeof_message_x64':C.sizeof(Msg),'direction_before_session':True},indent=2))
