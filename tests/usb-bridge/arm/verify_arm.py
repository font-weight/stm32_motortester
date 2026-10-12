#!/usr/bin/env python3
"""USB005 reproducible ARM relocatable-object evidence; no target execution/link."""
import argparse, datetime, difflib, hashlib, json, os, re, shlex, shutil, subprocess
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--bridge', type=Path, default=Path('/workspace/shared/usb-bridge-draft'))
p.add_argument('--epoch', type=Path, default=Path('/workspace/shared/usb-epoch-draft'))
p.add_argument('--adapter', type=Path, default=Path('/workspace/shared/usb-adapter-draft'))
p.add_argument('--setup', type=Path, default=Path('/workspace/shared/usb-setup-draft'))
p.add_argument('--reset', type=Path, default=Path('/workspace/shared/usb-fsdev-reset-draft'))
p.add_argument('--dependencies', type=Path, default=Path('/workspace/shared/usb-dependencies'))
p.add_argument('--toolchain', type=Path, default=Path('/workspace/shared/arm-resource-evidence/toolchain/root/usr'))
p.add_argument('--check-paths-only', action='store_true',
               help='validate input/output paths without creating output or running tools')
a=p.parse_args()

def validate_paths(args):
    """Read-only preflight; reject canonical overlap before creating any output.

    Protect entire supplied input roots as well as canonical copied subtrees:
    a firmware/vendor subtree may itself be a symlink outside its supplied root.
    Existing and dangling output symlinks are also rejected. Inputs must remain
    quiescent while the subsequent snapshot is taken; this is not a filesystem
    security boundary against an adversarial concurrent symlink replacement.
    """
    output_arg=args.output.absolute()
    if output_arg.exists() or output_arg.is_symlink():
        p.error('output already exists (including symlinks): '+str(output_arg))
    try:
        output=output_arg.resolve(strict=False)
        if output.exists() or output.is_symlink():
            p.error('resolved output already exists: '+str(output))
        protected=[]
        for name in ('bridge','epoch','adapter','setup','reset','dependencies','toolchain'):
            source=getattr(args,name).resolve(strict=True)
            if not source.is_dir():
                p.error('input root is not a directory: '+str(source))
            setattr(args,name,source)
            protected.append(source)
        copied_trees=(args.dependencies/'vendor',args.dependencies/'evidence/config',
                      args.adapter/'firmware',args.epoch/'firmware',args.bridge/'firmware')
        required_dirs=(*copied_trees,args.toolchain/'include/newlib',
                       args.toolchain/'lib/gcc/arm-none-eabi')
        for source in required_dirs:
            resolved=source.resolve(strict=True)
            if not resolved.is_dir():
                p.error('required input directory is missing: '+str(source))
            protected.append(resolved)
        required_files=[args.dependencies/'SHA256SUMS.sources',
            args.bridge/'tests/usb-bridge/README.md',
            args.setup/'build/usbd.c',args.reset/'build/dcd_stm32_fsdev.c',
            args.epoch/'firmware/transport/mt_usb_epoch.h',
            args.adapter/'firmware/transport/mt_usb_cdc.c',
            args.adapter/'firmware/transport/mt_usb_cdc.h',
            args.dependencies/'evidence/config/tusb_config.h']
        required_files += [args.bridge/'build'/name for name in
                           ('usbd.c','dcd_stm32_fsdev.c')]
        required_files += [args.bridge/'firmware/transport'/name for name in
                           ('mt_usb_bridge.c','mt_usb_bridge.h','mt_usb_cdc.c','mt_usb_cdc.h')]
        required_files += [args.toolchain/'bin'/('arm-none-eabi-'+name) for name in
                           ('gcc','as','size','nm','objdump','readelf')]
        for source in required_files:
            resolved=source.resolve(strict=True)
            if not resolved.is_file():
                p.error('required input file is missing: '+str(source))
            # Singleton input files can themselves be symlinks outside root.
            protected.append(resolved.parent)
        for source in protected:
            if output == source or source in output.parents or output in source.parents:
                p.error('output must be disjoint from every input tree: '+
                        str(output)+' overlaps '+str(source))
    except (OSError, RuntimeError) as exc:
        p.error('invalid or missing input/output path: '+str(exc))
    return output

out=validate_paths(a)
if a.check_paths_only:
    print('Path preflight passed; no output was created: '+str(out))
    raise SystemExit(0)
out.mkdir(parents=True, exist_ok=False)
sha=lambda x:hashlib.sha256(Path(x).read_bytes()).hexdigest()
manifest={}; commands=[]
def cp(src,dst):
    src=src.resolve(); dst.parent.mkdir(parents=True,exist_ok=True)
    digest=sha(src); shutil.copyfile(src,dst)
    assert sha(dst)==digest, f'Input changed while copying: {src}'
    manifest[str(src)]={'sha256':digest, 'snapshot':str(dst.relative_to(out))}
def tree(src,dst):
    for path in sorted(src.rglob('*')):
        if path.is_file(): cp(path,dst/path.relative_to(src))
env=dict(os.environ,PATH=str(a.toolchain/'bin')+os.pathsep+os.environ['PATH'])
def run(argv,name,cwd=None):
    argv=list(map(str,argv)); r=subprocess.run(argv,env=env,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    entry={'argv':argv,'cwd':str(cwd) if cwd else None,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
    commands.append(entry)
    (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    (out/(name+'.stdout')).write_text(r.stdout); (out/(name+'.stderr')).write_text(r.stderr)
    with (out/'commands.sh').open('a') as f:f.write((('cd '+shlex.quote(str(cwd))+' && ') if cwd else '')+shlex.join(argv)+'\n')
    if r.returncode: raise RuntimeError(f'Failed {r.returncode}: {shlex.join(argv)}; retained logs under {out}')
    return r.stdout,r.stderr
try:
    shutil.copyfile(__file__,out/'verify_arm.py')
    run(['sha256sum','-c','SHA256SUMS.sources'],'dependency-integrity',a.dependencies)
    tree(a.dependencies/'vendor',out/'snapshot/dependencies/vendor')
    tree(a.dependencies/'evidence/config',out/'snapshot/dependencies/evidence/config')
    tree(a.adapter/'firmware',out/'snapshot/adapter/firmware')
    tree(a.epoch/'firmware',out/'snapshot/epoch/firmware')
    tree(a.bridge/'firmware',out/'snapshot/bridge/firmware')
    for filename in ('usbd.c','dcd_stm32_fsdev.c'):
        cp(a.bridge/'build'/filename,out/'snapshot/bridge/build'/filename)
    cp(a.bridge/'tests/usb-bridge/README.md',out/'snapshot/bridge/tests/usb-bridge/README.md')
    cp(a.setup/'build/usbd.c',out/'snapshot/setup/build/usbd.c')
    cp(a.reset/'build/dcd_stm32_fsdev.c',out/'snapshot/reset/build/dcd_stm32_fsdev.c')
    (out/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    tc=a.toolchain.resolve(); cc=tc/'bin/arm-none-eabi-gcc'; dep=out/'snapshot/dependencies'
    run([cc,'--version'],'compiler-version')
    flags=['-std=gnu11','-Os','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-ffunction-sections','-fdata-sections','-fno-common','-fno-short-enums','-fstack-usage','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-DSTM32F103xB','-isystem',str(tc/'include/newlib')]
    inc=[out/'snapshot/bridge/firmware/transport',out/'snapshot/epoch/firmware/transport']
    inc += [out/'snapshot/adapter/firmware'/s for s in ('transport','app','protocol','acquisition')]
    inc += [dep/s for s in ('evidence/config','vendor/tinyusb/src','vendor/cmsis-device-f1/Include','vendor/cmsis-core/CMSIS/Core/Include','vendor/tinyusb/src/portable/st/stm32_fsdev')]
    flags += ['-I'+str(x) for x in inc]
    jobs=[('bridge-on','bridge/firmware/transport/mt_usb_bridge.c',1),('core-on','bridge/build/usbd.c',1),('dcd-on','bridge/build/dcd_stm32_fsdev.c',1),('adapter-on','bridge/firmware/transport/mt_usb_cdc.c',1),('core-off','bridge/build/usbd.c',0),('dcd-off','bridge/build/dcd_stm32_fsdev.c',0),('adapter-off','bridge/firmware/transport/mt_usb_cdc.c',0),('core-base','setup/build/usbd.c',0),('dcd-base','reset/build/dcd_stm32_fsdev.c',0),('adapter-base','adapter/firmware/transport/mt_usb_cdc.c',0)]
    objects={}; header_manifest={}
    for name,source,cfg in jobs:
        src=out/'snapshot'/source; obj=out/(name+'.o'); cflags=flags+['-DCFG_MT_USB_BRIDGE='+str(cfg)]
        _,warnings=run([cc,*cflags,'-MD','-MF',out/(name+'.d'),'-c',src,'-o',obj],name+'-compile')
        deps=(out/(name+'.d')).read_text().replace('\\\n',' ').split(':',1)[1].split()
        for header in deps: header_manifest[header]=sha(header)
        run([cc,*cflags,'-E','-P',src,'-o',out/(name+'.i')],name+'-preprocess')
        run([cc,*cflags,'-E','-dM',src,'-o',out/(name+'-macros.txt')],name+'-macros')
        for tool,options in [('size',['-A']),('nm',['-a','-S','--size-sort']),('objdump',['-dr']),('readelf',['-h','-A'])]:
            run([tc/('bin/arm-none-eabi-'+tool),*options,obj],name+'-'+tool)
        run([tc/'bin/arm-none-eabi-nm','-u',obj],name+'-undefined')
        sizes,_=run([tc/'bin/arm-none-eabi-size',obj],name+'-size-berkeley')
        text,data,bss,*_=sizes.splitlines()[-1].split()
        su=out/(name+'.su'); entries=[]
        if su.exists():
            for line in su.read_text().splitlines():
                location,n,qual=line.split('\t'); entries.append({'location':location,'bytes':int(n),'qualifier':qual})
        objects[name]={'source':str(src.relative_to(out)),'source_sha256':sha(src),'object_sha256':sha(obj),'preprocessed_sha256':sha(out/(name+'.i')),'text':int(text),'data':int(data),'bss':int(bss),'warning_count':len(re.findall(r'warning:',warnings)),'warnings':warnings,'maximum_local_stack_bytes':max((x['bytes'] for x in entries),default=0),'stack_entries':entries}
    (out/'compilation-inputs-manifest.json').write_text(json.dumps(header_manifest,indent=2)+'\n')
    comparisons={}
    for name in ('core','dcd','adapter'):
        base=out/(name+'-base.i'); off=out/(name+'-off.i')
        comparisons[name]={'preprocessed_identical':base.read_bytes()==off.read_bytes(),'object_identical':(out/(name+'-base.o')).read_bytes()==(out/(name+'-off.o')).read_bytes(),'off_minus_base':{key:objects[name+'-off'][key]-objects[name+'-base'][key] for key in ('text','data','bss')}}
        (out/(name+'-optout.diff')).write_text(''.join(difflib.unified_diff(base.read_text().splitlines(True),off.read_text().splitlines(True),fromfile=base.name,tofile=off.name)))
    tool_files=[tc/'bin'/('arm-none-eabi-'+x) for x in ('gcc','as','size','nm','objdump','readelf')]
    for path in sorted((tc/'lib/gcc/arm-none-eabi').rglob('cc1')):tool_files.append(path)
    tool_manifest={str(path):sha(path) for path in tool_files}
    (out/'toolchain-manifest.json').write_text(json.dumps(tool_manifest,indent=2)+'\n')
    changed={src:{'before':d['sha256'],'after':sha(src)} for src,d in manifest.items() if sha(src)!=d['sha256']}
    result={'date_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Relocatable ARM Cortex-M3 objects only; no target execution, PRIMASK/vector proof, final link, complete firmware resource use, or acceptance grade.','objects':objects,'optout_comparisons':comparisons,'original_inputs_changed_during_run':changed,'runner_sha256':sha(__file__)}
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='objects'},indent=2))
    for name,entry in objects.items(): print(name, {k:entry[k] for k in ('text','data','bss','warning_count','maximum_local_stack_bytes','source_sha256')})
except Exception as e:
    (out/'failure.txt').write_text(repr(e)+'\n'); raise
