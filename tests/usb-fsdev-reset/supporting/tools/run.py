#!/usr/bin/env python3
"""Run pinned original/candidate native model, preserving each fresh evidence directory."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from extract import function, generate, self_test, sha, validate_candidate

ROOT=Path(__file__).resolve().parents[1]
PINNED={
 'dcd_stm32_fsdev.c':'62b73c5fab0cbda70df6bd1bc1c9a7df0c8a59ea02c38c884b24b55e44ddfc9f',
 'fsdev_common.h':'83bd242c95387e0ba2158d9d4525ffd3bc3304533ea8d3ba3b78186dc57da3cb',
 'fsdev_common.c':'6ffeaa1f64fcdeaf0e7db1c932696bcc962cdd546032599ebccc41fe2b7e990f'}

def validate_output_path(path, root=ROOT):
    resolved=path.resolve()
    for name in ['inputs','src','tools']:
        protected=(root/name).resolve()
        if resolved==protected or protected in resolved.parents:
            raise ValueError(f'refusing evidence output inside protected source tree: {protected}')
    return resolved

def snapshot_sources(candidate, root=ROOT):
    inputs={str(path.relative_to(root)):sha(path.read_bytes())
        for folder in ['src','tools','inputs'] for path in sorted((root/folder).rglob('*'))
        if path.is_file() and '__pycache__' not in str(path)}
    return {'inputs_sha256':inputs,'candidate_sha256':sha(candidate.read_bytes())}

def require_unchanged(before,after):
    if before!=after:
        raise RuntimeError('recorded harness/input/candidate sources changed during execution')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--cc',default='gcc')
    p.add_argument('--no-mutations',action='store_true')
    args=p.parse_args()
    out=validate_output_path(args.out)  # Validate before any mkdir.
    out.mkdir(parents=True,exist_ok=True)
    if any(out.iterdir()): raise SystemExit('refusing to overwrite nonempty evidence directory')
    before=snapshot_sources(args.candidate)
    (out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
    try:
        originals={n:(ROOT/'inputs'/n).read_text() for n in PINNED}
        for n,text in originals.items():
            if sha(text)!=PINNED[n]: raise SystemExit(f'pin mismatch: {n}')
        original=originals['dcd_stm32_fsdev.c'];candidate=args.candidate.read_text()
        validate_candidate(original,candidate)
        (out/'candidate-input.c').write_text(candidate)
        records=[];matrix=[]
        def command(argv,expected=0,env=None):
            run=subprocess.run([str(x) for x in argv],capture_output=True,text=True,env=env,timeout=60)
            record={'argv':[str(x) for x in argv],'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
            if env is not None: record['instrumentation_env']={k:env[k] for k in ['ASAN_OPTIONS','UBSAN_OPTIONS']}
            records.append(record)
            (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
            print('$ '+' '.join(record['argv']),flush=True);print(run.stdout+run.stderr,end='',flush=True)
            if expected is not None and run.returncode!=expected: raise RuntimeError(f'expected exit {expected}, got {run.returncode}')
            return run
        command([args.cc,'--version'])
        extraction_checks=self_test(original,candidate)
        (out/'extraction-self-tests.json').write_text(json.dumps(extraction_checks,indent=2)+'\n')
        variants=[('original',original,False,False),('candidate',candidate,True,True),('guard_disabled',candidate,False,False)]
        if not args.no_mutations:
            raw,a,b=function(candidate,'handle_bus_reset')
            def mutation(name,old,new):
                if raw.count(old)!=1: raise RuntimeError(f'mutation {name}: expected one anchor')
                altered=raw.replace(old,new)
                variants.append((name,candidate[:a]+altered+candidate[b:],True,True))
            mutation('mut_missing_sweep','ep_write(i, 0, false);','/* test mutation: no sweep */')
            mutation('mut_rx_only','ep_write(i, 0, false);','ep_write(i, U_EP_CTR_TX, false);')
            mutation('mut_tx_only','ep_write(i, 0, false);','ep_write(i, U_EP_CTR_RX, false);')
            mutation('mut_logical_count','i < FSDEV_EP_COUNT','i < CFG_TUD_ENDPPOINT_MAX')
            mutation('mut_skip_slot7','i < FSDEV_EP_COUNT','i < FSDEV_EP_COUNT - 1')
            mutation('mut_missing_ownership_clear','tu_memclr(xfer_status, sizeof(xfer_status));','/* test mutation: no ownership clear */')
            mutation('mut_partial_ownership_clear','sizeof(xfer_status));','sizeof(xfer_status) - sizeof(xfer_status[0]));')
            mutation('mut_function_enabled','FSDEV_REG->DADDR = 0u;','FSDEV_REG->DADDR = U_DADDR_EF;')
            mutation('mut_no_function_enable','FSDEV_REG->DADDR = U_DADDR_EF;','FSDEV_REG->DADDR = 0u;')
            needle='  edpt0_open(rhport);'
            mutation('mut_clobber_ep0',needle,needle+'\n  tu_memclr(xfer_status, sizeof(xfer_status));')
            late=raw.replace('tu_memclr(xfer_status, sizeof(xfer_status));','/* test mutation: clear too late */')
            late=late.replace(needle,needle+'\n  tu_memclr(xfer_status, sizeof(xfer_status));')
            variants.append(('mut_late_clear',candidate[:a]+late+candidate[b:],True,True))
        for name,source,fixed,guard in variants:
            dest=out/name;dest.mkdir()
            manifest=generate(source,originals['fsdev_common.h'],originals['fsdev_common.c'],dest)
            (dest/'extraction-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            mutated=name.startswith('mut_')
            for endpoints in ([3] if mutated else [3,8]):
                for sanitizer in ([False] if mutated else [False,True]):
                    tag=f'ep{endpoints}'+('-asan-ubsan' if sanitizer else '-strict')
                    binary=dest/tag
                    cmd=[args.cc,'-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-Wshadow','-O1','-g','-fno-omit-frame-pointer','-fno-lto','-no-pie',f'-DMODEL_ENDPOINTS={endpoints}',f'-DEXPECT_FIXED={int(fixed)}']
                    if guard: cmd.append('-DSTM32F103xB=1')
                    if sanitizer: cmd+=['-fsanitize=address,undefined','-fno-sanitize-recover=all']
                    cmd+=['-I',str(dest),'-I',str(ROOT/'src'),str(ROOT/'src/translation.c'),'-o',str(binary)]
                    command(cmd)
                    env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0:halt_on_error=1';env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
                    run=command([binary],70 if mutated else 0,env)
                    matrix.append({'variant':name,'logical_endpoints':endpoints,'physical_slots':8,'sanitizers':sanitizer,'expect_fixed':fixed,'STM32F103xB':guard,'returncode':run.returncode,'expected_returncode':70 if mutated else 0,'output':run.stdout+run.stderr,'binary_sha256':sha(binary.read_bytes())})
                    (out/'matrix.json').write_text(json.dumps(matrix,indent=2)+'\n')
    finally:
        after=snapshot_sources(args.candidate)
        (out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
        require_unchanged(before,after)
    inputs=before['inputs_sha256']
    result={'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.platform(),'python':sys.version,'pinned_commit':'dae3f9a366bfcddbf9dcf1b48d7500286a849539','candidate_path':str(args.candidate.resolve()),'candidate_sha256':sha(candidate),'inputs_sha256':inputs,'result':'PASS supporting native extracted-function model only','runs':len(matrix),'mutation_rejections':sum(x['variant'].startswith('mut_') for x in matrix),'extraction_self_tests':extraction_checks,'inputs_unchanged':before==after}
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
