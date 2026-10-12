#!/usr/bin/env python3
"""Fail-closed native reproduction; never build or execute target firmware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

MANIFEST_SHA = 'f1c77a245be2ef7d6793d79441b7403526d17278d18ad398b82d14b3080bdfeb'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    require(sys.flags.optimize == 0, 'optimized Python is unsupported: test assertions must execute')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='new absolute output directory outside the checkout')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    root = package.parents[1]
    out = Path(args.out)
    require(out.is_absolute() and str(out) == os.path.normpath(args.out), 'output must be absolute and normalized')
    require(not out.exists() and not out.is_symlink(), 'output already exists')
    require(out.parent.is_dir() and out.parent.resolve() == out.parent, 'output parent must be an existing canonical directory')
    require(not out.is_relative_to(root) and not root.is_relative_to(out), 'output overlaps checkout')
    manifest_path = package/'package-manifest.json'
    require(digest(manifest_path) == MANIFEST_SHA, 'manifest hash mismatch')
    manifest = json.loads(manifest_path.read_text())
    pinned = {root/p: v for p, v in manifest['production_files'].items()}
    pinned.update({package/p: v for p, v in manifest['package_files'].items()})
    pinned[manifest_path] = MANIFEST_SHA
    pinned[Path(__file__).resolve()] = digest(Path(__file__).resolve())
    for path, expected in pinned.items():
        require(path.is_file() and path.resolve() == path, 'input missing or aliased: '+str(path))
        require(digest(path) == expected, 'input hash mismatch: '+str(path))
    cc = shutil.which('gcc', path='/usr/bin:/bin')
    require(cc is not None, 'native GCC unavailable; sanitizer mode will not be skipped')
    cc = str(Path(cc).resolve())
    python = str(Path(sys.executable).resolve())
    env = {'PATH':'/usr/bin:/bin', 'LANG':'C', 'LC_ALL':'C',
           'ASAN_OPTIONS':'detect_leaks=0', 'UBSAN_OPTIONS':'halt_on_error=1',
           'PYTHONDONTWRITEBYTECODE':'1', 'TMPDIR':str(out/'tmp')}
    cases = manifest['cases']
    names = [row['case'] for row in cases['wait_cases']]
    require(len(names) == 25 and len(set(names)) == 25, 'invalid declared case inventory')
    out.mkdir()
    (out/'tmp').mkdir()
    result = {'status':'RUNNING', 'scope':manifest['scope'], 'manifest_sha256':MANIFEST_SHA,
              'inputs_before':{str(p): v for p, v in pinned.items()}, 'commands':[],
              'compiler':cc, 'compiler_sha256':digest(Path(cc)), 'python':python,
              'python_version':sys.version, 'environment':env,
              'hardware_verified':False, 'shared_contract_accepted':False}
    def run(label, argv, cwd):
        log = out/(label+'.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(list(map(str,argv)), cwd=cwd, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            data = getattr(exc, 'stdout', None) or b''
            log.write_bytes(data if isinstance(data, bytes) else data.encode())
            result['commands'].append({'label':label,'argv':list(map(str,argv)),
                'cwd':str(cwd),'exit':None,'exception':type(exc).__name__,'error':str(exc)})
            raise
        log.write_bytes(p.stdout)
        result['commands'].append({'label':label,'argv':list(map(str,argv)),
                                  'cwd':str(cwd),'exit':p.returncode})
        require(p.returncode == 0, label+' failed; see preserved log')
        require(b'AddressSanitizer' not in p.stdout and b'runtime error:' not in p.stdout,
                label+' contains sanitizer diagnostic')
        return p.stdout
    try:
        run('compiler-version',[cc,'--version'],out)
        flags = ['-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion',
                 '-Wsign-conversion','-Werror','-I'+str(root),'-g']
        common = manifest['production_c'][:7]
        stop = ['firmware/protocol/mt_wire.c','firmware/protocol/mt_command.c',
                'firmware/protocol/mt_session.c','firmware/protocol/mt_stream.c',
                'firmware/transport/mt_tx.c','firmware/acquisition/acquisition.c',
                'firmware/app/mt_capture_link.c','firmware/app/mt_control.c',
                'firmware/app/mt_control_rx.c']
        suites = [('wait','test_wait_release_proposal.c',common),
                  ('modes','test_mode_retention.c',common),
                  ('stop','stop/test_stop_barrier.c',stop)]
        for suite, test, sources in suites:
            directory = out/suite
            directory.mkdir()
            for mode in ('native','sanitized'):
                options = [] if suite == 'stop' else ['-O2' if mode == 'native' else '-O1']
                if mode == 'sanitized':
                    options += ['-fsanitize=address,undefined','-fno-omit-frame-pointer']
                run(suite+'/compile-'+mode,[cc,*flags,*options,package/test,
                    *(root/s for s in sources),'-o',directory/mode],out)
        for mode in ('native','sanitized'):
            listed = run('wait/list-'+mode,[out/'wait'/mode,'--list'],out).decode().splitlines()
            require(listed == names, 'actual case names differ: '+mode)
        rows = []
        for case in names:
            for mode in ('native','sanitized'):
                directory = out/'wait'/(mode+'-'+case)
                (directory/'build').mkdir(parents=True)
                run('wait/'+mode+'-'+case+'/run',[out/'wait'/mode,case],directory)
                rows.append(case+'\t'+mode+'\t0')
                (out/'wait/results.tsv').write_text('\n'.join(rows)+'\n')
        expected_wait = {}
        for row in cases['wait_cases']:
            for mode in ('native','sanitized'):
                for snapshot in row['snapshots_per_mode']:
                    key = str(Path(mode+'-'+row['case'])/'build'/snapshot['name'])
                    expected_wait[key] = snapshot
        actual = {str(p.relative_to(out/'wait')) for p in (out/'wait').rglob('*.bin')}
        require(actual == set(expected_wait) and len(actual) == 72, 'WAIT snapshot inventory mismatch')
        run('wait/aggregate',[python,'-I','-B',package/'validate_run.py',out/'wait'],out)
        decoded = json.loads((out/'wait/independent-wire.json').read_text())
        require(len(decoded) == 72, 'WAIT decoder count mismatch')
        for row in decoded:
            key = str(Path(row['path']).relative_to(out/'wait'))
            expected = expected_wait[key]
            require(row['complete_frames'] == expected['complete_frames'] and
                    row['incomplete_tail_bytes'] == expected['incomplete_tail_bytes'],
                    'WAIT decoded frame/tail inventory mismatch: '+key)
        for suite, summary in [('stop',cases['stop_expected_summary']),
                               ('modes','PASS: 3 mode/storage cases; no physical hardware claim.')]:
            logs = []
            for mode in ('native','sanitized'):
                directory = out/suite/(mode+'-run')
                (directory/'build').mkdir(parents=True)
                logs.append(run(suite+'/'+mode+'-run/run',[out/suite/mode],directory))
                require(logs[-1].decode().splitlines().count(summary) == 1, suite+' summary missing or duplicated')
            require(logs[0] == logs[1], suite+' mode logs differ')
        expected_modes = {str(Path(mode+'-run')/'build'/row['snapshot'])
                          for mode in ('native','sanitized') for row in cases['mode_storage_cases']}
        actual_modes = {str(p.relative_to(out/'modes')) for p in (out/'modes').rglob('*.bin')}
        require(actual_modes == expected_modes and len(actual_modes) == 6, 'mode snapshot inventory mismatch')
        for row in cases['mode_storage_cases']:
            require((out/'modes/native-run/build'/row['snapshot']).read_bytes() ==
                    (out/'modes/sanitized-run/build'/row['snapshot']).read_bytes(), 'mode wire bytes differ')
        run('modes/decoder',[python,'-I','-B',package/'decode_wire.py',out/'modes',
                            '--output',out/'modes/independent-wire.json'],out)
        mode_decoded = json.loads((out/'modes/independent-wire.json').read_text())
        require(len(mode_decoded) == 6 and all(r['incomplete_tail_bytes'] == 0 and
                r['complete_frames'] > 0 for r in mode_decoded), 'mode decoder inventory mismatch')
        result.update(status='NATIVE_PROTOCOL_TESTS_PASS',wait_cases=25,stop_cases=103,
                      mode_cases=3,compiler_modes=2,wire_snapshots=78,intentional_partial_snapshots=6)
    except Exception as exc:
        result.update(status='FAILED',error=type(exc).__name__+': '+str(exc))
    finally:
        after = {}
        for path in pinned:
            try: after[str(path)] = digest(path)
            except OSError: after[str(path)] = None
        result['inputs_after'] = after
        result['inputs_unchanged'] = after == result['inputs_before']
        if not result['inputs_unchanged']:
            result.update(status='FAILED',error='input drift')
        (out/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
    require(result['status'] == 'NATIVE_PROTOCOL_TESTS_PASS', result.get('error','aggregate failure'))
    print('PASS: 25 WAIT + 103 Stop + 3 mode cases, twice; 78 wire snapshots; no hardware claim.')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError,OSError,KeyError,UnicodeError,json.JSONDecodeError) as exc:
        print('REJECTED: '+str(exc),file=sys.stderr)
        sys.exit(1)
