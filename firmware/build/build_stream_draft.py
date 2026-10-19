#!/usr/bin/env python3
"""Build a separate unqualified STREAM diagnostic ELF. Never export or flash."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tarfile

TOOL_SHA = '22f0c2204b8b381a26af3b910851b43b04f34549b6677cdb5b76f304c4e37b43'
REMOVE = {'firmware/boot/mt_device_main.c', 'firmware/acquisition/acquisition.c',
          *('firmware/app/'+x+'.c' for x in ('mt_capture_link','mt_adc_control','mt_control','mt_control_rx','mt_runtime')),
          *('firmware/protocol/'+x+'.c' for x in ('mt_stream','mt_session','mt_command')),
          'firmware/transport/mt_tx.c', 'firmware/transport/mt_usb_cdc.c'}
ADD = ['firmware/boot/mt_stream_main.c', 'firmware/app/mt_live_runtime.c',
       'firmware/app/mt_live_adc.c', 'firmware/platform/mt_live_clock.c',
       'firmware/protocol/mt_live_device.c', 'firmware/acquisition/mt_sample_queue.c']


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value, directory=False):
    text = os.fspath(value)
    require(not any(c in text for c in '\n\r:#$\\"\''), 'unsupported path character')
    path = Path(text)
    require(path.is_absolute() and os.path.normpath(text) == text, 'absolute normalized path required')
    require(not any(p.is_symlink() for p in (*path.parents, path)), 'symlink path forbidden')
    require(path.resolve(strict=True) == path, 'canonical path required')
    require(path.is_dir() if directory else path.is_file(), 'wrong path type')
    return path


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,
            'invoke python3 -I -B without optimization')
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source-commit', 'toolchain', 'board', 'timing', 'out'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--stack-reserve', type=int, required=True)
    parser.add_argument('--diagnostics', action='store_true', help='opt-in foreground DWT telemetry; still unqualified')
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{40}', args.source_commit) is not None, 'full source SHA required')
    require(0 < args.stack_reserve <= 20480 and args.stack_reserve % 8 == 0, 'aligned explicit stack reserve required')
    script = canonical(__file__)
    root = canonical(script.parents[2], True)
    tc = canonical(args.toolchain, True)
    board, timing = canonical(args.board), canonical(args.timing)
    python = canonical(Path(sys.executable).resolve())
    patch = canonical('/usr/bin/patch')
    git = canonical('/usr/bin/git')
    out = Path(args.out)
    require(out.is_absolute() and os.path.normpath(args.out) == args.out and
            not out.exists() and not out.is_symlink(), 'fresh absolute output required')
    canonical(out.parent, True)
    require(not any(c in args.out for c in '\n\r:#$\\"\''), 'unsupported output path character')
    for protected in (root, tc, board, timing, python.parent, patch.parent, Path(sys.base_prefix).resolve()):
        require(not out.is_relative_to(protected) and not protected.is_relative_to(out), 'output overlaps inputs')
    env = {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC','PYTHONDONTWRITEBYTECODE':'1'}
    head = subprocess.check_output([git,'-C',root,'rev-parse','HEAD'],env=env,text=True).strip()
    require(head == args.source_commit, 'source HEAD differs from declared frozen commit')
    require(not subprocess.check_output([git,'-C',root,'status','--porcelain'],env=env), 'source checkout must be clean')
    inputs = {str(p):sha(p) for p in (script, python, patch, git, board, timing)}
    declaration = load(script.with_name('stream_timing.py')).parse(timing.read_bytes())
    require(declaration['source_commit'] == head, 'timing declaration source mismatch')
    tool_manifest = canonical(script.with_name('toolchain.json'))
    require(sha(tool_manifest) == TOOL_SHA, 'toolchain manifest mismatch')
    for relative, expected in json.loads(tool_manifest.read_text())['files'].items():
        path = canonical(tc/relative)
        require(sha(path) == expected, 'toolchain pin mismatch: '+relative)
        inputs[str(path)] = expected
    out.mkdir()
    (out/'tmp').mkdir()
    env['TMPDIR'] = str(out/'tmp')
    result = {'status':'RUNNING', 'source_commit':head, 'inputs_before':inputs,
              'commands':[], 'dependencies':{}, 'generated':{}, 'objects':[],
              'transaction_bound_qualified':False, 'stack_bound_qualified':False,
              'runtime_profile_accepted':False, 'hardware_verified':False, 'flash_approved':False,
              'stack_reserve_bytes':args.stack_reserve,
              'instrumentation_profile':'foreground-dwt-v1' if args.diagnostics else 'none',
              'open_gates':['full callback/ABI/stack proof','target TX and IRQ WCET',
                            'USB traffic/fault scheduling','physical ADC/USB stream','independent profile review']}
    def save():
        (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    def run(label, argv):
        argv = list(map(str,argv))
        row = {'label':label,'argv':argv,'cwd':str(out)}
        result['commands'].append(row)
        save()
        p = subprocess.Popen(argv,cwd=out,env=env,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,start_new_session=True)
        try:
            stdout, stderr = p.communicate(timeout=180)
        except BaseException as exc:
            try:
                os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass  # Child exited between timeout/interrupt and group kill.
            stdout, stderr = p.communicate()
            row['exception'] = type(exc).__name__
            (out/(label+'.stdout')).write_bytes(stdout)
            (out/(label+'.stderr')).write_bytes(stderr)
            row['exit'] = p.returncode
            generated(out/(label+'.stdout')); generated(out/(label+'.stderr'))
            save()
            raise
        (out/(label+'.stdout')).write_bytes(stdout)
        (out/(label+'.stderr')).write_bytes(stderr)
        row['exit'] = p.returncode
        generated(out/(label+'.stdout')); generated(out/(label+'.stderr'))
        save()
        require(p.returncode == 0, label+' failed; retained stderr')
        return stdout.decode('utf-8')
    def generated(path):
        result['generated'][str(path)] = sha(path)
    try:
        for path, name in ((board,'board-input.json'),(timing,'timing-input.json')):
            saved = out/name
            saved.write_bytes(path.read_bytes())
            require(sha(saved) == inputs[str(path)], 'input drift before archive')
            generated(saved)
        archive = out/'source.tar'
        run('archive',[git,'-C',root,'archive','--format=tar','--output='+str(archive),head,'firmware','tests/boot-link'])
        generated(archive)
        source = out/'source'
        source.mkdir()
        with tarfile.open(archive) as tar:
            require(all(x.isfile() or x.isdir() for x in tar.getmembers()), 'nonregular archive entry')
            tar.extractall(source,filter='data')
        for path in source.rglob('*'):
            if path.is_file():
                inputs[str(canonical(path))] = sha(path)
        # Executed parser and runner must be exactly the archived source bytes.
        for name in ('build_stream_draft.py','stream_timing.py'):
            require(sha(script.with_name(name)) == sha(source/'firmware/build'/name), 'live/archived helper mismatch')
        tools = {n:canonical(tc/'bin'/('arm-none-eabi-'+n)) for n in ('gcc','as','ld','nm','readelf','objdump','size')}
        for name in tools:
            run('version-'+name,[tools[name],'--version'])
        for name in ('cc1','as'):
            selected = Path(os.path.normpath(run('selected-'+name,[tools['gcc'],'-print-prog-name='+name]).strip()))
            require(str(canonical(selected)) in inputs, 'unbound compiler subprocess')
        build = source/'firmware/build'
        config = load(build/'board_config.py')
        data = config.parse_board(board.read_bytes())
        result['board_input_kind'] = data['input_kind']
        result['declared_transaction_bound_ms'] = declaration['declared_transaction_bound_ms']
        board_c, timing_c = out/'board.c', out/'timing.c'
        board_c.write_text(config.generate(data))
        timing_c.write_text(load(build/'stream_timing.py').generate(declaration))
        generated(board_c); generated(timing_c)
        helpers = source/'firmware/third_party/usb/patches'
        vendor = source/'firmware/third_party/usb/vendor'
        for folder in ('stage1','patched','ctr','objects'):
            (out/folder).mkdir()
        py = [python,'-I','-B']
        run('patch-setup',[*py,helpers/'apply_setup_patch.py',vendor/'tinyusb/src/device/usbd.c',out/'stage1/usbd.c'])
        run('patch-reset',[*py,helpers/'apply_f103_reset_patch.py',vendor/'tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c',out/'stage1/dcd_stm32_fsdev.c'])
        for kind,name in (('core','usbd.c'),('fsdev','dcd_stm32_fsdev.c')):
            run('patch-'+kind,[*py,helpers/'apply_usb_bridge_patch.py',kind,out/'stage1'/name,out/'patched'/name])
        run('patch-ctr',[*py,helpers/'apply_stream_ctr_patch.py',out/'patched/dcd_stm32_fsdev.c',out/'ctr/dcd_stm32_fsdev.c'])
        patched = {'@patched/usbd.c':out/'patched/usbd.c', '@patched/dcd_stm32_fsdev.c':out/'ctr/dcd_stm32_fsdev.c'}
        for path in patched.values():
            generated(path)
        manifest = json.loads((build/'inputs.json').read_text())
        require(len(manifest['components']) == 32, 'historical flag template changed')
        flags = next(x['flags'] for x in manifest['components'] if x['source']=='firmware/app/mt_runtime.c')
        entries = [x for x in manifest['components'] if x['source'] not in REMOVE]
        require(len(entries) == 20, 'retained composition mismatch')
        entries += [{'source':x,'flags':flags} for x in ADD]
        if args.diagnostics:
            entries.append({'source':'firmware/platform/mt_stream_diag.c','flags':flags})
        includes = ['-I'+str(source/'firmware'/p) for p in ('boot','platform','app','protocol','transport','acquisition')]
        for path in [tc/'include/newlib',*(vendor/p for p in ('tinyusb/src','cmsis-device-f1/Include','cmsis-core/CMSIS/Core/Include','tinyusb/src/portable/st/stm32_fsdev'))]:
            includes += ['-isystem',str(path)]
        objects = []
        def compile_c(name, src, opts):
            obj = out/'objects'/(name+'.o'); dep = obj.with_suffix('.d')
            run('compile-'+name,[tools['gcc'],*opts,'-DCFG_MT_USB_CTR_FAIRNESS=1',
                '-DCFG_MT_STREAM_DIAGNOSTICS='+str(int(args.diagnostics)),*includes,'-MD','-MF',dep,'-c',src,'-o',obj])
            for item in shlex.split(dep.read_text().replace('\\\n',' ').split(':',1)[1]):
                path = canonical(os.path.normpath(item))
                expected = inputs.get(str(path),result['generated'].get(str(path)))
                require(expected is not None and sha(path)==expected, 'unbound compiler dependency: '+str(path))
                require(path.name!='tusb_config.h' or path==source/'firmware/boot/tusb_config.h','wrong USB config')
                result['dependencies'][str(path)] = expected
            generated(dep)
            if obj.with_suffix('.su').is_file():
                generated(obj.with_suffix('.su'))
            objects.append(obj); generated(obj)
            run('disassembly-'+name,[tools['objdump'],'-dr',obj])
            return obj
        for index,row in enumerate(entries):
            compile_c(str(index)+'-'+Path(row['source']).stem,patched.get(row['source'],source/row['source']),row['flags'])
        reset = out/'objects/reset.o'
        reset_flags = ['-B'+str(tc/'bin/arm-none-eabi-'),'-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-fno-common','-fno-short-enums','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-fno-stack-protector','-fno-lto','-fno-pic','-fno-pie','-ffunction-sections','-fdata-sections','-g0','-Os','-Wall','-Wextra','-Werror']
        run('compile-reset',[tools['gcc'],*reset_flags,'-c',source/'firmware/boot/mt_reset_stm32f103c8.S','-o',reset])
        require(sha(reset)==manifest['reset_object_sha256'],'reset bytes changed')
        objects.append(reset); generated(reset)
        data_flags = ['-std=c11','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-Os','-ffreestanding','-fno-common','-fno-short-enums','-Wa,--noexecstack','-ffunction-sections','-fdata-sections','-Wall','-Wextra','-Werror','-Wpedantic','-Wconversion']
        board_obj = compile_c('board',board_c,data_flags)
        result['board_object_check'] = load(build/'board_object.py').validate(board_obj.read_bytes(),board.read_bytes())
        compile_c('timing',timing_c,data_flags)
        require(len(objects)==(30 if args.diagnostics else 29),'explicit object count mismatch')
        checker = source/'tests/boot-link/tools/check_boot_image.py'
        vectors = source/'tests/boot-link/vector_map.json'
        result['input_audit'] = json.loads(run('input-audit',[*py,checker,'inputs','--readelf',tools['readelf'],'--reset-object',reset,'--vector-map',vectors,*objects]))
        elf = out/('DIAGNOSTIC_INSTRUMENTED_NEVER_FLASH.elf' if args.diagnostics else 'DIAGNOSTIC_UNQUALIFIED_NEVER_FLASH.elf')
        run('link',[tools['ld'],'--fatal-warnings','--no-gc-sections','--orphan-handling=error','--defsym=__boot_stack_bytes='+str(args.stack_reserve),'-T',source/'firmware/boot/stm32f103c8.ld','-Map='+str(out/'image.map'),'-o',elf,*objects])
        result['elf_audit'] = json.loads(run('elf-audit',[*py,checker,'elf','--elf',elf,'--vector-map',vectors,'--objdump',tools['objdump'],'--readelf',tools['readelf'],'--stack',args.stack_reserve]))
        require(not run('undefined',[tools['nm'],'-u',elf]).strip(),'unresolved ELF symbols')
        result['size'] = run('size',[tools['size'],elf])
        result['objects'] = [{'path':str(p),'sha256':sha(p)} for p in objects]
        generated(elf); generated(out/'image.map')
        result['status'] = 'DIAGNOSTIC_LINK_ONLY_NOT_QUALIFIED'
    except BaseException as exc:
        result.update(status='FAILED',error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        drift = [p for p,h in {**inputs,**result['generated']}.items() if not Path(p).is_file() or sha(Path(p))!=h]
        result['input_generated_drift'] = drift
        if drift:
            result['status'] = 'FAILED_INPUT_DRIFT'
        save()
        require(not drift,'input/generated drift')
    print(json.dumps({'status':result['status'],'receipt':str(out/'receipt.json')}))


if __name__ == '__main__':
    main()
