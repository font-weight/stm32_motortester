#!/usr/bin/env python3
"""Build a draft static ELF from pinned objects and explicit board data. Never flash."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys

OBJCOPY_SHA = 'b0f417cb958a1205aa5061b7bedcc05f16335dd718802d5265e8b7b0b5d78c88'

PINS = {
    'image_exports.py':'738b116c45de81a9435090abf9748c1959355c387624ecc8efd05459c890412d',
    'build_objects.py':'3acbaa9ae279d2483b37dd266a8826e26dc0cbdb507296ae286fd3aec861040b',
    'board_config.py':'0b5d6ba56603e908d7f0b079e3319863f1f7733c97324644377a3e46244f9f27',
    'board_object.py':'c8cce02a89bead37f59bff5c49572a08c55637d85a8fdf4f8f234ec89924dc9e',
    'image_equivalence.py':'b8322ee42b850ce38c025b213643a89ab3a71c25182b257aa2a6bf9e6b64a86d',
    'stack_reference.json':'c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d',
    'inputs.json':'898deea0d20bbeefd2a7131b14ac21e8b3205bd9d7325f6c992f9d034c3c60da',
    'toolchain.json':'22f0c2204b8b381a26af3b910851b43b04f34549b6677cdb5b76f304c4e37b43',
    'board-input.schema.json':'f4f020ecc3466112353a3d449a18200ea36adf040bc38bdf60634d0d4c4dc000',
}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_map(text, objects, board, reference, final_path, elf_type):
    """Bind exact GNU map rows to pinned input sections and validated ELF bytes."""
    require(len(text) <= 2097152 and '\r' not in text and '\0' not in text, 'invalid map size/control byte')
    section = re.compile(r'^ (\.[^ \t]+)(?:[ \t]+(.*))?$')
    detail = re.compile(r'^(0x[0-9a-fA-F]+)[ \t]+(0x[0-9a-fA-F]+)[ \t]+(.+)$')
    output = re.compile(r'^(\.[^ \t]+)(?:[ \t]+.*)?$')
    phase, current, pending = None, None, None
    rows, loads, starts, text_headers = [], [], 0, []
    for line in text.split('\n'):
        if pending is not None:
            match = detail.fullmatch(line.lstrip(' \t'))
            require(match is not None, 'broken immediate map continuation')
            name, saved_phase, saved_output = pending
            rows.append((name,int(match[1],16),int(match[2],16),match[3],saved_phase,saved_output))
            pending = None
            continue
        if line in ('Discarded input sections','Memory Configuration','Linker script and memory map'):
            phase = {'Discarded input sections':'discarded','Memory Configuration':'memory','Linker script and memory map':'mapped'}[line]
            if phase == 'mapped': starts += 1
            continue
        if line.startswith('LOAD '):
            require(phase == 'mapped','LOAD outside mapped body')
            loads.append(line[5:])
            continue
        if phase not in ('discarded','mapped'): continue
        if phase == 'mapped' and output.fullmatch(line):
            current = output.fullmatch(line)[1]
            if current == '.text':
                header = re.fullmatch(r'\.text[ \t]+(0x[0-9a-fA-F]+)[ \t]+(0x[0-9a-fA-F]+)',line)
                require(header is not None,'malformed output text header')
                text_headers.append((int(header[1],16),int(header[2],16)))
            continue
        match = section.fullmatch(line)
        require(match is not None or re.match(r'^[ \t]+\.[A-Za-z_]',line) is None,
                'unsupported input section indentation')
        if match:
            name,tail = match.groups()
            if not tail:
                pending = name,phase,current
                continue
            values = detail.fullmatch(tail)
            require(values is not None,'malformed input section row')
            rows.append((name,int(values[1],16),int(values[2],16),values[3],phase,current))
    require(pending is None and starts == 1,'missing/duplicate map body or unfinished row')
    paths = [*objects,board]
    require(loads == [str(p) for p in paths]+['linker stubs'],'map LOAD order/identity mismatch')
    known = {str(p):(i,elf_type(p)) for i,p in enumerate(paths)}
    require(len(known) == 34,'duplicate linked input path')
    final = elf_type(final_path)
    text_section = final.by_name['.text']
    require(text_headers == [(text_section['addr'],text_section['size'])], 'output text geometry mismatch')
    board_rows = []
    stub_rows, pseudo_rows = [], []
    for name,vma,size,path,where,destination in rows:
        if path == 'linker stubs':
            require(where=='mapped' and destination=='.unsupported_stubs' and size==0 and
                    vma==final.by_name['.unsupported_stubs']['addr'], 'invalid map stub row')
            stub_rows.append(name)
            continue
        require(path in known,'unknown input source path')
        if name in ('.iplt','.rel.iplt','.igot.plt'):
            expected_output = '.unsupported_stubs' if name=='.iplt' else name
            expected_vma = final.value('__static_end') if name=='.igot.plt' else final.by_name['.unsupported_stubs']['addr']
            require(path==str(objects[0]) and where=='mapped' and destination==expected_output and
                    size==0 and vma==expected_vma,'invalid linker-generated empty pseudo input')
            pseudo_rows.append(name)
            continue
        _,input_elf=known[path]
        matches=[x for x in input_elf.sections if x['name']==name]
        require(len(matches)==1 and matches[0]['size']==size,'input section identity/size mismatch')
        if path==str(board) and size and matches[0]['flags']&2:
            require(where=='mapped' and destination=='.text','misplaced board data')
            board_rows.append((name,vma,size))
    require(stub_rows==['.glue_7','.glue_7t','.vfp11_veneer','.v4_bx'],'stub map identity/order mismatch')
    require(pseudo_rows==['.iplt','.rel.iplt','.igot.plt'],'empty pseudo-input identity/order mismatch')
    expected_board=[('.rodata.mt_board',final.value('mt_board'),48)]
    for name in ('serial','product','manufacturer'):
        symbol=final.symbols['board_'+name]
        expected_board.append(('.rodata.board_'+name,symbol['value'],symbol['size']))
    require(board_rows==expected_board,'board input-map identity/order/layout mismatch')
    code = lambda name: name == '.text' or name.startswith('.text.')
    required = set()
    for ordinal,elf in known.values():
        for sec in elf.sections:
            if sec['flags'] & 4:
                require(code(sec['name']) and sec['type']==1 and sec['flags']==6,'unexpected executable input section')
                required.add((ordinal,sec['index']))
    seen, records, evidence = set(), [], []
    for name,vma,size,path,where,destination in rows:
        if not code(name): continue
        require(where == 'mapped' and destination == '.text','discarded or misplaced text input')
        require(path in known,'unknown exact text input path')
        ordinal,elf = known[path]
        matches = [sec for sec in elf.sections if sec['name']==name]
        require(len(matches)==1,'ambiguous input section name')
        sec = matches[0]; key = ordinal,sec['index']
        require(key in required and key not in seen and sec['size']==size,'input range identity/size mismatch')
        seen.add(key)
        if size == 0:
            require(name == '.text' and text_section['addr'] <= vma <= text_section['addr']+text_section['size'],
                    'unexpected empty text section or VMA')
            continue
        require(ordinal < 33 and sec['align'] and vma % sec['align']==0,'board code or input alignment mismatch')
        regions = [x for x in final.sections if x['name']=='.text' and x['addr'] <= vma and vma+size <= x['addr']+x['size']]
        require(len(regions)==1,'range outside actual final text')
        region = regions[0]; offset=region['offset']+vma-region['addr']
        segments=[x for x in final.segments if x['type']==1 and x['vaddr'] <= vma and vma+size <= x['vaddr']+x['filesz'] and x['offset']+vma-x['vaddr']==offset]
        require(len(segments)==1,'range/segment mapping mismatch')
        records.append([objects[ordinal].name,name,vma,size])
        evidence.append({'ordinal':ordinal,'section':name,'vma':vma,'size':size,'file_offset':offset,
                         'final_bytes_sha256':hashlib.sha256(final.raw[offset:offset+size]).hexdigest()})
    require(seen == required,'missing executable input section')
    expected = reference['text_input_ranges']
    canonical = json.dumps(sorted(records),sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    require(len(records)==expected['count'] and hashlib.sha256(canonical).hexdigest()==expected['sorted_records_sha256'],
            'canonical input-range multiset mismatch')
    intervals=sorted((r[2],r[2]+r[3]) for r in records)
    require(all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:])),'overlapping text input ranges')
    return {'verdict':'MAP_INPUT_RANGES_PASS','nonempty_ranges':len(records),'empty_ranges':len(seen)-len(records),
            'canonical_sha256':hashlib.sha256(canonical).hexdigest(),'load_rows':len(loads),'ranges':evidence}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--objects', required=True)
    parser.add_argument('--board', required=True)
    parser.add_argument('--toolchain', required=True)
    parser.add_argument('--stack-bytes', type=int, required=True)
    parser.add_argument('--stack-margin-bytes', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    script = Path(__file__)
    require(script.is_absolute() and os.path.normpath(str(script)) == str(script),
            'absolute normalized script path required')
    for part in [*reversed(script.parents),script]:
        require(not part.is_symlink(), 'symlink script path forbidden')
    require(script.resolve(strict=True) == script, 'canonical script path required')
    home = script.parent
    for name,expected in PINS.items():
        require(sha(home/name) == expected, 'helper/manifest pin mismatch: '+name)
    base = load(home/'build_objects.py')
    strict = base.strict_path
    strict(script)
    root = strict(script.parents[2], 'dir')
    tc = strict(args.toolchain, 'dir')
    objdir = strict(args.objects, 'dir')
    board_input = strict(args.board)
    python = strict(Path(sys.executable).resolve(), executable=True)
    require(3016 <= args.stack_bytes <= 8104 and args.stack_bytes % 8 == 0,
            'stack reservation outside aligned conditional SRAM geometry')
    require(0 <= args.stack_margin_bytes <= 5088 and
            args.stack_bytes == ((3016 + args.stack_margin_bytes + 7)//8)*8,
            'explicit margin does not match reservation')
    require(not any(c in args.out for c in '\n\r:#$\\"\''), 'unsupported output path character')
    out = Path(args.out)
    require(out.is_absolute() and os.path.normpath(args.out) == args.out and
            not out.exists() and not out.is_symlink(), 'fresh absolute normalized output required')
    strict(out.parent,'dir')
    protected = [root,tc,objdir,board_input,python.parent,strict(Path(sys.base_prefix).resolve(),'dir'),Path('/usr/bin')]
    for path in protected:
        require(not base.inside(out,path) and not base.inside(path,out), 'output overlaps protected input')
    inputs = {str(script):sha(script),str(python):sha(python)}
    def pin(path, expected=None, max_size=70*1024*1024):
        path = strict(path)
        require(path.stat().st_size <= max_size, 'oversize input: '+str(path))
        actual = sha(path)
        require(expected is None or expected == actual, 'input pin mismatch: '+str(path))
        inputs[str(path)] = actual
        return path
    for name,expected in PINS.items(): pin(home/name,expected)
    source = json.loads((home/'inputs.json').read_text())
    tool = json.loads((home/'toolchain.json').read_text())
    ref = json.loads((home/'stack_reference.json').read_text())
    for directory,rows in [(root,source['files']),(tc,tool['files'])]:
        for relative,expected in rows.items():
            p = Path(relative)
            require(not p.is_absolute() and '..' not in p.parts, 'unsafe manifest path')
            pin(directory/p,expected)
    pin(board_input,max_size=8192)
    config = load(home/'board_config.py')
    board_raw = board_input.read_bytes()
    data = config.parse_board(board_raw)
    board_check = load(home/'board_object.py')
    image_check = load(home/'image_equivalence.py')
    export_check = load(home/'image_exports.py')
    originals = []
    for ordinal,row in enumerate(ref['original_objects_in_order']):
        require(row['ordinal'] == ordinal and Path(row['name']).name == row['name'], 'invalid object ordering')
        originals.append(pin(objdir/row['name'],row['sha256'],1048576))
    require(len(originals) == 33 and len(set(originals)) == 33, 'original object set mismatch')
    tools = {n:strict(tc/'bin'/('arm-none-eabi-'+n),executable=True) for n in ('gcc','as','ld','readelf','objdump','size')}
    require(all(str(p) in inputs for p in tools.values()), 'unbound executable')
    tools['objcopy'] = strict(tc/'bin/arm-none-eabi-objcopy',executable=True)
    pin(tools['objcopy'],OBJCOPY_SHA)
    out.mkdir(); (out/'tmp').mkdir()
    env = {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(out/'tmp')}
    result = {'status':'RUNNING','scope':'Draft static ELF; no flash or hardware acceptance',
              'hardware_verified':False,'flash_approved':False,'stack_bound_transferred':False,
              'stack_bytes':args.stack_bytes,'explicit_margin_bytes':args.stack_margin_bytes,
              'alignment_slack_bytes':args.stack_bytes-3016-args.stack_margin_bytes,
              'input_kind':data['input_kind'],'caller_attestations':data['caller_attestations'],
              'inputs_before':inputs,'commands':[],'generated':{}}
    def run(label,argv):
        argv = list(map(str,argv))
        try:
            p = subprocess.run(argv,env=env,cwd=out,capture_output=True,text=True,timeout=120)
        except (subprocess.TimeoutExpired,OSError) as error:
            def text(value):
                return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else (value or '')
            (out/(label+'.stdout')).write_text(text(getattr(error,'stdout',None)))
            (out/(label+'.stderr')).write_text(text(getattr(error,'stderr',None)))
            result['commands'].append({'label':label,'argv':argv,'exit':None,'exception':type(error).__name__,'error':str(error)})
            raise
        (out/(label+'.stdout')).write_text(p.stdout); (out/(label+'.stderr')).write_text(p.stderr)
        result['commands'].append({'label':label,'argv':argv,'exit':p.returncode})
        require(p.returncode == 0,label+' failed; evidence preserved')
        return p.stdout
    def generated(path):
        value = sha(path); result['generated'][str(path)] = value; return value
    error = None
    try:
        for name,path in tools.items(): run('version-'+name,[path,'--version'])
        for name in ('cc1','as'):
            selected = run('select-'+name,[tools['gcc'],'-print-prog-name='+name]).strip()
            path = strict(os.path.abspath(os.path.normpath(selected)),executable=True)
            require(str(path) in inputs,'unbound compiler support tool')
        label = 'TEST_ONLY_NEVER_FLASH' if data['input_kind']=='synthetic_fixture' else 'CALLER_ASSERTED_NOT_FLASH_APPROVED'
        saved_json = out/'board-input.json'; saved_json.write_bytes(board_raw); generated(saved_json)
        c = out/(label+'_mt_board.c'); c.write_text(config.generate(data)); generated(c)
        obj = out/'board.o'; dep = out/'board.d'
        flags = ['-std=c11','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-Os','-ffreestanding','-fno-common',
                 '-fno-short-enums','-Wa,--noexecstack','-ffunction-sections','-fdata-sections',
                 '-Wall','-Wextra','-Werror','-Wpedantic','-Wconversion']
        includes = ['-I'+str(root/p) for p in ('firmware/boot','firmware/platform','firmware/transport')]
        run('compile-board',[tools['gcc'],*flags,*includes,'-isystem',tc/'include/newlib','-MD','-MF',dep,'-c',c,'-o',obj])
        deps = dep.read_text().replace('\\\n',' ').split(':',1)
        require(len(deps)==2,'invalid compiler dependency file')
        dependencies = {}
        for value in shlex.split(deps[1]):
            path = Path(value)
            for part in [*reversed(path.parents),path]: require(not part.is_symlink(),'symlink dependency')
            path = strict(os.path.normpath(value))
            expected = inputs.get(str(path),result['generated'].get(str(path)))
            require(expected is not None and sha(path)==expected,'unbound compiler dependency')
            dependencies[str(path)] = expected
        require(str(c) in dependencies and str(root/'firmware/boot/mt_board_inputs.h') in dependencies,'missing board dependencies')
        result['dependencies'] = dependencies; generated(dep); generated(obj)
        result['board_object_check'] = board_check.validate(obj.read_bytes(),board_raw)
        py = [python,'-I','-B']; boot = root/'tests/boot-link/tools/check_boot_image.py'; vectors=root/'tests/boot-link/vector_map.json'
        all_objects = [*originals,obj]
        result['ordered_objects'] = [{'ordinal':i,'path':str(p),'sha256':sha(p)} for i,p in enumerate(all_objects)]
        audit = run('boot-input-audit',[*py,boot,'inputs','--readelf',tools['readelf'],'--reset-object',originals[-1],'--vector-map',vectors,*all_objects])
        require(json.loads(audit)['verdict']=='CHECKS_PASS','input BOOT verdict mismatch')
        elf = out/(label+'.elf'); mapfile=out/(label+'.map')
        run('link-static',[tools['ld'],'--fatal-warnings','--no-gc-sections','--orphan-handling=error',
                           '--defsym=__boot_stack_bytes='+str(args.stack_bytes),'-T',root/'firmware/boot/stm32f103c8.ld',
                           '-Map='+str(mapfile),'-o',elf,*all_objects])
        generated(elf); generated(mapfile)
        audit = run('boot-elf-audit',[*py,boot,'elf','--elf',elf,'--vector-map',vectors,'--objdump',tools['objdump'],'--readelf',tools['readelf'],'--stack',str(args.stack_bytes)])
        require(json.loads(audit)['verdict']=='CHECKS_PASS','final BOOT verdict mismatch')
        result['image_check'] = image_check.validate(elf.read_bytes(),board_raw,args.stack_bytes)
        elf_reader = load(root/'tests/boot-link/tools/run_boot_link_tests.py')
        result['map_check'] = check_map(mapfile.read_text(),originals,obj,ref,elf,elf_reader.ELF)
        result['size'] = run('size',[tools['size'],elf])
        binary=out/(label+'.bin'); ihex=out/(label+'.hex')
        selected=['--only-section='+n for n in ('.isr_vector','.text','.data')]
        run('export-binary',[tools['objcopy'],'-O','binary','--gap-fill','0xff',*selected,elf,binary])
        generated(binary)
        run('export-hex',[tools['objcopy'],'-O','ihex',*selected,elf,ihex])
        generated(ihex)
        result['exports_check'] = export_check.validate_exports(elf_reader.ELF(elf),binary.read_bytes(),ihex.read_bytes())
        result['status'] = 'CONDITIONAL_STATIC_LINK_CHECKS_PASS'
        result['stack_bound_transferred'] = True
        result['conditional_stack_bound_bytes'] = 3016
        result['assumptions'] = ['Privileged reset-only MSP execution with STKALIGN',
            'Only equal-priority USB IRQ19/20 with original handlers',
            'Terminal default HardFault/NMI handlers and exception allowance',
            'No memory corruption, external code injection, RTOS or deferred-call producer',
            'Hardware and timing assumptions are not physically verified']
    except Exception as exc:
        error = exc; result.update(status='FAILED',error=str(exc))
    finally:
        after, failures = {}, {}
        for name in inputs:
            try: after[name] = sha(Path(name))
            except OSError as exc: after[name] = None; failures[name] = str(exc)
        result['inputs_after'] = after; result['postcheck_errors'] = failures
        result['inputs_unchanged'] = after == inputs
        generated_after = {}
        for name in result['generated']:
            try: generated_after[name] = sha(Path(name))
            except OSError: generated_after[name] = None
        result['generated_after'] = generated_after
        if after != inputs or generated_after != result['generated']:
            result['status']='FAILED';result['stack_bound_transferred']=False;result.setdefault('error','input/generated drift')
        (out/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
    if error is not None: raise error
    require(result['status']=='CONDITIONAL_STATIC_LINK_CHECKS_PASS','postcheck failed')
    print(json.dumps({'status':result['status'],'output':str(out),'stack_bound_transferred':result['stack_bound_transferred']}))

if __name__ == '__main__':
    main()
