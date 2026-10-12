#!/usr/bin/env python3
"""Bounded CLI path-isolation tests using disposable fixtures only."""
import argparse, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--runner',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();runner=a.runner.resolve(strict=True);out=a.output.resolve()
# Never create fixture trees in runner input trees: caller supplies a new owned evidence directory.
if out.exists() or a.output.is_symlink():p.error('test output must not exist')
out.mkdir(parents=True,exist_ok=False)
root_names=('bridge','epoch','adapter','setup','reset','dependencies','toolchain')
trees=('dependencies/vendor','dependencies/evidence/config','adapter/firmware','epoch/firmware','bridge/firmware')
files=('dependencies/SHA256SUMS.sources','bridge/tools/build_core_patch.py','bridge/tests/usb-bridge/README.md',
       'setup/build/usbd.c','reset/build/dcd_stm32_fsdev.c','epoch/firmware/transport/mt_usb_epoch.h',
       'adapter/firmware/transport/mt_usb_cdc.c','adapter/firmware/transport/mt_usb_cdc.h',
       'dependencies/evidence/config/tusb_config.h',
       *('bridge/build/'+n for n in ('usbd.c','dcd_stm32_fsdev.c','core.patch','dcd.patch')),
       *('bridge/firmware/transport/'+n for n in ('mt_usb_bridge.c','mt_usb_bridge.h','mt_usb_cdc.c','mt_usb_cdc.h')),
       *('toolchain/bin/arm-none-eabi-'+n for n in ('gcc','as','size','nm','objdump','readelf')))
def fixture(base):
    for name in (*root_names,*trees,'toolchain/include/newlib','toolchain/lib/gcc/arm-none-eabi'):
        (base/name).mkdir(parents=True,exist_ok=True)
    for name in files:
        path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture input: '+name+'\n')
    return {n:base/n for n in root_names}
def fingerprint(base):
    result={}
    for path in sorted(base.rglob('*')):
        rel=str(path.relative_to(base))
        if path.is_symlink():result[rel]=['symlink',os.readlink(path)]
        elif path.is_file():result[rel]=['file',hashlib.sha256(path.read_bytes()).hexdigest()]
        elif path.is_dir():result[rel]=['directory']
        else:result[rel]=['other']
    return result
cases=[]
def check(name,configure,expected=2):
    base=out/name;base.mkdir();args=fixture(base);target=base/'fresh'/'nested'/'output'
    target=configure(base,args,target)
    before=fingerprint(base)
    argv=[sys.executable,str(runner),'--output',str(target)]
    for key,value in args.items():argv += ['--'+key,str(value)]
    if expected==0:argv += ['--check-paths-only']
    start=time.monotonic()
    try:
        r=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        result={'name':name,'argv':argv,'expected_exit_code':expected,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr,'elapsed_seconds':time.monotonic()-start}
    except subprocess.TimeoutExpired as e:
        result={'name':name,'argv':argv,'expected_exit_code':expected,'exit_code':None,'timed_out':True,'stdout':str(e.stdout),'stderr':str(e.stderr),'elapsed_seconds':time.monotonic()-start}
    after=fingerprint(base);result['fixture_unchanged']=before==after
    result['added_paths']=sorted(set(after)-set(before));result['removed_paths']=sorted(set(before)-set(after))
    result['changed_paths']=[k for k in before.keys() & after.keys() if before[k]!=after[k]]
    result['passed']=result['exit_code']==expected and result['fixture_unchanged']
    cases.append(result);(out/'results.json').write_text(json.dumps(cases,indent=2)+'\n')
    print(('PASS' if result['passed'] else 'FAIL')+' '+name)

check('valid_disjoint',lambda b,a,t:t,0)
def safe_alias(b,a,t):
    (b/'safe').mkdir();(b/'safe-alias').symlink_to(b/'safe',target_is_directory=True);return b/'safe-alias'/'new'
check('valid_safe_parent_alias',safe_alias,0)
for name in root_names:
    check('inside_root_'+name,lambda b,a,t,n=name:a[n]/'must-not-exist')
for i,name in enumerate(trees):
    check('inside_copied_tree_'+str(i),lambda b,a,t,n=name:b/n/'must-not-exist')
check('inside_toolchain_headers',lambda b,a,t:a['toolchain']/'include/newlib'/'must-not-exist')
check('input_ancestor_existing',lambda b,a,t:b)
check('existing_output_directory',lambda b,a,t:(b/'existing').mkdir() or b/'existing')
check('existing_output_file',lambda b,a,t:(b/'existing').write_text('preserve') and b/'existing')
def existing_alias(b,a,t):
    (b/'existing').mkdir();(b/'alias').symlink_to(b/'existing',target_is_directory=True);return b/'alias'
check('existing_output_symlink',existing_alias)
def dangling_alias(b,a,t):
    (b/'alias').symlink_to(b/'not-created',target_is_directory=True);return b/'alias'
check('dangling_output_symlink',dangling_alias)
def output_alias(b,a,t):
    (b/'alias').symlink_to(a['bridge']/'firmware',target_is_directory=True);return b/'alias'/'must-not-exist'
check('output_parent_alias_into_input',output_alias)
def input_alias(b,a,t):
    (b/'bridge-alias').symlink_to(a['bridge'],target_is_directory=True);a['bridge']=b/'bridge-alias';return b/'bridge'/'firmware'/'must-not-exist'
check('input_root_alias',input_alias)
def subtree_alias(b,a,t):
    original=a['bridge']/'firmware';destination=b/'external-firmware';original.rename(destination);original.symlink_to(destination,target_is_directory=True);return destination/'must-not-exist'
check('copied_subtree_external_alias',subtree_alias)
def singleton_alias(b,a,t):
    source=a['bridge']/'build/usbd.c';destination=b/'external-file';destination.mkdir();source.rename(destination/'usbd.c');source.symlink_to(destination/'usbd.c');return destination/'must-not-exist'
check('singleton_external_alias',singleton_alias)
check('dotdot_output_alias',lambda b,a,t:a['bridge']/'firmware'/'missing'/'..'/'must-not-exist')
def loop_alias(b,a,t):
    (b/'alias').symlink_to(b/'alias',target_is_directory=True);return b/'alias'/'must-not-exist'
check('symlink_loop',loop_alias)
for name in root_names:
    def missing_root(b,a,t,n=name):a[n]=b/('absent-'+n);return t
    check('missing_root_'+name,missing_root)
for i,name in enumerate(trees):
    def missing_tree(b,a,t,n=name):shutil.rmtree(b/n);return t
    check('missing_copied_tree_'+str(i),missing_tree)
for i,name in enumerate(files):
    def missing_file(b,a,t,n=name):(b/n).unlink();return t
    check('missing_singleton_'+str(i),missing_file)
result={'runner_sha256':hashlib.sha256(runner.read_bytes()).hexdigest(),'case_count':len(cases),'passed':sum(c['passed'] for c in cases),'per_case_timeout_seconds':5,'all_fixture_trees_unchanged':all(c['fixture_unchanged'] for c in cases),'scope':'Disposable owned fixture trees only; no compiler or source-copy commands run on successful preflight or any rejected case.'}
(out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2));raise SystemExit(0 if all(c['passed'] for c in cases) else 1)
