#!/usr/bin/env python3
"""Strict provisional STREAM timing declaration; never a qualification gate."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def no_number(value):
    raise ValueError('non-integer JSON number: ' + value)


def parse(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 4096, 'input must be 1..4096 bytes')
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_float=no_number, parse_constant=no_number)
    require(type(data) is dict and set(data) == {
        'schema_version', 'input_kind', 'declared_transaction_bound_ms',
        'source_commit', 'profile'}, 'exact root fields required')
    require(type(data['schema_version']) is int and data['schema_version'] == 1,
            'schema_version must be integer 1')
    require(data['input_kind'] == 'diagnostic_provisional', 'only diagnostic provisional input allowed')
    require(data['profile'] == 'stream-only-v1', 'unknown profile')
    bound = data['declared_transaction_bound_ms']
    require(type(bound) is int and 1 <= bound <= 499, 'explicit integer bound 1..499 required')
    commit = data['source_commit']
    require(type(commit) is str and re.fullmatch('[0-9a-f]{40}', commit) is not None,
            'source_commit must be a full lowercase SHA')
    return data


def generate(data):
    # Revalidate even when called through Python instead of the CLI.
    data = parse(json.dumps(data).encode('utf-8'))
    return ('/* PROVISIONAL DIAGNOSTIC INPUT: NOT TIMING PROOF OR FLASH APPROVAL. */\n'
            '#include "mt_stream_timing.h"\n'
            'const mt_stream_timing_inputs mt_stream_timing = {\n'
            '    .schema_version = 1u,\n'
            '    .diagnostic_provisional = 1u,\n'
            '    .declared_transaction_bound_ms = ' +
            str(data['declared_transaction_bound_ms']) + 'u\n};\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source, output = Path(args.input), Path(args.output)
    raw = source.read_bytes()
    data = parse(raw)
    # Exclusive creation: never overwrite an existing source or artifact.
    with output.open('x', encoding='utf-8') as handle:
        handle.write(generate(data))
    print(json.dumps({'status': 'DIAGNOSTIC_DECLARATION_ONLY',
                      'input_sha256': hashlib.sha256(raw).hexdigest(),
                      'source_commit': data['source_commit'],
                      'profile': data['profile'],
                      'transaction_bound_qualified': False,
                      'runtime_profile_accepted': False, 'hardware_verified': False,
                      'flash_approved': False}))


if __name__ == '__main__':
    main()
