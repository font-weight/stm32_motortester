# Only mapped input rows, not discarded sections, determine placement.
# Separate layout gate owns generated stubs/pseudo rows.
# Ambiguous weak-only choices are not silently resolved.
"""Exploratory exact input-to-output relocation audit. No stack acceptance.
ARM ELF reference: https://github.com/ARM-software/abi-aa/blob/main/aaelf32/aaelf32.rst
Only ABS32 and Thumb-2 BL/B.W are admitted. Unknown relocation forms fail.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, re, struct, sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('elf_reader', ROOT / 'tests/boot-link/tools/run_boot_link_tests.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def require(ok, msg):
    if not ok:
        raise ValueError(msg)

def branch(raw, kind):
    a, b = struct.unpack('<HH', raw)
    require(a & 63488 == 61440, 'unsupported first branch halfword')
    require(b & 53248 == (53248 if kind == 10 else 36864), 'unsupported branch opcode/interworking')
    s = a >> 10 & 1
    i1 = 1 ^ b >> 13 & 1 ^ s
    i2 = 1 ^ b >> 11 & 1 ^ s
    value = s << 24 | i1 << 23 | i2 << 22 | (a & 1023) << 12 | (b & 2047) << 1
    return value - (1 << 25) if s else value

def audit(paths, map_path, elf_path):
    paths = [Path(p) for p in paths]
    require(len(paths) == 34 and len(set(paths)) == 34, 'object set')
    objects = {str(p): m.ELF(p) for p in paths}
    final = m.ELF(elf_path)
    text = map_path.read_text().split('Linker script and memory map\n')
    require(len(text) == 2, 'map body')
    placements = {}
    pending = None
    for line in text[1].splitlines():
        if pending:
            z = re.fullmatch('\\s+(0x[0-9a-f]+)\\s+(0x[0-9a-f]+)\\s+(.+)', line)
            require(z is not None, 'bad map continuation')
            name = pending
            pending = None
        else:
            match = re.fullmatch(' (\\.[^\\s]+)(?:\\s+(.*))?', line)
            if not match:
                continue
            name, tail = match.groups()
            if not tail:
                pending = name
                continue
            z = re.fullmatch('(0x[0-9a-f]+)\\s+(0x[0-9a-f]+)\\s+(.+)', tail)
            require(z is not None, 'bad map input row')
        vma, size, path = (int(z[1], 16), int(z[2], 16), z[3])
        if path not in objects:
            continue
        e = objects[path]
        matches = [s for s in e.sections if s['name'] == name]
        if not matches:
            require(size == 0, 'unknown nonempty input')
            continue
        require(len(matches) == 1 and matches[0]['size'] == size, 'input section mismatch')
        key = (path, matches[0]['index'])
        require(key not in placements, 'duplicate placement')
        placements[key] = vma
    require(pending is None, 'unfinished map')
    global_defs = {}
    for path, e in objects.items():
        for name, sym in sum(e.symbol_tables.values(), []):
            if name and sym['ndx'] and (sym['bind'] in (1, 2)):
                global_defs.setdefault(name, []).append((path, sym))

    def symbol_value(path, name, sym):
        if sym['bind'] in (1, 2) and name in global_defs:
            choices = global_defs[name]
            strong = [v for v in choices if v[1]['bind'] == 1]
            require(len(strong) <= 1, 'multiple strong symbol')
            if strong:
                path, sym = strong[0]
            else:
                require(len(choices) == 1, 'ambiguous weak resolution')
                path, sym = choices[0]
        if sym['ndx'] == 0:
            require(name in {'__StackTop', '__StackLimit', '__data_load', '__data_start', '__data_end', '__bss_start', '__bss_end'} and name in final.symbols and (final.symbols[name]['ndx'] != 0), 'undefined non-linker symbol ' + name)
            return final.symbols[name]['value']
        if sym['ndx'] == 65521:
            return sym['value']
        require((path, sym['ndx']) in placements, 'unmapped symbol ' + name)
        return placements[path, sym['ndx']] + sym['value']

    def at(addr, n):
        sections = [s for s in final.sections if s['flags'] & 2 and s['type'] != 8 and (s['addr'] <= addr) and (addr + n <= s['addr'] + s['size'])]
        require(len(sections) == 1, 'ambiguous final interval')
        s = sections[0]
        return final.raw[s['offset'] + addr - s['addr']:s['offset'] + addr - s['addr'] + n]
    rows = []
    counts = {}
    total = 0
    for path, e in objects.items():
        relocs = {}
        for rs in e.sections:
            if rs['type'] != 9:
                continue
            source = e.sections[rs['info']]
            if not source['flags'] & 2:
                continue
            require(rs['entsize'] == 8 and rs['size'] % 8 == 0, 'relocation table')
            for off in range(rs['offset'], rs['offset'] + rs['size'], 8):
                where, info = e.unpack('<II', off)
                kind = info & 255
                require(kind in (2, 10, 30), 'unsupported allocated relocation')
                name, sym = e.symbol_tables[rs['link']][info >> 8]
                relocs.setdefault(source['index'], []).append((where, kind, name, sym))
        for sec in e.sections:
            if not sec['flags'] & 2 or not sec['size']:
                continue
            require((path, sec['index']) in placements, 'missing allocated input')
            vma = placements[path, sec['index']]
            if sec['type'] == 8:
                require(not relocs.get(sec['index']), 'NOBITS relocation')
                continue
            raw = e.bytes(sec)
            actual = at(vma, len(raw))
            covered = set()
            for off, kind, name, sym in relocs.get(sec['index'], []):
                require(0 <= off <= len(raw) - 4 and (not covered.intersection(range(off, off + 4))), 'relocation overlap/bounds')
                covered.update(range(off, off + 4))
                value = symbol_value(path, name, sym)
                if kind == 2:
                    expected = value + struct.unpack_from('<I', raw, off)[0] & 4294967295
                    require(struct.unpack_from('<I', actual, off)[0] == expected, 'ABS32 mismatch ' + name)
                else:
                    require(value & 1, 'non-Thumb branch target ' + name)
                    addend = branch(raw[off:off + 4], kind)
                    encoded = branch(actual[off:off + 4], kind)
                    require(encoded == (value & ~1) + addend - (vma + off), 'branch destination mismatch ' + name)
                counts[kind] = counts.get(kind, 0) + 1
            require(all((a == b for i, (a, b) in enumerate(zip(raw, actual)) if i not in covered)), 'unexplained input-byte change ' + path + ' ' + sec['name'])
            total += len(raw)
            rows.append({'object': Path(path).name, 'section': sec['name'], 'vma': vma, 'size': len(raw), 'relocations': len(relocs.get(sec['index'], []))})
    return {'status': 'EXPLORATORY_RELOCATION_BYTES_PASS', 'elf_sha256': hashlib.sha256(final.raw).hexdigest(), 'relocations': counts, 'allocated_payload_bytes': total, 'sections': rows, 'stack_bound_transferred': False, 'limits': 'Does not establish whole image geometry, complete output coverage, machine CFG, callback integrity, stack bounds or hardware acceptance.'}
