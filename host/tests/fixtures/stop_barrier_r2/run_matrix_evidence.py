"""Сырые трассы и fail-closed отрицательный контроль опубликованного c44."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

EXPECTED_C44 = {
    '__init__.py': 'a9e7bc3ad035367282bcc6f940c95cf1cde2c6c1cee0bb4c3ead1f6eae845645',
    'wire.py': 'a8fa8edf58c22e87994674ad23fc654e4da93e229cfe21d482dbec3116bc8230',
    'device_emulator.py': 'e9f726bb4ed6591ae6810f351de5f7429e42a4316e8a7c109f9835d77ad1f9c6',
}


def source_hashes(root):
    return {name: hashlib.sha256((root / 'motortester' / name).read_bytes()).hexdigest()
            for name in EXPECTED_C44}


def legacy_expected(case, module, o):
    r, p = module.raw, o['peer']
    ack_frame = r.decode(o['ack'])
    next_tx = ack_frame.sequence + 1
    request = r.decode(o['command']).request
    window = o['before'].window_id
    name, kind = o['name'], o['kind']
    if name == 'valid_2':
        reply = r.packet(130, next_tx, request, r.CAPS)
    elif name == 'valid_5':
        reply = r.packet(133, next_tx, request, r.u(3) + b'\0' * 8)
    elif name == 'valid_6':
        reply = r.packet(134, next_tx, request, r.status(4, window=window))
    elif name == 'valid_7':
        reply = r.packet(135, next_tx, request, r.u(3) + r.u(window))
    else:
        error = 5 if name.startswith('unknown') else 6 if name.startswith(('length', 'reserved')) else 7 if kind == 7 else 2
        reply = r.packet(255, next_tx, request, bytes((kind, error, 0, 0)) + r.u(3))
    preceding = r.packet(130, ack_frame.sequence - 1, 4, r.CAPS) if o['caps'] and not o['count'] else b''
    tail = preceding + o['ack'][o['count']:] + reply + r.packet(134, next_tx + 1, 0, r.status(0, window=window))
    case.assertEqual(o['tail'], tail, f'Unexpected c44 bytes: {name}/{o["prefix"]}/{o["caps"]}')
    case.assertEqual(module.diagnostics(o['result']), ())
    case.assertEqual((o['after'].state, o['after'].reason, o['after'].negotiated, o['after'].stop_barrier), (4, 0, True, True))
    after = o['final']
    case.assertEqual((after.state, after.reason, after.negotiated, after.stop_barrier), (0, 0, True, False))
    case.assertEqual((after.config_id, after.config, after.generation, after.window_id, after.dropped_windows), (1, r.CONFIG, 3, window, 0))
    case.assertEqual((after.next_rx_sequence, after.last_request_id, after.next_tx_sequence),
                     (o['before'].next_rx_sequence + 1, o['before'].last_request_id + 1, next_tx + 2))
    case.assertEqual((after.control_descriptors, after.tx_size, after.rx_used), (0, 0, 0))
    case.assertFalse(after.held_samples)
    case.assertFalse(after.continuation)
    case.assertEqual(after.terminal, o['before'].terminal)
    case.assertEqual(after.tombstone, o['before'].tombstone)
    return tail


def json_value(value):
    if isinstance(value, bytes):
        return {'bytes_hex': value.hex()}
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('candidate', 'c44'), required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before = source_hashes(args.source_root)
    if args.mode == 'c44' and before != EXPECTED_C44:
        raise SystemExit('C44_SOURCE_HASH_MISMATCH')
    sys.path.insert(0, str(args.source_root.resolve()))
    test_path = Path(__file__).resolve().parents[2] / 'test_stop_barrier_conformance.py'
    spec = importlib.util.spec_from_file_location('stop_r2_evidence_tests', test_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from motortester import device_emulator
    if Path(device_emulator.__file__).resolve() != (args.source_root / 'motortester/device_emulator.py').resolve():
        raise SystemExit('WRONG_PRODUCTION_IMPORT')
    case = module.Checks()
    records = []
    expected_names = set(module.MATRIX_NAMES)
    case.assertEqual(len(expected_names), 96)
    mismatch = None
    if args.mode == 'c44':
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.StopBarrierMatrix)
        case.assertEqual(suite.countTestCases(), 96)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        # Каждое падение обязано быть ровно ожидаемым различием recovery-state.
        failed = {test._testMethodName for test, trace in result.failures}
        case.assertEqual(failed, expected_names)
        case.assertEqual((result.testsRun, len(result.failures), len(result.errors), len(result.skipped)), (96, 96, 0, 0))
        for test, trace in result.failures:
            case.assertIn('AssertionError: Tuples differ: (True, 4, 0) != (False, 5, 4)', trace)
        mismatch = {'tests_run': result.testsRun, 'expected_failures': len(result.failures),
                    'unexpected_errors': len(result.errors), 'skips': len(result.skipped),
                    'failed_ids': sorted(failed)}
    for name, _, _ in module.FORMS:
        for prefix in module.PREFIXES:
            for caps in (False, True):
                o = module.observe_matrix(case, name, prefix, caps)
                if args.mode == 'candidate':
                    case.candidate(o)
                    expected_tail = o['ack'][o['count']:] if o['count'] else b''
                else:
                    expected_tail = legacy_expected(case, module, o)
                records.append({'id': f'test_matrix_{name}_{prefix}_caps_{int(caps)}',
                                'events': o['peer'].events, 'before': asdict(o['before']),
                                'after_injection': asdict(o['after']), 'final': asdict(o['final']),
                                'expected_tail_hex': expected_tail.hex(), 'actual_tail_hex': o['tail'].hex()})
    case.assertEqual({record['id'] for record in records}, expected_names)
    case.assertEqual(source_hashes(args.source_root), before)
    document = {'fresh_timestamp_utc': datetime.now(timezone.utc).isoformat(),
                'mode': args.mode, 'source_root': str(args.source_root),
                'source_hashes_before_and_after': before, 'matrix_count': len(records),
                'mismatch_run': mismatch, 'traces': records}
    args.output.write_text(json.dumps(document, indent=2, default=json_value) + '\n')
    print(f'EXACT_{args.mode.upper()}_CASES={len(records)} RAW_TRACES={args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
