#!/usr/bin/env python3
"""SHA-bound native TX002 regression and differential tests; fresh output only."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--base', required=True, type=Path)
P.add_argument('--candidate', required=True, type=Path)
P.add_argument('--original', type=Path, default=HERE.parent/'original')
P.add_argument('--out', required=True, type=Path)
P.add_argument('--sanitize', action='store_true')
P.add_argument('--cc', default='/usr/bin/cc')
P.add_argument('--mutant', choices=['drop_reason','drop_tail','wrong_session','wrong_type','skip_zero_init','wrong_length','retain_as_reply','ignore_prior_replies','weaken_capacity','early_mutation'])
A=P.parse_args()

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def lexical(p): return Path(os.path.abspath(p))
def overlap(a,b): return a==b or a in b.parents or b in a.parents

# Resolve with the same clean search path later used by every compiler child.
# An unavailable driver must not leave an output directory behind.
compiler_path=shutil.which(A.cc,path='/usr/bin:/bin')
if compiler_path is None:P.error('compiler is unavailable or not executable: '+A.cc)
compiler_lexical=lexical(Path(compiler_path))
CC=str(compiler_lexical.resolve())
toolchain={'driver':{'lexical':str(compiler_lexical),'resolved':CC,'sha256':sha(Path(CC))}}
tool_queries=[]
for name in ['cc1','as','ld','collect2']:
    cmd=[CC,'-print-prog-name='+name]
    query=subprocess.run(cmd,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    tool_queries.append({'argv':cmd,'exit':query.returncode,'stdout':query.stdout,'stderr':query.stderr})
    if query.returncode==0:
        candidate=query.stdout.strip()
        selected=shutil.which(candidate,path='/usr/bin:/bin')
        if selected is None:P.error('selected compiler helper is unavailable: '+candidate)
        path=lexical(Path(selected));resolved=path.resolve()
        toolchain[name]={'lexical':str(path),'resolved':str(resolved),'sha256':sha(resolved)}

# Resolve every source independently; symlinked files may live outside roots.
# Protect both lexical and resolved directories, including script/provenance.
roots=[A.base,A.candidate,A.original,HERE]
sources={}
for version,root in [('old',A.base),('new',A.candidate)]:
    for name in ['mt_tx.c','mt_tx.h']:
        sources[f'{version}/firmware/transport/{name}']=root/'firmware/transport'/name
    for name in ['mt_wire.c','mt_wire.h']:
        sources[f'{version}/firmware/protocol/{name}']=A.base/'firmware/protocol'/name
    for name in ['run.sh','test_mt_tx.c']:
        sources[f'{version}/tests/transport/{name}']=A.original/name
sources['harness/differential.c']=HERE/'differential.c'
sources['harness/run.py']=Path(__file__).absolute()
sources['harness/provenance.json']=A.original/'provenance.json'
output=[lexical(A.out),A.out.resolve()]
protected=set()
for root in roots:
    protected.update([lexical(root),root.resolve()])
for source in sources.values():
    if not source.is_file(): P.error('missing input: '+str(source))
    protected.update([lexical(source).parent,source.resolve().parent])
for item in toolchain.values():
    protected.update([Path(item['lexical']).parent,Path(item['resolved']).parent])
for out in output:
    for source in protected:
        if overlap(out,source):P.error('output overlaps protected input: '+str(source))
if os.path.lexists(A.out):P.error('output must be fresh and nonexistent')
expected={
 'old/firmware/transport/mt_tx.c':'e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e',
 'new/firmware/transport/mt_tx.c':'b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf',
}
for label,path in sources.items():
    digest=sha(path)
    required=expected.get(label)
    if label.endswith('/mt_tx.h'):required='68f013a8ffb6e946e9cd92f3e760bf593746afdc7097d11d837a101c5ab83dd9'
    if label.endswith('/mt_wire.c'):required='84677c6054294cc1d3dbf7095352a265fc038288693e890f6673d911da5bdb8c'
    if label.endswith('/mt_wire.h'):required='c61ef9e7beba2947724ef2006ce305cdb7dd22204d03c9524b4a57fe2f3ae547'
    if required and digest!=required:P.error('SHA mismatch: '+label)
    blob={'run.sh':'581a7e174f51d0c65102e1f09924089934fd6bbf','test_mt_tx.c':'ad17814cd5512ff7c2788ebda3b6f06f31e17148'}.get(path.name)
    if blob:
        data=path.read_bytes()
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=blob:P.error('Git blob mismatch: '+label)
def fingerprint():return {str(lexical(v)):{'resolved':str(v.resolve()),'sha256':sha(v)} for v in sorted(set(sources.values()))}
before=fingerprint()
OUT=A.out.resolve();OUT.mkdir(parents=True,exist_ok=False)
(OUT/'temp').mkdir()
record={'status':'RUNNING','start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'invocation':sys.argv,
 'input_before':before,'expected_production_sha256':expected,'commands':[],'results':[],'sanitize':A.sanitize,'mutant':A.mutant,
 'toolchain_before':toolchain,'toolchain_queries':tool_queries,
 'scope':'Native synthetic tests. No MCU, IRQ, timing, hardware USB, Windows or stack-proof claim.'}
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C','LANG':'C','HOME':str(OUT),'TMPDIR':str(OUT/'temp'),
 'PYTHONDONTWRITEBYTECODE':'1','ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
record['environment']=ENV
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
COMMON=['-std=c11','-O1' if A.sanitize else '-O2','-g','-fno-lto','-Wall','-Wextra','-Werror','-pedantic-errors','-Wconversion','-Wshadow','-fno-omit-frame-pointer']
if A.sanitize:COMMON+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
EXIT=0
with (OUT/'commands.log').open('w') as log:
    def run(cmd,required=True,env=None):
        log.write('+ '+shlex.join(cmd)+'\n');log.flush()
        r=subprocess.run(cmd,cwd=OUT,env=env or ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        log.write(r.stdout);log.flush();record['commands'].append({'argv':cmd,'exit':r.returncode,'output':r.stdout})
        if required and r.returncode:raise RuntimeError(f'command failed ({r.returncode}): {cmd[0]}')
        return r
    try:
        for label,path in sources.items():
            dest=OUT/'snapshot'/label;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
            assert sha(dest)==before[str(lexical(path))]['sha256']
        if A.mutant:
            path=OUT/'snapshot/new/firmware/transport/mt_tx.c';text=path.read_text()
            controls={
             'drop_reason':('memcpy(slot->message.payload, retained_abort, sizeof retained_abort);','memcpy(slot->message.payload, retained_abort, sizeof retained_abort); slot->message.payload[8] = 1;'),
             'drop_tail':('memcpy(slot->message.payload, retained_abort, sizeof retained_abort);','memcpy(slot->message.payload, retained_abort, 8);'),
             'wrong_session':('slot->message.session = t->session;','slot->message.session = 0;'),
             'wrong_type':('slot->message.type = 147u;','slot->message.type = 146u;'),
             'skip_zero_init':('memset(&slot->message, 0, sizeof slot->message);','/* mutation: stale message metadata survives */'),
             'wrong_length':('slot->message.payload_length = (uint16_t)sizeof retained_abort;','slot->message.payload_length = 11;'),
             'retain_as_reply':('slot->reply = 0;','slot->reply = 1;'),
             'ignore_prior_replies':('if ((i == 0u && t->offset != 0u) || t->slots[i].reply)\n            t->slots[kept++] = t->slots[i];','if (i == 0u && t->offset != 0u)\n            t->slots[kept++] = t->slots[i];'),
             'weaken_capacity':('if (kept + 2u + (size_t)terminal_needed > MT_TX_CAPACITY)','if (kept + 2u + (size_t)terminal_needed > MT_TX_CAPACITY + 1u)'),
             'early_mutation':('if (t->fault != MT_TX_OK) return MT_TX_FAULT;\n    if (t->session == 0u) return MT_TX_NOT_NEGOTIATED;\n    if (t->stopping) return MT_TX_DRAINING;\n    terminal_needed', 't->stop_ms = now;\n    if (t->fault != MT_TX_OK) return MT_TX_FAULT;\n    if (t->session == 0u) return MT_TX_NOT_NEGOTIATED;\n    if (t->stopping) return MT_TX_DRAINING;\n    terminal_needed')}
            old,new=controls[A.mutant];assert text.count(old)==1;text=text.replace(old,new);path.write_text(text)
            record['mutated_source_sha256']=sha(path)
        run([CC,'--version'])
        for version in ['old','new']:
            root=OUT/'snapshot'/version
            exe=OUT/('original-'+version)
            run([CC,*COMMON,str(root/'firmware/protocol/mt_wire.c'),str(root/'firmware/transport/mt_tx.c'),str(root/'tests/transport/test_mt_tx.c'),'-o',str(exe)])
            result=run([str(exe)],required=False)
            record['results'].append({'case':'original_14_'+version,'pass':result.returncode==0,'output':result.stdout})
        functions=['init','hello','enqueue','stop','poll','terminal_committed','window_status'];objects=[]
        for version in ['old','new']:
            obj=OUT/(version+'.o');objects.append(str(obj))
            flags=[f'-Dmt_tx_{name}={version}_tx_{name}' for name in functions]
            run([CC,*COMMON,*flags,'-c',str(OUT/'snapshot'/version/'firmware/transport/mt_tx.c'),'-o',str(obj)])
        exe=OUT/'differential'
        run([CC,*COMMON,'-I'+str(OUT/'snapshot/new/firmware/transport'),str(OUT/'snapshot/harness/differential.c'),str(OUT/'snapshot/new/firmware/protocol/mt_wire.c'),*objects,'-o',str(exe)])
        record['executable_sha256']=sha(exe)
        names=run([str(exe),'--list']).stdout.splitlines()
        for name in names:
            result=run([str(exe),name],required=False)
            row={'case':name,'pass':result.returncode==0,'exit':result.returncode,'output':result.stdout}
            if result.returncode==0:row.update(json.loads(result.stdout))
            record['results'].append(row)
        # Execute the original shell runner unchanged in strict builds too.
        # Its mktemp output is disposable; persistent original-* binaries above
        # are retained whether tests pass or fail.
        if not A.sanitize and not A.mutant:
            result=run(['/bin/sh',str(OUT/'snapshot/new/tests/transport/run.sh')],required=False,env={**ENV,'CC':CC})
            record['original_shell_runner']={'exit':result.returncode,'output':result.stdout}
            if result.returncode:raise RuntimeError('immutable original shell runner failed')
        record['status']='PASS' if all(x['pass'] for x in record['results']) else 'FAIL'
        EXIT=int(record['status']!='PASS')
    except Exception as error:
        record['status']='FAIL';record['error']=repr(error);EXIT=1
    finally:
        try:after=fingerprint();record['input_after']=after;record['inputs_unchanged']=before==after
        except Exception as error:record['fingerprint_error']=repr(error);record['inputs_unchanged']=False
        if not record['inputs_unchanged']:record['status']='FAIL_INPUT_CHANGED';EXIT=1
        record['toolchain_after']={name:{**item,'sha256':sha(Path(item['resolved']))} for name,item in toolchain.items()}
        record['toolchain_unchanged']=toolchain==record['toolchain_after']
        if not record['toolchain_unchanged']:record['status']='FAIL_TOOLCHAIN_CHANGED';EXIT=1
        record['finish_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (OUT/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(record['status']+': '+str(OUT/'receipt.json'))
sys.exit(EXIT)
