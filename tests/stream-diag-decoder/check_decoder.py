#!/usr/bin/env python3
"""Independent software-only contract tests. No production writes/devices/network."""
import argparse, hashlib, json, os, platform, re, struct, subprocess, sys
from pathlib import Path

P = argparse.ArgumentParser()
P.add_argument('--decoder', type=Path, required=True)
P.add_argument('--output', type=Path, required=True)
P.add_argument('--header', type=Path, required=True)
P.add_argument('--cc', default='gcc')
a = P.parse_args()
D = a.decoder.resolve(); H = a.header.resolve(); O = a.output.resolve()
O.mkdir(parents=True, exist_ok=False)
(O/'fixtures').mkdir(); (O/'cli').mkdir()
source = D.read_bytes()
(O/'decoder_snapshot.py').write_bytes(source)
# This list is reviewer-authored from the ABI header, never imported from decoder.
F = '''magic version bytes firmware_build phase flags generation step_count step_body_max_cycles step_gap_max_cycles tx_count tx_max_cycles poll_count driver_observed_poll_gap_us poll_gap_max_cycles copy_count copy_max_us queue_high_water first_runtime_result first_local_error first_acquisition_reason first_adc_fault first_epoch final_runtime_result final_core_state final_epoch final_adc_state final_adc_owned final_hardware_off start_cycle last_cycle freeze_cycle nonce_low nonce_high'''.split()
M = 2**32-1; BUILD = 0xC19A372B; GEN = 0x87654321; NONCE = 0xFEDCBA9876543210
BASE = dict.fromkeys(F,0)
BASE.update(magic=0x4D544447,version=1,bytes=136,firmware_build=BUILD,phase=3,generation=GEN,step_count=2,step_body_max_cycles=21,step_gap_max_cycles=67,tx_count=1,tx_max_cycles=13,poll_count=2,driver_observed_poll_gap_us=30,poll_gap_max_cycles=61,copy_count=1,copy_max_us=7,queue_high_water=16,final_runtime_result=0,final_core_state=7,final_epoch=3,final_hardware_off=1,start_cycle=M-20,last_cycle=12,freeze_cycle=12,nonce_low=NONCE&M,nonce_high=NONCE>>32)
ns={'__name__':'independent_review_target'}
exec(compile(source,str(D),'exec'),ns)
decode=ns['decode']
checks=[]; commands=[]
def check(name, truth, actual=None, expected=None):
    checks.append(dict(name=name,pass_=bool(truth),actual=actual,expected=expected))
def blob(fields): return struct.pack('<34I',*(fields[x] for x in F))
def fixture(name,patch=None,second_patch=None,endian='<'):
    d=BASE.copy(); d.update(patch or {})
    first=struct.pack(endian+'34I',*(d[x] for x in F)); second=first
    if second_patch is not None:
        s=d.copy(); s.update(second_patch); second=blob(s)
    (O/'fixtures'/f'{name}.first.bin').write_bytes(first)
    (O/'fixtures'/f'{name}.second.bin').write_bytes(second)
    r=decode(first,second,BUILD,GEN,NONCE)
    (O/'fixtures'/f'{name}.json').write_text(json.dumps(r,indent=2)+'\n')
    return d,r

def accepted(name,patch=None,warnings=None,normal=False):
    d,r=fixture(name,patch)
    check(name+'.accepted',r.get('accepted_frozen_record') is True,r.get('errors'),[])
    check(name+'.raw',r.get('raw')==d)
    check(name+'.never_qualifies',r.get('timing_qualified') is False and r.get('physical_identity_verified') is False)
    check(name+'.normal_shape',r.get('normal_stop_record_shape') is normal,r.get('normal_stop_record_shape'),normal)
    if warnings is not None: check(name+'.warnings',r.get('warnings')==warnings,r.get('warnings'),warnings)
    return r

def rejected(name,patch,error=None):
    d,r=fixture(name,patch)
    check(name+'.rejected',r.get('accepted_frozen_record') is False,r.get('accepted_frozen_record'),False)
    check(name+'.raw_preserved',r.get('raw')==d)
    if error: check(name+'.error',error in r.get('errors',[]),r.get('errors'),error)
    return r

# Compile independent C offset/sizeof probe against exact header.
probe='#include <stddef.h>\n#include <stdio.h>\n#include "'+str(H)+'"\nint main(void){ printf("size %zu\\n",sizeof(mt_stream_diag_record));\n'
probe+=''.join('printf("'+f+' %zu\\n",offsetof(mt_stream_diag_record,'+f+'));\n' for f in F)+'return 0;}\n'
(O/'abi_probe.c').write_text(probe)
for argv in [[a.cc,'-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror',str(O/'abi_probe.c'),'-o',str(O/'abi_probe')],[str(O/'abi_probe')]]:
    q=subprocess.run(argv,capture_output=True,text=True)
    commands.append(dict(argv=argv,returncode=q.returncode,stdout=q.stdout,stderr=q.stderr))
    check('ABI command '+argv[0],q.returncode==0)
rows=commands[-1]['stdout'].splitlines()
check('ABI.size_and_all_34_offsets',rows==['size 136']+[f'{f} {i*4}' for i,f in enumerate(F)],rows)
body=re.search(r'typedef struct\s*\{(.*?)\}\s*mt_stream_diag_record;',H.read_text(),re.S).group(1)
headerfields=[]
for declaration in body.split(';'):
    if declaration.strip(): headerfields.extend(x.strip() for x in declaration.replace('uint32_t','').split(','))
check('ABI.header_fields_exact_order',headerfields==F,headerfields,F)
check('ABI.decoder_fields_exact_order',ns.get('FIELDS')==F)

r=accepted('canonical',warnings=[],normal=True)
check('canonical.hashes',r['first_sha256']==r['second_sha256']==hashlib.sha256(blob(BASE)).hexdigest())
check('nonce.high_low_64bit',r['nonce_hex']=='fedcba9876543210',r['nonce_hex'])
check('wrap.raw_preserved',r['raw']['last_cycle'] < r['raw']['start_cycle'])
check('wrap.no_elapsed_inference',not any('elapsed' in x for x in r))
# Distinct marker for every word ensures no transposition can be masked by zeros.
d,r=fixture('all_fields_distinct',{f:(0x80000000+i) for i,f in enumerate(F)})
check('all_fields_distinct.raw',r.get('raw')==d)
d,r=fixture('big_endian',endian='>')
check('big_endian.rejected',not r['accepted_frozen_record'])
for field,val,error in [('magic',0,'magic_mismatch'),('version',2,'version_mismatch'),('bytes',132,'bytes_mismatch'),('firmware_build',BUILD^1,'firmware_build_mismatch'),('generation',GEN^1,'generation_mismatch'),('nonce_low',BASE['nonce_low']^1,'nonce_mismatch'),('nonce_high',BASE['nonce_high']^1,'nonce_mismatch'),('flags',32,'undefined_flags'),('flags',M,'undefined_flags'),('freeze_cycle',13,'freeze_cycle_mismatch'),('queue_high_water',17,'queue_high_water_out_of_range'),('queue_high_water',M,'queue_high_water_out_of_range')]:
    rejected(f'bad_{field}_{val}',{field:val},error)
for p in [0,1,2,4,M]: rejected(f'phase_{p}',{'phase':p},'phase_mismatch')
for field,values in [('final_adc_state',[1,2,M]),('final_adc_owned',[1,2,M]),('final_hardware_off',[0,2,M])]:
    for val in values: rejected(f'quiescence_{field}_{val}',{field:val},'frozen_without_quiescence')
# Every-byte mismatches are rejected without erasing first dump raw values.
for i in range(136):
    first=blob(BASE); second=bytearray(first); second[i]^=1
    r=decode(first,bytes(second),BUILD,GEN,NONCE)
    check(f'mismatch_byte_{i}',not r['accepted_frozen_record'] and 'repeated_dump_mismatch' in r['errors'] and r['raw']==BASE)
for size in [0,1,135,137,4096]:
    for which in [0,1]:
        args=[blob(BASE),blob(BASE)]; args[which]=bytes(size)
        r=decode(*args,BUILD,GEN,NONCE)
        check(f'size_{size}_dump_{which}',not r['accepted_frozen_record'] and ('first' if which==0 else 'second')+'_size_not_136' in r['errors'] and r['raw'] is None)

# Missing observations use null; zero observed durations retain numerical zero.
zero={x:0 for x in ['step_count','tx_count','poll_count','copy_count']}
d,r=fixture('zero_counts',zero)
check('zero_counts.all_null',all(x is None for x in r['observed_metrics'].values()),r['observed_metrics'])
check('zero_counts.raw_unmodified',r['raw']==d)
d,r=fixture('one_step_one_poll',{'step_count':1,'poll_count':1})
check('single_observation.gaps_null',r['observed_metrics']['step_stamp_gap_cycles'] is None and r['observed_metrics']['poll_gap_cycles'] is None)
zero_metrics={x:0 for x in ['step_body_max_cycles','step_gap_max_cycles','tx_max_cycles','driver_observed_poll_gap_us','poll_gap_max_cycles','copy_max_us']}
d,r=fixture('measured_zero',zero_metrics)
check('measured_zero.retained',all(x==0 for x in r['observed_metrics'].values()),r['observed_metrics'])
accepted('first_fault_pre_tx',{'flags':4,'first_runtime_result':M,'first_local_error':7,'first_acquisition_reason':6,'first_adc_fault':8,'first_epoch':2,'final_runtime_result':3,'final_core_state':8},['first_fault'])
accepted('first_fault_by_result',{'flags':4,'first_runtime_result':3,'first_epoch':1,'final_runtime_result':3,'final_core_state':8},['first_fault'])
for state in [1,2]: accepted(f'replacement_{state}',{'flags':8,'final_runtime_result':M,'final_core_state':state},['replacement_hello_partial_step'])
accepted('saturated',{'flags':2,'step_count':M,'tx_count':M,'poll_count':M,'copy_count':M},['count_saturated'])
accepted('invalid_dwt',{'flags':16},['invalid_cycle_source'])
# DWT unavailable is structurally decodable warning evidence, not timing good.
rejected('unusable_dwt_cannot_freeze',{'flags':1})
# Strong structural contradictions. Expected rejection is recorded, never xfailed.
for state in [0,1,2,3,4,5,6,9,M]: rejected(f'invalid_ordinary_terminal_state_{state}',{'final_core_state':state})
for value in [6,M]: rejected(f'invalid_final_runtime_{value}',{'final_runtime_result':value})
for field in ['first_runtime_result','first_local_error','first_acquisition_reason','first_adc_fault','first_epoch']:
    rejected('nonzero_'+field+'_without_flag',{field:1})
rejected('firstfault_flag_without_cause',{'flags':4})
for field,value in [('first_runtime_result',6),('first_local_error',8),('first_acquisition_reason',7),('first_adc_fault',9)]:
    rejected(f'bad_firstfault_enum_{field}',{'flags':4,'first_runtime_result':3,'first_epoch':2,field:value})
for state in [0,3,4,5,6,7,8,9,M]: rejected(f'invalid_replacement_state_{state}',{'flags':8,'final_runtime_result':M,'final_core_state':state})
rejected('replacement_without_unknown_result',{'flags':8,'final_core_state':1,'final_runtime_result':0})
rejected('ordinary_zero_step',{'step_count':0})
rejected('normal_zero_tx',{'tx_count':0})
rejected('saturated_no_saturated_counter',{'flags':2})
rejected('final_runtime_fault_without_firstfault',{'final_runtime_result':3,'final_core_state':8})
accepted('replacement_zero_counts',{'flags':8,'final_runtime_result':M,'final_core_state':1,'step_count':0,'tx_count':0,'poll_count':0,'copy_count':0},['replacement_hello_partial_step'])

# CLI: exact command/stdout/stderr/exit preserved per case.
f1=O/'fixtures/canonical.first.bin'; f2=O/'fixtures/canonical.second.bin'
base=[str(f1),str(f2),'--build',str(BUILD),'--generation',str(GEN),'--nonce',str(NONCE)]
def cli(name,args,expected_rc,json_result=None):
    argv=[sys.executable,'-I','-B',str(D)]+args
    try:
        q=subprocess.run(argv,capture_output=True,text=True,timeout=5)
        row=dict(name=name,argv=argv,returncode=q.returncode,stdout=q.stdout,stderr=q.stderr)
        check('cli.'+name+'.exit',q.returncode==expected_rc,q.returncode,expected_rc)
        if json_result is not None:
            try: output=json.loads(q.stdout)
            except Exception as exc: output={'parse_error':str(exc)}
            check('cli.'+name+'.json',output.get('accepted_frozen_record') is json_result,output)
    except subprocess.TimeoutExpired as exc:
        row=dict(name=name,argv=argv,timeout=True,stdout=str(exc.stdout),stderr=str(exc.stderr))
        check('cli.'+name+'.no_hang',False)
    (O/'cli'/f'{name}.json').write_text(json.dumps(row,indent=2)+'\n')
    commands.append(row)
cli('canonical',base,0,True)
cli('help',['--help'],0)
cli('missing_all',[],2)
cli('missing_nonce',base[:-2],2)
for option,badvalues in [('--build',['-1',str(2**32),'xxx','0xz']),('--generation',['0','-1',str(2**32)]),('--nonce',['0','-1',str(2**64)])]:
    for i,value in enumerate(badvalues):
        args=base.copy(); args[args.index(option)+1]=value
        cli(option[2:]+'_bad_'+str(i),args,2)
args=base.copy(); args[3]='0x'+format(BUILD,'x'); args[5]='0x'+format(GEN,'x'); args[7]='0x'+format(NONCE,'x')
cli('hex_identity',args,0,True)
for name,path in [('absent',O/'no-such-file'),('directory',O)]:
    args=base.copy(); args[0]=str(path); cli(name,args,2)
for size in [0,135,137,1024*1024]:
    path=O/'fixtures'/f'cli_size_{size}.bin'; path.write_bytes(bytes(size))
    args=base.copy(); args[0]=str(path); cli('size_'+str(size),args,2,False)
args=base.copy(); args[1]=str(O/'fixtures/bad_magic_0.first.bin'); cli('mismatch',args,2,False)
# Boundary identities must be accepted losslessly by CLI.
for build,gen,nonce,name in [(0,1,1,'minimum_identity'),(M,M,2**64-1,'maximum_identity')]:
    vals=BASE.copy(); vals.update(firmware_build=build,generation=gen,nonce_low=nonce&M,nonce_high=nonce>>32)
    p=O/'fixtures'/f'{name}.bin'; p.write_bytes(blob(vals))
    cli(name,[str(p),str(p),'--build',str(build),'--generation',str(gen),'--nonce',str(nonce)],0,True)

result=dict(decoder=str(D),decoder_sha256=hashlib.sha256(source).hexdigest(),header=str(H),header_sha256=hashlib.sha256(H.read_bytes()).hexdigest(),python=sys.version,platform=platform.platform(),checks=checks,passed=sum(x['pass_'] for x in checks),failed=sum(not x['pass_'] for x in checks),commands=commands)
(O/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['decoder_sha256','passed','failed']}))
for row in checks:
    if not row['pass_']: print('FAIL',row['name'],json.dumps(row['actual']))
raise SystemExit(bool(result['failed']))
