#!/usr/bin/env python3
"""Draft reference bytes and real v0.1 negative checks; no v0.2 implementation."""
import argparse
import binascii
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / 'protocol/vectors-record-now-v0.2-draft.json'
SESSION = 0x0807060504030201


def cobs(raw):
    output = bytearray([0])
    at, code = 0, 1
    for value in raw:
        if value == 0:
            output[at] = code
            at = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 255:
                output[at] = code
                at = len(output)
                output.append(0)
                code = 1
    output[at] = code
    return bytes(output) + b'\0'


def crc(raw):
    return binascii.crc32(raw).to_bytes(4, 'little')


def reference():
    vectors = []

    def add(name, kind, payload, sequence, request, session=SESSION, minor=2,
            legacy_wire='MT_WIRE_VERSION', legacy_command=None, note=''):
        header = struct.pack('<BBBBHHQII', 0, minor, kind, 0, len(payload), 0,
                             session, sequence, request)
        raw = header + payload
        raw += crc(raw)
        vector = dict(name=name, decoded_hex=raw.hex(), frame_hex=cobs(raw).hex(),
                      fields=dict(major=0, minor=minor, type=kind, session=session,
                                  sequence=sequence, request=request,
                                  payload_hex=payload.hex()),
                      legacy_wire=legacy_wire, legacy_command=legacy_command,
                      note=note)
        vectors.append(vector)

    config = struct.pack('<HBBBBHHHI', 1, 2, 0, 0, 0, 2048, 0, 0, 0)
    add('hello_02', 1, struct.pack('<Q', SESSION), 1, 1, session=0)
    add('hello_ack_02', 129, struct.pack('<BBBBHHII', 0, 2, 1, 12, 512, 2048, 1, 113), 1, 1)
    add('get_caps_02', 2, b'', 2, 2)
    caps = struct.pack('<BBHIHHIHHBBBBHHH', 1, 0, 1, 72000000, 71, 19,
                       12000000, 143, 2048, 3, 4, 1, 3, 0, 512, 1024)
    add('caps_single_only_02', 130, caps, 2, 2)
    add('config_single_pre0_02', 3, config, 3, 3)
    add('config_ack_02', 131, struct.pack('<I', 1) + config, 3, 3)
    add('record_now_02', 8, struct.pack('<I', 1), 4, 4)
    add('record_now_arm_ack_02', 132, struct.pack('<II', 4, 1), 4, 4)

    def status(state, window=1):
        return struct.pack('<BBHIIIII', state, 0, 0, 4, window, 0, 0, 0)

    add('waiting_first_sample_02', 134, status(2, 0), 5, 0)
    add('capturing_02', 134, status(3), 6, 0)
    add('transferring_02', 134, status(4), 7, 0)
    begin = struct.pack('<IIIHHHHBBBBHHIHHI', 4, 1, 1, 1, 2048, 0, 0xffff,
                        3, 0, 0, 0, 2048, 2, 72000000, 71, 19, 0)
    add('begin_immediate_02', 144, begin, 8, 0)
    samples = struct.pack('<2048H', *range(2048))
    sequence = 9
    for offset in range(0, 2048, 250):
        count = min(250, 2048 - offset)
        data = struct.pack('<IIHH', 4, 1, offset, count)
        data += samples[2 * offset:2 * (offset + count)]
        add(f'data_{offset:04d}_02', 145, data, sequence, 0)
        sequence += 1
    sample_crc = binascii.crc32(samples)
    add('end_02', 146, struct.pack('<IIHHI', 4, 1, 2048, 0, sample_crc), sequence, 0)
    add('wait_release_02', 134, status(6), sequence + 1, 0)
    add('release_02', 7, struct.pack('<III', 4, 1, sample_crc), 5, 5)
    add('release_ack_02', 135, struct.pack('<II', 4, 1), sequence + 2, 5)
    add('stopped_02', 134, status(0), sequence + 3, 0)
    exchange_count = len(vectors)
    for length in (0, 3, 5):
        add(f'record_now_length_{length}_02', 8, bytes(length), 4, 4,
            note='Independent negative case: proposed v0.2 INVALID_PAYLOAD; v0.2 dispatcher not implemented/tested.')
    add('record_now_zero_config_02', 8, struct.pack('<I', 0), 4, 4,
        note='Independent negative case: idle/configured id1, proposed STALE_CONFIG; v0.2 state handling not tested.')
    add('record_now_stale_config_02', 8, struct.pack('<I', 2), 4, 4,
        note='Independent negative case: idle/configured id1, proposed STALE_CONFIG; v0.2 state handling not tested.')
    add('legacy_hello_01', 1, struct.pack('<Q', SESSION), 1, 1, session=0,
        minor=1, legacy_wire='MT_WIRE_OK', legacy_command='MT_COMMAND_OK')
    add('legacy_arm_01', 4, struct.pack('<I', 1), 4, 4, minor=1,
        legacy_wire='MT_WIRE_OK', legacy_command='MT_COMMAND_OK')
    add('record_now_disguised_as_01', 8, struct.pack('<I', 1), 4, 4, minor=1,
        legacy_wire='MT_WIRE_OK', legacy_command='MT_COMMAND_UNKNOWN_TYPE',
        note='Legacy codec accepts the envelope, but real command parser rejects type8.')
    # Corrupt only the CRC, then re-COBS: framing remains valid. CRC wins over version.
    raw = bytearray.fromhex(vectors[6]['decoded_hex'])
    raw[-1] ^= 1
    vectors.append(dict(name='bad_crc_before_version', decoded_hex=raw.hex(),
                        frame_hex=cobs(raw).hex(), fields=vectors[6]['fields'],
                        legacy_wire='MT_WIRE_CRC', legacy_command=None,
                        note='Deliberately wrong CRC; not a valid v0.2 packet.'))
    return dict(status='DRAFT reference bytes, not production v0.2 acceptance',
                base_commit='192fbb2ef9369bb1c32e11f97263094fca52763f',
                exchange_vector_count=exchange_count,
                synthetic_samples='code[i]=i for i=0..2047; not ignition data',
                samples_crc32=f'{sample_crc:08x}', vectors=vectors)


def probe_source(vectors):
    lines = ['#include <stdint.h>', '#include <stdio.h>', '#include <string.h>',
             '#include "mt_wire.h"', '#include "mt_command.h"',
             '#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d\\n", __LINE__); return 1; } } while (0)',
             'int main(void) {', 'mt_wire_message m, before;',
             'mt_command command, previous;', 'unsigned checked=0;']
    for vector in vectors:
        frame = bytes.fromhex(vector['frame_hex'])
        if frame[-1:] != b'\0' or b'\0' in frame[:-1]:
            raise ValueError('invalid reference COBS delimiter')
        values = ','.join(str(x) for x in frame[:-1])
        lines.extend(['{', f'const uint8_t frame[] = {{{values}}};',
                      'memset(&m, 0xa5, sizeof m); memcpy(&before, &m, sizeof m);',
                      f'CHECK(mt_wire_decode(frame, sizeof frame, &m) == {vector["legacy_wire"]});'])
        if vector['legacy_wire'] != 'MT_WIRE_OK':
            lines.append('CHECK(memcmp(&m, &before, sizeof m) == 0);')
        else:
            f = vector['fields']
            payload = bytes.fromhex(f['payload_hex'])
            lines.extend([f'CHECK(m.type == {f["type"]}u);',
                          f'CHECK(m.session == UINT64_C({f["session"]}));',
                          f'CHECK(m.sequence == {f["sequence"]}u && m.request == {f["request"]}u);',
                          f'CHECK(m.payload_length == {len(payload)}u);'])
            for index, value in enumerate(payload):
                lines.append(f'CHECK(m.payload[{index}] == {value}u);')
            lines.extend(['memset(&command, 0xa5, sizeof command);',
                          'memcpy(&previous, &command, sizeof command);',
                          f'CHECK(mt_command_parse(&m, &command) == {vector["legacy_command"]});'])
            if vector['legacy_command'] != 'MT_COMMAND_OK':
                lines.append('CHECK(memcmp(&command, &previous, sizeof command) == 0);')
        lines.extend(['++checked;', '}'])
    lines.extend(['printf("legacy boundaries: %u vectors passed\\n", checked);', 'return 0;', '}'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-reference', action='store_true',
                        help='Authoring only: deliberately replace the draft JSON catalogue')
    args = parser.parse_args()
    expected = reference()
    if args.write_reference:
        CATALOGUE.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Wrote draft reference; no acceptance implied')
        return
    actual = json.loads(CATALOGUE.read_text(encoding='utf-8'))
    if actual != expected:
        raise ValueError('committed reference differs from specified deterministic bytes')
    if binascii.crc32(b'123456789') != 0xcbf43926:
        raise ValueError('CRC check value mismatch')
    with tempfile.TemporaryDirectory(prefix='mt-record-now-contract-') as temp:
        directory = Path(temp)
        source = directory / 'legacy_probe.c'
        source.write_text(probe_source(actual['vectors']), encoding='utf-8')
        compiler = os.environ.get('CC', 'cc')
        subprocess.run([compiler, '--version'], check=True)
        flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Wconversion',
                 '-Wsign-conversion', '-Werror']
        for variant, extra in [('strict', []), ('ubsan', ['-fsanitize=undefined', '-fno-sanitize-recover=all'])]:
            executable = directory / variant
            command = [compiler, *flags, *extra, '-I' + str(ROOT / 'firmware/protocol'),
                       str(ROOT / 'firmware/protocol/mt_wire.c'),
                       str(ROOT / 'firmware/protocol/mt_command.c'), str(source), '-o', str(executable)]
            subprocess.run(command, check=True)
            print(variant + ':', flush=True)
            subprocess.run([str(executable)], check=True)
    print('PASS_REFERENCE_AND_LEGACY_ONLY; v0.2 production/state/USB/host/file are not tested')


if __name__ == '__main__':
    main()
