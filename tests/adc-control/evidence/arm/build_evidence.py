#!/usr/bin/env python3
"""IO-001 frozen target objects/ABI/stack evidence; never a firmware image."""
import hashlib,json,os,pathlib,re,shlex,shutil,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent
LIVE=pathlib.Path('/workspace/shared/adc-control-draft')
SUPPLEMENT=pathlib.Path('/workspace/shared/control-publication/candidate')
TC=pathlib.Path('/workspace/shared/arm-resource-evidence/toolchain/root/usr')
FLAGS=['-std=c11','-Os','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-ffunction-sections','-fdata-sections','-fno-common','-fno-short-enums','-fstack-usage','-Wall','-Wextra','-Werror','-Wpedantic','-Wconversion','-Wsign-conversion','-isystem',str(TC/'include/newlib')]
ENV=dict(os.environ,PATH=str(TC/'bin')+os.pathsep+os.environ['PATH'],LC_ALL='C',SOURCE_DATE_EPOCH='0')
def digest(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'git_blob_sha':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()}
def freeze():
 src=ROOT/'sources';src.mkdir()
 origins=[(p,p.relative_to(LIVE),'current IO-001 draft') for p in sorted((LIVE/'firmware').rglob('*')) if p.suffix in ('.c','.h')]
 for p in [SUPPLEMENT/'firmware/protocol/mt_stream.c',SUPPLEMENT/'firmware/protocol/mt_stream.h']:
  assert not (LIVE/p.relative_to(SUPPLEMENT)).exists()
  origins.append((p,p.relative_to(SUPPLEMENT),'RX stream supplement from control-publication/candidate; absent from ADC draft'))
 records=[]
 for origin,rel,provenance in origins:
  before=digest(origin);dest=src/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(origin,dest)
  assert digest(origin)==before==digest(dest)
  records.append({'path':str(rel),'origin':str(origin),'provenance':provenance,**before})
 (ROOT/'source-manifest.json').write_text(json.dumps(records,indent=2)+'\n')
 check_live(records, 'freeze-check.json')
 return records
def check_live(records,outname='live-source-check.json'):
 changed=[]
 for r in records:
  current=digest(pathlib.Path(r['origin']))
  if current['sha256']!=r['sha256']:changed.append({'path':r['path'],'frozen':r['sha256'],'live':current['sha256']})
 result={'checked_manifest_sha256':digest(ROOT/'source-manifest.json')['sha256'],'changed':changed,'sources_unchanged':not changed}
 (ROOT/outname).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));return result
PROBE='''/* Target ABI symbols only. NEVER link this probe into firmware. */
#include <stddef.h>
#include "mt_adc_control.h"
#include "mt_stream.h"
/* Session, TX, link and acquisition are all owned by control. No duplicates.
 * block in mt_adc_control_service is automatic storage, counted by .su. */
typedef struct {
    mt_control control;
    mt_adc_dma_t driver;
    mt_adc_control binding;
    mt_stream rx_stream;
    mt_wire_message rx_message;
} resource_owner_model;
_Static_assert(sizeof(((mt_control *)0)->session)==sizeof(mt_session), "embedded session");
_Static_assert(sizeof(((mt_control *)0)->tx)==sizeof(mt_tx), "embedded TX");
_Static_assert(sizeof(((mt_control *)0)->link)==sizeof(mt_capture_link), "embedded link");
_Static_assert(sizeof(((mt_capture_link *)0)->acquisition)==sizeof(mt_acq_t), "embedded acquisition");
_Static_assert(sizeof(((mt_adc_dma_t *)0)->dma_samples)==512, "one DMA ring");
_Static_assert(sizeof(((mt_adc_dma_t *)0)->snapshot)==256, "one snapshot");
_Static_assert(sizeof(mt_adc_dma_state_t)==4, "explicit full-width enums");
_Static_assert(sizeof(mt_adc_dma_fault_t)==4, "explicit full-width enums");
'''
def build(name,records):
 out=ROOT/'runs'/name;out.mkdir(parents=True);shutil.copyfile(__file__,out/'build_evidence.py')
 src=ROOT/'sources'
 for r in records:assert digest(src/r['path'])['sha256']==r['sha256']
 log=(out/'commands.log').open('w')
 def run(cmd):
  log.write('$ '+shlex.join(map(str,cmd))+'\n');log.flush()
  p=subprocess.run(list(map(str,cmd)),cwd=ROOT,env=ENV,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  log.write(p.stdout+'\nexit_status='+str(p.returncode)+'\n');log.flush()
  if p.returncode:raise RuntimeError(shlex.join(map(str,cmd)))
  return p.stdout
 def save(name,text):(out/name).write_text(text);return text
 compiler=TC/'bin/arm-none-eabi-gcc'
 report={'scope':'Cortex-M3 relocatable object code, ABI and compiler frames; NOT complete firmware','flags':FLAGS,'compiler':run([compiler,'--version']),'linker':run([TC/'bin/arm-none-eabi-ld','--version']),'source_manifest_sha256':digest(ROOT/'source-manifest.json')['sha256'],'object_sizes':[],'stack_usage':[]}
 includes=['-I'+str(src/'firmware'/s) for s in ['app','protocol','transport','acquisition','platform']]
 objects=out/'objects';objects.mkdir();units=sorted(src.rglob('*.c'))
 assert len({u.stem for u in units})==len(units)
 for u in units:
  obj=objects/(u.stem+'.o');run([compiler,*FLAGS,*includes,'-c',u,'-o',obj])
  sections=save('objects/'+u.stem+'.sections.txt',run([TC/'bin/arm-none-eabi-size','-A',obj]))
  save('objects/'+u.stem+'.elf.txt',run([TC/'bin/arm-none-eabi-readelf','-h','-A','-SW',obj]))
  unresolved=save('objects/'+u.stem+'.undefined.txt',run([TC/'bin/arm-none-eabi-nm','-u',obj]))
  save('objects/'+u.stem+'.symbols.txt',run([TC/'bin/arm-none-eabi-nm','-S',obj]))
  save('objects/'+u.stem+'.disassembly.txt',run([TC/'bin/arm-none-eabi-objdump','-dr',obj]))
  totals={'text_rodata':0,'data':0,'bss':0}
  for line in sections.splitlines():
   m=re.match(r'(\S+)\s+(\d+)\s+\d+$',line)
   if not m:continue
   n,size=m.group(1),int(m.group(2))
   if n.startswith(('.text','.rodata')):totals['text_rodata']+=size
   elif n.startswith('.data'):totals['data']+=size
   elif n.startswith('.bss'):totals['bss']+=size
  report['object_sizes'].append({'unit':str(u.relative_to(src)),**totals,**digest(obj),'undefined_symbols':unresolved})
 types=['mt_control','mt_control_io','mt_control_status','mt_adc_control','mt_acq_t','mt_tx','mt_session','mt_stream','mt_wire_message','mt_command','mt_command_support','mt_capture_link','mt_adc_dma_t','mt_adc_dma_block_t','mt_tx_event','resource_owner_model']
 fields=['control','driver','binding','rx_stream','rx_message']
 probe=out/'abi_probe.c';probe.write_text(PROBE+''.join(f'unsigned char sizeof_{t}[sizeof({t})];\nunsigned char alignof_{t}[_Alignof({t})];\n' for t in types)+''.join(f'unsigned char offset_plus_one_{f}[offsetof(resource_owner_model, {f})+1];\n' for f in fields))
 obj=objects/'abi_probe.o';run([compiler,*FLAGS,*includes,'-c',probe,'-o',obj]);nm=save('abi-symbol-sizes.txt',run([TC/'bin/arm-none-eabi-nm','-S','--size-sort','--radix=d',obj]))
 report.update(abi_sizes={},abi_alignments={},owner_field_offsets={})
 for line in nm.splitlines():
  m=re.match(r'\d+\s+(\d+)\s+\w\s+(sizeof|alignof|offset_plus_one)_(\w+)',line)
  if m:
   key={'sizeof':'abi_sizes','alignof':'abi_alignments','offset_plus_one':'owner_field_offsets'}[m.group(2)]
   report[key][m.group(3)]=int(m.group(1))-(m.group(2)=='offset_plus_one')
 for su in sorted(objects.glob('*.su')):
  for line in su.read_text().splitlines():
   if line:
    loc,size,kind=line.rsplit('\t',2);report['stack_usage'].append({'location':loc,'function':loc.rsplit(':',1)[1],'unit':su.stem,'bytes':int(size),'kind':kind})
 report['stack_usage'].sort(key=lambda x:-x['bytes'])
 combined=out/'modules-relocatable.o';run([TC/'bin/arm-none-eabi-ld','-r','-Map='+str(out/'modules-relocatable.map'),'-o',combined,*[objects/(u.stem+'.o') for u in units]])
 report['combined_hash']=digest(combined)
 report['aggregate_size']=save('aggregate-size.txt',run([TC/'bin/arm-none-eabi-size',combined]))
 report['aggregate_sections']=save('aggregate-sections.txt',run([TC/'bin/arm-none-eabi-size','-A',combined]))
 report['undefined_symbols']=save('undefined-symbols.txt',run([TC/'bin/arm-none-eabi-nm','-u',combined]))
 save('combined.elf.txt',run([TC/'bin/arm-none-eabi-readelf','-h','-A','-SW',combined]))
 report['complete_firmware_budget_proven']=False
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'run':name,'aggregate':report['aggregate_size'],'abi':report['abi_sizes'],'frames':report['stack_usage'][:15],'combined':report['combined_hash'],'undefined':report['undefined_symbols']},indent=2));return report
if __name__=='__main__':
 if len(sys.argv)!=2:raise SystemExit('Usage: build_evidence.py freeze|check|run-name')
 if sys.argv[1]=='freeze':freeze()
 elif sys.argv[1]=='check':check_live(json.loads((ROOT/'source-manifest.json').read_text()))
 else:build(sys.argv[1],json.loads((ROOT/'source-manifest.json').read_text()))
