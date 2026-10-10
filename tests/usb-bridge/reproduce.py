#!/usr/bin/env python3
"""Reproduce USB005 from preserved USB003/USB004/USB005 patches in fresh outputs.

This packaging wrapper preserves the frozen native runner and test sources.
ARM verification differs only in omitting unused exploratory-source snapshots.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--toolchain', required=True, type=Path,
                        help='existing root with bin/arm-none-eabi-gcc and include/newlib')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    out = args.output.resolve()
    tc = args.toolchain.resolve()
    for protected in [root, tc]:
        if out == protected or protected in out.parents:
            parser.error('output must be outside checkout and toolchain trees')
    if out.exists() or args.output.is_symlink():
        parser.error('output must not exist')
    if not (tc / 'bin/arm-none-eabi-gcc').is_file():
        parser.error('ARM compiler missing from toolchain/bin')
    manifest_path = here / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    inherited = json.loads((here / 'inherited-inputs.json').read_text())
    for row in manifest['files'] + inherited['files']:
        path = root / row['path']
        if path.resolve() != path or not path.is_file():
            parser.error('input missing or symlink: ' + row['path'])
        if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            parser.error('input hash mismatch: ' + row['path'])
    def inventory():
        return {str(p): sha(p) for tree in [root/'firmware', here,
                                          root/'tests/usb-cdc/supporting']
                for p in sorted(tree.rglob('*')) if p.is_file()}
    before = inventory()
    out.mkdir(parents=True, exist_ok=False)
    (out/'inputs-before.json').write_text(json.dumps(before, indent=2)+'\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    commands = []
    result = {'result':'FAIL', 'scope':'native guard model and ARM relocatable objects only',
              'inputs_unchanged':False}
    def run(label, argv, cwd=None):
        argv = [str(x) for x in argv]
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
        commands.append({'label':label, 'argv':argv, 'cwd':str(cwd) if cwd else None,
                         'exit_code':r.returncode, 'stdout':r.stdout, 'stderr':r.stderr})
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        (out/(label+'.log')).write_text(r.stdout+r.stderr)
        if r.returncode:
            raise RuntimeError(label+' failed; its command and output are retained')
        print(label+': PASS', flush=True)
    try:
        dep = root/'firmware/third_party/usb'
        patches = dep/'patches'
        work = out/'inputs'
        bridge, epoch, adapter, setup, reset = [work/name for name in
                                              ['bridge','epoch','adapter','setup','reset']]
        for destination in [bridge/'firmware/transport', epoch/'firmware/transport',
                            setup/'build', reset/'build', bridge/'build',
                            bridge/'tests/usb-bridge']:
            destination.mkdir(parents=True)
        for name in ['mt_usb_bridge.c','mt_usb_bridge.h','mt_usb_cdc.c','mt_usb_cdc.h']:
            shutil.copyfile(root/'firmware/transport'/name, bridge/'firmware/transport'/name)
        for name in ['mt_usb_epoch.c','mt_usb_epoch.h']:
            shutil.copyfile(root/'firmware/transport'/name, epoch/'firmware/transport'/name)
        shutil.copytree(root/'firmware', adapter/'firmware',
                        ignore=shutil.ignore_patterns('third_party'))
        for name in ['mt_usb_cdc.c','mt_usb_cdc.h']:
            shutil.copyfile(here/'inputs/pr61-adapter'/name, adapter/'firmware/transport'/name)
        shutil.copyfile(here/'README.md', bridge/'tests/usb-bridge/README.md')
        run('dependency-hashes', ['sha256sum','-c','SHA256SUMS.sources'], dep)
        run('usb003-apply', [sys.executable,patches/'apply_setup_patch.py',
                           dep/'vendor/tinyusb/src/device/usbd.c',setup/'build/usbd.c'])
        run('usb004-apply', [sys.executable,patches/'apply_f103_reset_patch.py',
                           dep/'vendor/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c',
                           reset/'build/dcd_stm32_fsdev.c'])
        for kind, source, target in [('core', setup/'build/usbd.c', bridge/'build/usbd.c'),
                                    ('fsdev',reset/'build/dcd_stm32_fsdev.c',bridge/'build/dcd_stm32_fsdev.c')]:
            run('usb005-'+kind+'-apply', [sys.executable,patches/'apply_usb_bridge_patch.py',kind,source,target])
        env.update(USB_BRIDGE_ROOT=str(bridge), USB_EPOCH_ROOT=str(epoch),
                   USB_DEPENDENCIES=str(dep), USB_CDC_TESTS=str(root/'tests/usb-cdc/supporting'),
                   USB_ADAPTER_ROOT=str(adapter))
        native = {}
        for label, options, count in [('shims',[],47),('core',['--integration'],40),('adapter',['--adapter'],45)]:
            for sanitized in [False, True]:
                name = label+('-sanitize' if sanitized else '')
                run(name,[sys.executable,here/'supporting/run.py','--out',out/'native'/name,
                          *options,*(['--sanitize'] if sanitized else [])])
                receipt = json.loads((out/'native'/name/'receipt.json').read_text())
                assert receipt['result']=='PASS' and receipt['inputs_unchanged']
                assert len(receipt['results'])==count
                native[name]={'cases':count,'inputs_unchanged':True}
        for name in ['core','core-sanitize']:
            run('critic-extra-'+name,[sys.executable,here/'critic-a/run_extra.py',
                '--base-run',out/'native'/name,'--out',out/'critic-extra'/name])
            extra=json.loads((out/'critic-extra'/name/'receipt.json').read_text())
            assert extra['result']=='PASS' and not extra['changed_inputs']
        run('critic-patch-tool',[sys.executable,here/'critic-a/test_patch_tool.py',
            '--patches',patches,'--core',setup/'build/usbd.c',
            '--fsdev',reset/'build/dcd_stm32_fsdev.c','--out',out/'critic-patch-tool'])
        patch_tests=json.loads((out/'critic-patch-tool/receipt.json').read_text())
        assert patch_tests['result']=='PASS' and patch_tests['invocations']==28
        run('native-tooling',[sys.executable,here/'supporting/test_runner.py','--out',out/'native-tooling'])
        tooling = json.loads((out/'native-tooling/tooling-regressions.json').read_text())
        assert len(tooling)==12 and all(x['pass'] for x in tooling)
        run('arm-path-isolation',[sys.executable,here/'arm/test_path_isolation.py',
              '--runner',here/'arm/verify_arm.py','--output',out/'arm-path-isolation'])
        path_checks=json.loads((out/'arm-path-isolation/summary.json').read_text())
        assert path_checks['case_count']==58 and path_checks['passed']==58
        assert path_checks['all_fixture_trees_unchanged']
        run('wrapper-path-isolation',[sys.executable,here/'test_reproduce_paths.py',
              '--output',out/'wrapper-path-isolation','--toolchain',tc])
        run('arm',[sys.executable,here/'arm/verify_arm.py','--output',out/'arm',
                   '--bridge',bridge,'--epoch',epoch,'--adapter',adapter,'--setup',setup,
                   '--reset',reset,'--dependencies',dep,'--toolchain',tc])
        run('arm-audit',[sys.executable,here/'arm/audit_objects.py',out/'arm',
                         '--compare-run',here/'arm/evidence-run-03'])
        arm = json.loads((out/'arm/summary.json').read_text())
        audit = json.loads((out/'arm/audit.json').read_text())
        assert not arm['original_inputs_changed_during_run']
        assert len(arm['objects'])==10 and all(audit['all_object_hashes_equal_to_compare_run'].values())
        result.update(result='PASS scoped reproduction',native=native,runner_tooling_cases=12,
                      arm_objects=10,all_arm_object_hashes_match_run03=True,
                      arm_path_isolation_cases=58,wrapper_path_isolation_cases=9,
                      critic_supplemental_cases_per_variant=10,critic_patch_tool_invocations=28,
                      generated_sources={name:sha(bridge/'build'/name) for name in ['usbd.c','dcd_stm32_fsdev.c']})
    except BaseException as error:
        result['error']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        after=inventory()
        (out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
        result['inputs_unchanged']=before==after
        if before!=after:
            result['result']='FAIL'
            result['preservation_error']='checkout inputs changed during execution'
        (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
        if before!=after:
            raise RuntimeError('checkout inputs changed during execution')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
