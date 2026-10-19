#!/usr/bin/env python3
"""Reproduce draft byte fixtures, not an implementation conformance test."""
import json
import struct
import zlib
from pathlib import Path


def cobs(data):
    out = bytearray([0]); anchor = 0; code = 1
    for b in data:
        if b == 0:
            out[anchor] = code; anchor = len(out); out.append(0); code = 1
        else:
            out.append(b); code += 1
            if code == 255:
                out[anchor] = code; anchor = len(out); out.append(0); code = 1
    out[anchor] = code
    return bytes(out)


def frame(name, kind, seq, req, payload, session=0x0102030405060708, expected='accept'):
    h = struct.pack('<BBBBHHQII', 0, 2, kind, 0, len(payload), 0, session, seq, req)
    raw = h + payload
    raw += struct.pack('<I', zlib.crc32(raw))
    encoded = cobs(raw) + b'\0'
    return dict(name=name, type=kind, sequence=seq, request=req, expected=expected,
                payload_hex=payload.hex(), decoded_hex=raw.hex(), framed_hex=encoded.hex(),
                decoded_bytes=len(raw), framed_bytes=len(encoded))


def build():
    # A short final DATA block exercises exact endpoint and CRC calculations.
    samples = struct.pack('<4H', 0, 1, 2048, 4095)
    data = struct.pack('<IIQHH', 3, 1, 0, 4, 0) + samples
    end = struct.pack('<IIQQIIHHI', 3, 1, 4, 4, 4, zlib.crc32(samples), 0, 0, 4)
    positive = [
        frame('hello', 1, 1, 1, struct.pack('<Q', 0x0102030405060708), session=0),
        frame('hello_ack', 129, 1, 1, struct.pack('<4BHHII', 0, 2, 1, 12, 512, 128, 20261019, 1)),
        frame('get_caps', 2, 2, 2, b''),
        frame('caps', 130, 2, 2, struct.pack('<IHBBIIHHI', 1, 1, 0, 12, 50000, 1, 128, 0, 1)),
        frame('start', 8, 3, 3, struct.pack('<HH', 1, 0)),
        frame('start_ack', 136, 3, 3, struct.pack('<IIQ', 3, 1, 0)),
        frame('stop', 5, 4, 4, struct.pack('<I', 3)),
        frame('final_data', 152, 4, 0, data),
        frame('end', 153, 5, 0, end),
        frame('stop_ack', 133, 6, 4, struct.pack('<IQII', 3, 4, 5, 0)),
    ]
    negative = [
        frame('zero_count', 152, 4, 0, struct.pack('<IIQHH', 3, 1, 0, 0, 0), expected='reject_count'),
        frame('oversized_count', 152, 4, 0, struct.pack('<IIQHH', 3, 1, 0, 129, 0)+bytes(258), expected='reject_count'),
        frame('out_of_range_code', 152, 4, 0, struct.pack('<IIQHHH', 3, 1, 0, 1, 0, 4096), expected='reject_code'),
        frame('reserved_data', 152, 4, 0, struct.pack('<IIQHHH', 3, 1, 0, 1, 1, 0), expected='reject_reserved'),
        frame('wrong_config', 152, 4, 0, struct.pack('<IIQHHH', 3, 2, 0, 1, 0, 0), expected='reject_config'),
        frame('wrong_generation', 152, 4, 0, struct.pack('<IIQHHH', 4, 1, 0, 1, 0, 0), expected='reject_generation'),
        frame('missing_first_sample', 152, 4, 0, struct.pack('<IIQHHH', 3, 1, 1, 1, 0, 0), expected='reject_sample_gap'),
        frame('index_overflow', 152, 4, 0, struct.pack('<IIQHHH', 3, 1, 2**64-1, 1, 0, 0), expected='reject_overflow'),
        frame('wrong_rate', 130, 2, 2, struct.pack('<IHBBIIHHI', 1, 1, 0, 12, 50001, 1, 128, 0, 1), expected='reject_caps'),
        frame('false_clean_tail', 153, 5, 0, struct.pack('<IIQQIIHHI', 3, 1, 4, 8, 4, zlib.crc32(samples), 0, 0, 4), expected='reject_terminal'),
    ]
    cases = [
        dict(name='identical_immediate_duplicate', input=['final_data','final_data'], outcome='ignore_second_without_index_or_crc_change'),
        dict(name='record_during_preview', record_first_index=128, terminal_prefix_end=384, stored_interval=[128,384], outcome='selected_crc_separate_from_device_whole_prefix_crc'),
        dict(name='record_after_terminal', outcome='reject_record_no_next_block'),
        dict(name='stop_during_started_frame', outcome='stop_hardware_then_finish_frame_and_bounded_prefix_end_ack'),
        dict(name='unknown_dma_tail', end_flags=3, produced_end=0, reason=2, outcome='retain_prefix_mark_incomplete'),
        dict(name='unsolicited_fault_then_stop', outcome='one_end_reference_same_terminal_in_ack_preserve_first_fault'),
        dict(name='trickle_drain_deadline', deadline_ms=500, outcome='fault_no_terminal_no_clean_ack_even_with_progress'),
        dict(name='host_stop_timeout', deadline_ms=2000, outcome='retain_prefix_unknown_tail_fresh_session'),
        dict(name='reset_midstream', outcome='no_join_nonce_changes_old_record_incomplete'),
        dict(name='disk_error_before_normal_end', outcome='retain_first_disk_error_no_clean_record'),
        dict(name='reordered_frame', outcome='recover_preserve_prefix'),
        dict(name='counter_reserve', next_sequence=0xffffff00, outcome='no_new_data_controlled_termination'),
    ]
    return dict(status='draft_reference_fixtures_not_endpoint_tests', wire='0.2',
                positive_frames=positive, negative_frames=negative, state_cases=cases)

if __name__ == '__main__':
    print(json.dumps(build(), indent=2) + '\n', end='')
