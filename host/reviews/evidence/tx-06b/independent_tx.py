#!/usr/bin/env python3
"""Independent Critic-B TX probes; no production source is changed.

Only the transport boundary is modeled. COBS/header/CRC expectations use Python
stdlib, never the production encoder/decoder. Payload semantic validation belongs
to the dispatcher and is deliberately not claimed here.
"""
import ctypes as C
import json
import random
import struct
import sys
import zlib

U8, U16, U32, U64, SZ = C.c_uint8, C.c_uint16, C.c_uint32, C.c_uint64, C.c_size_t
class Message(C.Structure):
    _fields_ = [("type", U8), ("session", U64), ("sequence", U32),
                ("request", U32), ("payload_length", U16), ("payload", U8 * 512)]
class Slot(C.Structure):
    _fields_ = [("message", Message), ("reply", C.c_uint)]
class Tx(C.Structure):
    _fields_ = [("slots", Slot * 6), ("frame", U8 * 544), ("count", SZ),
                ("length", SZ), ("offset", SZ), ("session", U64),
                ("next_sequence", U32), ("head_ms", U32), ("progress_ms", U32),
                ("stop_ms", U32), ("terminal_generation", U32),
                ("terminal_window", U32), ("terminal_type", U8),
                ("stopping", C.c_uint), ("fault", C.c_int)]
class Event(C.Structure):
    _fields_ = [("flags", C.c_uint), ("type", U8), ("session", U64),
                ("sequence", U32), ("request", U32), ("generation", U32),
                ("window_id", U32), ("accepted", SZ)]
CALLBACK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(U8), SZ)
lib = C.CDLL(sys.argv[1])
for name, args in {
    "mt_tx_init": [C.POINTER(Tx)],
    "mt_tx_hello": [C.POINTER(Tx), U64, C.POINTER(Message), U32],
    "mt_tx_enqueue": [C.POINTER(Tx), C.POINTER(Message), U32],
    "mt_tx_stop": [C.POINTER(Tx), U32, U32, C.POINTER(Message), C.POINTER(Message), C.POINTER(Message), U32],
    "mt_tx_poll": [C.POINTER(Tx), U32, CALLBACK, C.c_void_p, C.POINTER(Event)],
    "mt_tx_terminal_committed": [C.POINTER(Tx), U32, U32],
    "mt_tx_window_status": [C.POINTER(Tx), U32, U32],
}.items():
    getattr(lib, name).argtypes = args
    getattr(lib, name).restype = C.c_int
OK, ARG, NOT_NEG, FULL, DRAINING, FAULT, SEQUENCE, TIMEOUT, IO, IDLE, BLOCKED, PROGRESS = range(12)
counts = {}
checks = 0
def check(cond, note="check failed"):
    global checks
    checks += 1
    if not cond:
        raise RuntimeError(note)
def case(name):
    counts[name] = counts.get(name, 0) + 1
def cp(obj):
    return type(obj).from_buffer_copy(bytes(obj))
def put32(m, offset, value):
    m.payload[offset:offset+4] = struct.pack("<I", value)
def msg(kind, req=None, length=None):
    m = Message()
    m.type = kind
    m.request = (1 if kind == 129 else 8 if kind in (130,131,132,133,135,255) else 0) if req is None else req
    m.payload_length = {129:16,130:30,131:20,132:8,133:12,134:24,135:8,144:40,145:512,146:16,147:12,255:8}.get(kind, 0) if length is None else length
    if 144 <= kind <= 147:
        put32(m, 0, 7)
        put32(m, 4, 3)
    return m
def cobs(raw):
    out = bytearray([0]); start = 0; n = 1
    for b in raw:
        if b == 0:
            out[start] = n; start = len(out); out.append(0); n = 1
        else:
            out.append(b); n += 1
            if n == 255:
                out[start] = n; start = len(out); out.append(0); n = 1
    out[start] = n
    return bytes(out) + b"\0"
def wire(m, nonce, sequence):
    raw = struct.pack("<BBBBHHQII", 0, 1, m.type, 0, m.payload_length, 0, nonce, sequence, m.request) + bytes(m.payload[:m.payload_length])
    return cobs(raw + struct.pack("<I", zlib.crc32(raw)))
def decode_all(data):
    check(not data or data[-1] == 0, "unterminated wire frame")
    result = []
    for frame in bytes(data).split(b"\0")[:-1]:
        raw = bytearray(); i = 0
        while i < len(frame):
            code = frame[i]; check(code > 0 and i + code <= len(frame), "invalid COBS")
            raw += frame[i+1:i+code]; i += code
            if code < 255 and i < len(frame): raw.append(0)
        check(len(raw) >= 28, "short raw frame")
        header = struct.unpack("<BBBBHHQII", raw[:24])
        check(header[:2] == (0,1) and header[3] == 0 and header[5] == 0, "bad header")
        check(len(raw) == header[4] + 28, "bad payload length")
        check(struct.unpack("<I",raw[-4:])[0] == zlib.crc32(raw[:-4]), "CRC mismatch")
        result.append((header[2],header[6],header[7],header[8],bytes(raw[24:-4])))
    return result

class Rig:
    def __init__(self, now=0):
        self.tx = Tx(); self.data = bytearray(); self.quota = 64; self.error = None
        self.calls = 0; self.max_offer = 0; self.now = now; self.events = []
        @CALLBACK
        def write(_, data, length):
            self.calls += 1; self.max_offer = max(self.max_offer, length)
            if self.error is not None: return self.error if self.error < 0 else int(length) + 1
            n = min(length,self.quota); self.data.extend(C.string_at(data,n)); return n
        self.cb = write
        lib.mt_tx_init(C.byref(self.tx)); self.hello(11); self.drain()
        check(self.data == wire(msg(129),11,1), "HELLO encoding")
        self.data.clear(); self.events.clear()
    def hello(self, nonce):
        check(lib.mt_tx_hello(C.byref(self.tx),nonce,C.byref(msg(129)),self.now) == OK, "hello admission")
    def enqueue(self, m):
        return lib.mt_tx_enqueue(C.byref(self.tx),C.byref(m),self.now)
    def stop(self, active=3, request=9, reason=1, terminal=True):
        abort=msg(147); abort.payload[8]=reason
        ack=msg(133,request); put32(ack,0,7); put32(ack,4,3)
        status=msg(134); put32(status,4,7); put32(status,8,3)
        result = lib.mt_tx_stop(C.byref(self.tx),7,active,C.byref(abort) if terminal else None,C.byref(ack),C.byref(status),self.now)
        return result, [abort,ack,status]
    def poll(self):
        e=Event(); C.memset(C.byref(e),0xA5,C.sizeof(e)); before=bytes(e)
        r=lib.mt_tx_poll(C.byref(self.tx),self.now,self.cb,None,C.byref(e))
        check(self.max_offer <= 64, "write larger than 64 bytes")
        check(self.tx.count <= 6 and self.tx.offset <= self.tx.length <= 544, "queue/frame bound")
        if r==PROGRESS:
            check(e.accepted > 0 and e.accepted <=64, "bad event size")
            self.events.append(cp(e))
        else: check(bytes(e)==before,"event changed on non-progress")
        return r,e
    def prefix(self, n):
        while n:
            self.quota=min(n,64); old=len(self.data); r,_=self.poll()
            check(r==PROGRESS,"prefix progress"); n-=len(self.data)-old
        self.quota=0; seq=self.tx.next_sequence; off=self.tx.offset
        r,_=self.poll()
        check(r in (BLOCKED,IDLE),"zero write")
        check(self.tx.next_sequence==seq and self.tx.offset==off,"zero committed state")
    def drain(self):
        self.quota=64
        for _ in range(1000):
            r,_=self.poll()
            if r==IDLE: return
            check(r==PROGRESS,"drain failure")
        check(False,"unbounded drain")
    def expect(self, messages, nonce=11, seq=2):
        expected=b"".join(wire(m,nonce,seq+i) for i,m in enumerate(messages))
        check(bytes(self.data)==expected,"wire differs from independent byte oracle")
        check(len(decode_all(self.data))==len(messages),"frame count")

def stop_boundaries():
    for kind in (129,130,131,132,134,135,144,145,146,147,255):
        if kind == 129: continue # HELLO ACK is exercised separately below.
        m=msg(kind); m.payload[8 if kind==147 else 12]=2
        active=3 if kind>=144 and kind<=147 else 0
        frame=wire(m,11,2)
        for prefix in range(len(frame)+1):
            r=Rig(); check(r.enqueue(m)==OK,"enqueue initial")
            r.prefix(prefix)
            prior=msg(132,10); check(r.enqueue(prior)==OK,"prior reply admission")
            before=bytes(r.tx); result, tail=r.stop(active)
            check(result==OK,"stop reserved admission")
            after=bytes(r.tx); check(r.stop(active)[0]==DRAINING and bytes(r.tx)==after,"duplicate stop mutation")
            check(r.enqueue(msg(145))==DRAINING and bytes(r.tx)==after,"drain enqueue mutation")
            expected=[]
            if prefix>0 or m.request: expected.append(m)
            expected.append(prior)
            if active and not (kind in (146,147) and prefix>0):
                expected.append(m if kind==147 else tail[0])
            expected.extend(tail[1:]); r.drain(); r.expect(expected)
            stops=[e for e in r.events if e.flags&4]
            check(len(stops)==1 and stops[0].type==133 and stops[0].flags&2,"stop boundary flag")
            check(not r.tx.stopping,"stop remains latched")
            case("stop_byte_boundaries")

def hello_boundaries():
    for kind in (129,130,132,133,134,144,145,146,147,255):
        m=msg(kind)
        # Produce the dedicated ACK via its supported entry point.
        for prefix in range(len(wire(m,11,2))+1):
            r=Rig()
            if kind==129:
                r.hello(22); old_nonce=22; old_seq=1
            elif kind==133:
                result,tail=r.stop(0); check(result==OK); m=tail[1]; old_nonce=11; old_seq=2
            else:
                check(r.enqueue(m)==OK); old_nonce=11; old_seq=2
            length=len(wire(m,old_nonce,old_seq))
            if prefix>length: continue
            r.prefix(prefix)
            r.hello(33); r.hello(44); r.events.clear()
            check(r.tx.next_sequence==1,"HELLO sequence reset")
            result,tail=r.stop(0); check(result==OK)
            while r.tx.count:
                r.quota=1; result,e=r.poll(); check(result==PROGRESS)
                if e.type==133 and e.session!=44:
                    check(not(e.flags&4) and r.tx.stopping,"old Stop ACK released new session barrier")
            expected=(wire(m,old_nonce,old_seq) if prefix>0 else b"")+wire(msg(129),44,1)+wire(tail[1],44,2)+wire(tail[2],44,3)
            check(bytes(r.data)==expected,"HELLO replacement wire mismatch")
            decode_all(r.data)
            check(sum(bool(e.flags&4) for e in r.events)==1,"HELLO completion count")
            check(r.tx.next_sequence==4 and not r.tx.stopping,"new HELLO/Stop state")
            case("hello_byte_boundaries")

def resources_faults_timers():
    r=Rig(); data=msg(145); check(r.enqueue(data)==OK); r.prefix(1)
    for req in (7,8): check(r.enqueue(msg(132,req))==OK)
    before=bytes(r.tx); check(r.enqueue(msg(132,9))==FULL and bytes(r.tx)==before)
    check(r.stop()[0]==OK and r.tx.count==6,"full Stop capacity")
    r.drain(); check(r.tx.count==0); case("resource_limit")
    for kind in (144,145,146,147):
        m=msg(kind)
        for prefix in (0,1,len(wire(m,11,2))-1):
            for error in (-1,1):
                r=Rig(); check(r.enqueue(m)==OK); r.prefix(prefix); r.error=error
                before=bytes(r.tx); calls=r.calls; seq=r.tx.next_sequence
                check(r.poll()[0]==IO,"IO not latched"); check(r.calls==calls+1)
                check(r.tx.next_sequence==seq,"IO consumed sequence")
                calls=r.calls; check(r.poll()[0]==FAULT and r.calls==calls,"fault wrote bytes")
                check(r.enqueue(msg(132))==FAULT and r.stop()[0]==FAULT)
                r.error=None; r.hello(33); r.drain()
                expected=(wire(m,11,2) if prefix else b"")+wire(msg(129),33,1)
                check(bytes(r.data)==expected,"fault/HELLO byte preservation"); decode_all(r.data)
                case("io_recovery")
    for start in (0,0xFFFFFF00):
        for initial in (0,1):
            r=Rig(start); check(r.enqueue(msg(145))==OK); r.prefix(initial)
            r.now=(start+999)&0xFFFFFFFF; check(r.poll()[0]==BLOCKED)
            calls=r.calls; r.now=(start+1000)&0xFFFFFFFF; check(r.poll()[0]==TIMEOUT and r.calls==calls)
            check(r.poll()[0]==FAULT); case("stall_timeout")
        r=Rig(start); check(r.enqueue(msg(145))==OK); r.quota=1
        for delta in (0,900,1800,2700,2999):
            r.now=(start+delta)&0xFFFFFFFF; check(r.poll()[0]==PROGRESS)
        r.now=(start+3000)&0xFFFFFFFF; check(r.poll()[0]==TIMEOUT); case("frame_timeout")
        r=Rig(start); check(r.enqueue(msg(145))==OK); r.prefix(1); check(r.stop()[0]==OK); r.quota=1
        r.now=(start+999)&0xFFFFFFFF; check(r.poll()[0]==PROGRESS)
        r.now=(start+1000)&0xFFFFFFFF; check(r.poll()[0]==TIMEOUT); case("stop_timeout")
    r=Rig(); r.tx.next_sequence=0xFFFFFFFE; check(r.enqueue(msg(146))==OK); r.prefix(1)
    check(r.tx.next_sequence==0xFFFFFFFF and lib.mt_tx_terminal_committed(C.byref(r.tx),7,3)==146)
    r.drain(); check(r.enqueue(msg(132))==OK); calls=r.calls
    check(r.poll()[0]==SEQUENCE and r.calls==calls); r.hello(22); r.drain(); case("sequence_boundary")
    r=Rig(); check(r.enqueue(msg(145))==OK); r.prefix(1); lib.mt_tx_init(C.byref(r.tx))
    check(r.tx.count==r.tx.session==r.tx.offset==0 and r.enqueue(msg(145))==NOT_NEG); case("physical_reset")

def random_integrity():
    rng=random.Random(20261010)
    for _ in range(2000):
        length=rng.randrange(7,257)*2; m=msg(145,length=length)
        m.payload[8:length]=rng.randbytes(length-8)
        r=Rig(); check(r.enqueue(m)==OK); frozen=cp(m)
        for i in range(512): m.payload[i]=0xA5
        while r.tx.count:
            r.quota=rng.randrange(0,81); result,_=r.poll()
            check(result in (PROGRESS,BLOCKED))
        r.expect([frozen]); case("random_fragmentation_copy")

def stop_lifecycle_and_atomicity():
    # A Stop before any ARM has generation0, last_window0, and no terminal.
    r=Rig(); ack=msg(133,2); status=msg(134)
    check(lib.mt_tx_stop(C.byref(r.tx),0,0,None,C.byref(ack),C.byref(status),0)==OK)
    r.drain(); r.expect([ack,status]); case("initial_stop_no_window")
    # Allocated capture with no queued BEGIN must still emit one early ABORT.
    r=Rig(); result,tail=r.stop(3); check(result==OK); r.drain(); r.expect(tail)
    check(lib.mt_tx_window_status(C.byref(r.tx),7,3)==5); case("early_abort_no_begin")
    # Repeat after ACK, at every byte boundary of the trailing STATUS.
    sample=msg(134); put32(sample,4,7); put32(sample,8,3)
    for prefix in range(len(wire(sample,11,3))+1):
        r=Rig(); result,tail=r.stop(0); check(result==OK)
        r.quota=1
        while True:
            result,e=r.poll(); check(result==PROGRESS)
            if e.flags&4: break
        check(r.tx.count==1 and not r.tx.stopping,"ACK did not release barrier")
        r.prefix(prefix); prior=msg(132,10); check(r.enqueue(prior)==OK)
        result,newtail=r.stop(0,11); check(result==OK)
        r.drain(); expected=[tail[1]]+([tail[2]] if prefix else [])+[prior]+newtail[1:]
        r.expect(expected); check(sum(e[0]==147 for e in decode_all(r.data))==0)
        case("repeat_stop_status_boundaries")
    # Rejected Stop must preserve an already serialized/partially sent frame.
    for prefix in (0,1):
        for defect in range(14):
            r=Rig(); check(r.enqueue(msg(145))==OK); r.prefix(prefix)
            abort=msg(147); ack=msg(133,9); status=msg(134)
            put32(ack,0,7); put32(ack,4,3); put32(status,4,7); put32(status,8,3)
            gen=7; win=3; terminal=C.byref(abort)
            if defect==0: gen=0
            elif defect==1: ack.type=132
            elif defect==2: ack.request=0
            elif defect==3: ack.payload_length=11
            elif defect==4: put32(ack,0,8)
            elif defect==5: put32(ack,4,4)
            elif defect==6: status.type=133
            elif defect==7: status.request=9
            elif defect==8: status.payload[0]=1
            elif defect==9: status.payload[1]=1
            elif defect==10: put32(status,4,8)
            elif defect==11: put32(status,8,4)
            elif defect==12: put32(abort,4,4)
            else: terminal=None
            before=bytes(r.tx)
            check(lib.mt_tx_stop(C.byref(r.tx),gen,win,terminal,C.byref(ack),C.byref(status),0)==ARG)
            check(bytes(r.tx)==before,"invalid Stop changed queue"); case("invalid_stop_atomicity")

def argument_matrix():
    r=Rig(); base=bytes(r.tx)
    for kind in range(256):
        for length in (0,1,7,8,11,12,13,14,15,16,19,20,23,24,29,30,39,40,510,511,512,513):
            for request in (0,2):
                m=msg(kind,request,length)
                valid=((kind in (130,131,132,135,255) and request!=0 and length=={130:30,131:20,132:8,135:8,255:8}[kind]) or
                       (kind==134 and length==24) or (request==0 and ((kind in (144,146,147) and length=={144:40,146:16,147:12}[kind]) or
                       (kind==145 and 14<=length<=512 and length%2==0))))
                result=r.enqueue(m)
                check(result==(OK if valid else ARG),f"admission mismatch {kind}/{length}/{request}")
                if valid: C.memmove(C.byref(r.tx),base,len(base))
                else: check(bytes(r.tx)==base,"invalid enqueue mutated state")
                case("argument_shape")
    for kind in (144,145,146,147):
        for field in (0,4):
            m=msg(kind); put32(m,field,0); check(r.enqueue(m)==ARG and bytes(r.tx)==base); case("zero_window_identity")
    for field in ("session","sequence"):
        m=msg(132); setattr(m,field,1); check(r.enqueue(m)==ARG and bytes(r.tx)==base); case("caller_header_rejected")
    check(lib.mt_tx_enqueue(None,None,0)==ARG); check(lib.mt_tx_hello(None,0,None,0)==ARG)
    check(lib.mt_tx_stop(None,0,0,None,None,None,0)==ARG)
    e=Event(); check(lib.mt_tx_poll(None,0,r.cb,None,C.byref(e))==ARG); case("null_guards")

def main():
    check(C.sizeof(Message)==544 and C.sizeof(Tx)==3928 and C.sizeof(Slot)==552,"unexpected host ABI")
    if len(sys.argv)>2 and sys.argv[2]=="regression":
        hello_boundaries()
    else:
        stop_boundaries(); hello_boundaries(); resources_faults_timers(); random_integrity(); stop_lifecycle_and_atomicity(); argument_matrix()
    print(json.dumps({"status":"PASS","seed":20261010,"counts":counts,"checks":checks,
                      "scope":"host native TX queue; independent COBS/header/CRC byte oracle; no dispatcher/USB/Windows/hardware proof"},indent=2))
if __name__=="__main__": main()
