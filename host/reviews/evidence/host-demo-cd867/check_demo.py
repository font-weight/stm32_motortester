"""Critic-B standalone exact-SHA integration and fail-closed probes."""
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import csv, hashlib, json, math, random, struct, sys, tempfile, tracemalloc, xml.etree.ElementTree as ET, zlib
from uuid import UUID
root=Path(sys.argv[1]).resolve(); out=Path(__file__).resolve().parent; sys.path.insert(0,str(root/'host'))
from motortester.wire_demo import synthetic_window, capture_from_synthetic_stream, save_synthetic_stream, run_demo, CONTEXT
from motortester.wire import decode_frame, encode_frame, Frame
from motortester.recording import load, encode
from motortester.model import CaptureError
work=Path(tempfile.mkdtemp(prefix='probe-run-',dir=out))
STAMP='2026-10-10T12:00:00+00:00'; counts={}; rng=random.Random(5005)
def note(k):counts[k]=counts.get(k,0)+1
samples,wire=synthetic_window();frames=[decode_frame(p) for p in wire.split(b'\0') if p]
def transcript(fs):return b''.join(encode_frame(f) for f in fs)
def reject(data,tag,**kw):
    path=work/'rejected.mtcap';before=set(work.glob('*.tmp'))
    try:save_synthetic_stream(path,data,received_at_utc=STAMP,**kw)
    except CaptureError:pass
    else:raise AssertionError(('accepted invalid stream',tag))
    assert not path.exists(),tag
    assert set(work.glob('*.tmp'))==before
    note('invalid_stream_no_file')
# Independent expected waveform, not equality only to the production generator.
for win in range(1,21):
    wanted=tuple(([0,win,256,4095]+[round(2048+1000*math.sin(2*math.pi*(i-512)/100)) for i in range(4,2048)]))
    generated,data=synthetic_window(win,11*(win-1)+1)
    assert generated==wanted
    for chunk in (1,2,28,30,96,98,541,542,543,544,545,1024,4096):
        got=capture_from_synthetic_stream(data,first_sequence=11*(win-1)+1,chunk_size=chunk,received_at_utc=STAMP)
        assert got.samples==wanted
        assert (got.session_id,got.sequence,got.nominal_fs_hz,got.received_at_utc,got.origin,got.trigger_kind,got.trigger_index,got.trigger_edge,got.trigger_level_code,got.pretrigger_samples,got.firmware_version)==(str(UUID(int=CONTEXT.session)),win,50000,STAMP,'simulator','signal',512,'rising',2048,512,None)
        assert got.protocol_version=='draft-0.1@124b3f32e1e7f7282bce283baa4c9406d4d72950'
        assert got.quality_flags==('rail_observed','draft_protocol','nominal_timing_unverified')
        assert got.time_seconds(512)==0 and got.time_seconds(0)==-512/50000
        note('positive_chunk_window_metadata')
# Header changes with freshly valid framing/CRC, every frame position.
for i,f in enumerate(frames):
    for attr,value in [('session',f.session+1),('sequence',f.sequence+1),('request_id',1),('message_type',134)]:
        fs=frames.copy();fs[i]=replace(f,**{attr:value});reject(transcript(fs),(i,attr))
    reject(transcript(frames[:i]+frames[i+1:]),('missing',i))
    fs=frames.copy();fs.insert(i,f);reject(transcript(fs),('repeat',i))
# All fields of BEGIN: incompatible context, invalid event/quality and range values.
for offset,fmt,value in [(0,'I',3),(4,'I',0),(8,'I',2),(12,'H',2),(14,'H',2047),(16,'H',0),(18,'H',511),(20,'B',2),(21,'B',1),(22,'B',1),(23,'B',1),(24,'H',2047),(26,'H',0),(26,'H',6),(28,'I',71999999),(32,'H',72),(34,'H',20),(36,'I',17)]:
    p=bytearray(frames[0].payload);struct.pack_into('<'+fmt,p,offset,value)
    reject(transcript([replace(frames[0],payload=bytes(p))]+frames[1:]),('begin',offset,value))
# Data range/id/order/count and END consistency, all envelope checksums repaired.
for offset,fmt,value in [(0,'I',3),(4,'I',2),(8,'H',1),(10,'H',249),(12,'H',4096)]:
    p=bytearray(frames[1].payload);struct.pack_into('<'+fmt,p,offset,value)
    reject(transcript([frames[0],replace(frames[1],payload=bytes(p))]+frames[2:]),('data',offset))
for offset,fmt,value in [(0,'I',3),(4,'I',2),(8,'H',2047),(10,'H',1),(12,'I',0)]:
    p=bytearray(frames[-1].payload);struct.pack_into('<'+fmt,p,offset,value)
    reject(transcript(frames[:-1]+[replace(frames[-1],payload=bytes(p))]),('end',offset))
for reason in range(1,7):
    abort=Frame(147,CONTEXT.session,frames[-1].sequence,0,struct.pack('<IIHH',2,1,reason,0))
    reject(transcript(frames[:-1]+[abort]),('abort',reason))
for _ in range(200):
    pos=rng.randrange(len(wire));mutated=bytearray(wire);mutated[pos]^=1<<rng.randrange(8)
    reject(bytes(mutated),('one_bit',pos),chunk_size=rng.choice([1,97,4096]))
for cut in sorted(set(range(0,len(wire),137))|{len(wire)-1,len(wire)-2}):reject(wire[:cut],('truncated',cut))
for suffix in (b'\1',b'\x05\1',encode_frame(frames[-1]),encode_frame(replace(frames[0],sequence=12))):reject(wire+suffix,('tail',suffix[:4]))
# Direct input resource guard: bytes too large are rejected before another large allocation.
huge=b'\1'*(8*1024*1024);tracemalloc.start()
try:
    reject(huge,'huge');current,peak=tracemalloc.get_traced_memory();assert peak<256*1024,peak
finally:tracemalloc.stop()
# Valid transcript cannot overwrite; publication error does not leave a successful file.
keep=work/'keep.mtcap';keep.write_bytes(b'KEEP')
try:save_synthetic_stream(keep,wire,received_at_utc=STAMP)
except FileExistsError:pass
else:raise AssertionError('clobber accepted')
assert keep.read_bytes()==b'KEEP';note('no_clobber')
failed=work/'failed.mtcap'
with patch('motortester.recording.os.link',side_effect=OSError('review injected publish error')):
    try:save_synthetic_stream(failed,wire,received_at_utc=STAMP)
    except OSError:pass
    else:raise AssertionError('publish error suppressed')
assert not failed.exists() and not list(work.glob('.*.tmp'));note('atomic_publish_failure')
for kw in ({'windows':0},{'windows':21},{'windows':True},{'chunk_size':0},{'chunk_size':4097}):
    path=work/'invalid-options'
    try:run_demo(path,**kw)
    except CaptureError:pass
    else:raise AssertionError(kw)
    assert not path.exists();note('invalid_options_no_directory')
# Documented partial batch policy: earlier complete files remain, no success manifest.
partial=work/'partial-batch'
with patch('motortester.wire_demo.save_svg',side_effect=OSError('review injected SVG failure')):
    try:run_demo(partial,windows=2)
    except OSError:pass
    else:raise AssertionError('export failure suppressed')
assert not (partial/'manifest.json').exists() and load(partial/'synthetic-01.mtcap').samples==samples
assert not (partial/'synthetic-02.mtcap').exists();note('partial_batch_honesty')
# The actual CLI's twenty output files, numerical CSV and SVG coordinates.
path=out/'first-path';manifest=json.loads((path/'manifest.json').read_text())
assert len(manifest['records'])==20 and manifest['origin']=='simulator'
for i,item in enumerate(manifest['records'],1):
    p=path/item['file'];c=load(p)
    assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
    assert c.samples==synthetic_window(i)[0] and c.origin=='simulator'
    with p.with_suffix('.csv').open(newline='',encoding='utf-8') as f:rows=list(csv.DictReader(f))
    assert len(rows)==2048
    for n,row in enumerate(rows):
        assert int(row['sample_index'])==n and int(row['adc_code'])==c.samples[n]
        assert float(row['time_nominal_s'])==(n-512)/50000
        assert row['origin']=='simulator' and row['session_id']==c.session_id and int(row['window_sequence'])==i
    svg=ET.fromstring(p.with_suffix('.svg').read_text());poly=svg.find('.//{http://www.w3.org/2000/svg}polyline')
    xy=[tuple(map(float,s.split(','))) for s in poly.attrib['points'].split()];assert len(xy)==2048
    for n,(_,y) in enumerate(xy):assert abs(y-(100+360*(1-c.samples[n]/4095)))<0.00051
    texts=' '.join(e.text or '' for e in svg.iter());assert 'ДЕМОНСТРАЦИЯ' in texts and 'синтетические данные' in texts and 'точность не проверена' in texts
    note('file_csv_svg_identity')
# Reproduce documentation mismatch: nine valid blocks can use another partition.
fs=[frames[0]];offset=0
for i,size in enumerate([249]*8+[56],1):
    payload=struct.pack('<IIHH',2,1,offset,size)+struct.pack(f'<{size}H',*samples[offset:offset+size]);offset+=size
    fs.append(Frame(145,CONTEXT.session,1+i,0,payload))
fs.append(frames[-1]);alternate=capture_from_synthetic_stream(transcript(fs),received_at_utc=STAMP)
assert alternate.samples==samples
print(json.dumps({'status':'PASS functional probes; one documentation mismatch','host_sha':'cd86784b434662c7aa542713fa61a5bbd6b97938','counts':counts,'huge_input_peak_bytes':peak,'accepted_alternate_layout':[249]*8+[56],'scope':'synthetic normal/analog/profile1; no USB/handshake/Windows/device'},indent=2))
