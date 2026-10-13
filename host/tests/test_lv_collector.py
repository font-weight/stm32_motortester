"""Авторские программные проверки LV-001; физический опыт не выполнен."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from motortester.device_emulator import DeviceEmulator
from motortester import lv_collector as lv
from motortester.recording import load
from motortester.session import Config, Phase
from motortester.wire import Frame, decode_frame, encode_frame


class Link:
    def __init__(self):
        self.now = 0
        self.device = DeviceEmulator(all_analog_modes=True)
        self.read_size = self.write_size = 4096
        self.tx, self.rx = [], []
        self.replacement = None
        self.unknown_write = False
        self.fail_read = False
        self.block_read = False
        self.invalid_read = None

    def write(self, data):
        if self.unknown_write:
            self.device.write(data[:3], self.now)
            raise TimeoutError("Неизвестный префикс")
        count = self.device.write(data[:self.write_size], self.now).accepted_prefix
        self.tx.append(data[:count])
        return count

    def read(self, size):
        if self.fail_read:
            raise OSError("Отключение")
        if self.invalid_read is not None:
            return self.invalid_read
        size = min(size, self.read_size)
        if self.block_read:
            result = b""
        elif self.replacement is not None:
            result, self.replacement = self.replacement[:size], self.replacement[size:]
            if not self.replacement:
                self.replacement = None
        else:
            result = self.device.read(size, self.now)
        self.rx.append(result)
        return result

    def pending(self):
        parts = []
        for _ in range(32):
            raw = self.device.read(4096, self.now)
            if not raw:
                return b"".join(parts)
            parts.append(raw)
        raise AssertionError("Неограниченный peer")


def packets(raw):
    return [decode_frame(part) for part in raw.split(b"\0") if part]


class CollectorTests(unittest.TestCase):
    def make(self, *, negotiate=True, edge=0, timeout=100):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        link = Link()
        collector = lv.LVCollector(Path(root.name) / "опыт", link,
            clock_ms=lambda: link.now,
            identity=lv.RunIdentity("simulator", "1" * 40, "2" * 40),
            config=Config(mode=0, edge=edge, level_code=1600, auto_timeout_ms=timeout),
            nonce=101, utc_now=lambda: "2026-10-12T00:00:00Z")
        collector.start()
        if negotiate:
            self.until(collector, lambda: collector.state is lv.State.READY)
        return collector, link

    def until(self, collector, predicate):
        for _ in range(20000):
            if predicate():
                return
            collector.pump()
        self.fail("Не достигнуто ожидаемое состояние")

    def arm(self, collector):
        collector.arm_next()
        self.until(collector, lambda: collector.index["attempts"][collector._current]["arm_ack"])

    def complete(self, collector, link, *, timeout=False):
        self.arm(collector)
        if timeout:
            link.now += collector.config.auto_timeout_ms
            link.device.tick(link.now)
        else:
            self.assertTrue(link.device.trigger(link.now))
        self.until(collector, lambda: collector.state is lv.State.SAVEABLE)
        self.assertTrue(collector._stop_matched)
        return collector.runtime.pending_capture

    def body(self, collector, number=0):
        return json.loads((collector.directory / f"attempt-{number:02d}.json").read_bytes())["body"]

    def test_twenty_plus_warmup_exact_arrays_and_original_metadata(self):
        c, link = self.make(edge=1, timeout=60000)
        ids, raw_hashes = set(), set()
        for i in range(21):
            capture = self.complete(c, link, timeout=i % 2 == 0)
            self.assertIs(c.runtime.pending_capture, capture)
            c.persist_attempt()
            saved = load(c.directory / f"attempt-{i:02d}.mtcap")
            self.assertEqual(saved, capture)
            expected = [(73 * j + 19 * capture.sequence + 7 * c.runtime.snapshot().generation) % 4096 for j in range(2048)]
            if i % 2:
                expected[0] = 1599
            expected[511], expected[1023] = 0, 4095
            self.assertEqual(tuple(expected), saved.samples)
            raw_hashes.add(hashlib.sha256(struct.pack("<2048H", *saved.samples)).hexdigest())
            ids.add(saved.sequence)
            body = self.body(c, i)
            begin = body["accepted"]["begin"]["decoded"]
            self.assertEqual(begin["trigger_kind"], 2 if i % 2 == 0 else 0)
            self.assertEqual(begin["trigger_index"], 65535 if i % 2 == 0 else 0)
            self.assertEqual(begin["quality_flags"], 3 if i % 2 == 0 else 2)
            self.assertEqual((begin["source"], begin["channel"], begin["external_edge_id"]), (0, 0, 0))
            self.assertEqual(body["accepted"]["config"]["decoded"]["auto_timeout_ms"], 60000)
            self.assertEqual(body["accepted"]["caps"]["decoded"]["adc_sample_cycles_x2"], 143)
            self.assertEqual(body["identity"]["vdda_measurement"], "TBD")
            self.assertEqual(body["attempt"]["role"], "warmup" if i == 0 else "series")
            self.assertIsNone(c.runtime.pending_capture)
        self.assertEqual((len(ids), len(raw_hashes)), (21, 21))
        index = lv.verify_run(c.directory)
        self.assertEqual(index["state"], "sealed_unverified")
        self.assertEqual(index["artifact_integrity"], "RECOVERED_VERIFIED")
        self.assertEqual(index["prior_writer_outcome"], "UNKNOWN")
        self.assertEqual(sum(a["outcome"] == "completed" for a in index["attempts"][1:]), 20)
        types = [f.message_type for f in packets(b"".join(link.tx))]
        self.assertEqual(types.count(4), 21)
        self.assertEqual(types.count(5), 21)
        self.assertNotIn(7, types)
        with self.assertRaises(lv.CollectorError):
            c.arm_next()

    def test_intent_exists_before_first_arm_write(self):
        c, link = self.make()
        c.arm_next()
        durable = lv.verify_run(c.directory)["attempts"][0]
        self.assertEqual(durable["outcome"], "pending")
        self.assertTrue(durable["intent_committed"])
        self.assertIsNone(durable["arm_queued"])
        self.assertFalse(durable["arm_ack"])
        self.assertNotIn(4, [f.message_type for f in packets(b"".join(link.tx))])

    def test_partial_and_coalesced_io_stop_before_disk_same_object_ack(self):
        c, link = self.make()
        link.read_size, link.write_size = 97, 3
        capture = self.complete(c, link)
        self.assertEqual(c.runtime.snapshot().phase, Phase.READY)
        self.assertEqual(list(c.directory.glob("*.mtcap")), [])
        original = c.runtime.acknowledge
        with patch.object(c.runtime, "acknowledge", wraps=original) as ack:
            c.persist_attempt()
            ack.assert_called_once_with(capture)
            self.assertIs(ack.call_args.args[0], capture)
        self.assertTrue(self.body(c)["stop_matched"])
        self.assertTrue(any(r["kind"] == "tx" and r["accepted"] == 3 for r in self.body(c)["journal"]["records"]))

    def test_one_byte_read_write_within_bounds(self):
        c, link = self.make()
        link.read_size = link.write_size = 1
        capture = self.complete(c, link, timeout=True)
        c.persist_attempt()
        self.assertEqual(load(c.directory / "attempt-00.mtcap"), capture)
        self.assertLess(len(self.body(c)["journal"]["records"]), lv.MAX_JOURNAL_RECORDS)

    def test_no_premature_persist_or_ack(self):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        self.until(c, lambda: c.state is lv.State.STOPPING)
        capture = c.runtime.pending_capture
        self.assertIsNotNone(capture)
        with self.assertRaises(lv.CollectorError):
            c.persist_attempt()
        with self.assertRaises(lv.CollectorError):
            c.arm_next()
        self.assertIs(c.runtime.pending_capture, capture)

    def test_unknown_arm_write_does_not_confirm_execution(self):
        c, link = self.make()
        c.arm_next()
        link.unknown_write = True
        c.pump()
        c.persist_attempt()
        body = self.body(c)
        self.assertTrue(body["attempt"]["tx_unknown"])
        self.assertFalse(body["attempt"]["arm_ack"])
        self.assertEqual(body["attempt"]["tx_known_bytes"], 0)
        tx = [r for r in body["journal"]["records"] if r["command"] == "arm" and r["kind"] == "tx"]
        self.assertEqual(tx[0]["outcome"], "unknown_write")
        self.assertTrue(tx[0]["data_hex"])
        self.assertEqual(lv.verify_run(c.directory)["state"], "failed")
        self.assertEqual(sum(a["outcome"] == "cancelled" for a in c.index["attempts"]), 20)

    def test_stop_unknown_write_preserves_capture_no_ack_or_rearm(self):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        self.until(c, lambda: c.state is lv.State.STOPPING)
        capture = c.runtime.pending_capture
        link.unknown_write = True
        c.pump()
        c.persist_attempt()
        self.assertIs(c.runtime.pending_capture, capture)
        self.assertEqual(load(c.directory / "attempt-00.mtcap"), capture)
        self.assertEqual(c.state, lv.State.FAILED)
        self.assertFalse(self.body(c)["stop_matched"])

    def test_stop_timeout_late_ack_not_consumed(self):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        self.until(c, lambda: c.state is lv.State.STOPPING)
        link.block_read = True
        c.pump()
        link.now += 1000
        c.pump()
        self.assertEqual(c.runtime.snapshot().phase, Phase.RECOVERY)
        c.persist_attempt()
        self.assertEqual(self.body(c)["reason"], "TIMEOUT")
        self.assertFalse(self.body(c)["stop_matched"])

    def inject(self, c, link, kind, payload):
        s = c.runtime.snapshot()
        link.replacement = encode_frame(Frame(kind, s.session, s.next_rx_sequence, 0, payload))

    def test_numeric_status_error_retained_despite_runtime_drop(self):
        c, link = self.make()
        self.arm(c)
        s = c.runtime.snapshot()
        raw = struct.pack("<BBHIIIII", 5, 3, 0, s.generation, 0, 11, 2, 1)
        self.inject(c, link, 134, raw)
        c.pump()
        c.persist_attempt()
        body = self.body(c)
        self.assertEqual(body["reason"], "DEVICE_ERROR")
        self.assertIsNone(body["capture_sha256"])
        found = [f for f in packets(b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")) if f.message_type == 134]
        self.assertIn(raw, [f.payload for f in found])

    def test_early_abort_no_fabricated_capture(self):
        c, link = self.make()
        self.arm(c)
        s = c.runtime.snapshot()
        self.inject(c, link, 147, struct.pack("<IIHH", s.generation, 1, 2, 0))
        self.until(c, lambda: c.state is lv.State.SAVEABLE)
        c.persist_attempt()
        body = self.body(c)
        self.assertIsNone(body["capture_sha256"])
        self.assertIsNone(body["incomplete"])
        self.assertEqual(body["accepted"]["abort"]["decoded"]["reason"], 2)
        self.assertEqual(list(c.directory.glob("*.mtcap")), [])

    def corrupt_window(self, mutation):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        frames = packets(link.pending())
        link.replacement = b"".join(encode_frame(f) for f in mutation(frames))
        self.until(c, lambda: c.state is lv.State.SAVEABLE)
        c.persist_attempt()
        return c, self.body(c)

    def test_bad_sample_crc_preserves_all_fragments_and_original_end(self):
        def mutate(frames):
            return [replace(f, payload=f.payload[:-4] + bytes(4)) if f.message_type == 146 else f for f in frames]
        c, body = self.corrupt_window(mutate)
        self.assertEqual(body["incomplete"]["reason"], "CRC")
        self.assertEqual(sum(len(b["samples"]) for b in body["incomplete"]["fragments"]), 2048)
        self.assertEqual(body["incomplete"]["missing_ranges"], [])
        self.assertEqual(body["accepted"]["end"]["decoded"]["samples_crc32"], 0)
        self.assertIsNone(body["capture_sha256"])
        lv.verify_run(c.directory)

    def test_missing_data_preserves_ranges_without_padding(self):
        def mutate(frames):
            removed = False
            result = []
            for f in frames:
                if f.message_type == 145 and not removed:
                    removed = True
                    continue
                result.append(f)
            return result
        c, body = self.corrupt_window(mutate)
        self.assertEqual(body["incomplete"]["fragments"], [])
        self.assertEqual(body["incomplete"]["missing_ranges"], [[0, 2048]])
        self.assertIsNone(body["capture_sha256"])
        self.assertEqual(c.state, lv.State.FAILED)

    def test_frame_crc_invalid_raw_retained(self):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        raw = bytearray(link.pending())
        raw[20] ^= 0x20
        link.replacement = bytes(raw)
        self.until(c, lambda: c.state is lv.State.SAVEABLE)
        c.persist_attempt()
        body = self.body(c)
        observed = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
        self.assertIn(bytes(raw[:4096]), observed)
        self.assertIsNone(body["capture_sha256"])

    def test_duplicate_framed_data_observed_and_no_extra_samples(self):
        c, link = self.make()
        self.arm(c)
        link.device.trigger(link.now)
        frames = packets(link.pending())
        duplicate = next(i for i, f in enumerate(frames) if f.message_type == 145)
        frames.insert(duplicate + 1, frames[duplicate])
        link.replacement = b"".join(encode_frame(f) for f in frames)
        self.until(c, lambda: c.state is lv.State.SAVEABLE)
        c.persist_attempt()
        body = self.body(c)
        self.assertEqual(len(load(c.directory / "attempt-00.mtcap").samples), 2048)
        self.assertGreater(body["journal"]["raw_byte_count"], 4096)
        self.assertEqual(body["journal"]["identical_frame_replays"], 1)
        self.assertEqual(body["reason"], "REPEATED_DATA_BLOCK")
        self.assertEqual(body["attempt"]["outcome"], "failed")
        self.assertIsNotNone(c.runtime.pending_capture)

    def test_disconnect_partial_fragments_and_no_padding(self):
        c, link = self.make()
        self.arm(c)
        link.read_size = 544
        link.device.trigger(link.now)
        for _ in range(3):
            c.pump()
        link.fail_read = True
        c.pump()
        c.persist_attempt()
        body = self.body(c)
        received = sum(len(b["samples"]) for b in body["incomplete"]["fragments"])
        self.assertGreater(received, 0)
        self.assertLess(received, 2048)
        self.assertEqual(body["incomplete"]["missing_ranges"], [[received, 2048]])

    def test_storage_failure_never_acknowledges(self):
        c, link = self.make()
        capture = self.complete(c, link)
        with patch.object(lv.recording, "atomic_write", side_effect=OSError("disk")):
            with self.assertRaises(lv.CollectorError):
                c.persist_attempt()
        self.assertIs(c.runtime.pending_capture, capture)
        self.assertEqual(lv.verify_run(c.directory)["state"], "incomplete")
        with self.assertRaises(lv.CollectorError):
            c.arm_next()

    def test_stale_index_and_existing_sidecar_fail_closed(self):
        for stale_index in (True, False):
            with self.subTest(stale_index=stale_index):
                c, link = self.make()
                capture = self.complete(c, link)
                path = c.directory / ("index.json" if stale_index else "attempt-00.json")
                path.write_bytes(b"do not overwrite")
                with self.assertRaises(lv.CollectorError):
                    c.persist_attempt()
                self.assertEqual(path.read_bytes(), b"do not overwrite")
                self.assertIs(c.runtime.pending_capture, capture)

    def test_torn_sidecar_index_and_capture_detected(self):
        for filename in ("index.json", "attempt-00.json", "attempt-00.mtcap"):
            with self.subTest(filename=filename):
                c, link = self.make()
                self.complete(c, link)
                c.persist_attempt()
                path = c.directory / filename
                path.write_bytes(path.read_bytes()[:-1])
                with self.assertRaises((lv.CollectorError, ValueError)):
                    lv.verify_run(c.directory)

    def test_index_commit_failure_keeps_disk_pending_no_ack(self):
        c, link = self.make()
        capture = self.complete(c, link)
        original = lv.recording.atomic_write
        def fail(path, data, **options):
            if Path(path).name == "index.json":
                raise OSError("index")
            return original(path, data, **options)
        with patch.object(lv.recording, "atomic_write", side_effect=fail):
            with self.assertRaises(lv.CollectorError):
                c.persist_attempt()
        self.assertIs(c.runtime.pending_capture, capture)
        durable = lv.verify_run(c.directory)
        self.assertEqual(durable["attempts"][0]["outcome"], "pending")
        self.assertTrue((c.directory / "attempt-00.json").is_file())

    def test_cancel_after_warmup_does_not_replace_it(self):
        c, link = self.make()
        self.complete(c, link)
        c.persist_attempt()
        warmup = (c.directory / "attempt-00.json").read_bytes()
        c.cancel()
        c.persist_attempt()
        self.assertEqual((c.directory / "attempt-00.json").read_bytes(), warmup)
        index = lv.verify_run(c.directory)
        self.assertEqual(index["state"], "cancelled")
        self.assertEqual(index["attempts"][0]["outcome"], "completed")
        self.assertTrue(all(a["outcome"] == "cancelled" for a in index["attempts"][1:]))

    def test_empty_poll_coalescing_and_capacity_checked_before_read(self):
        c, link = self.make()
        self.arm(c)
        for _ in range(100):
            c.pump()
        empty = [r for r in c.observed.records if r["outcome"] == "empty"]
        self.assertTrue(any(r["count"] >= 100 for r in empty))
        with patch.object(lv, "MAX_JOURNAL_BYTES", c.observed.byte_count + 4095):
            before = len(link.rx)
            c.pump()
            self.assertEqual(len(link.rx), before)
        c.persist_attempt()
        self.assertEqual(self.body(c)["journal"]["failure"], "JOURNAL_OVERFLOW")

    def test_wrong_type_or_huge_transport_input_rejected_before_copy(self):
        for invalid in (bytearray(4096), b"x" * (1024 * 1024), [0] * 5000):
            with self.subTest(type=type(invalid)):
                c, link = self.make()
                self.arm(c)
                link.invalid_read = invalid
                c.pump()
                c.persist_attempt()
                self.assertEqual(self.body(c)["journal"]["failure"], "INVALID_READ")
                self.assertLess(c.observed.byte_count, 4096)

    def test_config_error_stops_and_persists_without_negotiation_hang(self):
        c, link = self.make(negotiate=False)
        self.until(c, lambda: c.runtime.snapshot().pending_request_id == 3)
        snap = c.runtime.snapshot()
        link.replacement = encode_frame(Frame(255, snap.session, snap.next_rx_sequence,
            snap.pending_request_id, struct.pack("<BBHI", 3, 1, 0, 0)))
        c.pump()
        self.assertEqual(c.state, lv.State.STOPPING)
        link.pending()  # Убираем подменённый настоящий CONFIG_ACK с тем же sequence.
        self.until(c, lambda: c.state is lv.State.SAVEABLE)
        c.persist_attempt()
        self.assertEqual(c.state, lv.State.FAILED)
        self.assertEqual(self.body(c)["reason"], "COMMAND_ERROR")
        self.assertIsNone(self.body(c)["capture_sha256"])
        self.assertFalse(any(a["arm_ack"] for a in lv.verify_run(c.directory)["attempts"]))

    def test_final_verification_failure_never_publishes_complete(self):
        for read_number in (3, 4):
            with self.subTest(read_number=read_number):
                c, link = self.make()
                for _ in range(20):
                    self.complete(c, link)
                    c.persist_attempt()
                capture = self.complete(c, link)
                original, calls = lv._read, [0]
                def fail(path, limit):
                    if path.name == "index.json":
                        calls[0] += 1
                        if calls[0] == read_number:
                            raise OSError("Повторное чтение индекса")
                    return original(path, limit)
                with patch.object(lv, "_read", side_effect=fail):
                    with self.assertRaises(lv.CollectorError):
                        c.persist_attempt()
                self.assertIs(c.runtime.pending_capture, capture)
                self.assertEqual(c.state, lv.State.FAILED)
                self.assertEqual(lv.verify_run(c.directory)["state"], "incomplete")
                self.assertEqual(c.storage_status, "INCOMPLETE")
                with self.assertRaises(lv.CollectorError):
                    c.arm_next()

    def test_strict_identity_config_and_json_bounds(self):
        with self.assertRaises(lv.CollectorError):
            lv.RunIdentity("simulator", "TBD", "TBD", vdda_measurement="x" * 513)
        with self.assertRaises(lv.CollectorError):
            lv.RunIdentity(True, "TBD", "TBD")
        c, link = self.make()
        with self.assertRaises(lv.CollectorError):
            lv.LVCollector(c.directory, link, clock_ms=lambda: 0, identity=lv.RunIdentity("simulator", "TBD", "TBD"), config=Config(), nonce=1)
        path = c.directory / "index.json"
        path.write_bytes(b" " * (lv.MAX_INDEX_BYTES + 1))
        with self.assertRaises(lv.CollectorError):
            lv.verify_run(c.directory)


if __name__ == "__main__":
    unittest.main()
