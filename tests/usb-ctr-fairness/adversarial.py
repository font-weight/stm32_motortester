#!/usr/bin/env python3
"""Negative profile and test-sensitivity probes on actual patched function bodies."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
if sys.flags.optimize:
 raise SystemExit('Run without Python optimization: assertion-based evidence checks are required')
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE/'tools'))
import extract

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--repo',type=Path,required=True,help='Same repository root or pinned export used by run.py');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 
 if a.output.is_symlink():p.error('output symlinks are not allowed')
 out=a.output.resolve();repo=a.repo.resolve();evidence=a.evidence.resolve()
 if out==repo or repo in out.parents or out==evidence or evidence in out.parents:p.error('output must be outside repository and existing evidence trees')
 out.mkdir(parents=True,exist_ok=False);(out/'tmp').mkdir();env=dict(os.environ,TMPDIR=str(out/'tmp'))
 evidence=a.evidence.resolve();source=(evidence/'fair/dcd.c').read_text();dep=repo/'firmware/third_party/usb/vendor/tinyusb/src/portable/st/stm32_fsdev';header=(dep/'fsdev_common.h').read_text();common=(dep/'fsdev_common.c').read_text()
 pins=json.loads((HERE/'source-pins.json').read_text())['source_files']
 def source_hashes():return {name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in pins}
 assert source_hashes()==pins, 'repository inputs differ from source pins'
 manifest=json.loads((repo/'firmware/third_party/usb/patches/stream-ctr-fairness.json').read_text())
 assert hashlib.sha256(source.encode()).hexdigest()==manifest['result_sha256'], 'evidence DCD differs from pinned patched result'
 commands=[];results=[]
 def run(argv):
  r=subprocess.run(list(map(str,argv)),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);commands.append({'argv':list(map(str,argv)),'exit_code':r.returncode,'output':r.stdout});(out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');return r
 def flags(build):return ['-std=c11','-O2','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-DEXPECT_FIXED=1','-DEXPECT_RESTRICTED=1','-I'+str(HERE/'src'),'-I'+str(build)]
 builds=out/'profiles';builds.mkdir();extract.generate(source,header,common,builds)
 profiles=[('bridge-off',['-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=1','-DCFG_MT_USB_BRIDGE=0'],'requires the guarded'),('missing-F103',['-DCFG_MT_USB_CTR_FAIRNESS=1'],'requires the guarded'),('wrong-MCU',['-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=1','-DCFG_TUSB_MCU=999'],'requires the guarded'),('value-two',['-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=2'],'must be exactly zero or one'),('value-negative',['-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=-1'],'must be exactly zero or one')]
 for name,defs,message in profiles:
  r=run(['gcc',*flags(builds),*defs,'-fsyntax-only',HERE/'src/translation.c']);assert r.returncode!=0 and message in r.stdout,(name,r.stdout);results.append({'test':name,'rejected':True,'type':'profile'})
 mutations=[
 ('no-software-defer-gate','while (!mt_ctr_deferred && mt_ctr_budget &&','while (mt_ctr_budget &&'),
 ('two-endpoint-quantum','unsigned mt_ctr_budget = 1u;','unsigned mt_ctr_budget = 2u;'),
 ('mask-remains-enabled','FSDEV_REG->CNTR &= (fsdev_bus_t)~U_CNTR_CTRM;','FSDEV_REG->CNTR |= U_CNTR_CTRM;'),
 ('last-quantum-not-deferred','if (mt_ctr_serviced) {','if (mt_ctr_serviced && (ep_read(2) & U_EP_CTR_RX)) {'),
 ('rearm-ignores-lifecycle','FSDEV_REG->ISTR & (U_ISTR_RESET | U_ISTR_PMAOVR | U_ISTR_SUSP)','FSDEV_REG->ISTR & 0u'),
 ('rearm-ignores-power','U_CNTR_FRES | U_CNTR_PDWN | U_CNTR_FSUSP','U_CNTR_FRES | U_CNTR_FSUSP'),
 ('reset-keeps-deferred','FSDEV_REG->DADDR = 0u; // disable USB Function\n#if CFG_MT_USB_CTR_FAIRNESS\n  mt_ctr_deferred = false;','FSDEV_REG->DADDR = 0u; // disable USB Function\n#if CFG_MT_USB_CTR_FAIRNESS\n  mt_ctr_deferred = true;'),
 ('reset-keeps-mask-off','FSDEV_REG->DADDR = 0u; // disable USB Function\n#if CFG_MT_USB_CTR_FAIRNESS\n  mt_ctr_deferred = false;\n  FSDEV_REG->CNTR |= U_CNTR_CTRM;','FSDEV_REG->DADDR = 0u; // disable USB Function\n#if CFG_MT_USB_CTR_FAIRNESS\n  mt_ctr_deferred = false;\n  FSDEV_REG->CNTR &= (fsdev_bus_t)~U_CNTR_CTRM;'),
 ('drop-unserviced-RX','if (mt_ctr_serviced) {\n    mt_ctr_deferred = true;','if (mt_ctr_serviced) {\n    ep_write_clear_ctr(2, TUSB_DIR_OUT);\n    mt_ctr_deferred = true;'),
 ('accept-unsupported-ISO','return false; // ISO/HP service is outside this single-buffer profile.','return true; // ISO/HP service is outside this single-buffer profile.'),
 ('skip-selected-TX','if (ep_reg & U_EP_CTR_TX) {\n      ep_write_clear_ctr(ep_id, TUSB_DIR_IN);','if (false && (ep_reg & U_EP_CTR_TX)) {\n      ep_write_clear_ctr(ep_id, TUSB_DIR_IN);'),
 ]
 for name,before,after in mutations:
  count=source.count(before);assert count in (1,2),(name,count)
  mutant=source.replace(before,after);build=out/name;build.mkdir();(build/'mutant.c').write_text(mutant);extract.generate(mutant,header,common,build)
  r=run(['gcc',*flags(build),'-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=1',HERE/'src/translation.c','-o',build/'test']);assert r.returncode==0,(name,r.stdout)
  r=run([build/'test']);assert r.returncode!=0,(name,'mutation survived');results.append({'test':name,'rejected':True,'type':'behavior mutation','exit_code':r.returncode,'reason':r.stdout.strip()})
 assert source_hashes()==pins, 'repository inputs changed during tests'
 (out/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps({'pass':True,'profile_rejections':len(profiles),'behavior_mutations_killed':len(mutations)},indent=2))
if __name__=='__main__':main()
