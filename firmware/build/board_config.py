#!/usr/bin/env python3
"""Validate explicit board data and generate one const definition; never verify hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

HSE_HZ = {4500000, 4800000, 6000000, 7200000, 8000000,
          9000000, 9600000, 12000000, 14400000, 16000000}
ATTESTATIONS = {'physical_mcu_and_memory', 'hse_frequency_and_mode',
                'fixed_pullup_topology', 'usb_bus_power_budget',
                'usb_vid_pid_authorized', 'stable_unique_physical_serial'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def keys(value, expected, name):
    require(type(value) is dict and set(value) == set(expected), name + ': exact fields required')

def integer(value, low, high, name):
    require(type(value) is int and low <= value <= high, name + ': integer out of range')

def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON field: ' + key)
        result[key] = value
    return result

def no_number(value):
    raise ValueError('non-integer JSON number forbidden: ' + value)

def parse_board(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 8192, 'input must be 1..8192 bytes')
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_float=no_number, parse_constant=no_number)
    keys(data, {'schema_version', 'input_kind', 'board', 'caller_attestations'}, 'root')
    integer(data['schema_version'], 1, 1, 'schema_version')
    kind = data['input_kind']
    require(type(kind) is str and kind in ('caller_board', 'synthetic_fixture'), 'unknown input_kind')
    board = data['board']
    keys(board, {'verified_hse_hz', 'clock_poll_budget', 'usb_irq_priority', 'firmware_build',
                 'hse_mode', 'require_dtr', 'verified_fixed_pullup', 'usb'}, 'board')
    integer(board['verified_hse_hz'], 4000000, 16000000, 'verified_hse_hz')
    require(board['verified_hse_hz'] in HSE_HZ, 'HSE cannot produce exact profile PLL72')
    for name, low, high in [('clock_poll_budget', 1, 1000000), ('usb_irq_priority', 0, 15),
                            ('firmware_build', 0, 4294967295), ('require_dtr', 0, 1),
                            ('verified_fixed_pullup', 1, 1)]:
        integer(board[name], low, high, name)
    require(type(board['hse_mode']) is str and board['hse_mode'] in ('crystal', 'bypass'), 'invalid hse_mode')
    usb = board['usb']
    keys(usb, {'vid', 'pid', 'bcd_device', 'bus_power_ma', 'manufacturer', 'product', 'serial'}, 'usb')
    for name, low, high in [('vid', 1, 65534), ('pid', 0, 65535),
                           ('bcd_device', 0, 39321), ('bus_power_ma', 2, 500)]:
        integer(usb[name], low, high, 'usb.' + name)
    require(all(((usb['bcd_device'] >> shift) & 15) <= 9 for shift in range(0, 16, 4)), 'invalid BCD nibble')
    require(usb['bus_power_ma'] % 2 == 0, 'USB bus power must be even mA')
    for name in ('manufacturer', 'product', 'serial'):
        text = usb[name]
        require(type(text) is str and 1 <= len(text) <= 31 and
                all(32 <= ord(c) <= 126 for c in text), 'invalid ASCII string: ' + name)
    require(all(c in '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz' for c in usb['serial']),
            'serial must be ASCII alphanumeric')
    attest = data['caller_attestations']
    keys(attest, ATTESTATIONS, 'caller_attestations')
    required = kind == 'caller_board'
    require(all(type(v) is bool and v is required for v in attest.values()),
            'caller assertions must all be true; synthetic assertions must all be false')
    return data

def generate(data):
    board, usb = data['board'], data['board']['usb']
    label = 'SYNTHETIC TEST FIXTURE: NEVER FLASH.' if data['input_kind'] == 'synthetic_fixture' else \
            'CALLER-ASSERTED BOARD DATA: NOT HARDWARE VERIFICATION OR FLASH APPROVAL.'
    lines = ['/* ' + label + ' */', '#include "mt_board_inputs.h"']
    for name in ('manufacturer', 'product', 'serial'):
        values = ', '.join(str(ord(c)) for c in usb[name]) + ', 0'
        lines.append('static const char board_' + name + '[] = {' + values + '};')
    lines += ['const mt_board_inputs mt_board = {']
    for name in ('verified_hse_hz', 'clock_poll_budget', 'usb_irq_priority', 'firmware_build'):
        lines.append('    .' + name + ' = ' + str(board[name]) + 'u,')
    mode = 'MT_CLOCK_CRYSTAL' if board['hse_mode'] == 'crystal' else 'MT_CLOCK_BYPASS'
    lines += ['    .hse_mode = ' + mode + ',',
              '    .require_dtr = ' + str(board['require_dtr']) + 'u,',
              '    .verified_fixed_pullup = 1u,', '    .usb = {']
    for name in ('vid', 'pid', 'bcd_device', 'bus_power_ma'):
        lines.append('        .' + name + ' = ' + str(usb[name]) + 'u,')
    for name in ('manufacturer', 'product', 'serial'):
        lines.append('        .' + name + ' = board_' + name + ',')
    return '\n'.join(lines + ['    }', '};', ''])

# Same deliberately narrow canonical-only contract as the object builder.
def strict_path(value, directory=False):
    raw = os.fspath(value)
    require(not any(c in raw for c in '\n\r:#$\\"\''), 'unsupported path character')
    path = Path(raw)
    require(path.is_absolute() and os.path.normpath(raw) == raw, 'absolute normalized path required')
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'symlink path forbidden')
    require(path.resolve(strict=True) == path, 'noncanonical path')
    require(stat.S_ISDIR(path.stat().st_mode) if directory else stat.S_ISREG(path.stat().st_mode), 'wrong input type')
    return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    script = strict_path(__file__)
    source = strict_path(args.input)
    require(source.stat().st_size <= 8192, 'input exceeds 8192 bytes')
    raw = source.read_bytes()
    data = parse_board(raw)
    generated = generate(data).encode('ascii')
    require(not any(c in args.out for c in '\n\r:#$\\"\''), 'unsupported output path character')
    out = Path(args.out)
    require(out.is_absolute() and os.path.normpath(args.out) == args.out, 'absolute normalized output required')
    require(not out.exists() and not out.is_symlink(), 'output must not exist')
    strict_path(out.parent, directory=True)
    for protected in (script.parents[2], source, Path(sys.executable).resolve().parent,
                      Path(sys.base_prefix).resolve()):
        require(not out.is_relative_to(protected) and not protected.is_relative_to(out),
                'output overlaps protected input')
    require(source.read_bytes() == raw, 'input changed before output')
    out.mkdir()
    record = {'status': 'RUNNING', 'input_kind': data['input_kind'], 'hardware_verified': False,
              'flash_approved': False, 'input_sha256': hashlib.sha256(raw).hexdigest(),
              'generator_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
              'caller_attestations': data['caller_attestations']}
    try:
        filename = 'TEST_ONLY_mt_board.c' if data['input_kind'] == 'synthetic_fixture' else 'CALLER_ASSERTED_mt_board.c'
        (out / filename).write_bytes(generated)
        require(source.read_bytes() == raw, 'input changed during generation')
        record.update(status='GENERATED_DATA_ONLY', source_file=filename,
                      source_sha256=hashlib.sha256(generated).hexdigest())
    except Exception as exc:
        record.update(status='FAILED', error=str(exc))
        raise
    finally:
        (out / 'RESULTS.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'status': record['status'], 'input_kind': data['input_kind'], 'output': str(out)}))

if __name__ == '__main__':
    main()
