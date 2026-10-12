"""Независимая проверка byte-peer → SerialTransport → runtime → файл.

Только синтетика: 20 окон на каждую пару mode/edge (всего 120). В Auto
по 10 окон при 100 и 60000 мс, поровну signal и auto_timeout. Физическое
время, аппаратный порог, USB и Windows этим набором не подтверждаются.
"""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from motortester.device_emulator import DeviceEmulator, STOPPED, WAITING, WAIT_RELEASE
from motortester.recording import save, load
from motortester.runtime import AcquisitionRuntime, RuntimeError as AcquisitionError
from motortester.session import Config, Phase, EventKind, SessionError, RecoveryReason
from motortester.transport import SerialTransport, TransportState
from motortester.window import WindowCode


FIXTURES = Path(__file__).with_name("fixtures") / "runtime_modes"
UTC = "2026-10-12T03:00:00Z"
SESSION_PIN = "b6ae8025baf1a47e8173997029d2d8b858ada74c"
EMULATOR_PIN = "c54bd955aaf3cb863ef3df043ea237ea62e74ad4"
MATRIX_OBSERVATIONS = []


def crc_reference(data):
    """Битовый CRC-32/ISO-HDLC, независимо от zlib в коде прибора/ПК."""
    crc = 0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    return crc ^ 0xffffffff


def unpack_packet(encoded):
    """Малый независимый oracle COBS/заголовка, только для свидетельства."""
    raw = bytearray()
    offset = 0
    while offset < len(encoded):
        width = encoded[offset]
        end = offset + width
        if not width or end > len(encoded):
            raise ValueError("Повреждён тестовый COBS")
        raw.extend(encoded[offset + 1:end])
        offset = end
        if width < 255 and offset < len(encoded):
            raw.append(0)
    if len(raw) < 28:
        raise ValueError("Короткий тестовый кадр")
    major, minor, kind, reserved, size, reserved2, nonce, seq, request = struct.unpack_from(
        "<BBBBHHQII", raw)
    if ((major, minor, reserved, reserved2) != (0, 1, 0, 0)
            or len(raw) != size + 28
            or crc_reference(raw[:-4]) != int.from_bytes(raw[-4:], "little")):
        raise ValueError("Неверная целостность тестового кадра")
    return dict(kind=kind, session=nonce, sequence=seq, request=request,
                payload=bytes(raw[24:-4]))


def packets(chunks, *, complete_only=False):
    raw = b"".join(chunks)
    if raw and not raw.endswith(b"\0"):
        if not complete_only:
            raise ValueError("Не завершена тестовая порция")
        raw = raw[:raw.rfind(b"\0") + 1]
    return [unpack_packet(part) for part in raw.split(b"\0") if part]


def pack_packet(frame):
    """Только инъекция повреждения: новый CRC не делает метаданные верными."""
    payload = frame["payload"]
    body = struct.pack("<BBBBHHQII", 0, 1, frame["kind"], 0, len(payload), 0,
                       frame["session"], frame["sequence"], frame["request"]) + payload
    body += struct.pack("<I", crc_reference(body))
    result = bytearray(b"\0")
    code_index, width = 0, 1
    for byte in body:
        if byte == 0:
            result[code_index] = width
            code_index = len(result)
            result.append(0)
            width = 1
        else:
            result.append(byte)
            width += 1
            if width == 255:
                result[code_index] = width
                code_index = len(result)
                result.append(0)
                width = 1
    result[code_index] = width
    return bytes(result) + b"\0"


def expected_codes(window, generation, edge, level, timeout):
    result = tuple((i * 73 + window * 19 + generation * 7) % 4096 for i in range(2048))
    result = list(result)
    if not timeout:
        result[0] = level - edge
    result[511], result[1023] = 0, 4095
    return tuple(result)


class ByteBackend:
    """Явно внедрённый backend: реальных serial/COM-вызовов здесь нет."""
    def __init__(self, clock, **options):
        if options["port"] is not None:
            raise ValueError("Backend должен создаваться закрытым")
        self.clock = clock
        self.device = DeviceEmulator(all_analog_modes=True)
        self.port, self.is_open = None, False
        self.open_count = self.close_count = 0
        self.write_pattern, self.read_pattern = (4096,), (4096,)
        self.write_calls, self.read_calls = [], []
        self.tx, self.rx = [], []
        self.write_index = self.read_index = 0
        self.block_read = False
        self.write_failure = None
        self.read_failure = False
        self.replacement = None

    def open(self):
        self.is_open = True
        self.open_count += 1

    def close(self):
        self.is_open = False
        self.close_count += 1

    def write(self, offered):
        limit = self.write_pattern[self.write_index % len(self.write_pattern)]
        self.write_index += 1
        if self.write_failure == "prefix":
            accepted = self.device.write(offered[:3], self.clock[0]).accepted_prefix
            self.tx.append(offered[:accepted])
            self.write_calls.append((offered, None))
            raise TimeoutError("Внедрённый неизвестный префикс")
        outcome = self.device.write(offered[:limit], self.clock[0])
        if outcome.diagnostics:
            raise ValueError(outcome.diagnostics)
        count = outcome.accepted_prefix
        self.tx.append(offered[:count])
        self.write_calls.append((offered, count))
        return count

    def read(self, requested):
        if self.read_failure:
            self.read_calls.append((requested, None))
            raise OSError("Внедрённый обрыв")
        limit = min(requested, self.read_pattern[self.read_index % len(self.read_pattern)])
        self.read_index += 1
        if self.block_read:
            result = b""
        elif self.replacement is not None:
            result, self.replacement = self.replacement[:limit], self.replacement[limit:]
        else:
            result = self.device.read(limit, self.clock[0])
        self.rx.append(result)
        self.read_calls.append((requested, len(result)))
        return result

    def mutate_pending_frames(self, mutation):
        raw = bytearray()
        while True:
            chunk = self.device.read(4096, self.clock[0])
            if not chunk:
                break
            raw.extend(chunk)
        frames = packets([bytes(raw)])
        self.replacement = b"".join(pack_packet(mutation(frame)) for frame in frames)


class Rig:
    def __init__(self, owner, mode=0, edge=0, timeout=100, level=2048, *, armed=True,
                 write_pattern=(4096,), read_pattern=(4096,), nonce=0x102030405060708, utc_now=None):
        self.test, self.clock, self.nonce = owner, [0], nonce
        self.backends = []
        def factory(**options):
            backend = ByteBackend(self.clock, **options)
            self.backends.append(backend)
            return backend
        self.transport = SerialTransport(backend_factory=factory)
        self.transport.open("SYNTHETIC-RUNTIME-MODES", read_timeout=0, write_timeout=0.1)
        owner.addCleanup(self.transport.close)
        self.backend = self.backends[-1]
        self.backend.write_pattern, self.backend.read_pattern = write_pattern, read_pattern
        self.runtime = AcquisitionRuntime(self.transport, origin="simulator",
            clock_ms=lambda: self.clock[0], utc_now=utc_now or (lambda: UTC))
        self.events = []
        self.runtime.start(nonce)
        self.until(lambda: self.runtime.snapshot().phase is Phase.READY)
        self.config = Config(mode=mode, edge=edge, level_code=level,
                             auto_timeout_ms=timeout if mode == 0 else 0)
        self.runtime.configure(self.config)
        self.until(lambda: not self.runtime.snapshot().pending_request_id)
        if armed:
            self.arm()

    @property
    def device(self):
        return self.backend.device

    @property
    def now(self):
        return self.clock[0]

    @now.setter
    def now(self, value):
        self.clock[0] = value

    def pump(self):
        before = (len(self.backend.write_calls), len(self.backend.read_calls))
        events = self.runtime.pump()
        after = (len(self.backend.write_calls), len(self.backend.read_calls))
        self.test.assertLessEqual(after[0] - before[0], 1)
        self.test.assertLessEqual(after[1] - before[1], 1)
        self.events.extend(events)
        return events

    def until(self, predicate, *, allow_recovery=False):
        for _ in range(30000):
            if predicate():
                return
            self.pump()
            if not allow_recovery:
                self.test.assertIsNot(self.runtime.snapshot().phase, Phase.RECOVERY,
                                     self.events[-3:])
        self.test.fail("Ограниченный тестовый цикл не достиг ожидаемого состояния")

    def arm(self):
        self.runtime.arm()
        self.until(lambda: self.runtime.snapshot().phase is Phase.ARMED
                   and not self.runtime.snapshot().pending_request_id)

    def capture(self, *, timeout=False, trigger_at=None):
        if timeout:
            self.now += self.config.auto_timeout_ms
            self.device.tick(self.now)
        else:
            if trigger_at is not None:
                self.now = trigger_at
            self.test.assertTrue(self.device.trigger(self.now))
        self.until(lambda: self.runtime.pending_capture is not None)
        return self.runtime.pending_capture

    def release(self, capture):
        generation = self.runtime.snapshot().generation
        self.runtime.acknowledge(capture)
        self.until(lambda: self.runtime.pending_capture is None)
        release = [f for f in self.commands() if f["kind"] == 7][-1]
        self.test.assertEqual(struct.unpack("<III", release["payload"]),
                              (generation, capture.sequence,
                               crc_reference(struct.pack("<2048H", *capture.samples))))
        ack = [f for f in packets(self.backend.rx, complete_only=True) if f["kind"] == 135][-1]
        self.test.assertEqual(struct.unpack("<II", ack["payload"]),
                              (generation, capture.sequence))
        self.test.assertEqual(ack["request"], release["request"])

    def commands(self):
        return packets(self.backend.tx)


class RuntimeModesIntegrationTests(unittest.TestCase):
    def verify_capture_file(self, rig, capture, path, *, timeout):
        s = rig.runtime.snapshot()
        expected = expected_codes(capture.sequence, s.generation, rig.config.edge,
                                  rig.config.level_code, timeout)
        self.assertEqual(capture.samples, expected)
        self.assertEqual(capture.session_id, str(UUID(int=rig.nonce)))
        self.assertEqual(capture.trigger_kind, "auto_timeout" if timeout else "signal")
        self.assertEqual(capture.trigger_index, None if timeout else 0)
        self.assertEqual(capture.trigger_edge, "falling" if rig.config.edge else "rising")
        self.assertEqual(capture.trigger_level_code, rig.config.level_code)
        self.assertEqual(capture.pretrigger_samples, 0)
        self.assertEqual((capture.origin, capture.received_at_utc), ("simulator", UTC))
        self.assertEqual(capture.nominal_fs_hz, 50000.0)
        self.assertEqual(capture.protocol_version, "draft-0.1@" + SESSION_PIN)
        self.assertEqual(capture.firmware_version, "0")
        self.assertEqual(capture.quality_flags,
                         ("draft_protocol", "nominal_timing_unverified", "rail_observed"))
        self.assertIn("Синтетический", capture.sensor)
        self.assertTrue(capture.complete)
        self.assertEqual(capture.time_seconds(0), 0.0)
        self.assertEqual(capture.time_seconds(2047), 2047 / 50000)
        self.assertTrue(rig.device.snapshot().provenance.simulator)
        self.assertEqual(rig.device.snapshot().provenance.source_sha, EMULATOR_PIN)
        self.assertTrue(rig.device.snapshot().provenance.nominal_timing)
        self.assertTrue(rig.device.snapshot().provenance.synthetic_waveform)
        self.assertTrue(rig.device.snapshot().provenance.timeout_provisional)
        self.assertIs(rig.runtime.pending_capture, capture)
        commands_before = len(rig.backend.write_calls)
        for _ in range(3):
            rig.pump()
            self.assertIs(rig.runtime.pending_capture, capture)
        self.assertEqual(len(rig.backend.write_calls), commands_before)
        self.assertFalse(rig.device.trigger(rig.now))
        self.assertTrue(s.window_barrier)
        save(path, capture)
        raw = path.read_bytes()
        magic, meta_length, count = struct.unpack_from("<8sII", raw)
        self.assertEqual((magic, count), (b"MTCAP\x00\x01\x00", 2048))
        self.assertEqual(len(raw), 16 + meta_length + 4096 + 32)
        self.assertEqual(sha256(raw[:-32]).digest(), raw[-32:])
        meta = json.loads(raw[16:16 + meta_length])
        self.assertEqual(meta["time_basis"], "nominal_unverified")
        self.assertEqual(meta["timestamp_origin"], "host_receive")
        self.assertEqual(meta["amplitude_unit"], "adc_code")
        self.assertEqual(meta["calibration"], "uncalibrated")
        self.assertEqual(meta["trigger_kind"], capture.trigger_kind)
        self.assertEqual(meta["trigger_index"], capture.trigger_index)
        self.assertEqual(meta["sequence"], capture.sequence)
        self.assertEqual(meta["session_id"], capture.session_id)
        self.assertEqual(struct.unpack_from("<2048H", raw, 16 + meta_length), expected)
        reopened = load(path)
        self.assertEqual(reopened, capture)
        self.assertIsNot(reopened, capture)
        frames = packets(rig.backend.rx, complete_only=True)
        begin = [f for f in frames if f["kind"] == 144][-1]
        fields = struct.unpack("<IIIHHHHBBBBHHIHHI", begin["payload"])
        self.assertEqual(fields, (s.generation, capture.sequence, s.config_id, 1,
            2048, 0, 65535 if timeout else 0, 2 if timeout else 0, 0,
            rig.config.edge, 0, rig.config.level_code, 3 if timeout else 2,
            72000000, 71, 19, 0))
        wire_samples = []
        for frame in frames:
            if frame["kind"] != 145:
                continue
            generation, window, offset, count = struct.unpack_from("<IIHH", frame["payload"])
            if (generation, window) != (s.generation, capture.sequence):
                continue
            self.assertEqual(offset, len(wire_samples))
            self.assertEqual(len(frame["payload"]), 12 + 2 * count)
            wire_samples.extend(struct.unpack_from(f"<{count}H", frame["payload"], 12))
        self.assertEqual(tuple(wire_samples), expected)
        end = [f for f in frames if f["kind"] == 146][-1]
        self.assertEqual(struct.unpack("<IIHHI", end["payload"]),
                         (s.generation, capture.sequence, 2048, 0,
                          crc_reference(struct.pack("<2048H", *expected))))
        self.assertEqual(list(path.parent.glob(".motortester-*")), [])
        return sha256(raw).hexdigest()

    def test_120_windows_six_mode_edge_pairs_save_verify_before_ack(self):
        identities = set()
        MATRIX_OBSERVATIONS.clear()
        with tempfile.TemporaryDirectory() as folder:
            for mode in (0, 1, 2):
                for edge in (0, 1):
                    for timeout_ms in ((100, 60000) if mode == 0 else (0,)):
                        with self.subTest(mode=mode, edge=edge, timeout=timeout_ms):
                            count = 10 if mode == 0 else 20
                            nonce = 1000 + 10 * mode + edge + timeout_ms
                            rig = Rig(self, mode, edge, timeout_ms, level=1 if edge == 0 else 4094,
                                      write_pattern=(0, 1, 3, 17, 4096),
                                      read_pattern=(0, 1, 7, 541, 4096), nonce=nonce)
                            generations = []
                            files = []
                            for index in range(1, count + 1):
                                if mode == 2 and index > 1:
                                    before = len(rig.backend.write_calls)
                                    for _ in range(3):
                                        rig.pump()
                                    self.assertEqual(len(rig.backend.write_calls), before)
                                    self.assertFalse(rig.device.trigger(rig.now))
                                    self.assertIsNone(rig.runtime.pending_capture)
                                    rig.arm()
                                generations.append(rig.runtime.snapshot().generation)
                                timed_out = mode == 0 and index % 2 == 0
                                capture = rig.capture(timeout=timed_out)
                                self.assertEqual(capture.sequence, index)
                                path = Path(folder) / f"{mode}-{edge}-{timeout_ms}-{index}.mtcap"
                                digest = self.verify_capture_file(rig, capture, path, timeout=timed_out)
                                self.assertNotIn(digest, identities)
                                identities.add(digest)
                                files.append((path, digest, capture.samples))
                                with self.assertRaises(AcquisitionError):
                                    rig.runtime.acknowledge(load(path))
                                self.assertIs(rig.runtime.pending_capture, capture)
                                rig.release(capture)
                                self.assertEqual(sha256(path.read_bytes()).hexdigest(), digest)
                                self.assertIs(rig.runtime.snapshot().phase,
                                              Phase.READY if mode == 2 else Phase.ARMED)
                                self.assertEqual(rig.device.snapshot().state,
                                                 STOPPED if mode == 2 else WAITING)
                            commands = rig.commands()
                            self.assertEqual(sum(f["kind"] == 4 for f in commands),
                                             count if mode == 2 else 1)
                            self.assertEqual(sum(f["kind"] == 7 for f in commands), count)
                            self.assertEqual(sum(e.kind is EventKind.WINDOW_RELEASED for e in rig.events), count)
                            self.assertEqual(len(set(generations)), count if mode == 2 else 1)
                            for path, digest, samples in files:
                                self.assertEqual(sha256(path.read_bytes()).hexdigest(), digest)
                                self.assertEqual(load(path).samples, samples)
                            self.assertTrue(any(n == 0 for _, n in rig.backend.write_calls))
                            self.assertTrue(any(0 < n < len(data) for data, n in rig.backend.write_calls))
                            self.assertTrue(any(n == 1 for _, n in rig.backend.read_calls))
                            self.assertTrue(any(chunk.count(b"\0") > 1 for chunk in rig.backend.rx))
                            self.assertTrue(all(size == 4096 for size, _ in rig.backend.read_calls))
                            self.assertEqual((len(rig.backends), rig.backend.open_count), (1, 1))
                            MATRIX_OBSERVATIONS.append(dict(
                                mode=mode, edge=edge, timeout_ms=timeout_ms, windows=count,
                                signal_windows=count // 2 if mode == 0 else count,
                                timeout_windows=count // 2 if mode == 0 else 0,
                                generations=generations, files_sha256=[digest for _, digest, _ in files],
                                write_calls=len(rig.backend.write_calls), read_calls=len(rig.backend.read_calls),
                                zero_writes=sum(n == 0 for _, n in rig.backend.write_calls),
                                short_writes=sum(0 < n < len(data) for data, n in rig.backend.write_calls),
                                one_byte_reads=sum(n == 1 for _, n in rig.backend.read_calls),
                                coalesced_reads=sum(chunk.count(b"\0") > 1 for chunk in rig.backend.rx),
                                accepted_tx_bytes=sum(len(chunk) for chunk in rig.backend.tx),
                                received_rx_bytes=sum(len(chunk) for chunk in rig.backend.rx)))
            self.assertEqual(len(identities), 120)

    def test_auto_100_and_60000_before_exact_and_same_time_trigger(self):
        for timeout_ms in (100, 60000):
            for edge in (0, 1):
                for signal in (False, True):
                    with self.subTest(timeout=timeout_ms, edge=edge, signal=signal):
                        rig = Rig(self, edge=edge, timeout=timeout_ms)
                        rig.now = timeout_ms - 1
                        rig.pump()
                        self.assertIsNone(rig.runtime.pending_capture)
                        self.assertIsNone(rig.runtime.incomplete_result)
                        self.assertEqual(rig.device.snapshot().window_id, 0)
                        if signal:
                            self.assertTrue(rig.device.trigger(rig.now))
                            rig.now += 1
                        else:
                            rig.now += 1
                            self.assertFalse(rig.device.trigger(rig.now))
                        rig.until(lambda: rig.runtime.pending_capture is not None)
                        capture = rig.runtime.pending_capture
                        with tempfile.TemporaryDirectory() as folder:
                            self.verify_capture_file(rig, capture, Path(folder) / "deadline.mtcap",
                                                     timeout=not signal)
                        rig.release(capture)

    def test_auto_timeout_capture_is_not_discarded_as_invalid_signal(self):
        """Регрессия: старое signal/index=65535 теряло полный Auto-массив."""
        rig = Rig(self)
        capture = rig.capture(timeout=True)
        self.assertEqual(capture.trigger_kind, "auto_timeout")
        self.assertIsNone(capture.trigger_index)
        self.assertIsNone(rig.runtime.incomplete_result)
        self.assertEqual(capture.samples, expected_codes(1, 4, 0, 2048, True))

    def test_normal_and_single_have_no_trigger_wait_deadline(self):
        for mode in (1, 2):
            for edge in (0, 1):
                rig = Rig(self, mode, edge)
                before = len(rig.backend.write_calls)
                for now in (999, 1000, 3000, 60000, 86400000, (1 << 64) - 1):
                    rig.now = now
                    rig.pump()
                    self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
                    self.assertIsNone(rig.runtime.pending_capture)
                    self.assertIsNone(rig.runtime.incomplete_result)
                    self.assertEqual(rig.device.snapshot().window_id, 0)
                self.assertEqual(len(rig.backend.write_calls), before)
                capture = rig.capture()
                rig.release(capture)

    def test_full_release_ack_byte_gates_single_stop_and_auto_new_deadline(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            capture = rig.capture(timeout=mode == 0)
            self.assertEqual(rig.device.snapshot().state, WAIT_RELEASE)
            rig.runtime.acknowledge(capture)
            with self.assertRaises(AcquisitionError):
                rig.runtime.acknowledge(capture)
            rig.backend.read_pattern = (1,)
            rig.pump()
            self.assertIs(rig.runtime.pending_capture, capture)
            self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
            self.assertEqual(rig.device.snapshot().state, WAIT_RELEASE)
            self.assertFalse(rig.device.trigger(rig.now))
            rig.now += 250
            while rig.runtime.pending_capture is not None:
                self.assertEqual(rig.device.snapshot().state, WAIT_RELEASE)
                self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
                self.assertFalse(rig.device.trigger(rig.now))
                rig.pump()
            self.assertEqual(rig.device.snapshot().state, STOPPED if mode == 2 else WAITING)
            if mode == 0:
                rig.backend.read_pattern = (4096,)
                rig.now += 99
                rig.pump()
                self.assertIsNone(rig.runtime.pending_capture)
                rig.now += 1
                rig.until(lambda: rig.runtime.pending_capture is not None)
                self.assertEqual(rig.runtime.pending_capture.sequence, 2)
                self.assertEqual(rig.runtime.pending_capture.trigger_kind, "auto_timeout")
            if mode == 2:
                self.assertFalse(rig.device.snapshot().continuation)
                self.assertFalse(rig.device.trigger(rig.now))
                generation = rig.runtime.snapshot().generation
                rig.arm()
                self.assertGreater(rig.runtime.snapshot().generation, generation)
                self.assertTrue(rig.device.trigger(rig.now))

    def test_auto_wait_starts_at_arm_execution_not_delayed_ack(self):
        rig = Rig(self, armed=False)
        rig.backend.read_pattern = (1,)
        rig.runtime.arm()
        rig.pump()
        self.assertEqual(rig.device.snapshot().state, WAITING)
        self.assertNotEqual(rig.runtime.snapshot().pending_request_id, 0)
        rig.now = 100
        rig.device.tick(rig.now)
        self.assertEqual(rig.device.snapshot().window_id, 1)
        rig.backend.read_pattern = (4096,)
        rig.until(lambda: rig.runtime.pending_capture is not None)
        self.assertEqual(rig.runtime.pending_capture.trigger_kind, "auto_timeout")

    def test_save_failure_or_existing_path_keeps_pending_without_release(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            capture = rig.capture(timeout=mode == 0)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "protected.mtcap"
                path.write_bytes(b"existing-user-file")
                before = len(rig.backend.write_calls)
                with self.assertRaises(FileExistsError):
                    save(path, capture)
                self.assertEqual(path.read_bytes(), b"existing-user-file")
                with patch("motortester.recording.os.fsync", side_effect=OSError("test disk failure")):
                    with self.assertRaises(OSError):
                        save(Path(folder) / "new.mtcap", capture)
                self.assertFalse((Path(folder) / "new.mtcap").exists())
                self.assertEqual(list(Path(folder).glob(".motortester-*")), [])
                rig.pump()
                self.assertEqual(len(rig.backend.write_calls), before)
                self.assertIs(rig.runtime.pending_capture, capture)
                with self.assertRaises(AcquisitionError):
                    rig.runtime.acknowledge(replace(capture))
                with self.assertRaises(AcquisitionError):
                    rig.runtime.arm()
                with self.assertRaises(AcquisitionError):
                    rig.runtime.configure(rig.config)
                good = Path(folder) / "saved.mtcap"
                digest = self.verify_capture_file(rig, capture, good, timeout=mode == 0)
                rig.release(capture)
                self.assertEqual(sha256(good.read_bytes()).hexdigest(), digest)

    def test_stop_before_trigger_and_after_capture_preserves_no_implicit_arm(self):
        for mode in (0, 1, 2):
            for complete in (False, True):
                rig = Rig(self, mode)
                capture = rig.capture(timeout=mode == 0) if complete else None
                rig.runtime.stop()
                if complete:
                    with self.assertRaises(AcquisitionError):
                        rig.runtime.acknowledge(capture)
                rig.until(lambda: not rig.runtime.snapshot().stop_barrier)
                self.assertIs(rig.runtime.snapshot().phase, Phase.READY)
                self.assertEqual(rig.device.snapshot().state, STOPPED)
                if complete:
                    self.assertIs(rig.runtime.pending_capture, capture)
                    rig.runtime.acknowledge(capture)
                self.assertIsNone(rig.runtime.pending_capture)
                before = len(rig.backend.write_calls)
                rig.now += 100000
                rig.pump()
                self.assertEqual(len(rig.backend.write_calls), before)
                self.assertFalse(rig.device.trigger(rig.now))
                self.assertEqual(sum(f["kind"] == 7 for f in rig.commands()), 0)

    def test_partial_window_stop_preserves_fragments_without_capture(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            rig.backend.read_pattern = (700,)
            self.assertTrue(rig.device.trigger(rig.now))
            rig.pump()
            rig.runtime.stop()
            rig.until(lambda: not rig.runtime.snapshot().stop_barrier)
            self.assertIsNone(rig.runtime.pending_capture)
            result = rig.runtime.incomplete_result
            self.assertIsNotNone(result)
            self.assertIs(result.reason, WindowCode.ABORT)
            self.assertIsNone(result.samples)
            self.assertTrue(result.fragments)
            self.assertTrue(result.missing_ranges)
            expected = expected_codes(1, rig.runtime.snapshot().generation, 0, 2048, False)
            for block in result.fragments:
                self.assertEqual(block.samples, expected[block.offset:block.offset + len(block.samples)])
            self.assertIs(rig.runtime.take_incomplete(), result)
            self.assertIsNone(rig.runtime.take_incomplete())

    def test_unknown_release_write_and_ack_timeout_preserve_complete_capture(self):
        for mode in (0, 1, 2):
            for fault in ("prefix", "ack_timeout"):
                rig = Rig(self, mode)
                capture = rig.capture(timeout=mode == 0)
                rig.runtime.acknowledge(capture)
                if fault == "prefix":
                    rig.backend.write_failure = "prefix"
                    rig.pump()
                    self.assertIs(rig.transport.state, TransportState.RECOVERY)
                    reason = RecoveryReason.UNKNOWN_WRITE
                else:
                    rig.backend.block_read = True
                    rig.pump()
                    rig.now += 999
                    rig.pump()
                    self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
                    rig.now += 1
                    rig.pump()
                    reason = RecoveryReason.TIMEOUT
                self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
                self.assertTrue(any(e.reason is reason for e in rig.events))
                self.assertIs(rig.runtime.pending_capture, capture)
                before = (len(rig.backend.write_calls), len(rig.backend.read_calls))
                for _ in range(5):
                    rig.pump()
                self.assertEqual((len(rig.backend.write_calls), len(rig.backend.read_calls)), before)
                self.assertEqual((len(rig.backends), rig.backend.open_count), (1, 1))
                with self.assertRaises(AcquisitionError):
                    rig.runtime.start(rig.nonce + 1, reset_confirmed=True)
                rig.runtime.acknowledge(capture)
                self.assertIsNone(rig.runtime.pending_capture)
                self.assertEqual(len(rig.backend.write_calls), before[0])

    def test_partial_disconnect_requires_retention_and_explicit_fresh_reset(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            self.assertTrue(rig.device.trigger(rig.now))
            rig.backend.read_pattern = (700,)
            rig.pump()
            rig.backend.read_failure = True
            rig.pump()
            self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
            result = rig.runtime.incomplete_result
            self.assertIsNone(rig.runtime.pending_capture)
            self.assertIs(result.reason, WindowCode.DISCONNECT)
            self.assertTrue(result.fragments)
            self.assertIsNone(result.samples)
            with self.assertRaises(AcquisitionError):
                rig.runtime.start(rig.nonce + 1, reset_confirmed=True)
            self.assertIs(rig.runtime.take_incomplete(), result)
            with self.assertRaises(AcquisitionError):
                rig.runtime.start(rig.nonce + 1)
            rig.transport.close()
            rig.transport.open("SYNTHETIC-RUNTIME-MODES", read_timeout=0, write_timeout=0.1)
            rig.backend = rig.backends[-1]
            rig.runtime.start(rig.nonce + 1, reset_confirmed=True)
            rig.until(lambda: rig.runtime.snapshot().phase is Phase.READY)
            self.assertIsNone(rig.runtime.snapshot().config)
            self.assertIsNone(rig.runtime.pending_capture)
            self.assertEqual(sum(f["kind"] == 4 for f in rig.commands()), 0)
            self.assertFalse(rig.device.trigger(rig.now))

    def test_partial_frame_timeout_and_window_progress_timeout_never_make_capture(self):
        for mode in (0, 1, 2):
            for frame_partial in (False, True):
                rig = Rig(self, mode)
                self.assertTrue(rig.device.trigger(rig.now))
                if frame_partial:
                    rig.backend.read_pattern = (700,)
                    rig.pump()
                    deadline = 1000
                else:
                    rig.backend.read_pattern = (1,)
                    while not any(e.kind is EventKind.WINDOW_DATA for e in rig.events):
                        rig.pump()
                    deadline = 3000
                rig.backend.block_read = True
                rig.now = deadline - 1
                rig.pump()
                self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
                rig.now += 1
                rig.pump()
                self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
                self.assertIsNone(rig.runtime.pending_capture)
                self.assertIsNotNone(rig.runtime.incomplete_result)
                self.assertIsNone(rig.runtime.incomplete_result.samples)
                self.assertTrue(rig.runtime.incomplete_result.fragments)
                self.assertIs(rig.runtime.incomplete_result.reason,
                              WindowCode.DISCONNECT if frame_partial else WindowCode.TIMEOUT)

    def test_invalid_received_metadata_crc_or_utc_never_releases_or_makes_capture(self):
        for mode in (0, 1, 2):
            for fault in ("edge", "window_crc", "utc"):
                rig = Rig(self, mode, utc_now=(lambda: "not-a-UTC-date") if fault == "utc" else None)
                self.assertTrue(rig.device.trigger(rig.now))
                if fault != "utc":
                    def mutate(frame):
                        payload = bytearray(frame["payload"])
                        if fault == "edge" and frame["kind"] == 144:
                            payload[22] ^= 1
                        if fault == "window_crc" and frame["kind"] == 146:
                            payload[-1] ^= 1
                        return dict(frame, payload=bytes(payload))
                    rig.backend.mutate_pending_frames(mutate)
                rig.until(lambda: rig.runtime.snapshot().phase is Phase.RECOVERY,
                          allow_recovery=True)
                self.assertIsNone(rig.runtime.pending_capture)
                self.assertFalse(any(f["kind"] == 7 for f in rig.commands()))
                if fault in ("window_crc", "utc"):
                    result = rig.runtime.incomplete_result
                    self.assertIsNotNone(result)
                    self.assertEqual(sum(len(block.samples) for block in result.fragments), 2048)
                    self.assertEqual(result.missing_ranges, ())
                    self.assertIs(result.reason, WindowCode.CRC if fault == "window_crc" else WindowCode.COMPLETE)

    def test_provisional_hold_expiry_keeps_capture_and_saved_file(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            capture = rig.capture(timeout=mode == 0)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "held.mtcap"
                digest = self.verify_capture_file(rig, capture, path, timeout=mode == 0)
                completed = rig.now
                rig.now = completed + 4999
                rig.pump()
                self.assertIs(rig.runtime.pending_capture, capture)
                self.assertIs(rig.runtime.snapshot().phase, Phase.ARMED)
                rig.now += 1
                rig.pump()
                self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
                self.assertIs(rig.runtime.pending_capture, capture)
                self.assertEqual(rig.device.snapshot().terminal.outcome, "END")
                self.assertFalse(rig.device.snapshot().continuation)
                self.assertEqual(sha256(path.read_bytes()).hexdigest(), digest)
                self.assertEqual(load(path), capture)
                self.assertEqual(sum(f["kind"] == 146 for f in packets(rig.backend.rx)), 1)
                self.assertEqual(sum(f["kind"] == 147 for f in packets(rig.backend.rx)), 0)
                before = len(rig.backend.write_calls)
                rig.runtime.acknowledge(capture)
                rig.pump()
                self.assertEqual(len(rig.backend.write_calls), before)

    def test_large_auto_clock_jump_yields_one_window_and_fresh_deadline(self):
        rig = Rig(self)
        rig.now = (1 << 64) - 101
        rig.device.tick(rig.now)
        rig.until(lambda: rig.runtime.pending_capture is not None)
        capture = rig.runtime.pending_capture
        self.assertEqual(capture.sequence, 1)
        self.assertEqual(capture.trigger_kind, "auto_timeout")
        self.assertFalse(rig.device.trigger(rig.now))
        rig.release(capture)
        rig.now += 99
        rig.pump()
        self.assertIsNone(rig.runtime.pending_capture)
        self.assertEqual(rig.device.snapshot().window_id, 1)
        rig.now += 1
        rig.until(lambda: rig.runtime.pending_capture is not None)
        self.assertEqual(rig.runtime.pending_capture.sequence, 2)
        self.assertEqual(rig.runtime.pending_capture.trigger_kind, "auto_timeout")
        self.assertEqual(sum(f["kind"] == 4 for f in rig.commands()), 1)

    def test_auto_deadline_stop_retains_abort_event_without_fake_capture(self):
        rig = Rig(self)
        rig.now = 100
        rig.runtime.stop()
        rig.until(lambda: not rig.runtime.snapshot().stop_barrier)
        self.assertEqual(rig.device.snapshot().window_id, 1)
        self.assertEqual(rig.device.snapshot().state, STOPPED)
        self.assertIsNone(rig.runtime.pending_capture)
        self.assertIsNone(rig.runtime.incomplete_result)
        aborts = [e for e in rig.events if e.kind is EventKind.WINDOW_ABORT]
        self.assertEqual(len(aborts), 1)
        self.assertEqual(aborts[0].window_id, 1)
        self.assertEqual(sum(f["kind"] == 7 for f in rig.commands()), 0)
        self.assertEqual([f["kind"] for f in packets(rig.backend.rx)
                          if f["kind"] in (144, 146, 147)], [147])

    def test_corrupt_coalesced_batch_is_rejected_before_admitting_good_begin(self):
        for mode in (0, 1, 2):
            rig = Rig(self, mode)
            self.assertTrue(rig.device.trigger(rig.now))
            rig.backend.mutate_pending_frames(lambda frame: frame)
            rig.backend.replacement = b"\x01\0" + rig.backend.replacement
            rig.pump()
            self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
            self.assertFalse(any(e.kind is EventKind.WINDOW_BEGIN for e in rig.events))
            self.assertIsNone(rig.runtime.pending_capture)
            self.assertIsNone(rig.runtime.incomplete_result)
            self.assertEqual(sum(f["kind"] == 7 for f in rig.commands()), 0)

    def test_minimal_raw_fixture_matches_received_auto_timeout_and_release(self):
        manifest = json.loads((FIXTURES / "auto-timeout-100.json").read_text())
        rig = Rig(self)
        capture = rig.capture(timeout=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.mtcap"
            digest = self.verify_capture_file(rig, capture, path, timeout=True)
            self.assertEqual(digest, manifest["capture_sha256"])
            self.assertEqual(sha256(struct.pack("<2048H", *capture.samples)).hexdigest(),
                             manifest["raw_codes_sha256"])
            rig.release(capture)
            for _ in range(3):
                rig.pump()
            tx, rx = b"".join(rig.backend.tx), b"".join(rig.backend.rx)
            self.assertEqual(tx, (FIXTURES / "auto-timeout-100-host.bin").read_bytes())
            self.assertEqual(rx, (FIXTURES / "auto-timeout-100-device.bin").read_bytes())
            self.assertEqual(sha256(tx).hexdigest(), manifest["host_bytes_sha256"])
            self.assertEqual(sha256(rx).hexdigest(), manifest["device_bytes_sha256"])
            self.assertEqual([f["kind"] for f in packets([tx])], [1, 2, 3, 4, 7])
            self.assertEqual(sum(f["kind"] == 135 for f in packets([rx])), 1)

    def test_invalid_config_and_write_stall_have_no_unintended_capture(self):
        rig = Rig(self, armed=False)
        for timeout in (0, 99, 60001, True, 100.0):
            before = rig.runtime.snapshot()
            with self.assertRaises(SessionError):
                rig.runtime.configure(Config(mode=0, auto_timeout_ms=timeout))
            self.assertEqual(rig.runtime.snapshot(), before)
        rig.runtime.arm()
        rig.backend.write_pattern = (0,)
        rig.pump()
        rig.now = 999
        rig.pump()
        self.assertIs(rig.runtime.snapshot().phase, Phase.READY)
        before = len(rig.backend.write_calls)
        rig.now = 1000
        rig.pump()
        self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual(len(rig.backend.write_calls), before)
        self.assertIsNone(rig.runtime.pending_capture)
        self.assertEqual(rig.device.snapshot().window_id, 0)


if __name__ == "__main__":
    unittest.main()
