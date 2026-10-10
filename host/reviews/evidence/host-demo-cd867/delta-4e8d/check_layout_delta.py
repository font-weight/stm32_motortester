"""Independent documented-layout delta check; argument is exact source archive."""
from pathlib import Path
from dataclasses import replace
import hashlib, json, struct, sys
source=Path(sys.argv[1]).resolve();sys.path.insert(0,str(source/'host'))
from motortester.wire_demo import synthetic_window,capture_from_synthetic_stream
from motortester.wire import decode_frame,encode_frame
from motortester.model import CaptureError
samples,wire=synthetic_window();frames=[decode_frame(p) for p in wire.split(b'\0') if p]
checked=[]
for sizes in ([249]*8+[56],[248]*8+[64],[250]*8+[48],[250]*7+[149,149],[225]*9+[23],[200]*10+[48]):
    records=[frames[0]];offset=0
    for seq,count in enumerate(sizes,2):
        payload=struct.pack('<IIHH',2,1,offset,count)+struct.pack(f'<{count}H',*samples[offset:offset+count]);offset+=count
        records.append(replace(frames[1],sequence=seq,payload=payload))
    records.append(replace(frames[-1],sequence=len(records)+1))
    try:c=capture_from_synthetic_stream(b''.join(encode_frame(f) for f in records),received_at_utc='2026-10-10T12:00:00+00:00')
    except CaptureError:
        assert len(sizes)!=9;accepted=False
    else:
        assert len(sizes)==9 and c.samples==samples;accepted=True
    checked.append({'sizes':sizes,'accepted':accepted})
print(json.dumps({'sha':'4e8d0a3497ee2a8203d2f3e97d8c1293e92dade4','wire_demo_sha256':hashlib.sha256((source/'host/motortester/wire_demo.py').read_bytes()).hexdigest(),'result':'PASS','checks':checked},indent=2))
