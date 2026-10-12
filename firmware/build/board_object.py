#!/usr/bin/env python3
"""Check a data-only ARM board object against explicit JSON. No linking or flashing."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

GENERATOR_SHA = '0b5d6ba56603e908d7f0b079e3319863f1f7733c97324644377a3e46244f9f27'
COMMENT = bytes.fromhex('004743433a202831353a31342e322e72656c312d31292031342e322e3120323032343131313900')
ATTRIBUTES = bytes.fromhex('412c000000616561626900012200000005372d4d00060a074d09021204140115011703180119011a021e042201')
NAMES = ['', '.text', '.data', '.bss', '.rodata.mt_board', '.rel.rodata.mt_board',
         '.rodata.board_serial', '.rodata.board_product', '.rodata.board_manufacturer',
         '.comment', '.note.GNU-stack', '.ARM.attributes', '.symtab', '.strtab', '.shstrtab']

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def generator():
    path = Path(__file__).resolve().with_name('board_config.py')
    require(sha(path.read_bytes()) == GENERATOR_SHA, 'generator hash mismatch')
    spec = importlib.util.spec_from_file_location('pinned_board_config', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(sha(path.read_bytes()) == GENERATOR_SHA, 'generator changed during import')
    return module

def validate(raw, input_raw):
    """Return structural evidence for bytes; input_raw must be original JSON bytes.

    Deliberately accepts only the current GCC14 -Os data-section profile.
    This is not proof of compilation provenance, final-image safety or stack use.
    """
    config = generator()
    data = config.parse_board(input_raw)
    require(type(raw) is bytes and 52 <= len(raw) <= 16384, 'object size out of bounds')
    def unpack(fmt, offset):
        require(0 <= offset <= len(raw) - struct.calcsize(fmt), 'ELF read out of bounds')
        return struct.unpack_from(fmt, raw, offset)
    h = unpack('<16sHHIIIIIHHHHHH', 0)
    require(h[0] == b'\x7fELF\x01\x01\x01' + bytes(9), 'ELF identification mismatch')
    require((h[1], h[2], h[3], h[4], h[5], h[7], h[8], h[9], h[10], h[11], h[12], h[13]) ==
            (1, 40, 1, 0, 0, 0x05000000, 52, 0, 0, 40, 15, 14), 'ELF header/profile mismatch')
    table = h[6]
    require(table >= 52 and table % 4 == 0 and table + 600 == len(raw), 'section table geometry mismatch')
    sections = [unpack('<10I', table + 40*i) for i in range(15)]
    require(sections[0] == (0,)*10, 'null section must be zero')
    def payload(index):
        s = sections[index]
        require(s[1] != 8, 'NOBITS has no payload')
        require(52 <= s[4] <= table and s[5] <= table - s[4], 'section payload out of bounds')
        return raw[s[4]:s[4]+s[5]]
    def string(blob, offset):
        require(0 <= offset < len(blob), 'string offset out of bounds')
        end = blob.find(b'\0', offset)
        require(end >= 0, 'unterminated string')
        return blob[offset:end].decode('ascii')
    shstrings = payload(14)
    require(shstrings[:1] == b'\0' and shstrings[-1:] == b'\0', 'invalid section string table')
    require([string(shstrings, s[0]) for s in sections] == NAMES, 'section names/order mismatch')
    # (type, flags, link, info, alignment, entry size); no unrecognized metadata.
    profiles = [(1,6,0,0,2,0), (1,3,0,0,1,0), (8,3,0,0,1,0), (1,2,0,0,4,0),
                (9,64,12,4,4,8), (1,2,0,0,1,0), (1,2,0,0,1,0), (1,2,0,0,1,0),
                (1,48,0,0,1,1), (1,0,0,0,1,0), (0x70000003,0,0,0,1,0),
                (2,0,13,16,4,16), (3,0,0,0,1,0), (3,0,0,0,1,0)]
    occupied = bytearray(len(raw))
    occupied[:52] = b'\1'*52
    occupied[table:] = b'\1'*600
    for i, expected in enumerate(profiles, 1):
        s = sections[i]
        require((s[1],s[2],s[6],s[7],s[8],s[9]) == expected and s[3] == 0,
                'section profile mismatch: ' + NAMES[i])
        require(52 <= s[4] <= table and s[4] % s[8] == 0, 'section offset/alignment mismatch')
        if s[1] != 8:
            payload(i)
            require(not any(occupied[s[4]:s[4]+s[5]]), 'overlapping section payloads')
            occupied[s[4]:s[4]+s[5]] = b'\1'*s[5]
    require(all(used or byte == 0 for used, byte in zip(occupied, raw)), 'nonzero unclaimed file bytes')
    require(all(sections[i][5] == 0 for i in (1,2,3,10)), 'nonempty code/writable/stack section')
    require(sections[4][5] == 48 and sections[5][5] == 24 and sections[12][5] == 272,
            'board/relocation/symbol table size mismatch')
    require(payload(9) == COMMENT and payload(11) == ATTRIBUTES, 'compiler metadata/ARM attributes mismatch')
    strings = payload(13)
    require(strings[:1] == b'\0' and strings[-1:] == b'\0', 'invalid symbol strings')
    symbols = []
    for off in range(0, 272, 16):
        n,v,z,info,other,ndx = struct.unpack_from('<IIIBBH', payload(12), off)
        symbols.append((string(strings,n),v,z,info,other,ndx))
    require(symbols[0] == ('',0,0,0,0,0), 'null symbol mismatch')
    filename,v,z,info,other,ndx = symbols[1]
    require(filename.endswith('.c') and 1 <= len(filename) <= 255 and
            all(32 <= ord(c) <= 126 and c not in '/\\' for c in filename) and
            (v,z,info,other,ndx) == (0,0,4,0,65521), 'FILE symbol mismatch')
    expected = [('',0,0,3,0,i) for i in (1,2,3,4)]
    expected += [('$d',0,0,0,0,4)]
    expected += [('board_'+name,0,sections[i][5],1,0,i)
                 for name,i in [('manufacturer',8),('product',7),('serial',6)]]
    expected += [('',0,0,3,0,i) for i in (6,7,8,9,10,11)]
    expected += [('mt_board',0,48,17,0,4)]
    require(symbols[2:] == expected, 'symbol bindings/geometry mismatch')
    relocations = [struct.unpack_from('<II', payload(5), off) for off in (0,8,16)]
    require(relocations == [(36,(12<<8)|2),(40,(11<<8)|2),(44,(10<<8)|2)],
            'board string relocations mismatch')
    board, usb = data['board'], data['board']['usb']
    values = [board[k] for k in ('verified_hse_hz','clock_poll_budget','usb_irq_priority','firmware_build')]
    values += [0 if board['hse_mode'] == 'crystal' else 1, board['require_dtr'], 1]
    values += [usb[k] for k in ('vid','pid','bcd_device','bus_power_ma')]
    expected_board = struct.pack('<7I4H3I', *values, 0, 0, 0)
    require(payload(4) == expected_board, 'board scalar bytes or relocation addends mismatch')
    for name,i in [('manufacturer',8),('product',7),('serial',6)]:
        require(payload(i) == usb[name].encode('ascii') + b'\0', 'string bytes mismatch: ' + name)
    return {'verdict':'BOARD_OBJECT_STRUCTURE_PASS', 'object_sha256':sha(raw),
            'input_sha256':sha(input_raw), 'generator_sha256':GENERATOR_SHA,
            'input_kind':data['input_kind'], 'readonly_bytes':48+sum(sections[i][5] for i in (6,7,8)),
            'relocations':3, 'hardware_verified':False, 'flash_approved':False,
            'limits':'Data-only object check; not compilation provenance, BOOT audit, final-image or stack acceptance.'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--object', required=True)
    parser.add_argument('--input', required=True)
    args = parser.parse_args()
    try:
        config = generator()
        paths = [config.strict_path(p) for p in (args.object, args.input)]
        require(paths[0] != paths[1], 'object and JSON must be distinct')
        require(paths[0].stat().st_size <= 16384 and paths[1].stat().st_size <= 8192, 'input too large')
        before = [p.read_bytes() for p in paths]
        result = validate(*before)
        require(before == [p.read_bytes() for p in paths], 'input changed during validation')
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, UnicodeError, struct.error) as error:
        print(json.dumps({'verdict':'REJECTED', 'error':str(error), 'hardware_verified':False,
                          'flash_approved':False}, sort_keys=True))
        return 1

if __name__ == '__main__':
    sys.exit(main())
