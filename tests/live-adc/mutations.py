#!/usr/bin/env python3
"""Behavioral mutation controls; never edits the supplied production tree."""
import argparse, hashlib, json, pathlib, shutil, subprocess, sys
HERE=pathlib.Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=pathlib.Path)
    p.add_argument('--output',required=True,type=pathlib.Path)
    args=p.parse_args(); root=args.output.resolve(); source=args.source.resolve()
    if args.output.exists() or args.output.is_symlink(): p.error('Output exists; overwrite forbidden')
    if source in root.parents or source==root: p.error('Output must be external to source')
    manifest=json.loads((HERE/'source-manifest.json').read_text())
    data={n:(source/n).read_bytes() for n in manifest['sha256']}
    for n,expected in manifest['sha256'].items():
        if hashlib.sha256(data[n]).hexdigest()!=expected: p.error('Source mismatch: '+n)
    root.mkdir(); records=[]
    target='firmware/app/mt_live_adc.c'
    mutations=[
      ('fabricate-known-end','out->hardware_off = b->stop_scratch.hardware_off ? 1u : 0u;','out->hardware_off = b->stop_scratch.hardware_off ? 1u : 0u; out->produced_known=1u; out->produced_end=b->stop_scratch.dma_end;','tails'),
      ('drop-last-tail-sample','i<b->stop_scratch.count','i+1u<b->stop_scratch.count','tails'),
      ('narrow-tail-index','b->stop_scratch.first_sample_index +','(uint32_t)b->stop_scratch.first_sample_index +','high_indices'),
      ('skip-generation','b->device->generation != generation ||','', 'stale_generation'),
      ('allow-untimed-guard','!guard->leave || !guard->allowed || !guard->timed_allowed ||','!guard->leave || !guard->allowed ||','arguments'),
      ('ignore-time-health','if (mt_timebase_update(b->clock)) return 1;','if (b->clock) return 1;','postread_clock'),
      ('rewrite-first-transfer-reason','default: return 2u;','default: return 3u;','transfer_faults'),
      ('fabricate-hardware-off','out->hardware_off = b->stop_scratch.hardware_off ? 1u : 0u;','out->hardware_off = 1u;','cleanup_failures'),
      ('shift-offered-index','b->device, block.first_sample_index,','b->device, block.first_sample_index+128u,','tails'),
      ('hide-offer-rejection','block.samples, (uint16_t)block.count) ? 1 : -1;','block.samples, (uint16_t)block.count) ? 1 : 1;','queue_full'),
    ]
    for name,old,new,case in mutations:
        folder=root/name; folder.mkdir(); text=data[target].decode()
        if text.count(old)!=1: raise RuntimeError('Mutation anchor not unique: '+name)
        for n,contents in data.items():
            path=folder/n; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(text.replace(old,new).encode() if n==target else contents)
        binary=folder/'test'
        cmd=['cc','-std=c11','-Wall','-Wextra','-Werror','-pedantic','-O2']
        for sub in ['app','platform','protocol','acquisition']: cmd+=['-I',str(folder/'firmware'/sub)]
        cmd+=[str(HERE/'test_live_adc.c')]+[str(folder/n) for n in sorted(data) if n.endswith('.c')]+['-o',str(binary)]
        build=subprocess.run(cmd,capture_output=True,timeout=120)
        (folder/'compile.stdout').write_bytes(build.stdout); (folder/'compile.stderr').write_bytes(build.stderr)
        if build.returncode: raise RuntimeError('Mutation did not compile: '+name)
        run=subprocess.run([str(binary),case],capture_output=True,timeout=120)
        (folder/'run.stdout').write_bytes(run.stdout); (folder/'run.stderr').write_bytes(run.stderr)
        if run.returncode!=1 or b'FAIL '+case.encode() not in run.stderr: raise RuntimeError('Mutant not killed by assertion: '+name)
        records.append({'name':name,'case':case,'returncode':run.returncode,'compile':cmd,'assertion':run.stderr.decode().strip()})
        print('KILLED',name,flush=True)
    (root/'results.json').write_text(json.dumps({'source_pin':manifest,'harness_sha256':hashlib.sha256((HERE/'test_live_adc.c').read_bytes()).hexdigest(),'mutations':records},indent=2)+'\n')
    print('PASS',len(records),'behavioral mutation controls')
if __name__=='__main__': main()
