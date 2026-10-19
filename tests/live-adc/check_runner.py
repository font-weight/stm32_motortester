#!/usr/bin/env python3
"""Replay relocation, source/overwrite refusal, and fail-fast sanitizer controls."""
import argparse, hashlib, json, os, pathlib, shutil, subprocess
HERE=pathlib.Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=pathlib.Path)
    p.add_argument('--output',required=True,type=pathlib.Path)
    args=p.parse_args(); source=args.source.resolve(); root=args.output.resolve()
    if args.output.exists() or args.output.is_symlink(): p.error('Output exists; overwrite forbidden')
    if source in root.parents or source==root: p.error('Output must be external to source')
    root.mkdir(); records=[]
    def run(name,cmd,expected=0,env=None,fragment=None):
        result=subprocess.run(cmd,capture_output=True,env=env,timeout=120)
        (root/(name+'.stdout')).write_bytes(result.stdout);(root/(name+'.stderr')).write_bytes(result.stderr)
        if result.returncode!=expected and not(expected=='nonzero' and result.returncode!=0):
            raise RuntimeError(f'{name}: unexpected exit {result.returncode}')
        if fragment and fragment not in result.stderr: raise RuntimeError(f'{name}: missing expected diagnostic')
        records.append({'name':name,'argv':cmd,'exit':result.returncode})
    relocated=root/'relocated harness with spaces'; relocated.mkdir()
    for name in ['run.py','test_live_adc.c','source-manifest.json']:
        shutil.copyfile(HERE/name,relocated/name)
    manifest=json.loads((HERE/'source-manifest.json').read_text())
    sources=root/'source only with spaces'; sources.mkdir()
    for name in manifest['sha256']:
        dest=sources/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source/name,dest)
    good=root/'relocated full results'
    cmd=['python3',str(relocated/'run.py'),'--source',str(sources),'--output',str(good)]
    run('relocation',cmd)
    before={str(p.relative_to(good)):hashlib.sha256(p.read_bytes()).hexdigest() for p in good.rglob('*') if p.is_file()}
    run('overwrite-refusal',cmd,2,fragment=b'Output already exists')
    after={str(p.relative_to(good)):hashlib.sha256(p.read_bytes()).hexdigest() for p in good.rglob('*') if p.is_file()}
    if before!=after: raise RuntimeError('Refusal modified prior evidence')
    dangling=root/'existing dangling output'; target=root/'must not create symlink target'; dangling.symlink_to(target)
    run('dangling-output-refusal',['python3',str(relocated/'run.py'),'--source',str(sources),'--output',str(dangling)],2,fragment=b'Output already exists')
    if target.exists(): raise RuntimeError('Dangling output target was created')
    changed=sources/'firmware/app/mt_live_adc.c'; changed.write_bytes(changed.read_bytes()+b'\n/* explicit negative pin mismatch */\n')
    bad=root/'must not be created'
    run('source-mismatch-refusal',['python3',str(relocated/'run.py'),'--source',str(sources),'--output',str(bad)],2,fragment=b'Pinned source mismatch')
    if bad.exists(): raise RuntimeError('Mismatch created outputs before refusal')
    canaries={
       'asan':('int main(void) { volatile unsigned n=1; volatile int a[1]={0}; return a[n]; }\n',b'AddressSanitizer'),
       'ubsan':('#include <limits.h>\nint main(void) { volatile int n=INT_MAX; return n+1; }\n',b'runtime error: signed integer overflow')}
    for name,(text,fragment) in canaries.items():
        c=root/(name+'-canary.c'); c.write_text(text); binary=root/(name+'-canary')
        flags=['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer','-fno-pie','-no-pie']
        # ASan's bounds report is reached with UBSan disabled solely in the ASan canary.
        if name=='asan':flags[2]='-fsanitize=address'
        run('compile-'+name+'-canary',['cc','-std=c11']+flags+[str(c),'-o',str(binary)])
        env=os.environ.copy();env['ASAN_OPTIONS']='halt_on_error=1:abort_on_error=1:detect_leaks=0';env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
        run('run-'+name+'-canary',[str(binary)],'nonzero',env,fragment)
    (root/'results.json').write_text(json.dumps({'checks':records,'result':'PASS','relocated_harness_sha256':hashlib.sha256((relocated/'test_live_adc.c').read_bytes()).hexdigest()},indent=2)+'\n')
    print('PASS relocated full suite, overwrite/source pin refusal, ASan/UBSan fail-fast canaries')
if __name__=='__main__': main()
