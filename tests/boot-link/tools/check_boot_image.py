#!/usr/bin/env python3
"""Required BOOT001 input/ELF audit stages; stdout JSON, no filesystem writes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode=True
import importlib.util
# Resolve exactly this distribution's sibling, without CWD or PYTHONPATH lookup.
helper_path=Path(__file__).resolve().parent/'run_boot_link_tests.py'
if helper_path.is_symlink() or not helper_path.is_file():
    raise SystemExit('ERROR: invalid sibling helper backing path')
spec=importlib.util.spec_from_file_location('_boot_link_core',helper_path)
core=importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
for name in ('ELF','VECTOR_SHA','audit_input_metadata','check_layout','check_reset',
             'check_vectors','check_vector_relocations','digest','require','strict_path','check_attributes'):
    globals()[name]=getattr(core,name)



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='stage',required=True)
    inputs=sub.add_parser('inputs',help='Run BEFORE ld on every explicit input object; archives/LTO unsupported.')
    inputs.add_argument('objects',nargs='+')
    inputs.add_argument('--readelf',required=True)
    inputs.add_argument('--reset-object',help='Optional exact reset object for full symbolic vector relocation check')
    inputs.add_argument('--vector-map',help='Required with --reset-object')
    final=sub.add_parser('elf',help='Run AFTER supported no-GC link; cannot recover discarded input evidence.')
    final.add_argument('--elf',required=True)
    final.add_argument('--vector-map',required=True)
    final.add_argument('--objdump',required=True)
    final.add_argument('--readelf',required=True)
    final.add_argument('--stack',type=int,required=True)
    args=parser.parse_args()
    readelf=strict_path(args.readelf,executable=True)
    def attributes(path):
        command=[str(readelf),'-A',str(path)]
        p=subprocess.run(command,env={'PATH':'/usr/bin:/bin','LC_ALL':'C','LANG':'C'},text=True,capture_output=True)
        require(p.returncode==0,'readelf failed: '+p.stderr)
        return check_attributes(p.stdout)
    if args.stage=='inputs':
        paths=[strict_path(x) for x in args.objects]
        reset=strict_path(args.reset_object) if args.reset_object else None
        require(bool(args.reset_object)==bool(args.vector_map),'reset-object and vector-map must be paired')
        vectors=strict_path(args.vector_map) if args.vector_map else None
        if vectors:require(digest(vectors)==VECTOR_SHA,'vector map SHA mismatch')
        require(not reset or reset in paths,'reset-object must appear among all input objects')
        result={'stage':'pre_link_input_metadata','objects':[audit_input_metadata(ELF(p)) for p in paths]}
        result['attributes']=[attributes(p) for p in paths]
        if reset:result['vector_relocations']=check_vector_relocations(ELF(reset),json.loads(vectors.read_text())['rows'][:59])
    else:
        elf=strict_path(args.elf)
        vectors=strict_path(args.vector_map)
        objdump=strict_path(args.objdump,executable=True)
        require(digest(vectors)==VECTOR_SHA,'vector map SHA mismatch')
        require(0 < args.stack <= 20480 and args.stack%8==0,'invalid stack parameter')
        rows=json.loads(vectors.read_text())['rows'][:59]
        parsed=ELF(elf)
        layout=check_layout(parsed,args.stack)
        words=check_vectors(parsed,rows)
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C','LANG':'C'}
        command=[str(objdump),'-d','-r',str(elf)]
        p=subprocess.run(command,env=env,text=True,capture_output=True)
        require(p.returncode==0,'objdump failed: '+p.stderr)
        reset=check_reset(parsed,p.stdout)
        result={'stage':'post_link_elf','elf_sha256':digest(elf),'objdump_sha256':digest(objdump),'command':command,'layout':layout,'attributes':attributes(elf),'vectors':words,'reset':reset}
    result['verdict']='CHECKS_PASS'
    result['limits']='Only listed input metadata and static ELF/disassembly checks; no target execution, device closure, physical safety or product stack guarantee.'
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:main()
    except (AssertionError,FileNotFoundError,ValueError,KeyError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        sys.exit(2)
