#!/usr/bin/env python3
"""Portable, adversarial path/environment/failure checks for native runner."""
import argparse, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys
sys.dont_write_bytecode=True
P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--base',required=True,type=Path);P.add_argument('--candidate',required=True,type=Path)
P.add_argument('--original',required=True,type=Path);P.add_argument('--out',required=True,type=Path)
A=P.parse_args();HERE=Path(__file__).absolute().parent
roots=[A.base,A.candidate,A.original,HERE]
files=set(f for root in roots for f in root.rglob('*') if f.is_file())
protected={p for root in roots for p in [Path(os.path.abspath(root)),root.resolve()]}
protected.update(p for f in files for p in [Path(os.path.abspath(f)).parent,f.resolve().parent])
def overlap(a,b):return a==b or a in b.parents or b in a.parents
for out in [Path(os.path.abspath(A.out)),A.out.resolve()]:
    if any(overlap(out,p) for p in protected):P.error('output overlaps protected input')
if os.path.lexists(A.out):P.error('output must be fresh')
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def fingerprint():return {str(f):sha(f) for f in sorted(files)}
before=fingerprint();A.out.mkdir(parents=True)
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
record={'status':'RUNNING','input_before':before,'results':[]}
def execute(name,out,base=None,candidate=None,original=None,runner=None,extra=(),environment=None,should_reject=True):
    cmd=['/usr/bin/python3',str(runner or HERE/'run.py'),'--base',str(base or A.base),'--candidate',str(candidate or A.candidate),'--original',str(original or A.original),'--out',str(out),*extra]
    existed=os.path.lexists(out)
    result=subprocess.run(cmd,env=environment or env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (A.out/(name+'.log')).write_text(result.stdout)
    passed=(result.returncode==2 and (existed or not os.path.lexists(out))) if should_reject else result.returncode==0
    record['results'].append({'case':name,'pass':passed,'exit':result.returncode,'command':cmd})
    return result
try:
    existing=A.out/'existing';existing.mkdir();existing_file=A.out/'existing-file';existing_file.write_text('unchanged')
    execute('existing_directory',existing);execute('existing_file',existing_file)
    execute('base_descendant',A.base/'tx-test-must-not-exist')
    execute('candidate_descendant',A.candidate/'tx-test-must-not-exist')
    execute('original_descendant',A.original/'tx-test-must-not-exist')
    execute('script_descendant',HERE/'tx-test-must-not-exist')
    execute('input_ancestor',A.base.parent)
    link=A.out/'output-alias';link.symlink_to(A.candidate.resolve(),target_is_directory=True)
    execute('resolved_output_symlink',link/'tx-test-must-not-exist')
    # A source FILE can escape its advertised root through a symlink.
    candidate=A.out/'candidate-link';(candidate/'firmware/transport').mkdir(parents=True)
    backing=A.out/'backing';backing.mkdir();shutil.copyfile(A.candidate/'firmware/transport/mt_tx.c',backing/'mt_tx.c')
    (candidate/'firmware/transport/mt_tx.c').symlink_to(backing/'mt_tx.c')
    (candidate/'firmware/transport/mt_tx.h').symlink_to((A.candidate/'firmware/transport/mt_tx.h').resolve())
    execute('resolved_source_backing_parent',backing/'must-not-exist',candidate=candidate)
    execute('lexical_source_root',candidate/'unused/must-not-exist',candidate=candidate)
    # Script root is protected even when __file__ arrived through a directory alias.
    script_alias=A.out/'script-alias';script_alias.symlink_to(HERE.resolve(),target_is_directory=True)
    execute('resolved_script_parent',HERE/'must-not-exist',runner=script_alias/'run.py')
    original=A.out/'original-link';original.mkdir();evidence=A.out/'evidence-backing';evidence.mkdir()
    for name in ['run.sh','test_mt_tx.c','provenance.json']:
        shutil.copyfile(A.original/name,evidence/name);(original/name).symlink_to(evidence/name)
    execute('resolved_evidence_parent',evidence/'must-not-exist',original=original)
    bad=A.out/'bad-sha';shutil.copytree(A.candidate,bad);path=bad/'firmware/transport/mt_tx.c';path.write_text(path.read_text()+'\n/* deliberate input mismatch */\n')
    execute('sha_mismatch_before_mkdir',A.out/'sha-mismatch-output',candidate=bad)
    missing=A.out/'missing';missing.mkdir();execute('missing_input_before_mkdir',A.out/'missing-output',candidate=missing)
    execute('missing_compiler_before_mkdir',A.out/'missing-compiler-output',extra=['--cc',str(A.out/'nonexistent-compiler')])
    # No compiler flags or include/library injection may come from caller env.
    poison={**env,'CC':'/usr/bin/false','CFLAGS':'-DNDEBUG -include does-not-exist','CPPFLAGS':'-DNDEBUG','CPATH':'/does-not-exist','C_INCLUDE_PATH':'/does-not-exist','LIBRARY_PATH':'/does-not-exist','GCC_EXEC_PREFIX':'/does-not-exist/','COMPILER_PATH':'/does-not-exist','LD_PRELOAD':'/does-not-exist.so','SANITIZE':'1','ASAN_OPTIONS':'invalid_option=1','UBSAN_OPTIONS':'invalid_option=1'}
    execute('clean_environment',A.out/'clean-environment',environment=poison,should_reject=False)
    clean=json.loads((A.out/'clean-environment/receipt.json').read_text())
    record['results'].append({'case':'toolchain_hashes_bound','pass':set(clean['toolchain_before'])=={'driver','cc1','as','ld','collect2'} and clean['toolchain_unchanged']})
    # Explicit bad compiler is allowed for a deterministic failure-preservation test.
    result=execute('compile_failure_receipt',A.out/'compiler-failure',extra=['--cc','/usr/bin/false'],should_reject=False)
    failure=json.loads((A.out/'compiler-failure/receipt.json').read_text())
    record['results'][-1]['pass']=result.returncode==1 and failure['status']=='FAIL' and failure['inputs_unchanged'] and bool(failure['commands'])
    # A relocated script and all four relocated input roots prove no workspace defaults.
    portable=A.out/'portable';portable.mkdir();portable_harness=portable/'harness';shutil.copytree(HERE,portable_harness)
    portable_original=portable/'original';shutil.copytree(A.original,portable_original)
    portable_base=portable/'base';shutil.copytree(A.base,portable_base)
    portable_candidate=portable/'candidate';shutil.copytree(A.candidate,portable_candidate)
    execute('portable_relocated_inputs',A.out/'portable-result',base=portable_base,candidate=portable_candidate,original=portable_original,runner=portable_harness/'run.py',should_reject=False)
    assert existing_file.read_text()=='unchanged'
    record['status']='PASS' if all(x['pass'] for x in record['results']) else 'FAIL'
except Exception as error:record['status']='FAIL';record['error']=repr(error)
finally:
    record['input_after']=fingerprint();record['inputs_unchanged']=before==record['input_after']
    if not record['inputs_unchanged']:record['status']='FAIL_INPUT_CHANGED'
    (A.out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(record['status'],len(record['results']),[(x['case'],x['pass']) for x in record['results']]);sys.exit(record['status']!='PASS')
