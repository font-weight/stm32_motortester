#!/usr/bin/env python3
"""Portable replay of the existing independent offline STREAM decoder review."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--source', type=Path, required=True, help='repository root')
P.add_argument('--output', type=Path, required=True, help='new external output directory')
P.add_argument('--decoder', type=Path, help='override firmware/build/decode_stream_diag.py')
P.add_argument('--cc', default='gcc', help='native C11 compiler; default gcc')
a = P.parse_args()
source = a.source.resolve(strict=True)
output = a.output.resolve()
decoder = a.decoder.resolve(strict=True) if a.decoder else source/'firmware/build/decode_stream_diag.py'
# Evidence must not write into source or into this test package.
for protected in (source, HERE):
    if output == protected or protected in output.parents or output in protected.parents:
        P.error('output must be external to source and tests')
output.mkdir(parents=True, exist_ok=False)
commands = []
provenance = json.loads((HERE/'PROVENANCE.json').read_text())
checks = []
for rel, expected in provenance['reference_source_sha256'].items():
    path = source/rel
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    checks.append({'path':rel, 'expected_sha256':expected, 'actual_sha256':actual, 'pass':actual==expected})
fixture_writer = HERE/provenance['fixture_writer']['path']
fixture_actual = hashlib.sha256(fixture_writer.read_bytes()).hexdigest()
checks.append({'path':str(fixture_writer), 'expected_sha256':provenance['fixture_writer']['sha256'], 'actual_sha256':fixture_actual, 'pass':fixture_actual==provenance['fixture_writer']['sha256']})
actual = hashlib.sha256(decoder.read_bytes()).hexdigest()
checks.append({'path':str(decoder), 'expected_sha256':provenance['candidate_sha256'], 'actual_sha256':actual, 'pass':actual==provenance['candidate_sha256']})
(output/'source-pins.json').write_text(json.dumps(checks, indent=2)+'\n')
if not all(row['pass'] for row in checks):
    raise SystemExit('source/candidate pin mismatch; no acceptance score is transferable to changed bytes')

def run(name, argv, stdout_path=None):
    r = subprocess.run(argv, capture_output=True)
    stdout = r.stdout.decode('utf-8', errors='replace')
    stderr = r.stderr.decode('utf-8', errors='replace')
    (output/(name+'.stdout')).write_text(stdout)
    (output/(name+'.stderr')).write_text(stderr)
    if stdout_path is not None:
        stdout_path.write_bytes(r.stdout)
    commands.append({'name':name,'argv':argv,'returncode':r.returncode,'stdout':name+'.stdout','stderr':name+'.stderr'})
    (output/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
    if r.returncode != 0:
        raise SystemExit(name+' failed; evidence retained in '+str(output))
    return stdout

run('python-version', [sys.executable,'--version'])
run('compiler-version', [a.cc,'--version'])
run('decoder-checks', [sys.executable,'-I','-B',str(HERE/'check_decoder.py'),'--decoder',str(decoder),'--header',str(source/'firmware/platform/mt_stream_diag.h'),'--output',str(output/'decoder'),'--cc',a.cc])
run('filetype-checks', [sys.executable,'-I','-B',str(HERE/'check_filetypes.py'),'--decoder',str(decoder),'--fixture',str(output/'decoder/fixtures/canonical.first.bin'),'--output',str(output/'filetypes')])
records=output/'c-records'
records.mkdir()
exe=records/'emit_records'
run('compile-c-records', [a.cc,'-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-Wconversion','-Wsign-conversion','-DCFG_MT_STREAM_DIAGNOSTICS=1','-I'+str(source/'firmware/platform'),str(HERE/'emit_records.c'),str(source/'firmware/platform/mt_stream_diag.c'),'-o',str(exe)])
cases={
 'replacement_zero_counts':('replacement_hello_partial_step',False),
 'normal_zero_metrics':(None,True),
 'observed':(None,True),
 'first_fault':('first_fault',False),
 'saturated':('count_saturated',False),
 'invalid_dwt':('invalid_cycle_source',False),
}
record_results=[]
for name,(warning,normal) in cases.items():
    dump=records/(name+'.bin')
    run('emit-'+name, [str(exe),name], stdout_path=dump)
    stdout=run('decode-'+name, [sys.executable,'-I','-B',str(decoder),str(dump),str(dump),'--build','0xc19a372b','--generation','0x87654321','--nonce','0xfedcba9876543210'])
    result=json.loads(stdout)
    (records/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    digest=hashlib.sha256(dump.read_bytes()).hexdigest()
    warnings_ok=(result['warnings']==[] if warning is None else warning in result['warnings'])
    ok=(dump.stat().st_size==136 and result['accepted_frozen_record'] is True and result['normal_stop_record_shape'] is normal and result['physical_identity_verified'] is False and result['timing_qualified'] is False and warnings_ok and result['first_sha256']==digest)
    record_results.append({'case':name,'pass':ok,'sha256':digest,'warnings':result['warnings'],'normal_stop_record_shape':result['normal_stop_record_shape']})
(records/'results.json').write_text(json.dumps(record_results,indent=2)+'\n')
if not all(row['pass'] for row in record_results):
    raise SystemExit('C-generated record check failed; evidence retained')
dec=json.loads((output/'decoder/results.json').read_text())
ft=json.loads((output/'filetypes/results.json').read_text())
summary={'candidate_sha256':actual,'python':sys.version,'platform':platform.platform(),'decoder_assertions_passed':dec['passed'],'decoder_assertions_failed':dec['failed'],'filetype_checks_passed':sum(row['pass_'] for row in ft),'filetype_checks_failed':sum(not row['pass_'] for row in ft),'c_record_cases_passed':sum(row['pass'] for row in record_results),'c_record_cases_failed':sum(not row['pass'] for row in record_results),'timing_qualified':False,'physical_identity_verified':False,'review_score':9.83,'review_scope':'pinned offline decoder only'}
(output/'RESULTS.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
