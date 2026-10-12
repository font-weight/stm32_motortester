"""Read-only wheel verification; writes new resolver evidence, never installs."""
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.tags import cpython_tags, compatible_tags, parse_tag
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename

root = Path('/tmp/motortester-host-serial-package-r2')
evidence = Path('/tmp/serial-r2-resolver-current-evidence').read_text()
evidence = Path(evidence)
request = json.loads((evidence / 'request.json').read_text())
wheelhouse = Path(request['wheelhouse'])
lock = root / 'host/packaging/requirements-win-x64.lock'
pins = {}
for line in lock.read_text().splitlines():
    if not line or line.startswith('#'):
        continue
    match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})', line)
    if not match:
        raise RuntimeError('Unexpected lock syntax: ' + line)
    name, version, digest = match.groups()
    key = canonicalize_name(name)
    if key in pins:
        raise RuntimeError('Duplicate pinned distribution')
    pins[key] = {'name': name, 'version': version, 'sha256': digest}
if len(pins) != 14:
    raise RuntimeError('Expected exactly 14 pinned distributions')

# pip's cross-platform options are not relied on to prove marker closure.
# Explicitly evaluate each wheel's core Requires-Dist for the intended target.
target = default_environment() | {
    'implementation_name': 'cpython', 'implementation_version': '3.13.16',
    'os_name': 'nt', 'platform_machine': 'AMD64',
    'platform_python_implementation': 'CPython', 'platform_release': '10',
    'platform_system': 'Windows', 'platform_version': '10.0.19045',
    'python_full_version': '3.13.16', 'python_version': '3.13',
    'sys_platform': 'win32', 'extra': '',
}
supported = set(cpython_tags(python_version=(3, 13), abis=['cp313'], platforms=['win_amd64']))
supported.update(compatible_tags(python_version=(3, 13), interpreter='cp313', platforms=['win_amd64']))
records = []
seen = set()
for path in sorted(wheelhouse.iterdir()):
    if path.suffix != '.whl':
        raise RuntimeError('Non-wheel download: ' + path.name)
    name, version, build, tags = parse_wheel_filename(path.name)
    name = canonicalize_name(name)
    if name not in pins or name in seen:
        raise RuntimeError('Unpinned/duplicate wheel: ' + path.name)
    seen.add(name)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != pins[name]['sha256'] or str(version) != pins[name]['version']:
        raise RuntimeError('Wheel version/hash mismatch: ' + path.name)
    matching_tags = sorted(str(t) for t in tags & supported)
    if not matching_tags:
        raise RuntimeError('Wheel tags cannot target CPython3.13 win_amd64: ' + path.name)
    with zipfile.ZipFile(path) as archive:
        metadata_files = [p for p in archive.namelist() if p.count('/') == 1 and p.endswith('.dist-info/METADATA')]
        wheel_files = [p for p in archive.namelist() if p.count('/') == 1 and p.endswith('.dist-info/WHEEL')]
        if len(metadata_files) != 1 or len(wheel_files) != 1:
            raise RuntimeError('Ambiguous wheel metadata')
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        wheel = BytesParser().parsebytes(archive.read(wheel_files[0]))
        notice_files = [p for p in archive.namelist() if any(k in p.lower() for k in ('license','licence','copying','notice','copyright'))]
    if canonicalize_name(metadata['Name']) != name or metadata['Version'] != pins[name]['version']:
        raise RuntimeError('Filename/METADATA mismatch: ' + path.name)
    requires_python = metadata.get('Requires-Python', '')
    if not SpecifierSet(requires_python).contains('3.13.16', prereleases=True):
        raise RuntimeError('Requires-Python rejects target: ' + path.name)
    wheel_metadata_tags = set()
    for value in wheel.get_all('Tag', []):
        wheel_metadata_tags.update(parse_tag(value))
    if wheel_metadata_tags != tags:
        raise RuntimeError('Filename/WHEEL tag mismatch: ' + path.name)
    dependencies = []
    for raw in metadata.get_all('Requires-Dist', []):
        req = Requirement(raw)
        active = req.marker is None or req.marker.evaluate(target)
        dependency = {'requirement': raw, 'active_for_target_without_extras': active}
        if active:
            if req.url or req.extras:
                raise RuntimeError('Unexpected active URL/extras dependency: ' + raw)
            dependency_name = canonicalize_name(req.name)
            if dependency_name not in pins or not req.specifier.contains(pins[dependency_name]['version'], prereleases=True):
                raise RuntimeError('Target dependency not satisfied by lock: ' + raw)
            dependency['satisfied_by'] = pins[dependency_name]['name'] + '==' + pins[dependency_name]['version']
        dependencies.append(dependency)
    records.append({'filename': path.name, 'distribution': metadata['Name'], 'version': metadata['Version'],
                    'bytes': len(data), 'sha256': digest, 'lock_hash_matches': True,
                    'wheel_tags': sorted(str(t) for t in tags), 'compatible_target_tags': matching_tags,
                    'requires_python': requires_python, 'requires_python_accepts_3_13_16': True,
                    'requires_dist': dependencies, 'notice_filenames': notice_files if name == 'pyserial' else None})
if seen != pins.keys():
    raise RuntimeError('Not all pinned wheels were downloaded')
source_manifest = root / 'host/packaging/evidence/serial-r2/SOURCE-MANIFEST.json'
source = json.loads(source_manifest.read_text())
for item in source['source_files']:
    if hashlib.sha256((root/item['path']).read_bytes()).hexdigest() != item['sha256']:
        raise RuntimeError('Frozen source modified: ' + item['path'])
result = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
          'status': 'PASS: 14-wheel download/hash/tag/Requires-Python/target-marker closure only',
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'],cwd=root,text=True).strip(),
          'source_manifest_sha256': hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
          'source_unchanged': True, 'lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
          'target': {'python': 'CPython 3.13.16 regular GIL', 'abi': 'cp313', 'platform': 'win_amd64', 'marker_environment': target},
          'marker_scope': 'No optional extras requested. Windows 10 build marker values are explicit evaluation assumptions, not an observed OS. This does not install or execute any Windows code.',
          'count': len(records), 'wheels': records, 'actual_windows_execution': False, 'installation_performed': False}
(evidence / 'wheel-verification.json').write_text(json.dumps(result,indent=2)+'\n')
(evidence / 'WHEEL-SHA256SUMS.txt').write_text(''.join(f'{r["sha256"]}  {r["filename"]}\n' for r in records))
print('Verified', len(records), 'wheels; all pinned hashes and target dependencies match.')
for record in records:
    print(record['filename'], record['bytes'], record['sha256'])
