"""Verify exact archived source against captured Git blob IDs and SHA-256 hashes."""
from pathlib import Path
import hashlib
import json
import sys
here=Path(__file__).resolve().parent
root=Path(sys.argv[1])
manifest=json.loads((here/'source-verification.json').read_text())
for name,want in manifest['files'].items():
    data=(root/name).read_bytes()
    git_blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if git_blob!=want['git_blob'] or hashlib.sha256(data).hexdigest()!=want['sha256']:
        raise SystemExit('SOURCE DRIFT '+name)
print('VERIFIED',len(manifest['files']),'Git blobs and SHA-256 hashes for',manifest['head'])
