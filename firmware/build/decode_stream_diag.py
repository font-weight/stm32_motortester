#!/usr/bin/env python3
"""Offline ABI-v1 RAM record decoder. No device access or physical qualification."""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import struct

FIELDS = '''magic version bytes firmware_build phase flags generation
step_count step_body_max_cycles step_gap_max_cycles tx_count tx_max_cycles
poll_count driver_observed_poll_gap_us poll_gap_max_cycles copy_count copy_max_us
queue_high_water first_runtime_result first_local_error first_acquisition_reason
first_adc_fault first_epoch final_runtime_result final_core_state final_epoch
final_adc_state final_adc_owned final_hardware_off start_cycle last_cycle freeze_cycle
nonce_low nonce_high'''.split()
FLAGS = {1: 'dwt_unusable', 2: 'count_saturated', 4: 'first_fault',
         8: 'replacement_hello_partial_step', 16: 'invalid_cycle_source'}


def integer(bits, nonzero=False):
    def parse(value):
        try:
            n = int(value, 16 if value.lower().startswith('0x') else 10)
        except ValueError as exc:
            raise argparse.ArgumentTypeError('expected decimal or 0x integer') from exc
        if n < int(nonzero) or n >= 1 << bits:
            raise argparse.ArgumentTypeError('integer outside allowed range')
        return n
    return parse


def decode(first, second, build, generation, nonce):
    result = {'schema': 'stream-diag-v1-offline', 'accepted_frozen_record': False,
              'physical_identity_verified': False, 'timing_qualified': False,
              'errors': [], 'warnings': [], 'raw': None}
    for label, data in (('first', first), ('second', second)):
        result[label + '_sha256'] = hashlib.sha256(data).hexdigest() if len(data) == 136 else None
        if len(data) != 136:
            result['errors'].append(label + '_size_not_136')
    if result['errors']:
        return result
    d = dict(zip(FIELDS, struct.unpack('<34I', first)))
    result['raw'] = d
    if first != second:
        result['errors'].append('repeated_dump_mismatch')
    expected = {'magic': 0x4d544447, 'version': 1, 'bytes': 136, 'phase': 3,
                'firmware_build': build, 'generation': generation}
    for key, value in expected.items():
        if d[key] != value:
            result['errors'].append(key + '_mismatch')
    actual_nonce = d['nonce_low'] | d['nonce_high'] << 32
    result['nonce_hex'] = f'{actual_nonce:016x}'
    if actual_nonce != nonce:
        result['errors'].append('nonce_mismatch')
    if d['flags'] & ~31:
        result['errors'].append('undefined_flags')
    if d['final_adc_owned'] != 0 or d['final_hardware_off'] != 1 or d['final_adc_state'] != 0:
        result['errors'].append('frozen_without_quiescence')
    if d['freeze_cycle'] != d['last_cycle']:
        result['errors'].append('freeze_cycle_mismatch')
    if d['queue_high_water'] > 16:
        result['errors'].append('queue_high_water_out_of_range')
    for key, maximum in (('first_local_error', 7), ('first_acquisition_reason', 6),
                         ('first_adc_fault', 8)):
        if d[key] > maximum:
            result['errors'].append(key + '_out_of_range')
    if d['first_runtime_result'] not in (*range(6), 0xffffffff):
        result['errors'].append('first_runtime_result_out_of_range')
    if d['flags'] & 8:
        if d['final_core_state'] not in (1, 2) or d['final_runtime_result'] != 0xffffffff:
            result['errors'].append('replacement_terminal_mismatch')
    elif d['final_core_state'] not in (7, 8) or d['final_runtime_result'] not in range(6):
        result['errors'].append('ordinary_terminal_mismatch')
    first_fields = ('first_runtime_result', 'first_local_error',
                    'first_acquisition_reason', 'first_adc_fault', 'first_epoch')
    if not d['flags'] & 4:
        if any(d[key] for key in first_fields):
            result['errors'].append('cause_fields_without_fault_flag')
    elif not (d['first_local_error'] or d['first_acquisition_reason'] or d['first_adc_fault']
              or d['first_runtime_result'] in (3, 4, 5)):
        result['errors'].append('fault_flag_without_cause')
    if not d['flags'] & 8 and d['step_count'] == 0:
        result['errors'].append('ordinary_freeze_without_step')
    if d['final_runtime_result'] in (3, 4, 5) and not d['flags'] & 4:
        result['errors'].append('final_fault_without_first_fault')
    if d['flags'] & 1:
        result['errors'].append('unavailable_dwt_cannot_freeze')
    counts = ('step_count', 'tx_count', 'poll_count', 'copy_count')
    if d['flags'] & 2 and not any(d[key] == 0xffffffff for key in counts):
        result['errors'].append('saturation_without_saturated_counter')
    if d['flags'] == 0 and d['final_runtime_result'] == 0 and d['final_core_state'] == 7 and d['tx_count'] == 0:
        result['errors'].append('normal_stop_without_tx_observation')
    result['warnings'] = [label for bit, label in FLAGS.items() if d['flags'] & bit]
    result['observed_metrics'] = {
        'step_body_cycles': d['step_body_max_cycles'] if d['step_count'] else None,
        'step_stamp_gap_cycles': d['step_gap_max_cycles'] if d['step_count'] >= 2 else None,
        'tx_bracket_cycles': d['tx_max_cycles'] if d['tx_count'] else None,
        'driver_poll_gap_us': d['driver_observed_poll_gap_us'] if d['poll_count'] else None,
        'poll_gap_cycles': d['poll_gap_max_cycles'] if d['poll_count'] >= 2 else None,
        'copy_validation_us': d['copy_max_us'] if d['copy_count'] else None}
    result['accepted_frozen_record'] = not result['errors']
    result['normal_stop_record_shape'] = (not result['errors'] and d['flags'] == 0
        and d['final_runtime_result'] == 0 and d['final_core_state'] == 7)
    result['limits'] = ['Local dumps and expected values do not prove flashed image identity.',
        'HOST END/STOP_ACK and actual USB delivery require separate evidence.',
        'Cycles are modulo uint32; no elapsed-time or calibrated clock inference.',
        'Debugger halt and intervals below one cycle-counter wrap are external assumptions.',
        'TX bracket is not the qualified transaction or exact IRQ-off duration.',
        'A record with warnings remains evidence of a failed or limited observation.']
    return result


def read_dump(path):
    # Local regular files only. No FIFO/device access; no symlink final component.
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError('dump must be a regular file, not a link or special file')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError('dump changed or is not a regular file')
        data = stream.read(137)
        after = os.fstat(stream.fileno())
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('dump changed while reading')
        return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('first', type=Path)
    ap.add_argument('second', type=Path)
    ap.add_argument('--build', type=integer(32), required=True)
    ap.add_argument('--generation', type=integer(32, True), required=True)
    ap.add_argument('--nonce', type=integer(64, True), required=True)
    a = ap.parse_args()
    try:
        # Read at most one extra byte to reject oversized files without allocating them.
        first = read_dump(a.first)
        second = read_dump(a.second)
    except OSError as exc:
        ap.error(str(exc))
    result = decode(first, second, a.build, a.generation, a.nonce)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['accepted_frozen_record'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
