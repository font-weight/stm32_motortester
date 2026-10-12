#!/usr/bin/env python3
# A finite manual callback/storage certificate is independently reviewed with this code.
# It is not inferred merely from these target names or static pointer words.
# .su is an observed auxiliary cross-check; machine decoding determines the frame.
# Add the exact changed-site callback memberships; all other old sites inherit
# byte/relocation and storage-context correspondence, not arbitrary new sets.
# Fixed ELF subject: geometry and ownership assumptions are separately reviewed
# finite obligations in THEOREM_RU.md. This is not a generic program analyzer.
"""Replay an exact native0.2 incremental proof candidate. Never flash or accept hardware."""
import argparse, hashlib, importlib.util, json, os, re, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode = True
HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[2]
MANIFEST_SHA = '56dc7340052515316ed77d40bb51705dae96b1b15b8ef85a9d2a22e4cfa67b4c'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def normalized(e):
    result = {}
    for sec in e.sections:
        if not sec['flags'] & 2:
            continue
        require(sec['name'] not in result, 'duplicate allocated section')
        rel = []
        for rs in e.sections:
            if rs['type'] != 9 or rs['info'] != sec['index']:
                continue
            require(rs['entsize'] == 8 and rs['size'] % 8 == 0, 'malformed relocation section')
            for off in range(rs['offset'], rs['offset'] + rs['size'], 8):
                where, info = e.unpack('<II', off)
                name, sym = e.symbol_tables[rs['link']][info >> 8]
                target = e.sections[sym['ndx']]['name'] if sym['ndx'] not in (0, 65521) else str(sym['ndx'])
                rel.append((where, info & 255, name, target, sym['value'], sym['bind'], sym['type']))
        result[sec['name']] = {'type': sec['type'], 'flags': sec['flags'], 'align': sec['align'], 'size': sec['size'], 'bytes': e.bytes(sec).hex() if sec['type'] != 8 else None, 'relocations': rel}
    return result
SITE_MAP = [('mt_control_receive', 44, 44, 'stop'), ('mt_control_receive', 70, 70, 'profile'), ('start_capture', 128, 820, 'profile'), ('start_capture', 244, 924, 'start'), ('mt_control_receive', 784, 974, 'stop'), ('mt_control_receive', 1070, 1244, 'profile'), ('mt_control_receive', 1088, 924, 'start')]

def register_sites(text):
    section = None
    sites = []
    for line in text.splitlines():
        z = re.fullmatch('Disassembly of section (.+):', line)
        if z:
            section = z[1]
        z = re.match('^\\s*([0-9a-f]+):\\s+[0-9a-f]{4}(?: [0-9a-f]{4})?\\s+(blx|bx)\\s+(\\w+)\\s*$', line)
        if z and z[3] != 'lr':
            sites.append((section, int(z[1], 16), z[2], z[3]))
    return sites

def callback_loads(text, rows):
    sections = {}
    section = None
    for line in text.splitlines():
        z = re.fullmatch('Disassembly of section (.+):', line)
        if z:
            section = z[1]
            sections[section] = {}
        z = re.match('^\\s*([0-9a-f]+):\\s+[0-9a-f]{4}(?: [0-9a-f]{4})?\\s+([a-z][a-z0-9.]*)\\s*(.*)$', line)
        if z and section:
            sections[section][int(z[1], 16)] = (z[2].split('.')[0], z[3].split('@')[0].strip())
    for name, pc, target in rows:
        ins = sections['.text.' + name]
        base = 'r5' if name == 'start_capture' else 'r6'
        require(ins[pc] == ('blx', 'r3'), 'callback register mismatch')
        if target == 'stop':
            require(ins[pc - 4] == ('ldrd', f'r3, r0, [{base}, #536]'), 'stop/context load mismatch')
        else:
            field = 528 if target == 'profile' else 532
            require(ins[pc - 8] == ('ldr', f'r3, [{base}, #{field}]'), 'callback field mismatch')
            require(ins[pc - 4] == ('ldr', f'r0, [{base}, #540]'), 'callback context mismatch')
            if target == 'start':
                require(ins[pc - 10] == ('mov', 'r1, r7'), 'callback now argument mismatch')

def run(args):
    for name, path in vars(args).items():
        require(path.is_absolute() and os.path.normpath(str(path)) == str(path), 'absolute normalized argument required: ' + name)
        for part in [*reversed(path.parents), path]:
            require(not part.is_symlink(), 'symlink argument forbidden')
        if name != 'out':
            require(path.exists(), 'missing input: ' + name)
    manifest_path = HOME / 'inputs.json'
    require(sha(manifest_path) == MANIFEST_SHA, 'proof manifest mismatch')
    manifest = json.loads(manifest_path.read_text())
    inputs = {str(manifest_path): sha(manifest_path), str(Path(__file__).resolve()): sha(__file__)}

    def pin(path, digest):
        path = Path(path).resolve(strict=True)
        require(path.is_file() and sha(path) == digest, 'input mismatch: ' + str(path))
        inputs[str(path)] = digest
        return path
    for relative, digest in manifest['repository_files'].items():
        pin(ROOT / relative, digest)
    for relative, digest in manifest['proof_helpers'].items():
        pin(HOME / relative, digest)
    pin(args.old_review, manifest['old_theorem']['review_sha256'])
    for kind, directory in [('old', args.old_objects), ('new', args.new_objects)]:
        for name, digest in manifest[kind + '_objects'].items():
            pin(directory / name, digest)
    for name, path in [('old_elf', args.old_elf), ('new_elf', args.new_elf)]:
        pin(path, manifest[name])
    pin(args.objdump, manifest['objdump_sha256'])
    pin(args.board_object, manifest['board_object_sha256'])
    for path in (args.old_map, args.new_map):
        inputs[str(path.resolve())] = sha(path)
    require(not args.out.exists(), 'fresh proof output required')
    args.out.mkdir(parents=False)
    reader = module(ROOT / 'tests/boot-link/tools/run_boot_link_tests.py')
    frames = module(HOME / 'frames.py')
    projection = module(HOME / 'projection.py')
    relocations = module(HOME / 'relocations.py')
    commands = []

    def disassemble(obj, label, section=None):
        argv = [str(args.objdump), '-dr'] + (['-j', section] if section else []) + [str(obj)]
        p = subprocess.run(argv, env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, cwd=args.out, capture_output=True, text=True, timeout=60)
        commands.append({'argv': argv, 'exit': p.returncode})
        (args.out / (label + '.stderr')).write_text(p.stderr)
        require(p.returncode == 0, 'objdump failure')
        (args.out / (label + '.dis')).write_text(p.stdout)
        return p.stdout
    oldref = json.loads((ROOT / 'firmware/build/stack_reference.json').read_text())
    require({x['name']: x['sha256'] for x in oldref['original_objects_in_order']} == manifest['old_objects'], 'old theorem object mismatch')
    require(oldref['independent_review_sha256'] == manifest['old_theorem']['review_sha256'], 'external review identity mismatch')
    for field, key in [('foreground', 'foreground_including_main'), ('usb_irq', 'usb_irq_software'), ('startup', 'startup')]:
        require(manifest['old_theorem'][field] == oldref['reviewed_stack_bounds'][key], 'old theorem numeric mismatch')
    changed = set()
    old_register = []
    new_register = []
    abs_old = []
    abs_new = []
    for name in manifest['old_objects']:
        old = reader.ELF(args.old_objects / name)
        new = reader.ELF(args.new_objects / name)
        a, b = (normalized(old), normalized(new))
        for section in set(a) | set(b):
            if a.get(section) != b.get(section):
                changed.add((name, section))
        for target, sections in [(abs_old, a), (abs_new, b)]:
            for sec, row in sections.items():
                target.extend(((name, sec, *r) for r in row['relocations'] if r[1] == 2))
        for kind, path, target in [('old', args.old_objects / name, old_register), ('new', args.new_objects / name, new_register)]:
            target.extend(((name, *row) for row in register_sites(disassemble(path, kind + '-' + name))))
    require(changed == {tuple(x) for x in manifest['changed_sections']}, 'unexpected cumulative allocated delta')
    require(sorted(abs_old) == sorted(abs_new), 'changed ABS32 symbolic initialization')
    old_changed = [x for x in old_register if x[0] == '3-mt_control.o' and x[1] == '.text.mt_control_receive']
    new_changed = [x for x in new_register if x[0] == '3-mt_control.o' and x[1] in {'.text.mt_control_receive', '.text.start_capture'}]
    require({(x[1].removeprefix('.text.'), x[2]) for x in new_changed} == {(n, p) for n, p, _, _ in SITE_MAP}, 'uncovered new register site')
    require({x[2] for x in old_changed} == {old for _, _, old, _ in SITE_MAP}, 'uncovered old register site')
    require(sorted((x for x in new_register if x not in new_changed)) == sorted((x for x in old_register if x not in old_changed)), 'changed inherited register transfer')
    old_local = []
    old_frame_dir = args.out / 'old-frames'
    old_frame_dir.mkdir()
    frames.HOME = old_frame_dir
    frames.OBJECTS = args.old_objects
    frames.TABLES = {'mt_control_receive': (374, 6, 2, 'r3'), 'mt_command_parse': (90, 7, 1, 'r3')}
    old_wanted = {'0-acquisition.o': ['mt_acq_arm', 'mt_acq_push'], '1-mt_capture_link.o': ['mt_capture_link_arm'], '3-mt_control.o': ['mt_control_receive'], '9-mt_wire.o': ['mt_wire_encode', 'mt_wire_decode'], '12-mt_command.o': ['mt_command_parse']}
    for obj, names in old_wanted.items():
        for name in names:
            body = disassemble(args.old_objects / obj, 'old-frame-' + name, '.text.' + name)
            (old_frame_dir / (name + '.dis')).write_text(body)
            su = args.old_objects / (obj[:-2] + '.su')
            inputs[str(su.resolve())] = sha(su)
            old_local.append(frames.inspect(obj, name))
    callback_loads((args.out / 'new-3-mt_control.o.dis').read_text(), [(n, p, t) for n, p, _, t in SITE_MAP])
    callback_loads((args.out / 'old-3-mt_control.o.dis').read_text(), [('mt_control_receive', p, t) for _, _, p, t in SITE_MAP])
    frames.HOME = args.out
    frames.OBJECTS = args.new_objects
    frames.TABLES = {'mt_control_receive': (368, 7, 2, 'r3'), 'mt_command_parse': (90, 8, 1, 'r3')}
    local = []
    for obj, names in frames.WANTED.items():
        for name in names:
            disassemble(args.new_objects / obj, name, '.text.' + name)
            su = args.new_objects / (obj[:-2] + '.su')
            inputs[str(su.resolve())] = sha(su)
            local.append(frames.inspect(obj, name))
    expected_frames = manifest['changed_frame_bounds']
    require({x['function']: x['frame'] for x in local} == expected_frames, 'frame certificate mismatch')
    old_weights = {x['function']: x['frame'] for x in old_local}
    aliases = {'mt_acq_record_now': 'mt_acq_arm', 'mt_capture_link_record_now': 'mt_capture_link_arm'}
    for row in local:
        if row['function'] == 'start_capture':
            continue
        require(row['frame'] <= old_weights[aliases.get(row['function'], row['function'])], 'frame domination failure')
    oldnodes, old_edges = projection.graph(args.old_objects)
    newnodes, new_edges = projection.graph(args.new_objects)
    for name, new_pc, old_pc, target in SITE_MAP:
        old_edges.add(('3-mt_control.o::.text.mt_control_receive', '2-mt_adc_control.o::.text.' + target))
        new_edges.add(('3-mt_control.o::.text.' + name, '2-mt_adc_control.o::.text.' + target))
    simulation = projection.project(oldnodes, old_edges, newnodes, new_edges)
    old_relocations = relocations.audit([args.old_objects / x['name'] for x in oldref['original_objects_in_order']] + [args.board_object], args.old_map, args.old_elf)
    new_relocations = relocations.audit([args.new_objects / x['name'] for x in oldref['original_objects_in_order']] + [args.board_object], args.new_map, args.new_elf)
    image = reader.ELF(args.new_elf)
    foreground = manifest['old_theorem']['foreground'] + expected_frames['start_capture']
    terminal = max(foreground + manifest['old_theorem']['usb_irq'] + 36 + 72, manifest['old_theorem']['startup'] + 72)
    bound = (terminal + 7) // 8 * 8
    require(bound == 3104 and image.value('__boot_stack_bytes') >= bound, 'under-reserved proof subject')
    require(all((sha(p) == digest for p, digest in inputs.items())), 'proof inputs changed')
    result = {'status': 'DRAFT_INCREMENTAL_PROOF_REPLAY_PASS', 'independent_acceptance': False, 'hardware_verified': False, 'flash_approved': False, 'stack_bound_transferred': False, 'conditional_derived_bytes': bound, 'foreground_ceiling': foreground, 'external_lemma': manifest['old_theorem'], 'simulation': simulation, 'frames': local, 'old_frames': old_local, 'old_relocations': old_relocations, 'new_relocations': new_relocations, 'register_sites': {'old': len(old_register), 'new': len(new_register)}, 'inputs': inputs, 'commands': commands, 'limits': 'Requires independent acceptance of exact replay and finite manual CFG/ABI/callback/root/loaded-layout obligations in THEOREM_RU.md. No image recipe/reference is rebound.'}
    (args.out / 'RESULTS.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'conditional_derived_bytes', 'independent_acceptance', 'stack_bound_transferred')}))
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('old-objects', 'new-objects', 'old-elf', 'old-map', 'new-elf', 'new-map', 'board-object', 'old-review', 'objdump', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    run(p.parse_args())
