#!/usr/bin/env python3
"""Minimal native A / frozen Python B early-RELEASE comparison; no installs."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

def main():
    if sys.flags.optimize:
        raise ValueError('Run without -O: experiment assertions must execute')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,help='new absolute output directory')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent;out=Path(args.out)
    if not out.is_absolute() or out.exists() or not out.parent.is_dir():
        raise ValueError('Output must be a new absolute directory with an existing parent')
    manifest=json.loads((root/'REPRO_MANIFEST.json').read_text())
    for name,sha in manifest.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=sha:
            raise ValueError('Input hash mismatch: '+name)
    out.mkdir();(out/'results').mkdir()
    source=root/'snapshots/a-native'
    m=json.loads((source/'tests/wait-release/package-manifest.json').read_text())
    cc=shutil.which('gcc',path='/usr/bin:/bin')
    if cc is None:raise ValueError('Native GCC unavailable')
    argv=[cc,'-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-O2',
          '-I'+str(source),str(root/'device_bridge.c')]+[str(source/x) for x in m['production_c'][:7]]+['-o',str(out/'device_bridge')]
    p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    (out/'compile.log').write_bytes(p.stdout);p.check_returncode()
    spec=importlib.util.spec_from_file_location('compatibility_audit',root/'run_audit.py')
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    audit.ROOT=out
    a=audit.native_premature_release();b=audit.emulator_premature_release()
    native_log=json.loads((out/'results/native-premature-release-partial-end.json').read_text())
    native_bytes=b''.join(bytes.fromhex(row['wire']) for row in native_log)
    (out/'a-accepted.bin').write_bytes(native_bytes)
    (out/'b-capture-accepted.bin').write_bytes(bytes.fromhex(b['accepted_prefix_hex']+b['accepted_remainder_hex']))
    result={'comparison':'DEMONSTRATED_DIVERGENCE','scope':'Adversarial early RELEASE; no severity or acceptance verdict',
        'a_commit':'66fce97ba86de459e6d597deb32130e030f34594',
        'b_commit':'c44bae42b19ec8208c2ebee4a3e662ce1b7ff240',
        'timing_ms':{'first_accepted_end_byte':100,'release_received_before_remaining_end_bytes':1099,'remaining_end_accepted':1099},
        'accepted_end_prefix_bytes':1,'a':a,'b':b,'compile_argv':argv,
        'a_full_accepted_stream_sha256':hashlib.sha256(native_bytes).hexdigest(),
        'input_hashes':manifest,'python':sys.version,'physical_or_windows_validation':False}
    (out/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
    print('DEMONSTRATED_DIVERGENCE: after 1 accepted END byte, matching RELEASE at t=1099ms:')
    print('A: ERROR(BUSY), WAIT_RELEASE, anchor=1099; timeout at6099. B: RELEASE_ACK, WAITING, no hold timer.')
    print('No severity, shared-contract acceptance, hardware, serial-port, or Windows claim.')

if __name__=='__main__':main()
