#!/usr/bin/env python3
"""Relocatable independent CTR fairness tests; no firmware mutation or flashing."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
if sys.flags.optimize:
 raise SystemExit('Run without Python optimization: assertion-based evidence checks are required')
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'tools'))
import extract

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--toolchain',type=Path);p.add_argument('--repo',type=Path,required=True,help='Repository root or hash-pinned source export root');p.add_argument('--no-sanitize',action='store_true');a=p.parse_args()
 
 if a.output.is_symlink():p.error('output symlinks are not allowed')
 out=a.output.resolve(); inp=a.repo.resolve()
 if out.exists() or out==inp or inp in out.parents or out==HERE/'src' or out==HERE/'tools':p.error('use a new output directory outside inputs')
 out.mkdir(parents=True);(out/'tmp').mkdir(); env=dict(os.environ,TMPDIR=str(out/'tmp'),ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1')
 commands=[]
 def run(argv,expected=0):
  argv=list(map(str,argv)); r=subprocess.run(argv,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  commands.append({'argv':argv,'exit_code':r.returncode,'expected':expected,'output':r.stdout});(out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
  if (expected=='nonzero' and r.returncode==0) or (expected!='nonzero' and r.returncode!=expected):raise RuntimeError(f'{r.returncode}: {argv}\n{r.stdout}')
  return r.stdout
 pins=json.loads((HERE/'source-pins.json').read_text())
 def source_hashes():
  return {name:sha(inp/name) for name in pins['source_files']}
 before=source_hashes()
 assert before==pins['source_files'], 'repository inputs differ from recorded source pins'
 harness_files=[HERE/'run.py',HERE/'adversarial.py',HERE/'source-pins.json',*sorted((HERE/'src').glob('*')),*sorted((HERE/'tools').glob('*.py'))]
 (out/'harness-hashes.json').write_text(json.dumps({str(f.relative_to(HERE)):sha(f) for f in harness_files},indent=2)+'\n')
 dep=inp/'firmware/third_party/usb';pat=dep/'patches';vendor=dep/'vendor';dcd=vendor/'tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c';common=dcd.parent/'fsdev_common.c';header=dcd.parent/'fsdev_common.h'
 for name in ('reset','base','fair','integrity'):(out/name).mkdir()
 reset=out/'reset/dcd.c';base=out/'base/dcd.c';fair=out/'fair/dcd.c'
 run(['python3',pat/'apply_f103_reset_patch.py',dcd,reset]);run(['python3',pat/'apply_usb_bridge_patch.py','fsdev',reset,base]);run(['python3',pat/'apply_stream_ctr_patch.py',base,fair])
 manifest=json.loads((pat/'stream-ctr-fairness.json').read_text());assert sha(base)==manifest['source_sha256'];assert sha(fair)==manifest['result_sha256']
 integrity=[]
 def rejection(name,source,output,helper=pat/'apply_stream_ctr_patch.py'):
  before_paths={str(f):sha(f) for f in [source,output] if f.is_file()}
  run(['python3',helper,source,output],expected='nonzero')
  assert all(sha(Path(f))==h for f,h in before_paths.items())
  integrity.append(name)
 rejection('existing output preserved',base,fair)
 wrong=out/'integrity/wrong.c';wrong.write_bytes(base.read_bytes()+b'\n')
 rejection('wrong source pin',wrong,out/'integrity/missing.c');assert not (out/'integrity/missing.c').exists()
 rejection('source itself preserved',base,base)
 rejection('new sibling of source blocked',base,out/'base/forbidden.c');assert not (out/'base/forbidden.c').exists()
 (out/'base/sub').mkdir();rejection('source subtree blocked',base,out/'base/sub/forbidden.c')
 dangling=out/'integrity/dangling.c';dangling.symlink_to(out/'integrity/no-target.c');rejection('dangling output symlink blocked',base,dangling)
 existing=out/'integrity/existing.c';existing.write_text('sentinel');link=out/'integrity/link.c';link.symlink_to(existing);rejection('output symlink target preserved',base,link)
 hard=out/'integrity/hard.c';os.link(existing,hard);rejection('existing hardlink preserved',base,hard)
 clone=out/'integrity/clone';shutil.copytree(pat,clone)
 pp=clone/manifest['patch'];pp.write_bytes(pp.read_bytes()+b'\n');rejection('patch pin mismatch',base,out/'integrity/no-patch.c',clone/'apply_stream_ctr_patch.py');assert not (out/'integrity/no-patch.c').exists()
 pp.write_bytes((pat/manifest['patch']).read_bytes()); badmanifest=dict(manifest,result_sha256='0'*64);(clone/'stream-ctr-fairness.json').write_text(json.dumps(badmanifest))
 rejection('result pin mismatch',base,out/'integrity/no-result.c',clone/'apply_stream_ctr_patch.py');assert not (out/'integrity/no-result.c').exists()
 (out/'patch-integrity.json').write_text(json.dumps({'passed':integrity},indent=2)+'\n')
 native=[]
 for name,source,defines in [('base-off',base,['-DCFG_MT_USB_CTR_FAIRNESS=0']),('fair-default',fair,[]),('fair-off',fair,['-DCFG_MT_USB_CTR_FAIRNESS=0']),('fair-on',fair,['-DCFG_MT_USB_CTR_FAIRNESS=1'])]:
  build=out/name;build.mkdir(); extract.generate(source.read_text(),header.read_text(),common.read_text(),build)
  flags=['-DEXPECT_RESTRICTED='+str(int('ISO/HP service is outside' in source.read_text())),'-std=c11','-O2','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-DSTM32F103xB','-DEXPECT_FIXED=1','-I'+str(HERE/'src'),'-I'+str(build),*defines]
  for sanitizer in ([False] if a.no_sanitize else [False,True]):
   tag='sanitized' if sanitizer else 'normal';binary=build/tag
   extra=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer','-fno-pie','-no-pie'] if sanitizer else []
   run(['gcc',*flags,*extra,HERE/'src/translation.c','-o',binary]);result=run([binary]);print(result,end='');native.append({'profile':name,'sanitized':sanitizer,'result':result.strip()})
 (out/'native-results.json').write_text(json.dumps(native,indent=2)+'\n')
 arm=[]
 if a.toolchain:
  tc=a.toolchain.resolve(); cc=tc/'bin/arm-none-eabi-gcc'; assert cc.exists()
  (out/'compiler-version.txt').write_text(run([cc,'--version']))
  flags=['-std=gnu11','-Os','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-ffunction-sections','-fdata-sections','-fno-common','-fno-short-enums','-fstack-usage','-Wall','-Wextra','-Werror','-DSTM32F103xB','-isystem',str(tc/'include/newlib')]
  flags+=['-I'+str(inp/'firmware/boot'),'-I'+str(vendor/'tinyusb/src'),'-I'+str(dcd.parent),'-I'+str(vendor/'cmsis-device-f1/Include'),'-I'+str(vendor/'cmsis-core/CMSIS/Core/Include')]
  for name,source,defines in [('base',base,[]),('fair-default',fair,[]),('fair-off',fair,['-DCFG_MT_USB_CTR_FAIRNESS=0']),('fair-on',fair,['-DCFG_MT_USB_CTR_FAIRNESS=1'])]:
   obj=out/(name+'.o');run([cc,*flags,*defines,'-c',source,'-o',obj]);size=run([tc/'bin/arm-none-eabi-size',obj]);nm=run([tc/'bin/arm-none-eabi-nm','-S',obj]);dis=run([tc/'bin/arm-none-eabi-objdump','-dr',obj]);(out/(name+'-nm.txt')).write_text(nm);(out/(name+'-disassembly.txt')).write_text(dis)
   arm.append({'profile':name,'sha256':sha(obj),'size':size})
  assert (out/'base.o').read_bytes()==(out/'fair-default.o').read_bytes()==(out/'fair-off.o').read_bytes(),'default/off ARM object differs from prepatch baseline'
  assert 'mt_usb_fsdev_ctr_rearm' in (out/'fair-on-nm.txt').read_text()
  assert 'mt_usb_fsdev_ctr_rearm' not in (out/'fair-default-nm.txt').read_text()
  (out/'arm-results.json').write_text(json.dumps(arm,indent=2)+'\n')
 after=source_hashes();assert before==after
 summary={'pass':True,'source_sha256':manifest['source_sha256'],'result_sha256':manifest['result_sha256'],'inputs_unchanged':True,'native_runs':len(native),'patch_integrity_cases':len(integrity),'arm_compiles':len(arm),'no_hardware_or_WCET_claim':True}
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(out/'input-hashes.json').write_text(json.dumps(before,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
