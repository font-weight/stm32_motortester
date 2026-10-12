"""Produce bounded HOST-008 evidence without git/network/install side effects."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / 'host/tests/fixtures/session_release'
EVIDENCE = FIXTURE / 'evidence'
MANIFEST = EVIDENCE / 'verified-manifest.json'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           PYTHONPATH='host:/tmp/motortester-deps', QT_QPA_PLATFORM='offscreen')
EXCLUDED_PARTS = {'.git', '__pycache__'}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(ROOT.rglob('*'))
            if p.is_file() and not (set(p.parts) & EXCLUDED_PARTS)
            and EVIDENCE not in p.parents}


def checked_run(name, command, *, suite=False):
    log = EVIDENCE / ('verified-' + name + '.log')
    # Log closed before its digest is calculated below; no open console log is
    # created, and stdout is not redirected to a member of this evidence set.
    started = time.monotonic()
    with log.open('w') as output:
        output.write('cwd=' + str(ROOT) + '\n')
        output.write('PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen ' + shlex.join(command) + '\n')
        output.flush()
        result = subprocess.run(command, cwd=ROOT, env=ENV, stdout=output,
                                stderr=subprocess.STDOUT, timeout=900)
    entry = {'command': command, 'returncode': result.returncode,
             'elapsed_seconds': round(time.monotonic() - started, 3),
             'log': str(log.relative_to(ROOT)), 'sha256': sha(log)}
    if suite:
        lines = [line for line in log.read_text().splitlines() if line.startswith('HOST008_RESULT=')]
        entry['result'] = json.loads(lines[-1].partition('=')[2]) if len(lines) == 1 else None
        if entry['result'] is None or entry['result']['tests'] <= 0 or entry['result']['skips']:
            entry['invalid_discovery_or_skips'] = True
    return entry


before = sources()
author_freeze = {
    'host/motortester/session.py': 'f42f973c19cc382671da85139e60ec9d3cf30feb3e8f1646a37d56ceb4969f0e',
    'host/tests/test_session_release.py': '01723594b1c72525a0f46d32546ccd9577c12bb54a7ddc2ee4d569c30aa5baec',
    'host/docs/session-controller.md': 'dc5c6739b7c034629a1290db6435e52cd312a777da415689d5aa94bb2bdc000a',
}
for name, digest in author_freeze.items():
    if before.get(name) != digest:
        raise SystemExit('Author freeze mismatch: ' + name)
peer_test_sha256 = '0ea0071b163a392865498daec425cd1bb555d7c080b9578a5fcbf0e89a3a0d8e'
if before.get('host/tests/test_session_release_conformance.py') != peer_test_sha256:
    raise SystemExit('Final independent test freeze mismatch')
original = json.loads((EVIDENCE / 'dependency-before.json').read_text())
scope_extension = {
    'path': 'host/tests/test_device_emulator_conformance.py',
    'function': 'Host006ClosedLoop.test_existing_session_and_wire_files_keep_own_pins',
    'before_sha256': original['all_files_sha256']['host/tests/test_device_emulator_conformance.py'],
    'after_sha256': '82f9536a4634f4f81b8379a32ae329a3929a9e5d76b8e13084c03e31018ea157',
    'owner': 'root coordinator',
    'authorization': 'explicit one-function scope extension reported by coordinator 2026-10-10',
    'purpose': 'retain historical HOST-007 source evidence and protocol/wire pins while allowing the separately hashed HOST-008 implementation',
}
if before.get(scope_extension['path']) != scope_extension['after_sha256']:
    raise SystemExit('Approved provenance correction hash mismatch')
allowed_changed = {'host/motortester/session.py', 'host/docs/session-controller.md', scope_extension['path']}
drift = {name: {'expected': digest, 'actual': before.get(name)}
         for name, digest in original['all_files_sha256'].items()
         if name not in allowed_changed and before.get(name) != digest}
if drift:
    raise SystemExit('Out-of-scope dependency changes: ' + json.dumps(drift))

runner = 'host/tests/fixtures/session_release/run_suite.py'
runs = []
runs.append(checked_run('final-imports', [sys.executable, '-c',
    'import sys,numpy,PySide6,pyqtgraph; from motortester import session,window,device_emulator; '
    'print(sys.version); '
    '[(print(m.__name__,getattr(m,"__version__","n/a"),m.__file__)) '
    'for m in (numpy,PySide6,pyqtgraph,session,window,device_emulator)]']))
commands = [
    ('final-focused', [], ['test_session_release_conformance.py']),
    ('final-new-normal', [], ['test_session_release*.py']),
    ('final-new-optimized', ['-O'], ['test_session_release*.py']),
    ('final-existing-session', [], ['test_session.py', 'test_session_conformance.py']),
    ('final-full-host', [], ['test*.py']),
]
for name, options, patterns in commands:
    runs.append(checked_run(name, [sys.executable, *options, runner, *patterns], suite=True))
    print(name, runs[-1]['returncode'], runs[-1].get('result'), flush=True)

after = sources()
source_drift = {name: [before.get(name), after.get(name)] for name in before.keys() | after.keys()
                if before.get(name) != after.get(name)}
evidence_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sorted(EVIDENCE.rglob('*'))
                   if p.is_file() and p != MANIFEST}
owned = ['host/tests/test_session_release_conformance.py'] + [
    str(p.relative_to(ROOT)) for p in sorted(FIXTURE.rglob('*')) if p.is_file()
    and '__pycache__' not in p.parts and p != MANIFEST] + [str(MANIFEST.relative_to(ROOT))]
manifest = {
    'issue': 'https://github.com/font-weight/stm32_motortester/issues/48',
    'dependency_base': original['dependency_base'],
    'author_frozen_sha256': author_freeze,
    'peer_test_sha256': peer_test_sha256,
    'peer_assertion_correction': 'Same seven real-stream negative cases; assert factory itself raises, with no downstream release inside assertRaises. Count unchanged at 36.',
    'intermediate_corrected_manifest': {'path': 'host/tests/fixtures/session_release/evidence/corrected-manifest.json',
                                       'sha256': sha(EVIDENCE / 'corrected-manifest.json')},
    'authorized_scope_extension': scope_extension,
    'historical_failed_manifest': {'path': 'host/tests/fixtures/session_release/evidence/manifest.json',
                                   'sha256': sha(EVIDENCE / 'manifest.json')},
    'source_mapping': {'union': original['union'], 'emulator_dependency_27_files': original['emulator'],
        'reviewed_session_source': '477057aadc9bc8e757bc8ef817125377e816d212',
        'session_protocol': 'b6ae8025baf1a47e8173997029d2d8b858ada74c',
        'window_protocol': '124b3f32e1e7f7282bce283baa4c9406d4d72950',
        'emulator_protocol': 'c54bd955aaf3cb863ef3df043ea237ea62e74ad4'},
    'scope': 'synthetic Normal/analog host RELEASE only; no score or shared acceptance',
    'policy_status': {'CA-CTRL-4': 'conservative host scheduling policy; A normative pin pending',
                      'emulator_hold_timeout': 'seven existing cases remain PROVISIONAL'},
    'unchanged_dependency_check': {'passed': not drift, 'examined_files': len(original['all_files_sha256']),
                                  'allowed_modified_files': sorted(allowed_changed)},
    'environment': {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': ENV['PYTHONPATH'],
                    'QT_QPA_PLATFORM': ENV['QT_QPA_PLATFORM'], 'python': sys.version},
    'runs': runs, 'sources_before': before, 'sources_after': after,
    'source_drift': source_drift, 'evidence_sha256': evidence_hashes,
    'hash_exclusions': ['verified-manifest.json excludes its own digest to avoid a recursive hash; all logs are closed before hashing; no runner console log is written'],
    'conformance_publication_files': owned,
    'integration_publication_files': [scope_extension['path']],
    'implementation_publication_files': ['host/motortester/session.py', 'host/tests/test_session_release.py',
                                       'host/docs/session-controller.md'],
    'success': not source_drift and all(r['returncode'] == 0 and not r.get('invalid_discovery_or_skips') for r in runs),
}
MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
print('manifest=' + str(MANIFEST.relative_to(ROOT)), flush=True)
sys.exit(0 if manifest['success'] else 1)
