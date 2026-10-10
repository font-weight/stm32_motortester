#!/usr/bin/env python3
"""Reproduce the unchanged Critic-A tests in a fresh, external directory.

Run beside the two original test_*.py files. --checkout must contain USB001 and
USB003 paths. Only four historical absolute path literals are relocated in an
isolated copy of the separation test. No target toolchain is invoked here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PINS = {
 'test_patch_tool.py': '55f379b38bf3490c98eebbe1dd27c3afee95e96ce5ca6c6a51f190af19aa3704',
 'test_output_separation.py': 'cfe46a9aa7797cbc0d6bcabcefbe777bd28f8f3eb2487959796f88a838947f08',
}
INPUTS = {
 'firmware/third_party/usb/patches/0001-setup-rejection-accounting.patch': '51492cd96f4f9f8dd0699fe915f8c157c866f04cdc129e4c7c734e21f49b0f37',
 'firmware/third_party/usb/patches/apply_setup_patch.py': '28b1672b247a71d149b47d631659df9e09c9d761ea413bbedbf1c571658bb189',
 'firmware/third_party/usb/patches/setup-accounting.json': '7fc3ac47a74ef0e85e1aadccf4207ea11fad238a690f43790fc66aaf44767a19',
 'firmware/third_party/usb/vendor/tinyusb/src/device/usbd.c': '38de54351d81878e6543f2b022d0c39f45d28c8f0888491796398dec45e727be',
 'tests/usb-setup/check_patch.py': '06b9b9237295786ddbdc152e2040a42e57e9fc6632685dbdfe00698e2483d57b',
}

def sha(p):
 return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--checkout',type=Path,required=True)
 parser.add_argument('--out',type=Path,required=True)
 args=parser.parse_args(); root=args.checkout.resolve(); out=args.out.resolve()
 for input_root in (root,HERE):
  if out==input_root or input_root in out.parents:
   parser.error('--out must be outside checkout and supplemental source directory')
 for name,pin in PINS.items():
  if sha(HERE/name)!=pin: parser.error('supplemental source hash mismatch: '+name)
 for name,pin in INPUTS.items():
  if sha(root/name)!=pin: parser.error('checkout source hash mismatch: '+name)
 if not (root/'tests/usb-cdc/supporting').is_dir(): parser.error('missing USB001 supporting directory')
 out.mkdir(parents=True,exist_ok=False);(out/'inputs').mkdir()
 for name in PINS: shutil.copyfile(HERE/name,out/name)
 for name in INPUTS:
  source=root/name
  dest=out/'snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
  if '/patches/' in name: shutil.copyfile(source,out/'inputs'/source.name)
 shutil.copyfile(root/'firmware/third_party/usb/vendor/tinyusb/src/device/usbd.c',out/'inputs/upstream-usbd.c')
 source=(out/'test_output_separation.py').read_text()
 replacements={
  "'/workspace/shared/usb-dependencies'":repr(str(root/'firmware/third_party/usb')),
  "'/workspace/shared/usb-cdc-tests'":repr(str(root/'tests/usb-cdc/supporting')),
  "'/workspace/shared/usb-adapter-draft'":repr(str(root)),
  "'/workspace/shared/arm-resource-evidence/toolchain/root/usr'":repr(str(out/'unused-toolchain')),
 }
 for old,new in replacements.items():
  if source.count(old)!=1: raise RuntimeError('historical path occurrence mismatch')
  source=source.replace(old,new)
 (out/'test_output_separation.py').write_text(source)
 commands=[]
 for name in PINS:
  argv=[sys.executable,str(out/name)]
  result=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  (out/(name+'.log')).write_text(result.stdout)
  commands.append({'argv':argv,'returncode':result.returncode,'output':result.stdout})
  (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
  if result.returncode: raise RuntimeError('supplemental test failed: '+name)
 for name,pin in PINS.items(): assert sha(HERE/name)==pin
 for name,pin in INPUTS.items(): assert sha(root/name)==pin
 receipt={'result':'PASS','original_tests':PINS,'checkout_inputs':INPUTS,
 'relocated_separation_test_sha256':sha(out/'test_output_separation.py'),
 'replacements':replacements,'patch_invocations':11,'output_separation_cases':12,
 'input_bytes_unchanged':True,'scope':'patch tooling only; no target execution or IRQ proof'}
 (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print('PASS: 11 patch-tool invocations and 12 output-isolation cases; original inputs unchanged')

if __name__=='__main__': main()
