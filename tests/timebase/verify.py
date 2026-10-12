#!/usr/bin/env python3
"""Verify published input bytes before executing the original test logic."""
from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parents[2]
manifest=root/'tests/timebase/publication-manifest.json'
if manifest.exists():
 entries=json.loads(manifest.read_text())['files']
else:
 entries=json.loads((root/'tests/timebase/evidence/critic-a/source-manifest.json').read_text())['files']
for entry in entries:
 p=root/entry['path'];actual=hashlib.sha256(p.read_bytes()).hexdigest()
 if actual != entry['sha256']:raise SystemExit('SHA256 mismatch: '+entry['path'])
print(f'Verified {len(entries)} frozen text inputs by SHA256.')
