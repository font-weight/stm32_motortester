#!/usr/bin/env python3
"""Pinned-source, fail-fast native ADC/TIME/live composition reproducer."""
import argparse, datetime, hashlib, json, os, pathlib, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent

def digest(data): return hashlib.sha256(data).hexdigest()
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=pathlib.Path)
    p.add_argument('--output', required=True, type=pathlib.Path, help='New external directory; never reused')
    p.add_argument('--manifest', type=pathlib.Path, default=HERE/'source-manifest.json')
    p.add_argument('--case', default=None)
    p.add_argument('--cc', default=os.environ.get('CC','cc'))
    args = p.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if args.output.exists() or args.output.is_symlink(): p.error('Output already exists; overwrite is forbidden')
    if source == output or source in output.parents: p.error('Output must be outside the source tree')
    manifest = json.loads(args.manifest.read_text())
    inputs = {}
    for name, expected in manifest['sha256'].items():
        data = (source/name).read_bytes()
        if digest(data) != expected: p.error(f'Pinned source mismatch: {name}')
        inputs[name] = data
    output.mkdir(parents=False)
    # Compile immutable checked bytes, so a concurrent author edit cannot mix revisions.
    snapshot = output/'source'; snapshot.mkdir()
    for name, data in inputs.items():
        path = snapshot/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        if digest(path.read_bytes()) != manifest['sha256'][name]: raise RuntimeError('Source copy mismatch')
    shutil.copyfile(HERE/'test_live_adc.c',output/'test_live_adc.c')
    shutil.copyfile(__file__,output/'run.py')
    shutil.copyfile(args.manifest,output/'source-manifest.json')
    provenance = {'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'source_path':str(source),'source_pin':manifest,'harness_sha256':digest((output/'test_live_adc.c').read_bytes()),
       'runner_sha256':digest(pathlib.Path(__file__).read_bytes()),'commands':[],
       'sanitizer_environment':{'ASAN_OPTIONS':'halt_on_error=1:abort_on_error=1:detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'},
       'scope':'Native register model only; no hardware/USB timing or final acquisition/drain certificate. LeakSanitizer unsupported under executor ptrace; no heap allocation in firmware/harness.'}
    def save(): (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    def command(name,cmd,env=None):
        print('+',name,flush=True)
        with (output/(name+'.stdout')).open('wb') as stdout, (output/(name+'.stderr')).open('wb') as stderr:
            result=subprocess.run(cmd,stdout=stdout,stderr=stderr,env=env,timeout=120)
        provenance['commands'].append({'name':name,'argv':cmd,'returncode':result.returncode}); save()
        if result.returncode:
            print((output/(name+'.stderr')).read_text(),file=sys.stderr)
            raise RuntimeError(f'{name} failed ({result.returncode}); evidence retained: {output}')
        if name.startswith('run-'): print((output/(name+'.stdout')).read_text(),end='')
    save(); command('compiler',[args.cc,'--version'])
    includes=[]
    for sub in ['app','platform','protocol','acquisition']: includes+=['-I',str(snapshot/'firmware'/sub)]
    sources=[str(snapshot/name) for name in sorted(inputs) if name.endswith('.c')]
    common=[args.cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic']+includes+[str(output/'test_live_adc.c')]+sources
    for kind,options in [('strict',['-O2']),('asan-ubsan',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer','-fno-pie','-no-pie'])]:
        binary=output/kind
        command('compile-'+kind,common+options+['-o',str(binary)])
        env=os.environ.copy(); env['ASAN_OPTIONS']='halt_on_error=1:abort_on_error=1:detect_leaks=0'; env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
        command('run-'+kind,[str(binary)]+([args.case] if args.case else []),env)
    provenance['result']='PASS'; save()
    print('PASS evidence:',output)

if __name__ == '__main__':
    try: main()
    except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
        print(str(error),file=sys.stderr); sys.exit(1)
