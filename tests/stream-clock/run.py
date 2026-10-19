#!/usr/bin/env python3
"""Relocatable native tests; only supplied source files are compiled."""
import argparse,datetime,hashlib,json,os,pathlib,platform,shlex,subprocess,sys,tempfile
HERE=pathlib.Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',help='Repository or source-export root containing firmware and tests')
p.add_argument('--out',help='New output directory; existing paths are refused')
p.add_argument('--mutations',action='store_true',help='Run targeted negative controls too')
a=p.parse_args();src=pathlib.Path(a.source).resolve()
if a.out:
    out=pathlib.Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
else:out=pathlib.Path(tempfile.mkdtemp(prefix='mt-live-clock-native-')).resolve()
manifest=json.loads((HERE/'source-manifest.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
actual={name:sha(src/name) for name in manifest['sha256']}
if actual!=manifest['sha256']:
    (out/'source-mismatch.json').write_text(json.dumps(actual,indent=2)+'\n')
    raise SystemExit('Source does not match pinned manifest; results not run.')
cc=shlex.split(os.environ.get('CC','cc'))
flags=['-std=c11','-O2','-g','-Wall','-Wextra','-Wpedantic','-Werror','-Wconversion','-Wsign-conversion','-Wshadow']
includes=['-I'+str(src/x) for x in ['firmware/platform','firmware/protocol','tests/coherent-clock']]
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
records=[]
for name in ['run.py','test_live_clock.c','source-manifest.json']:
    (out/name).write_bytes((HERE/name).read_bytes())
def command(name,args,expected=0):
    result=subprocess.run([str(x) for x in args],capture_output=True,env=env)
    (out/(name+'.stdout')).write_bytes(result.stdout);(out/(name+'.stderr')).write_bytes(result.stderr)
    ok=result.returncode==expected if expected is not None else result.returncode!=0 and b'Assertion' in result.stderr
    records.append(dict(name=name,command=[str(x) for x in args],returncode=result.returncode,
                        expected='assertion failure' if expected is None else expected,pass_=ok))
    print(f'{name}: {"PASS" if ok else "FAIL"} (exit {result.returncode})',flush=True)
    return ok
provenance=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source=str(src),output=str(out),
               commit=manifest['commit'],base=manifest['base'],platform=platform.platform(),
               compiler=subprocess.check_output(cc+['--version'],text=True).splitlines()[0],
               source_sha256=actual,runner_sha256=sha(HERE/'run.py'),test_sha256=sha(HERE/'test_live_clock.c'),
               sanitizer_options={k:env[k] for k in ['ASAN_OPTIONS','UBSAN_OPTIONS']},git_required=False)
try:
    provenance['source_git_head']=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],stderr=subprocess.DEVNULL,text=True).strip()
except (FileNotFoundError,subprocess.CalledProcessError):pass
(out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
source_names=['firmware/platform/mt_live_clock.c','firmware/platform/mt_timebase.c',
              'firmware/protocol/mt_live_device.c','firmware/protocol/mt_wire.c','firmware/acquisition/mt_sample_queue.c']
sources=[src/x for x in source_names]
for variant in ['strict','asan-ubsan']:
    san=[] if variant=='strict' else ['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer']
    targets=[('wrapper',HERE/'test_live_clock.c',sources,[]),
             ('inline-regression',src/'tests/timebase-inline/test_trace.c',[sources[1]],[out/(variant+'-inline.trace')]),
             ('endpoint-regression',src/'tests/protocol/live-device/native/test_live_device.c',sources[2:],[])]
    for label,test,prod,args in targets:
        exe=out/(variant+'-'+label)
        if command('compile-'+exe.name,cc+flags+san+includes+[test]+prod+['-o',exe]):
            command('run-'+exe.name,[exe]+args)
if a.mutations:
    original=sources[0].read_text();endpoint=sources[2].read_text()
    mutants=[
      ('drop-us-bound','service','    if ((uint32_t)(us - clock->last_us) > MT_TIMEBASE_MAX_DELTA ||\n        delta > MT_TIMEBASE_MAX_DELTA / 1000u ||','    if (delta > MT_TIMEBASE_MAX_DELTA / 1000u ||'),
      ('drop-ms-bound','service','        delta > MT_TIMEBASE_MAX_DELTA / 1000u ||\n',''),
      ('reject-exact-us-bound','service','(uint32_t)(us - clock->last_us) > MT_TIMEBASE_MAX_DELTA','(uint32_t)(us - clock->last_us) >= MT_TIMEBASE_MAX_DELTA'),
      ('reject-exact-ms-bound','service','delta > MT_TIMEBASE_MAX_DELTA / 1000u','delta >= MT_TIMEBASE_MAX_DELTA / 1000u'),
      ('drop-sticky-check','service','clock == NULL || clock->failed || clock->source == NULL','clock == NULL || clock->source == NULL'),
      ('replace-u64-with-ms32','wrap','clock->milliseconds += delta;','clock->milliseconds = ms;'),
      ('derive-ms-from-us32','wrap','delta = ms - clock->last_ms;','delta = us / 1000u - clock->last_ms;'),
      ('reject-exact-int64-max','limit','clock->milliseconds > (uint64_t)INT64_MAX - delta','clock->milliseconds >= (uint64_t)INT64_MAX - delta'),
      ('drop-overflow-check','limit','        delta > MT_TIMEBASE_MAX_DELTA / 1000u ||\n        clock->milliseconds > (uint64_t)INT64_MAX - delta','        delta > MT_TIMEBASE_MAX_DELTA / 1000u'),
      ('return-stale-after-source-failure','faults','    if (!mt_timebase_snapshot(clock->source, &us, &ms)) {\n        clock->failed = 1u;\n        return UINT64_MAX;\n    }','    if (!mt_timebase_snapshot(clock->source, &us, &ms)) {\n        us = clock->last_us; ms = clock->last_ms;\n    }'),
      ('double-snapshot','init','!mt_timebase_snapshot(clock->source, &us, &ms)','!(mt_timebase_snapshot(clock->source, &us, &ms) && mt_timebase_snapshot(clock->source, &us, &ms))'),
      ('forget-us-publication','wrap','    clock->last_us = us;\n    return clock->milliseconds;','    return clock->milliseconds;'),
    ]
    mdir=out/'mutations';mdir.mkdir()
    for name,group,old,new in mutants:
        if original.count(old)!=1:raise RuntimeError('Ambiguous mutation '+name)
        mp=mdir/(name+'.c');mp.write_text(original.replace(old,new))
        exe=mdir/name
        if command('compile-mutation-'+name,cc+flags+includes+[HERE/'test_live_clock.c',mp]+sources[1:]+['-o',exe]):
            command('run-mutation-'+name,[exe,group],expected=None)
    name='endpoint-allows-invalid-upper-range';old='n>(uint64_t)INT64_MAX-LIMIT_MS'
    assert endpoint.count(old)==1
    mp=mdir/(name+'.c');mp.write_text(endpoint.replace(old,'0'))
    exe=mdir/name
    if command('compile-mutation-'+name,cc+flags+includes+[HERE/'test_live_clock.c']+sources[:2]+[mp]+sources[3:]+['-o',exe]):
        command('run-mutation-'+name,[exe,'endpoint'],expected=None)
# Ensure no source file was altered while testing.
after={name:sha(src/name) for name in actual}
records.append(dict(name='source-unchanged',pass_=after==actual))
receipt=dict(provenance=provenance,results=records,pass_=all(x['pass_'] for x in records))
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Artifacts:',out)
raise SystemExit(0 if receipt['pass_'] else 1)
