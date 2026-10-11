#!/usr/bin/env python3
"""Supporting TX002 comparison: object relink evidence, never final firmware."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def su(p):
    result = {}
    for line in p.read_text().splitlines():
        loc, n, kind = line.split('\t')
        result[loc.rsplit(':', 1)[1]] = {'bytes': int(n), 'kind': kind}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for n in ('baseline', 'old-source', 'candidate', 'recipe', 'toolchain', 'input-root', 'out'):
        parser.add_argument('--' + n, type=Path, required=True)
    parser.add_argument('--check-paths-only', action='store_true')
    a = parser.parse_args()
    runner = Path(__file__).resolve(strict=True).parent
    contract = json.loads((runner/'input_contract.json').read_text())
    base = a.baseline.resolve(strict=True)
    old = a.old_source.resolve(strict=True)
    cand = a.candidate.resolve(strict=True)
    recipe = a.recipe.resolve(strict=True)
    tc = a.toolchain.resolve(strict=True)
    ir = a.input_root.resolve(strict=True)
    raw = a.out.absolute()
    if raw.exists() or raw.is_symlink():
        parser.error('output already exists')
    out = raw.resolve()
    expected = {}
    def bind(p, digest=None):
        p = p.resolve(strict=True)
        if not p.is_file():
            parser.error('not a regular input: ' + str(p))
        h = sha(p)
        if digest is not None and h != digest:
            parser.error('hash mismatch: ' + str(p))
        if p in expected and expected[p] != h:
            parser.error('conflicting input identity: ' + str(p))
        expected[p] = h
        return p
    bind(base/'receipt.json', contract['baseline_receipt'])
    prior = json.loads((base/'receipt.json').read_text())
    if prior['status'] != 'PASS_RELOCATABLE_ONLY' or not prior['inputs_unchanged']:
        parser.error('baseline receipt is not a successful stable-input closure')
    oldroot = Path(prior['invocation'][prior['invocation'].index('--out')+1]).parent
    oldtc = Path(prior['invocation'][prior['invocation'].index('--toolchain')+1])
    def relocate(p):
        p = Path(p)
        if p.is_relative_to(oldtc):
            return tc/p.relative_to(oldtc)
        if p.is_relative_to(oldroot):
            return ir/p.relative_to(oldroot)
        parser.error('baseline input cannot be portably relocated: ' + str(p))
    # Bind every baseline source/header/tool, plus every actual snapshot input.
    for p, digest in prior['inputs_before'].items():
        bind(relocate(p), digest)
    for rel, original in prior['input_map'].items():
        bind(base/'snapshot'/rel, prior['inputs_before'][original])
    for name, digest in prior['objects_sha256'].items():
        bind(base/name, digest)
    for name, digest in contract['baseline_auxiliary'].items():
        bind(base/name, digest)
    bind(old, contract['old_source'])
    bind(cand/'firmware/transport/mt_tx.c', contract['new_source'])
    bind(cand/'firmware/transport/mt_tx.h', contract['header'])
    bind(cand/'tests/transport-stack/README.md', contract['readme'])
    bind(recipe, contract['recipe'])
    for name in ('compare_arm.py', 'run.sh', 'input_contract.json', 'abi_probe.c'):
        bind(runner/name)
    tools = {n:bind(tc/'bin'/('arm-none-eabi-'+n)) for n in ('gcc','ld','nm','size','readelf','objdump','as')}
    cc1 = bind(tc/'lib/gcc/arm-none-eabi/14.2.1/cc1')
    selected_as = bind(tc/'lib/arm-none-eabi/bin/as')
    compile_prior = next(c for c in prior['commands'] if c['label']=='compile-13-mt_tx')
    link_prior = next(c for c in prior['commands'] if c['label']=='link-relocatable')
    ordered = [Path(s).name for s in link_prior['argv'][5:]]
    if len(ordered)!=25 or len(set(ordered))!=25 or ordered[13]!='13-mt_tx.o' or 'components.o' in ordered:
        parser.error('unexpected baseline individual-object link list')
    compile_flags = compile_prior['argv'][1:compile_prior['argv'].index('-MD')]
    required = {'-std=c11','-Os','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-fno-common','-fno-short-enums','-ffunction-sections','-fdata-sections','-fstack-usage','-Werror','-pedantic-errors','-Wconversion','-Wsign-conversion'}
    if not required <= set(compile_flags):
        parser.error('baseline flags are not strict expected ARM flags')
    recipe_args = []
    for opt in ('base','adc','bridge','epoch','runtime','irq','dependencies','config','toolchain','descriptors','memory'):
        value = prior['invocation'][prior['invocation'].index('--'+opt)+1]
        recipe_args += ['--'+opt, str(relocate(value).resolve(strict=True))]
    roots = [base, cand, tc, old.parent, recipe.parent, runner]
    roots += [Path(recipe_args[i]) for i in range(1,len(recipe_args),2)]
    # Check every resolved file parent and both own script/wrapper parents before mkdir.
    protected = set(roots) | {p.parent for p in expected}
    for p in [out, *expected, *protected]:
        if any(c in str(p) for c in ('\n','\r',':','#','$','\\','"',"'")):
            parser.error('unsupported dependency-path character: ' + str(p))
    for p in protected:
        if out==p or out in p.parents or p in out.parents:
            parser.error('output overlaps protected input: ' + str(p))
    if a.check_paths_only:
        print('PASS: hash-bound read-only path preflight'); return 0
    out.mkdir(parents=True, exist_ok=False)
    (out/'tmp').mkdir()
    env={'PATH':str(tc/'bin')+':/usr/bin:/bin','LC_ALL':'C','LANG':'C','TZ':'UTC','PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(out/'tmp')}
    record={'status':'RUNNING','start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'invocation':sys.argv,'environment':env,'inputs_before':{str(p):h for p,h in sorted(expected.items())},'commands':[],'scope':'Supporting strict ARM TX object and 25-individual-object relocatable closure comparison; no final ELF, no runtime/high-water/IRQ/timing/full-stack or hardware/resource-fit proof.'}
    def run(label, argv, allow_failure=False):
        argv=list(map(str,argv))
        r=subprocess.run(argv,cwd=out,env=env,capture_output=True,text=True,input='')
        (out/(label+'.stdout')).write_text(r.stdout)
        (out/(label+'.stderr')).write_text(r.stderr)
        record['commands'].append({'label':label,'argv':argv,'exit':r.returncode})
        with (out/'commands.log').open('a') as f:
            f.write('+ '+shlex.join(argv)+'\n'+r.stdout+r.stderr)
        if r.returncode and not allow_failure:
            raise RuntimeError(label+' failed: '+str(r.returncode))
        return r
    def mapped_flags(stage):
        oldstage=str(Path(prior['invocation'][prior['invocation'].index('--out')+1])/'snapshot')
        return [f.replace(oldstage,str(stage)).replace(str(oldtc),str(tc)) for f in compile_flags]
    deps={}
    def compile_one(label, src, obj, stage):
        dep=obj.with_suffix('.d')
        run(label,[tools['gcc'],*mapped_flags(stage),'-MD','-MF',dep,'-c',src,'-o',obj])
        for word in shlex.split(dep.read_text().replace('\\\n',' '))[1:]:
            p=Path(word).resolve(strict=True)
            if p.is_relative_to(stage):
                rel=str(p.relative_to(stage))
                origin = cand/'firmware/transport/mt_tx.c' if label=='compile-new' and rel=='firmware/transport/mt_tx.c' else (cand/'firmware/transport/mt_tx.h' if label=='compile-new' and rel=='firmware/transport/mt_tx.h' else base/'snapshot'/rel)
                if sha(p)!=expected[origin.resolve()]:
                    raise RuntimeError('snapshot dependency changed: '+str(p))
            elif p not in expected or sha(p)!=expected[p]:
                raise RuntimeError('unbound compiled dependency: '+str(p))
            deps[str(p)]=sha(p)
    def inspect(label,obj):
        size_text=run(label+'-size',[tools['size'],obj]).stdout
        size=[int(s) for s in size_text.splitlines()[1].split()[:3]]
        for suffix,args in [('nm',['-S','--size-sort']),('global-nm',['-g','--defined-only']),('undefined',['-u'])]:
            run(label+'-'+suffix,[tools['nm'],*args,obj])
        run(label+'-elf',[tools['readelf'],'-h','-A','-S',obj])
        run(label+'-disassembly',[tools['objdump'],'-dr',obj])
        return {'text':size[0],'data':size[1],'bss':size[2]}
    try:
        (out/'runner-snapshot').mkdir()
        for name in ('compare_arm.py','run.sh','input_contract.json','abi_probe.c'):
            shutil.copyfile(runner/name,out/'runner-snapshot'/name)
        run('compiler-version',[tools['gcc'],'--version'])
        for n,p in [('cc1',cc1),('as',selected_as)]:
            actual=Path(run('selected-'+n,[tools['gcc'],'-print-prog-name='+n]).stdout.strip()).resolve(strict=True)
            if actual!=p or sha(actual)!=expected[p]:
                raise RuntimeError('unbound selected compiler helper '+str(actual))
        for variant in ('old','new'):
            stage=out/variant/'snapshot'
            for rel in prior['input_map']:
                target=stage/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                src=base/'snapshot'/rel
                if rel=='firmware/transport/mt_tx.c':
                    src=old if variant=='old' else cand/rel
                elif variant=='new' and rel=='firmware/transport/mt_tx.h':
                    src=cand/rel
                shutil.copyfile(src,target)
                if not (sha(target)==expected[src.resolve()]):
                    raise RuntimeError(str('verification failed at: sha(target)==expected[src.resolve()]'))
            obj=out/variant/'13-mt_tx.o'
            compile_one('compile-'+variant,stage/'firmware/transport/mt_tx.c',obj,stage)
            record[variant+'_tx_size']=inspect(variant+'-tx',obj)
            record[variant+'_stack_usage']=su(obj.with_suffix('.su'))
            abi=out/variant/'abi_probe.o'
            compile_one('compile-'+variant+'-abi',runner/'abi_probe.c',abi,stage)
            run(variant+'-abi',[tools['nm'],'-S','--size-sort','--radix=d',abi])
            objects=[out/variant/n if n=='13-mt_tx.o' else base/n for n in ordered]
            combined=out/variant/'components.o'
            run('link-'+variant,[tools['ld'],'-r','-Map='+str(out/variant/'components.map'),'-o',combined,*objects])
            record[variant+'_link_inputs']={str(p):sha(p) for p in objects}
            record[variant+'_aggregate_size']=inspect(variant+'-aggregate',combined)
            actual={s.split()[-1] for s in (out/(variant+'-aggregate-undefined.stdout')).read_text().splitlines() if s.strip()}
            if actual!={'tusb_time_millis_api'}:
                raise RuntimeError('unexpected aggregate externals: '+str(actual))
        if not (sha(out/'old/13-mt_tx.o')==expected[base/'13-mt_tx.o']):
            raise RuntimeError(str("verification failed at: sha(out/'old/13-mt_tx.o')==expected[base/'13-mt_tx.o']"))
        if not (sha(out/'old/components.o')==expected[base/'components.o']):
            raise RuntimeError(str("verification failed at: sha(out/'old/components.o')==expected[base/'components.o']"))
        if not ((out/'old-abi.stdout').read_bytes()==(out/'new-abi.stdout').read_bytes()):
            raise RuntimeError(str("verification failed at: (out/'old-abi.stdout').read_bytes()==(out/'new-abi.stdout').read_bytes()"))
        def symbols(p):
            return sorted(' '.join(line.split()[1:]) for line in p.read_text().splitlines() if line.strip())
        if not (symbols(out/'old-tx-global-nm.stdout')==symbols(out/'new-tx-global-nm.stdout')):
            raise RuntimeError(str("verification failed at: symbols(out/'old-tx-global-nm.stdout')==symbols(out/'new-tx-global-nm.stdout')"))
        if not ((out/'old-tx-undefined.stdout').read_bytes()==(out/'new-tx-undefined.stdout').read_bytes()):
            raise RuntimeError(str("verification failed at: (out/'old-tx-undefined.stdout').read_bytes()==(out/'new-tx-undefined.stdout').read_bytes()"))
        if not (record['old_tx_size']['data']==record['new_tx_size']['data']==0):
            raise RuntimeError(str("verification failed at: record['old_tx_size']['data']==record['new_tx_size']['data']==0"))
        if not (record['old_tx_size']['bss']==record['new_tx_size']['bss']==0):
            raise RuntimeError(str("verification failed at: record['old_tx_size']['bss']==record['new_tx_size']['bss']==0"))
        chain=[('5-mt_runtime.su','mt_runtime_step'),('15-mt_usb_cdc.su','mt_usb_cdc_step'),('15-mt_usb_cdc.su','mt_usb_cdc_step_impl'),('4-mt_control_rx.su','mt_control_rx_feed'),('3-mt_control.su','mt_control_receive'),('1-mt_capture_link.su','mt_capture_link_stop'),('13-mt_tx.su','mt_tx_stop'),('13-mt_tx.su','append'),('18-mt_memory.su','memcpy')]
        frames={}
        for variant in ('old','new'):
            frames[variant]=[]
            for name,func in chain:
                p=out/variant/name if name=='13-mt_tx.su' else base/name
                frame=su(p)[func]
                if not (frame['kind']=='static'):
                    raise RuntimeError(str("verification failed at: frame['kind']=='static'"))
                frames[variant].append({'function':func,**frame})
        record['selected_nested_stop_chain']={v:{'frames':f,'sum_bytes':sum(x['bytes'] for x in f)} for v,f in frames.items()}
        for function, frame in record['old_stack_usage'].items():
            if function != 'mt_tx_stop':
                if not (frame == record['new_stack_usage'][function]):
                    raise RuntimeError(str("verification failed at: frame == record['new_stack_usage'][function]"))
        edges = []
        for variant in ('old', 'new'):
            asm = (out/(variant+'-aggregate-disassembly.stdout')).read_text()
            funcs = [entry[1] for entry in chain]
            for caller, callee in zip(funcs, funcs[1:]):
                section = asm.split('Disassembly of section .text.'+caller+':\n')[1].split('Disassembly of section ',1)[0]
                lines = section.splitlines()
                matches = []
                for index, line in enumerate(lines):
                    if re.search(r'R_ARM_THM_CALL\s+'+re.escape(callee)+r'$',line):
                        if not ('\tbl\t' in lines[index-1]):
                            raise RuntimeError(str("verification failed at: '\\tbl\\t' in lines[index-1]"))
                        matches.append({'instruction':lines[index-1].strip(),'relocation':line.strip()})
                if not (matches):
                    raise RuntimeError(str((variant, caller, callee)))
                edges.append({'variant':variant,'caller':caller,'callee':callee,'calls':matches,'entry_excerpt':lines[1:10]})
        record['selected_chain_direct_calls'] = edges
        (out/'selected_chain_direct_calls.json').write_text(json.dumps(edges,indent=2)+'\n')
        old_attrs = (out/'old-tx-elf.stdout').read_text().split('Attribute Section: aeabi')[1]
        new_attrs = (out/'new-tx-elf.stdout').read_text().split('Attribute Section: aeabi')[1]
        if not (old_attrs == new_attrs):
            raise RuntimeError(str('verification failed at: old_attrs == new_attrs'))
        # Independent production recipe: baseline mode and candidate overlay mode.
        for variant in ('baseline','overlay'):
            dest=out/('recipe-'+variant)
            args=[sys.executable,'-B',recipe,*recipe_args,'--out',dest]
            if variant=='overlay':
                args+=['--tx-stop',cand]
            run('recipe-'+variant,args)
            receipt=json.loads((dest/'receipt.json').read_text())
            if not (receipt['status']=='PASS_RELOCATABLE_ONLY' and receipt['inputs_unchanged']):
                raise RuntimeError(str("verification failed at: receipt['status']=='PASS_RELOCATABLE_ONLY' and receipt['inputs_unchanged']"))
            for name in ordered:
                expected_obj=out/'new'/name if variant=='overlay' and name=='13-mt_tx.o' else base/name
                if not (sha(dest/name)==sha(expected_obj)):
                    raise RuntimeError(str((variant,name)))
            expected_aggregate=out/('new' if variant=='overlay' else 'old')/'components.o'
            if not (sha(dest/'components.o')==sha(expected_aggregate)):
                raise RuntimeError(str("verification failed at: sha(dest/'components.o')==sha(expected_aggregate)"))
            for name in ordered:
                expected_su=su(out/('new' if variant=='overlay' else 'old')/'13-mt_tx.su') if name=='13-mt_tx.o' else su(base/Path(name).with_suffix('.su'))
                if not (su(dest/Path(name).with_suffix('.su'))==expected_su):
                    raise RuntimeError(str("verification failed at: su(dest/Path(name).with_suffix('.su'))==expected_su"))
        # Run recipe read-only guards: no output directory may be created.
        bad_targets=[('existing',out),('base-descendant',Path(recipe_args[1])/'reject-tx002-output'),('candidate-descendant',cand/'reject-tx002-output'),('recipe-parent',recipe.parent/'reject-tx002-output'),('toolchain-descendant',tc/'reject-tx002-output'),('input-ancestor',ir)]
        guards=[]
        for name,dest in bad_targets:
            existed=dest.exists()
            r=run('recipe-guard-'+name,[sys.executable,'-B',recipe,*recipe_args,'--tx-stop',cand,'--out',dest,'--check-paths-only'],True)
            if not (r.returncode!=0 and (existed or not dest.exists())):
                raise RuntimeError(str('verification failed at: r.returncode!=0 and (existed or not dest.exists())'))
            guards.append({'case':name,'target':str(dest),'exit':r.returncode,'no_creation':existed or not dest.exists()})
        good=out/'recipe-preflight-fresh'
        run('recipe-guard-fresh',[sys.executable,'-B',recipe,*recipe_args,'--tx-stop',cand,'--out',good,'--check-paths-only'])
        if not (not good.exists()):
            raise RuntimeError(str('verification failed at: not good.exists()'))
        # Symlink source escape must be protected by the resolved file parent.
        fixture=out/'guard-fixtures'
        overlay=fixture/'overlay'
        escaped=fixture/'escaped'
        (overlay/'firmware/transport').mkdir(parents=True)
        escaped.mkdir()
        shutil.copyfile(cand/'firmware/transport/mt_tx.c',escaped/'mt_tx.c')
        shutil.copyfile(cand/'firmware/transport/mt_tx.h',overlay/'firmware/transport/mt_tx.h')
        (overlay/'firmware/transport/mt_tx.c').symlink_to(escaped/'mt_tx.c')
        dest=escaped/'reject-output'
        r=run('recipe-guard-resolved-file-parent',[sys.executable,'-B',recipe,*recipe_args,'--tx-stop',overlay,'--out',dest,'--check-paths-only'],True)
        if not (r.returncode!=0 and not dest.exists()):
            raise RuntimeError(str('verification failed at: r.returncode!=0 and not dest.exists()'))
        guards.append({'case':'resolved-file-parent','target':str(dest),'exit':r.returncode,'no_creation':not dest.exists()})
        alias=fixture/'output-alias'
        alias.symlink_to(cand,target_is_directory=True)
        dest=alias/'reject-output'
        r=run('recipe-guard-output-parent-symlink',[sys.executable,'-B',recipe,*recipe_args,'--tx-stop',cand,'--out',dest,'--check-paths-only'],True)
        if not (r.returncode!=0 and not dest.exists()):
            raise RuntimeError(str('verification failed at: r.returncode!=0 and not dest.exists()'))
        guards.append({'case':'output-parent-symlink','target':str(dest),'exit':r.returncode,'no_creation':not dest.exists()})
        # Own portable runner rejects output under its script/wrapper directory.
        self_args=[runner/'run.sh','--baseline',base,'--old-source',old,'--candidate',cand,'--recipe',recipe,'--toolchain',tc,'--input-root',ir]
        for name,dest in [('own-script-wrapper-parent',runner/'reject-output'),('baseline-input-child',base/'reject-output'),('own-existing-output',out)]:
            existed=dest.exists()
            r=run('runner-guard-'+name,[*self_args,'--out',dest,'--check-paths-only'],True)
            if not (r.returncode!=0 and (existed or not dest.exists())):
                raise RuntimeError(str('verification failed at: r.returncode!=0 and (existed or not dest.exists())'))
            guards.append({'case':'runner-'+name,'target':str(dest),'exit':r.returncode,'no_creation':existed or not dest.exists()})
        self_fresh=out/'runner-preflight-fresh'
        run('runner-guard-fresh', [*self_args,'--out',self_fresh,'--check-paths-only'])
        if not (not self_fresh.exists()):
            raise RuntimeError(str('verification failed at: not self_fresh.exists()'))
        record['recipe_path_guards']=guards
        record['compiled_dependencies_sha256']=deps
        record['all_objects_sha256']={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*.o'))}
        record['status']='PASS_SUPPORTING_ARM_COMPARISON_ONLY'
    except Exception as exc:
        record['status']='FAIL'; record['error']=repr(exc)
    finally:
        after={str(p):sha(p) for p in sorted(expected)}
        record['inputs_after']=after
        record['inputs_unchanged']=after==record['inputs_before']
        if not record['inputs_unchanged']:
            record['status']='FAIL_INPUT_CHANGED'
        record['finish_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
    print(record['status']+': '+str(out/'receipt.json'))
    if record['status'].startswith('FAIL'):
        print(record.get('error','input changed'),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
