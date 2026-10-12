"""Bounded current-A software bridge through B's exact LV collector; no device."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import zlib

parser = argparse.ArgumentParser()
parser.add_argument('--host-root', type=Path, required=True)
parser.add_argument('--bridge', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
if not sys.flags.isolated or not sys.dont_write_bytecode:
    raise ValueError('Run with -I -B')
args.out.mkdir(exist_ok=False)
spec = importlib.util.spec_from_file_location('adapter', Path(__file__).with_name('adapter.py'))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
sys.path.insert(0, str(args.host_root))
from motortester import lv_collector as L, session as S, recording as REC, wire as W
require = A.require
save = A.save
inputs = {str(p): A.digest(p) for p in args.host_root.rglob('*') if p.is_file()}
inputs.update({str(p): A.digest(p) for p in Path(__file__).parent.glob('*') if p.is_file()})
inputs[str(args.bridge)] = A.digest(args.bridge)
save(args.out / 'INPUTS_BEFORE.json', inputs)
result = {'status': 'RUNNING', 'physical_verified': False, 'full_runtime': False,
          'a': 'db09c263c022c27723fd9de9e4a79905097767c6',
          'b': '76f9e41a12a69651ed39ce8fded31f0c0a25b471', 'attempts': [], 'events': []}
native = None
try:
    native = A.Native(args.bridge, args.out)
    link = A.Link(native)
    identity = L.RunIdentity(origin='simulator', application_commit=result['b'],
                             application_tree='a5fc78572e68f561eb9926f39bf0c95f1ee6d02a',
                             firmware_commit=result['a'])
    collector = L.LVCollector(args.out / 'run', link, clock_ms=lambda: link.now,
                              identity=identity, config=S.Config(mode=0, auto_timeout_ms=100),
                              nonce=11, utc_now=lambda: '2026-10-12T07:00:00Z')
    def pump(label):
        events = collector.pump()
        result['events'].append({'label': label, 'events': events,
                                 'state': collector.state, 'runtime': collector.runtime.snapshot()})
        require(collector.runtime.snapshot().phase is not S.Phase.RECOVERY, 'unexpected recovery')
    def until(test, label):
        for _ in range(40):
            if test():
                return
            pump(label)
        raise ValueError('bounded pump exceeded: ' + label)
    collector.start()
    until(lambda: collector.state is L.State.READY, 'handshake/config')
    for attempt in range(21):
        require(collector.state is L.State.READY, 'not ready for explicit ARM')
        collector.arm_next()
        until(lambda: collector.runtime.snapshot().phase is S.Phase.ARMED, 'arm')
        number = attempt + 1
        link.now += 100
        link.op('LVWINDOW %d %d' % (link.now, number), link.now)
        until(lambda: collector.state is L.State.SAVEABLE, 'capture/Stop')
        capture = collector.runtime.pending_capture
        require(capture is not None, 'no verified capture')
        expected = [(73 * i + 19 * number) & 4095 for i in range(2048)]
        expected[511], expected[1023] = 0, 4095
        raw = struct.pack('<2048H', *expected)
        require(capture.samples == tuple(expected), 'raw sample mismatch')
        require(capture.trigger_kind == 'auto_timeout' and capture.trigger_index is None, 'not Auto timeout')
        require(collector.runtime.snapshot().phase is S.Phase.READY, 'Stop not completed')
        before = len(link.writes)
        starts = link.states[-1]['starts']
        # Disk may take longer than WAIT_RELEASE: matching Stop already cancels that deadline.
        link.now += 5001
        state = link.op('POLL %d 64 10' % link.now, link.now)
        require(state['ready'] and not state['hw'] and not state['armed'] and state['starts'] == starts,
                'post-Stop disk delay changed device state')
        require(not link.rx, 'unexpected wire after stopped delay')
        # No collector pumping or implicit capture acknowledgement while the file is unverified.
        require(collector.runtime.pending_capture is capture, 'capture released before persistence')
        row = collector.persist_attempt()
        require(row['outcome'] == 'completed', 'attempt not completed')
        require(len(link.writes) == before and collector.runtime.pending_capture is None,
                'persistence caused wire RELEASE or retained acknowledged capture')
        body = json.loads((args.out / 'run' / row['sidecar_file']).read_bytes())['body']
        accepted = body['accepted']
        begin, end = accepted['begin']['decoded'], accepted['end']['decoded']
        require(begin['trigger_kind'] == 2 and begin['trigger_index'] == 65535, 'numeric trigger metadata')
        require(begin['quality_flags'] == 3, 'Auto and rail quality flags')
        require(begin['generation'] == end['generation'] == row['arm_request_id'], 'generation binding')
        require(begin['window_id'] == end['window_id'] == number, 'window binding')
        require(end['samples_crc32'] == zlib.crc32(raw), 'original END CRC')
        require(accepted['config']['decoded']['mode'] == 0 and accepted['caps']['decoded']['adc_clock_hz'] == 12000000,
                'original config/caps metadata')
        loaded = REC.load(args.out / 'run' / row['mtcap_file'])
        require(loaded == capture and struct.pack('<2048H', *loaded.samples) == raw, 'reopen raw/metadata mismatch')
        result['attempts'].append({'number': attempt, 'row': row, 'raw_sha256': hashlib.sha256(raw).hexdigest(),
                                   'state_after_delay': state, 'sidecar': body})
        save(args.out / 'IN_PROGRESS.json', result)
    require(collector.state is L.State.COMPLETE, 'collector not COMPLETE')
    verified = L.verify_run(args.out / 'run')
    require(verified['artifact_integrity'] == 'RECOVERED_VERIFIED' and verified['prior_writer_outcome'] == 'UNKNOWN',
            'offline verifier overclaimed prior outcome')
    require(len({r['raw_sha256'] for r in result['attempts']}) == 21, 'non-distinct sample windows')
    command_batch = W.StreamDecoder().feed(b''.join(link.writes), link.now)
    require(not command_batch.diagnostics, 'command decoding error')
    command_types = [f.message_type for f in command_batch.frames]
    require(command_types == [1, 2, 3] + [4, 5] * 21, 'expected 21ARM/21Stop and zero RELEASE')
    require(link.states[-1]['starts'] == 21 and not link.states[-1]['hw'], 'unexpected hardware fake starts')
    result.update(status='SOFTWARE_COLLECTOR_PASS', verified=verified, command_types=command_types,
                  native_exit=native.close())
except BaseException as error:
    result.update(status='FAILED', error_type=type(error).__name__, error=str(error))
    raise
finally:
    if native:
        native.dispose()
        save(args.out / 'NATIVE_TRACE.json', native.log)
    if 'link' in locals():
        save(args.out / 'TRANSPORT.json', {'writes': link.writes, 'reads': link.reads, 'states': link.states})
        (args.out / 'device.wire').write_bytes(link.wire)
        (args.out / 'host.wire').write_bytes(b''.join(link.writes))
    save(args.out / 'RESULTS.json', result)
    after = {p: A.digest(Path(p)) for p in inputs}
    save(args.out / 'INPUTS_AFTER.json', after)
    require(inputs == after, 'input changed')
print(json.dumps({'status': result['status'], 'attempts': len(result['attempts'])}))
