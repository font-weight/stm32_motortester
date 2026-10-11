#!/usr/bin/env python3
"""Compile/run the independent TX002 oracle in a fresh disjoint directory."""
import argparse, hashlib, json, os, pathlib, shlex, shutil, subprocess, sys
P=pathlib.Path
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root',type=P,required=True)
    ap.add_argument('--wire-root',type=P,required=True)
    ap.add_argument('--out',type=P,required=True)
    ap.add_argument('--cc',default='gcc')
    ap.add_argument('--sanitize',action='store_true')
    a=ap.parse_args()
    env={'PATH':os.defpath,'LANG':'C','LC_ALL':'C','TZ':'UTC','ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
    cc_path=shutil.which(a.cc,path=env['PATH'])
    if not cc_path: ap.error('compiler unavailable')
    cc=P(cc_path).resolve(strict=True)
    source=a.source_root.resolve(strict=True); wire=a.wire_root.resolve(strict=True)
    raw=a.out.absolute(); out=raw.resolve()
    files={
        'firmware/transport/mt_tx.c':source/'firmware/transport/mt_tx.c',
        'firmware/transport/mt_tx.h':source/'firmware/transport/mt_tx.h',
        'firmware/protocol/mt_wire.c':wire/'firmware/protocol/mt_wire.c',
        'firmware/protocol/mt_wire.h':wire/'firmware/protocol/mt_wire.h',
        'test_retained_abort.c':P(__file__).resolve().parent/'test_retained_abort.c',
        'run_oracle.py':P(__file__).resolve(),
    }
    for k,p in list(files.items()):
        files[k]=p.resolve(strict=True)
        if not files[k].is_file(): ap.error('missing regular input')
    protected=[a.source_root.absolute(),source,a.wire_root.absolute(),wire,P(__file__).absolute().parent,P(__file__).resolve().parent]
    protected += [p.parent for p in files.values()]
    if raw.exists() or raw.is_symlink(): ap.error('output exists')
    for q in (raw,out):
        for p in protected:
            if q==p or q in p.parents or p in q.parents: ap.error('output overlaps input '+str(p))
    before={str(p):sha(p) for p in files.values()}
    before[str(cc)]=sha(cc)
    programs={}
    for name in ('cc1','as'):
        value=subprocess.check_output([str(cc),'-print-prog-name='+name],env=env,text=True).strip()
        selected=P(value) if '/' in value else P(shutil.which(value,path=env['PATH']) or value)
        selected=selected.resolve(strict=True); programs[name]={'path':str(selected),'sha256':sha(selected)}
    for p in [cc.parent, *(P(v['path']).parent for v in programs.values())]:
        for q in (raw,out):
            if q==p or q in p.parents or p in q.parents: ap.error('output overlaps compiler input '+str(p))
    out.mkdir(parents=True,exist_ok=False)
    receipt={'argv':sys.argv,'inputs_before':before,'selected_programs':programs,'commands':[],'status':'RUNNING','scope':'native transport representation; no controller/hardware execution'}
    def run(name,cmd):
        r=subprocess.run(list(map(str,cmd)),env=env,cwd=out,capture_output=True,text=True)
        (out/(name+'.log')).write_text(shlex.join(list(map(str,cmd)))+'\n'+r.stdout+r.stderr)
        receipt['commands'].append({'label':name,'argv':list(map(str,cmd)),'exit':r.returncode})
        if r.returncode: raise RuntimeError(name+' failed')
        return r.stdout
    try:
        for rel,p in files.items():
            target=out/'snapshot'/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
            assert sha(target)==before[str(p)]
        receipt['compiler_version']=run('version',[cc,'--version'])
        flags=['-std=c11','-Wall','-Wextra','-Werror','-pedantic','-Wconversion','-Wshadow']
        flags += ['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'] if a.sanitize else ['-O2']
        stage=out/'snapshot'
        run('compile',[cc,*flags,'-I',stage/'firmware/transport',stage/'firmware/transport/mt_tx.c',stage/'firmware/protocol/mt_wire.c',stage/'test_retained_abort.c','-o',out/'oracle'])
        receipt['result']=run('test',[out/'oracle']).strip()
        receipt['inputs_after']={p:sha(P(p)) for p in before}
        assert receipt['inputs_after']==before
        for item in programs.values(): assert sha(P(item['path']))==item['sha256']
        receipt['status']='PASS'
    finally: (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(receipt['result'])
if __name__=='__main__': main()
