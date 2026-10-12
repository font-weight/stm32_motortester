"""Fresh-process archive/member recheck against observed source metadata and hashes."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
p=argparse.ArgumentParser();p.add_argument('--host-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
if not sys.flags.isolated or not sys.dont_write_bytecode:raise ValueError('Use -I -B')
sys.path.insert(0,str(a.host_root))
from motortester.session_archive import load
count=0
for expected_path in sorted(a.out.glob('*/expected-*.json')):
    expected=json.loads(expected_path.read_bytes());archive=load(expected_path.with_name('recording-'+expected_path.stem.split('-')[-1]+'.mtrec'))
    # JSON normalizes immutable tuples to lists, preserving all field values.
    actual=json.loads(json.dumps(dict(identity=asdict(archive.identity),entries=[asdict(e) for e in archive.entries],outcome=asdict(archive.outcome))))
    if actual!=dict(identity=expected['identity'],entries=expected['entries'],outcome=expected['outcome']):raise ValueError('metadata changed')
    for ordinal,item in enumerate(expected['members']):
        data=archive.read_segment(ordinal)
        if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('member changed')
    count+=1
if count!=8:raise ValueError('expected eight finalized archives')
print(json.dumps(dict(status='PASS',archives=count)))
