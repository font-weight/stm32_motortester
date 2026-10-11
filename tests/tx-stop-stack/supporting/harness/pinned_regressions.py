#!/usr/bin/env python3
"""Run unchanged PR30/PR35 author/independent regression scripts and sources."""
import argparse, datetime, hashlib, json, os
from pathlib import Path
import resource, shutil, subprocess, sys
sys.dont_write_bytecode=True
P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--pinned',required=True,type=Path)
P.add_argument('--candidate',required=True,type=Path)
P.add_argument('--out',required=True,type=Path)
A=P.parse_args();HERE=Path(__file__).absolute().parent
COMMITS={'pr30':'a451e5a491439fcfed31f298a589643cf7050ec9','pr35':'0ddea1e5992caa6231203838c3956e19675867a8'}
NEW='b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf'
OLD='e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e'
HEADER='68f013a8ffb6e946e9cd92f3e760bf593746afdc7097d11d837a101c5ab83dd9'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def lexical(p):return Path(os.path.abspath(p))
def overlap(a,b):return a==b or a in b.parents or b in a.parents
manifest=A.pinned/'provenance.json'
if not manifest.is_file():P.error('missing pinned provenance')
records=json.loads(manifest.read_text());sources={manifest,Path(__file__).absolute()}
for r in records:
    if r['label'] not in COMMITS or r['ref']!=COMMITS[r['label']]:P.error('unexpected pinned commit')
    rel=Path(r['path'])
    if rel.is_absolute() or '..' in rel.parts:P.error('unsafe manifest path')
    f=A.pinned/r['label']/rel
    if not f.is_file():P.error('missing pinned input: '+str(f))
    data=f.read_bytes()
    if sha(f)!=r['sha256'] or hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=r['sha']:P.error('pinned file hash mismatch: '+str(f))
    sources.add(f)
for name,expected in [('mt_tx.c',NEW),('mt_tx.h',HEADER)]:
    f=A.candidate/'firmware/transport'/name
    if not f.is_file() or sha(f)!=expected:P.error('candidate hash mismatch: '+name)
    sources.add(f)
for label in COMMITS:
    if sha(A.pinned/label/'firmware/transport/mt_tx.c')!=OLD or sha(A.pinned/label/'firmware/transport/mt_tx.h')!=HEADER:P.error('pinned TX dependency mismatch')
CC=shutil.which('cc',path='/usr/bin:/bin')
if not CC:P.error('compiler unavailable')
CC=str(Path(CC).resolve())
tools={'driver':CC,'python3':str(Path('/usr/bin/python3').resolve()),'bash':str(Path('/bin/bash').resolve()),'sh':str(Path('/bin/sh').resolve())}
queries=[]
for name in ['cc1','as','ld','collect2','libasan.so','libubsan.so']:
    command=[CC,('-print-file-name=' if name.endswith('.so') else '-print-prog-name=')+name]
    query=subprocess.run(command,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},capture_output=True,text=True)
    queries.append({'argv':command,'exit':query.returncode,'stdout':query.stdout,'stderr':query.stderr})
    if query.returncode:P.error('toolchain query failed')
    path=query.stdout.strip();selected=path if name.endswith('.so') else shutil.which(path,path='/usr/bin:/bin')
    if not selected or not Path(selected).is_file():P.error('toolchain component unavailable: '+name)
    tools[name]=str(Path(selected).resolve())
def toolhashes():return {name:{'resolved':path,'sha256':sha(Path(path))} for name,path in tools.items()}
tool_before=toolhashes()
protected={p for root in [A.pinned,A.candidate,HERE] for p in [lexical(root),root.resolve()]}
protected.update(p for f in sources for p in [lexical(f).parent,f.resolve().parent])
protected.update(Path(p).parent for p in tools.values())
for out in [lexical(A.out),A.out.resolve()]:
    if any(overlap(out,p) for p in protected):P.error('output overlaps protected input')
if os.path.lexists(A.out):P.error('output must be fresh and nonexistent')
def fingerprints():return {str(lexical(p)):{'resolved':str(p.resolve()),'sha256':sha(p)} for p in sorted(sources)}
before=fingerprints();OUT=A.out.resolve();OUT.mkdir(parents=True);(OUT/'temp').mkdir()
ENV={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','HOME':str(OUT),'TMPDIR':str(OUT/'temp'),'PYTHONDONTWRITEBYTECODE':'1','ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
record={'status':'RUNNING','invocation':sys.argv,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'commits':COMMITS,'candidate_tx_sha256':NEW,'inputs_before':before,'toolchain_before':tool_before,'toolchain_queries':queries,'environment':ENV,'commands':[],'snapshots':[],'results':[],'scope':'Immutable pinned PR30/PR35 regressions. Only mt_tx.c differs in candidate snapshots. Native synthetic software only.'}
def run(name,command,cwd,env=None):
    result=subprocess.run(command,cwd=cwd,env=env or ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
    log=OUT/(name+'.log');log.write_text(result.stdout)
    record['commands'].append({'name':name,'argv':command,'cwd':str(cwd),'env_overrides':{k:v for k,v in (env or ENV).items() if ENV.get(k)!=v},'exit':result.returncode,'log':str(log.relative_to(OUT))})
    return result
try:
    run('compiler-version',[CC,'--version'],OUT)
    for label in ['pr30','pr35']:
        for variant in ['old','candidate']:
            root=OUT/'snapshots'/(label+'-'+variant)
            selected=[r for r in records if r['label']==label]
            for r in selected:
                dest=root/r['path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(A.pinned/label/r['path'],dest)
                assert sha(dest)==r['sha256']
            if variant=='candidate':shutil.copyfile(A.candidate/'firmware/transport/mt_tx.c',root/'firmware/transport/mt_tx.c')
            changes=[r['path'] for r in selected if sha(root/r['path'])!=r['sha256']]
            assert changes==(['firmware/transport/mt_tx.c'] if variant=='candidate' else [])
            snapshot={'label':label,'variant':variant,'changed_paths':changes,'files':{r['path']:sha(root/r['path']) for r in selected}}
            record['snapshots'].append(snapshot)
            tests='app' if label=='pr30' else 'control';prefix=label+'-'+variant
            authorout=OUT/'runs'/(prefix+'-author')
            response=run(prefix+'-author',['/bin/bash','-x',str(root/'tests'/tests/'run.sh')],OUT,{**ENV,'OUT':str(authorout)})
            record['results'].append({'name':prefix+'-author','pass':response.returncode==0,'exit':response.returncode,'runs':'strict and ASan+UBSan','immutable_script':str(root/'tests'/tests/'run.sh')})
            if label=='pr30':
                for mode,script in [('strict','run.sh'),('sanitize','run-sanitized.sh')]:
                    response=run(prefix+'-independent-'+mode,['/bin/sh','-x',str(root/'tests/app/independent'/script)],OUT,{**ENV,'SOURCE':str(root)})
                    record['results'].append({'name':prefix+'-independent-'+mode,'pass':response.returncode==0,'exit':response.returncode,'expected_groups':24,'expected_full_windows':44,'immutable_script':str(root/'tests/app/independent'/script)})
            else:
                independentout=OUT/'runs'/(prefix+'-independent')
                response=run(prefix+'-independent',['/bin/bash','-x',str(root/'tests/control/independent/run.sh'),str(root),str(independentout)],OUT)
                row={'name':prefix+'-independent','pass':response.returncode==0,'exit':response.returncode,'expected_scenarios_per_mode':39,'runs':'strict and ASan+UBSan plus independent wire/sample oracle'}
                report=independentout/'independent-wire.json'
                if report.exists():row['wire_oracle']=json.loads(report.read_text())
                record['results'].append(row)
                # The two unchanged targeted reproducers are documented by
                # PR35 REPRODUCTION.md; run both on exact dependencies too.
                units=['app/mt_control','app/mt_capture_link','acquisition/acquisition','protocol/mt_wire','protocol/mt_command','protocol/mt_session','transport/mt_tx']
                for test in ['tombstone_hardware_repro','stale_stop_capacity_repro']:
                    for mode in ['strict','sanitize']:
                        flags=['-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-Wpedantic']
                        if mode=='sanitize':flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
                        dest=OUT/'runs'/(prefix+'-'+test+'-'+mode);dest.mkdir(parents=True)
                        command=[CC,*flags,'-I'+str(root),'-I'+str(root/'tests/control/independent'),str(root/'tests/control/critic-a'/(test+'.c')),*[str(root/'firmware'/(unit+'.c')) for unit in units],'-o',str(dest/'test')]
                        build=run(prefix+'-'+test+'-'+mode+'-build',command,OUT)
                        result=run(prefix+'-'+test+'-'+mode,[str(dest/'test')],dest) if build.returncode==0 else build
                        record['results'].append({'name':prefix+'-'+test+'-'+mode,'pass':build.returncode==0 and result.returncode==0,'build_exit':build.returncode,'exit':result.returncode})
            snapshot['source_and_test_inputs_unchanged']=all(sha(root/name)==digest for name,digest in snapshot['files'].items())
            assert snapshot['source_and_test_inputs_unchanged']
    record['status']='PASS' if all(r['pass'] for r in record['results']) else 'FAIL'
except Exception as error:record['status']='FAIL';record['error']=repr(error)
finally:
    record['inputs_after']=fingerprints();record['inputs_unchanged']=before==record['inputs_after']
    record['toolchain_after']=toolhashes();record['toolchain_unchanged']=record['toolchain_before']==record['toolchain_after']
    if not record['inputs_unchanged'] or not record['toolchain_unchanged']:record['status']='FAIL_INPUT_CHANGED'
    record['finish_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(record['status'],[(r['name'],r['pass']) for r in record['results']]);sys.exit(record['status']!='PASS')
