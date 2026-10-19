#!/usr/bin/env python3
"""Independent strict/sanitized stop-certificate native checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

p=argparse.ArgumentParser()
p.add_argument('source', type=Path)
p.add_argument('--output', type=Path)
p.add_argument('--only', nargs='*')
a=p.parse_args()
here=Path(__file__).resolve().parent
source=a.source.resolve()
if a.output is None:
    out=Path(tempfile.mkdtemp(prefix='motortester-adc-stop-')).resolve()
else:
    out=a.output.resolve()
    try:
        out.mkdir(parents=True,exist_ok=False)
    except FileExistsError:
        p.error(f'output already exists; refusing to overwrite evidence: {out}')
print(f'Results: {out}')
sanitizer_options={'ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1',
                   'UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
sanitizer_env=dict(os.environ,**sanitizer_options)
(out/'sanitizer-options.json').write_text(json.dumps(sanitizer_options,indent=2)+'\n')
def source_hashes():
    return {str(path.relative_to(source)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((source/'firmware').rglob('*'))
            if path.is_file() and path.suffix in ('.c','.h')}
initial_hashes=source_hashes()
(out/'tested-source-manifest.json').write_text(json.dumps(initial_hashes,indent=2)+'\n')
cc=os.environ.get('CC','cc')
flags=['-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-I'+str(source/'firmware/platform')]
sources=[str(source/'firmware/platform/adc_timer_dma.c'),str(here/'test_stop_certificate.c')]
versions=subprocess.run([cc,'--version'],capture_output=True,text=True,check=True).stdout
(out/'compiler.txt').write_text(versions)
failed=[]
for variant,extra in [('strict',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'])]:
    exe=out/variant
    command=[cc,*flags,*extra,*sources,'-o',str(exe)]
    build=subprocess.run(command,capture_output=True,text=True)
    (out/f'{variant}-build.log').write_text(' '.join(command)+'\n'+build.stdout+build.stderr)
    if build.returncode:
        print(build.stderr); failed.append(variant+':build'); continue
    cases=a.only or subprocess.run([str(exe),'--list'],capture_output=True,text=True,check=True,env=sanitizer_env).stdout.splitlines()
    for case in cases:
        env=sanitizer_env
        run=subprocess.run([str(exe),case],capture_output=True,text=True,env=env)
        (out/f'{variant}-{case}.log').write_text(run.stdout+run.stderr+f'\nexit={run.returncode}\n')
        print(f'{variant} {case}: {"PASS" if run.returncode==0 else "FAIL"}')
        if run.returncode: print(run.stderr.strip()); failed.append(variant+':'+case)
if not a.only:
    for variant,extra in [('strict',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'])]:
        exe=out/(variant+'-legacy')
        command=[cc,*flags,'-I'+str(source/'firmware/app'),*extra,str(source/'firmware/app/mt_adc_control.c'),str(here/'test_legacy_narrowing.c'),'-o',str(exe)]
        build=subprocess.run(command,capture_output=True,text=True)
        (out/f'{variant}-legacy-build.log').write_text(' '.join(command)+'\n'+build.stdout+build.stderr)
        if build.returncode:
            print(build.stderr); failed.append(variant+':legacy-build'); continue
        run=subprocess.run([str(exe)],capture_output=True,text=True,env=sanitizer_env)
        (out/f'{variant}-legacy.log').write_text(run.stdout+run.stderr+f'\nexit={run.returncode}\n')
        print(f'{variant} legacy narrowing: {"PASS" if run.returncode==0 else "FAIL"}')
        if run.returncode: print(run.stderr.strip()); failed.append(variant+':legacy')
if source_hashes()!=initial_hashes:
    print('FAIL: source changed while running; these results are not bound to one revision')
    failed.append('source-drift')
(out/'summary.txt').write_text('FAIL '+', '.join(failed)+'\n' if failed else 'PASS all requested tests\n')
sys.exit(bool(failed))
