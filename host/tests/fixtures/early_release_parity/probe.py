"""Шесть qualified raw-peer сценариев с неизменным BUSY oracle.

Отрицательный запуск использует отдельную точную копию старого emulator source;
он обязан вернуть exit1, а не превращать старый ACK в успешную проверку parity.
"""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_value(value):
    if isinstance(value, bytes):
        return {'bytes_hex': value.hex()}
    raise TypeError('Неподдерживаемое поле trace: ' + type(value).__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--emulator', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = args.emulator.resolve(strict=True)
    args.out.mkdir(parents=False, exist_ok=False)
    tests = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(tests))
    import test_early_release_parity as checks
    spec = importlib.util.spec_from_file_location('motortester._early_release_probe', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    before_hash = digest(source)
    spec.loader.exec_module(module)
    checks.DeviceEmulator = module.DeviceEmulator
    case = unittest.TestCase()
    observations, failures = [], []
    for mode in range(3):
        for edge in range(2):
            p = checks.setup(case, mode, edge, prefix=1)
            p.now = 1099
            before = asdict(p.device.snapshot())
            packet, result = p.command(7, p.release)
            after = asdict(p.device.snapshot())
            tail, frames = p.drain()
            replies = [frame for frame in frames if frame.request == p.request - 1]
            valid = (len(replies) == 1 and replies[0].kind == 255
                     and replies[0].payload[:2] == b'\7\2'
                     and after['held_samples'] and after['hold_started_ms'] is None
                     and p.device.snapshot().state == 6
                     and p.device.snapshot().hold_started_ms == 1099)
            retained = bytes(p.device._samples) == p.samples
            observation = dict(mode=mode, edge=edge, prefix=1, accepted=result.accepted_prefix,
                               diagnostics=checks.diagnostics(result), release_hex=packet.hex(),
                               before=before, after_command=after, final=asdict(p.device.snapshot()),
                               sample_crc32=checks.raw.crc(p.samples), sample_retained=retained,
                               sample_sha256=hashlib.sha256(p.samples).hexdigest(),
                               tail_hex=tail.hex(),
                               frames=[dict(kind=f.kind, request=f.request, sequence=f.sequence,
                                            session=f.session, payload_hex=f.payload.hex()) for f in frames],
                               busy_oracle_passed=valid and retained)
            observations.append(observation)
            if not observation['busy_oracle_passed']:
                failures.append((mode, edge))
            print(f'mode={mode} edge={edge} BUSY_ORACLE={observation["busy_oracle_passed"]} '
                  f'replies={[f.kind for f in replies]} retained={retained}')
    record = dict(source=str(source), source_before_sha256=before_hash,
                  source_after_sha256=digest(source), optimize=sys.flags.optimize,
                  observations=observations, tests_run=6, failures=len(failures),
                  errors=0, skips=0)
    (args.out / 'observations.json').write_text(json.dumps(record, indent=2, default=json_value) + '\n')
    if digest(source) != before_hash:
        raise RuntimeError('Проверяемый источник изменился во время запуска')
    print(f'RESULT tests=6 failures={len(failures)} errors=0 skips=0')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
