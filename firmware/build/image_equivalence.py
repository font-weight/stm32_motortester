#!/usr/bin/env python3
"""Check narrow final ELF byte/layout equivalence. NOT a complete stack-proof gate."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

REFERENCE_SHA = 'e720fcf41cd06a4eb77c47bf579d8289b0c13e44143fc2b4e3322aaaeddf923d'
GENERATOR_SHA = '0b5d6ba56603e908d7f0b079e3319863f1f7733c97324644377a3e46244f9f27'
ATTRIBUTES = bytes.fromhex('412a000000616561626900012000000005372d4d00060a074d0902120414011501170318011a021e042201')
NAMES = ['', '.isr_vector', '.text', '.data', '.bss', '.unsupported_stubs',
         '.ARM.attributes', '.symtab', '.strtab', '.shstrtab']

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def digest(records):
    return sha(json.dumps(records, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii'))

def dependencies():
    root = Path(__file__).resolve().parent
    p = root/'board_config.py'
    require(sha(p.read_bytes()) == GENERATOR_SHA, 'generator hash mismatch')
    spec = importlib.util.spec_from_file_location('pinned_board_config', p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(sha(p.read_bytes()) == GENERATOR_SHA, 'generator changed during import')
    raw = (root/'stack_reference.json').read_bytes()
    require(sha(raw) == REFERENCE_SHA, 'reference hash mismatch')
    return module, json.loads(raw)

def validate(raw, input_raw, stack_bytes):
    config, ref = dependencies()
    data = config.parse_board(input_raw)
    require(type(stack_bytes) is int and 3064 <= stack_bytes <= 8104 and stack_bytes % 8 == 0,
            'explicit stack reservation must be aligned and within conditional geometry')
    require(type(raw) is bytes and 452 <= len(raw) <= 1048576, 'ELF size out of bounds')
    def unpack(fmt, off):
        require(0 <= off <= len(raw)-struct.calcsize(fmt), 'ELF read out of bounds')
        return struct.unpack_from(fmt, raw, off)
    h = unpack('<16sHHIIIIIHHHHHH', 0)
    require(h[0] == b'\x7fELF\x01\x01\x01' + bytes(9), 'ELF identification mismatch')
    require((h[1],h[2],h[3],h[4],h[5],h[7],h[8],h[9],h[10],h[11],h[12],h[13]) ==
            (2,40,1,0x08008155,52,0x05000200,52,32,5,40,10,9), 'ELF header/profile mismatch')
    sht = h[6]
    require(sht >= 212 and sht % 4 == 0 and sht+400 == len(raw), 'section table geometry mismatch')
    ss = [unpack('<10I', sht+40*i) for i in range(10)]
    require(ss[0] == (0,)*10, 'null section mismatch')
    def payload(i):
        s = ss[i]
        require(s[1] != 8 and 212 <= s[4] <= sht and s[5] <= sht-s[4], 'section payload bounds')
        return raw[s[4]:s[4]+s[5]]
    def string(blob, off):
        require(0 <= off < len(blob), 'string offset out of bounds')
        end = blob.find(b'\0', off)
        require(end >= 0, 'unterminated string')
        return blob[off:end].decode('ascii')
    shstrings = payload(9)
    require(shstrings[:1] == b'\0' and shstrings[-1:] == b'\0', 'invalid section strings')
    require([string(shstrings,s[0]) for s in ss] == NAMES, 'section names/order mismatch')
    usb, board = data['board']['usb'], data['board']
    address = 0x0800825c
    starts = {}
    tail = b''
    for name in ('serial','product','manufacturer'):
        starts[name] = address + len(tail)
        tail += usb[name].encode('ascii') + b'\0'
    tail += bytes((-len(tail)) % 4)
    data_lma = address + len(tail)
    image_end = data_lma + 128
    require(image_end <= 0x08010000, 'flash overflow')
    # Exact output section roles, including empty executable stub bookkeeping.
    profiles = [(1,2,0x08000000,236,0,0,512,0),
                (1,6,0x080000ec,data_lma-0x080000ec,0,0,4,0),
                (1,3,0x20000000,128,0,0,4,0),
                (8,3,0x20000080,12248,0,0,8,0),
                (1,6,image_end,0,0,0,4,0),
                (0x70000003,0,0,len(ATTRIBUTES),0,0,1,0)]
    used = bytearray(len(raw)); used[:212] = b'\1'*212; used[sht:] = b'\1'*400
    for i,s in enumerate(ss[1:],1):
        if i <= 6:
            require((s[1],s[2],s[3],s[5],s[6],s[7],s[8],s[9]) == profiles[i-1],
                    'section profile mismatch: '+NAMES[i])
        elif i == 7:
            require((s[1],s[2],s[3],s[6],s[8],s[9]) == (2,0,0,8,4,16) and
                    0 < s[5] <= 32000 and s[5] % 16 == 0 and s[7] <= s[5]//16, 'symbol table profile')
        else:
            require((s[1],s[2],s[3],s[6],s[7],s[8],s[9]) == (3,0,0,0,0,1,0), 'string table profile')
        require(s[4] % s[8] == 0, 'section alignment mismatch')
        if s[1] != 8:
            payload(i)
            require(not any(used[s[4]:s[4]+s[5]]), 'overlapping file sections')
            used[s[4]:s[4]+s[5]] = b'\1'*s[5]
    require(all(u or b == 0 for u,b in zip(used,raw)), 'nonzero unclaimed file bytes')
    require(payload(6) == ATTRIBUTES, 'ARM attributes mismatch')
    ph = [unpack('<8I',52+32*i) for i in range(5)]
    require(ss[1][4] == 0x10000 and ss[2][4] == 0x100ec and ss[3][4] == 0x20000 and
            ss[4][4] == 0x30080 and ss[5][4] == 0x20000+(image_end & 0xffff), 'section file placement mismatch')
    expected_ph = [(1,0x10000,0x08000000,0x08000000,data_lma-0x08000000,data_lma-0x08000000,5,0x10000),
                   (1,0x20000,0x20000000,data_lma,128,128,6,0x10000),
                   (1,ss[5][4],image_end,image_end,0,0,5,0x10000),
                   (1,0x80,0x20000080,image_end,0,12248,6,0x10000),
                   (0x6474e551,0,0,0,0,0,6,16)]
    require(ph == expected_ph, 'program headers/load mapping mismatch')
    def at(addr,n):
        matches = [i for i,s in enumerate(ss) if s[1] != 8 and s[2]&2 and s[3] <= addr and addr+n <= s[3]+s[5]]
        require(len(matches) == 1, 'ambiguous loaded bytes')
        i = matches[0]; return payload(i)[addr-ss[i][3]:addr-ss[i][3]+n]
    for region in ref['immutable_loaded_regions']:
        require(sha(at(region['vma'],region['length'])) == region['sha256'], 'immutable region mismatch: '+region['label'])
    require(at(0x080081ac,4) == struct.pack('<I',data_lma), 'reset data-load literal mismatch')
    values = [board[k] for k in ('verified_hse_hz','clock_poll_budget','usb_irq_priority','firmware_build')]
    values += [0 if board['hse_mode'] == 'crystal' else 1, board['require_dtr'], 1]
    values += [usb[k] for k in ('vid','pid','bcd_device','bus_power_ma')]
    values += [starts[k] for k in ('manufacturer','product','serial')]
    require(at(0x0800822c,data_lma-0x0800822c) == struct.pack('<7I4H3I',*values)+tail, 'board bytes/pointers/padding mismatch')
    names = payload(8)
    require(names[:1] == b'\0' and names[-1:] == b'\0', 'invalid symbol names')
    symbols = []
    for i,off in enumerate(range(0,ss[7][5],16)):
        n,v,z,info,other,ndx = struct.unpack_from('<IIIBBH',payload(7),off)
        require((info>>4 == 0) == (i < ss[7][7]), 'symbol local boundary mismatch')
        require(ndx < len(ss) or ndx == 65521, 'unsupported symbol section index')
        sec = NAMES[ndx] if 0 < ndx < len(ss) else {0:'SHN_UNDEF',65521:'SHN_ABS'}[ndx]
        symbols.append([string(names,n),v,z,info&15,info>>4,other,sec])
    require(symbols[0] == ['',0,0,0,0,0,'SHN_UNDEF'], 'null symbol mismatch')
    require(not any(s[6]=='SHN_UNDEF' for s in symbols[1:]), 'undefined symbol')
    for name,value in [('__boot_stack_bytes',stack_bytes),('__StackLimit',0x20005000-stack_bytes),('__data_load',data_lma)]:
        found = [s for s in symbols if s[0] == name]
        require(len(found) == 1 and found[0] == [name,value,0,0,1,0,'SHN_ABS'], 'variable linker symbol mismatch: '+name)
    extra = []
    for name in ('manufacturer','product','serial'):
        row = ['board_'+name,starts[name],len(usb[name])+1,1,0,0,'.text']
        mapping = ['$d',starts[name],0,0,0,0,'.text']
        require(symbols.count(row) == 1 and symbols.count(mapping) == 1, 'board symbol/mapping mismatch')
        extra += [row,mapping]
    functions = sorted(s for s in symbols if s[3] == 2)
    require(len(functions) == ref['function_symbol_multiset']['count'] and
            digest(functions) == ref['function_symbol_multiset']['sorted_records_sha256'], 'function multiset mismatch')
    immutable = []
    for s in symbols:
        if s in extra or s[0] in ('__boot_stack_bytes','__StackLimit','__data_load'):
            continue
        if s[3] == 4:
            require(s[1:] == [0,0,4,0,0,'SHN_ABS'], 'FILE metadata mismatch')
            continue
        if s[3] == 3 and s[6] == '.unsupported_stubs':
            require(s == ['',image_end,0,3,0,0,'.unsupported_stubs'], 'stub SECTION metadata mismatch')
            continue
        immutable.append(s)
    require(len(immutable) == ref['immutable_symbol_multiset']['count'] and
            digest(sorted(immutable)) == ref['immutable_symbol_multiset']['sorted_records_sha256'], 'immutable symbol multiset mismatch')
    return {'verdict':'ELF_BYTE_LAYOUT_EQUIVALENCE_PASS', 'elf_sha256':sha(raw), 'input_sha256':sha(input_raw),
            'reference_sha256':REFERENCE_SHA, 'stack_bytes':stack_bytes, 'data_lma':data_lma,
            'input_kind':data['input_kind'], 'hardware_verified':False,'flash_approved':False,
            'stack_bound_transferred':False,
            'limits':'Requires ordered-object/board provenance, linker-map and original BOOT audits; this layer alone does not transfer the conditional stack proof.'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--stack-bytes', required=True, type=int)
    args = parser.parse_args()
    try:
        config,_ = dependencies()
        paths = [config.strict_path(p) for p in (args.elf,args.input)]
        require(paths[0] != paths[1], 'ELF and JSON must be distinct')
        require(paths[0].stat().st_size <= 1048576 and paths[1].stat().st_size <= 8192, 'input too large')
        before = [p.read_bytes() for p in paths]
        result = validate(*before,args.stack_bytes)
        require(before == [p.read_bytes() for p in paths], 'input changed during validation')
        print(json.dumps(result,sort_keys=True)); return 0
    except (ValueError,OSError,UnicodeError,struct.error) as error:
        print(json.dumps({'verdict':'REJECTED','error':str(error),'stack_bound_transferred':False,
                          'hardware_verified':False,'flash_approved':False},sort_keys=True)); return 1

if __name__ == '__main__':
    sys.exit(main())
