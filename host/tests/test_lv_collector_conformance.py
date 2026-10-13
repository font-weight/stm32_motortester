"""Независимые LV-001 conformance-проверки: реальные байты → Stop → диск.

Автор production не писал этот набор. Это не заключение Critic-B и не
низковольтное измерение. Все проверки остаются включены при python -O.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import contextlib
import gc
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

from motortester import lv_collector as lv
from motortester.device_emulator import DeviceEmulator
from motortester.recording import load
from motortester.runtime import AcquisitionRuntime
from motortester.session import Config, Phase


_SPEC = importlib.util.spec_from_file_location(
    "lv_independent_reference", Path(__file__).parent / "fixtures" / "lv_collector" / "reference.py")
ref = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ref
_SPEC.loader.exec_module(ref)


class Checks(unittest.TestCase):
    def make(self, *, emulator=False, config=None):
        temporary = tempfile.TemporaryDirectory(prefix="lv-independent-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "run"
        clock = ref.Clock()
        peer = (ref.EmulatorTransport(DeviceEmulator(all_analog_modes=True), clock)
                if emulator else ref.BytePeer(clock))
        config = config or Config(mode=0, auto_timeout_ms=100)
        collector = lv.LVCollector(root, peer, clock_ms=clock,
            identity=lv.RunIdentity("simulator", ref.BASE, ref.TREE), config=config,
            nonce=ref.NONCE, utc_now=lambda: "2026-10-12T06:00:00Z")
        return collector, peer, clock, root

    def pump(self, collector, peer):
        count = len(peer.calls)
        result = collector.pump()
        fresh = peer.calls[count:]
        self.assertLessEqual(sum(kind == "read" for kind, _ in fresh), 1)
        self.assertLessEqual(sum(kind == "write" for kind, _ in fresh), 1)
        for _, size in fresh:
            self.assertLessEqual(size, 4096)
        return result

    def until(self, collector, peer, state, limit=10000):
        for _ in range(limit):
            if collector.state is state:
                return
            self.assertIn(collector.state, (lv.State.NEGOTIATING, lv.State.ACTIVE, lv.State.STOPPING))
            self.pump(collector, peer)
        self.fail(f"Не достигнуто состояние {state}; последнее {collector.state}")

    def ready(self, **kwargs):
        collector, peer, clock, root = self.make(**kwargs)
        collector.start()
        self.until(collector, peer, lv.State.READY)
        return collector, peer, clock, root

    def armed(self, **kwargs):
        collector, peer, clock, root = self.ready(**kwargs)
        collector.arm_next()
        while collector.runtime.snapshot().phase is not Phase.ARMED:
            self.pump(collector, peer)
        return collector, peer, clock, root

    def saved(self, **kwargs):
        collector, peer, clock, root = self.armed(**kwargs)
        peer.window_frames()
        self.until(collector, peer, lv.State.SAVEABLE)
        collector.persist_attempt()
        return collector, peer, clock, root

    def index(self, root):
        return json.loads((root / "index.json").read_bytes())["body"]

    def sidecar(self, root, number=0):
        return json.loads((root / f"attempt-{number:02d}.json").read_bytes())["body"]

    def final_stopped(self, *, final_options=None):
        c, p, clock, root = self.ready()
        for number in range(21):
            c.arm_next()
            while c.runtime.snapshot().phase is not Phase.ARMED:
                self.pump(c, p)
            p.window_frames(**(final_options or {} if number == 20 else {}))
            self.until(c, p, lv.State.SAVEABLE)
            if number < 20:
                c.persist_attempt()
        return c, p, clock, root

    def change_index(self, root, change):
        body = self.index(root)
        change(body)
        (root / "index.json").write_bytes(ref.envelope("mtlv-index", body))

    def change_sidecar(self, root, change, number=0):
        body = self.sidecar(root, number)
        change(body)
        path = root / f"attempt-{number:02d}.json"
        raw = ref.envelope("mtlv-sidecar", body)
        path.write_bytes(raw)
        self.change_index(root, lambda index: index["attempts"][number].update(sidecar_sha256=sha256(raw).hexdigest()))

    def fail_saved(self, collector, peer, root):
        self.until(collector, peer, lv.State.SAVEABLE)
        pending = collector.runtime.pending_capture
        incomplete = collector.runtime.incomplete_result
        with patch.object(collector.runtime, "acknowledge", wraps=collector.runtime.acknowledge) as ack:
            result = collector.persist_attempt()
        ack.assert_not_called()
        self.assertIn(result["outcome"], ("failed", "cancelled"))
        self.assertIs(collector.state, lv.State.FAILED)
        self.assertIs(collector.runtime.pending_capture, pending)
        self.assertIs(collector.runtime.incomplete_result, incomplete)
        recovered = lv.verify_run(root)
        self.assertNotEqual(recovered["state"], "complete")
        self.assertEqual(recovered["artifact_integrity"], "PARTIAL_VERIFIED")
        self.assertEqual(recovered["prior_writer_outcome"], "UNKNOWN")
        self.assertEqual(len(self.index(root)["attempts"]), 21)
        self.assertTrue(all(a["outcome"] == "cancelled" for a in self.index(root)["attempts"][1:]))
        with self.assertRaises(lv.CollectorError):
            collector.arm_next()
        return self.sidecar(root)


class IndependentReferenceTests(Checks):
    def test_reference_crc_and_packet_known_values(self):
        self.assertEqual(ref.crc(b"123456789"), 0xCBF43926)
        packet = ref.encode(1, 1, 1, ref.u(ref.NONCE, 8), session=0)
        self.assertEqual(ref.decode(packet), ref.Packet(1, 0, 1, 1, ref.u(ref.NONCE, 8)))
        self.assertEqual(ref.uncobs(ref.cobs(bytes(range(256)) * 2)), bytes(range(256)) * 2)
        self.assertEqual(len(ref.CAPS), 30)
        self.assertEqual(len(ref.HELLO_ACK), 16)


class SuccessfulRunTests(Checks):
    def test_plan_persisted_before_handshake_and_arm_intent_before_transport(self):
        c, p, _, root = self.make()
        c.start()
        self.assertEqual(p.calls, [])
        initial = self.index(root)
        self.assertEqual(initial["state"], "incomplete")
        self.assertEqual([a["number"] for a in initial["attempts"]], list(range(21)))
        self.assertEqual([a["role"] for a in initial["attempts"]], ["warmup"] + ["series"] * 20)
        self.assertTrue(all(a["outcome"] == "planned" and a["intended"] for a in initial["attempts"]))
        self.until(c, p, lv.State.READY)
        before = len(p.tx_log)
        c.arm_next()
        intent = lv.verify_run(root)["attempts"][0]
        self.assertTrue(intent["intent_committed"])
        self.assertIsNone(intent["arm_queued"])
        self.assertFalse(intent["arm_ack"])
        self.assertEqual(intent["tx_known_bytes"], 0)
        self.assertEqual(len(p.tx_log), before)
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3])
        self.assertEqual(intent["outcome"], "pending")

    def test_21_true_byte_windows_exact_metadata_reopen_warmup_excluded(self):
        for edge, timeout in ((0, 100), (1, 60000)):
            with self.subTest(edge=edge, timeout=timeout):
                c, p, clock, root = self.ready(emulator=True,
                    config=Config(mode=0, edge=edge, level_code=1234, auto_timeout_ms=timeout))
                all_keys = set()
                ack_count = 0
                original_ack = c.runtime.acknowledge

                def checked_ack(capture):
                    nonlocal ack_count
                    self.assertIs(capture, c.runtime.pending_capture)
                    self.assertIs(c.runtime.snapshot().phase, Phase.READY)
                    self.assertFalse(c.runtime.snapshot().stop_barrier)
                    disk = lv.verify_run(root)
                    self.assertEqual(disk["attempts"][ack_count]["outcome"], "completed")
                    self.assertEqual(load(root / f"attempt-{ack_count:02d}.mtcap"), capture)
                    ack_count += 1
                    return original_ack(capture)

                with patch.object(c.runtime, "acknowledge", side_effect=checked_ack):
                    for number in range(21):
                        c.arm_next()
                        while c.runtime.snapshot().phase is not Phase.ARMED:
                            self.pump(c, p)
                        generation = c.runtime.snapshot().generation
                        signal = number % 2 == 1
                        if signal:
                            self.assertTrue(p.device.trigger(clock.now))
                        else:
                            clock.now += timeout
                        self.until(c, p, lv.State.STOPPING)
                        capture = c.runtime.pending_capture
                        self.assertIsNotNone(capture)
                        self.assertEqual(ack_count, number)
                        self.assertEqual(capture.samples, ref.codes(generation, number + 1, edge, 1234, not signal))
                        self.assertFalse((root / f"attempt-{number:02d}.mtcap").exists())
                        with self.assertRaises(lv.CollectorError):
                            c.persist_attempt()
                        with self.assertRaises(lv.CollectorError):
                            c.arm_next()
                        self.until(c, p, lv.State.SAVEABLE)
                        c.persist_attempt()
                        body = self.sidecar(root, number)
                        self.assertEqual(body["attempt"]["role"], "warmup" if number == 0 else "series")
                        self.assertTrue(body["attempt"]["intent_committed"])
                        self.assertTrue(body["attempt"]["arm_queued"])
                        self.assertTrue(body["attempt"]["arm_ack"])
                        self.assertGreater(body["attempt"]["tx_known_bytes"], 0)
                        self.assertFalse(body["attempt"]["tx_unknown"])
                        self.assertTrue(body["stop_matched"])
                        self.assertIsNone(body["incomplete"])
                        all_frames = ref.packets(b"".join(p.rx_log))
                        accepted = body["accepted"]
                        for name, kind in (("hello", 129), ("caps", 130), ("config", 131),
                                           ("begin", 144), ("end", 146), ("stop", 133)):
                            actual = accepted[name]
                            expected = [f for f in all_frames if f.kind == kind][-1]
                            self.assertEqual(actual["payload_hex"], expected.payload.hex())
                            self.assertEqual((actual["session"], actual["sequence"], actual["request_id"]),
                                             (expected.session, expected.sequence, expected.request))
                        b, caps = accepted["begin"]["decoded"], accepted["caps"]["decoded"]
                        self.assertEqual((b["trigger_kind"], b["trigger_index"], b["quality_flags"]),
                                         (0, 0, 2) if signal else (2, 65535, 3))
                        self.assertEqual((b["generation"], b["window_id"], b["config_id"], b["sample_count"]),
                                         (generation, number + 1, 1, 2048))
                        self.assertEqual((b["source"], b["channel"], b["external_edge_id"], b["edge"], b["level_code"]),
                                         (0, 0, 0, edge, 1234))
                        self.assertEqual((b["timer_clock_hz"], b["timer_psc"], b["timer_arr"]), (72000000, 71, 19))
                        self.assertEqual((caps["adc_clock_hz"], caps["adc_sample_cycles_x2"]), (12000000, 143))
                        self.assertEqual(accepted["config"]["decoded"]["auto_timeout_ms"], timeout)
                        self.assertEqual(accepted["end"]["decoded"]["samples_crc32"], ref.crc(ref.sample_bytes(capture.samples)))
                        mtcap = (root / f"attempt-{number:02d}.mtcap").read_bytes()
                        magic, metadata_size, count = struct.unpack_from("<8sII", mtcap)
                        self.assertEqual((magic, count), (b"MTCAP\0\1\0", 2048))
                        self.assertEqual(mtcap[16 + metadata_size:-32], ref.sample_bytes(capture.samples))
                        self.assertEqual(sha256(mtcap[:-32]).digest(), mtcap[-32:])
                        self.assertEqual(body["capture_sha256"], sha256(mtcap).hexdigest())
                        self.assertIsNone(c.runtime.pending_capture)
                        all_keys.add((accepted["begin"]["session"], b["window_id"]))
                        before = len(p.tx_log)
                        with self.assertRaises(lv.CollectorError):
                            c.pump()
                        self.assertEqual(before, len(p.tx_log))
                self.assertIs(c.state, lv.State.COMPLETE)
                verified = lv.verify_run(root)
                self.assertEqual(verified["state"], "sealed_unverified")
                self.assertEqual(verified["artifact_integrity"], "RECOVERED_VERIFIED")
                self.assertEqual(verified["prior_writer_outcome"], "UNKNOWN")
                self.assertEqual(self.index(root)["state"], "sealed_unverified")
                self.assertNotIn("prior_writer_outcome", self.index(root))
                self.assertEqual((ack_count, len(all_keys)), (21, 21))
                self.assertEqual(sum(a["outcome"] == "completed" for a in verified["attempts"][1:]), 20)
                self.assertEqual([f.kind for f in ref.packets(b"".join(p.tx_log))], [1, 2, 3] + [4, 5] * 21)
                self.assertEqual(len(list(root.glob("*.mtcap"))), 21)
                for key in ("hardware_passport_id", "schematic_id", "firmware_commit", "application_build",
                            "board_json_sha256", "vdda_measurement", "adc_in_measurement",
                            "independent_timebase", "measured_fs_and_uncertainty"):
                    self.assertEqual(verified["identity"][key], "TBD")

    def test_one_byte_reads_and_writes_keep_stop_barrier_until_last_ack_byte(self):
        c, p, _, root = self.armed()
        p.window_frames(timeout=False)
        p.read_limit = 1
        p.write_limit = 1
        self.until(c, p, lv.State.STOPPING)
        capture = c.runtime.pending_capture
        count = 0
        while c.state is lv.State.STOPPING:
            self.assertIs(c.runtime.pending_capture, capture)
            with self.assertRaises(lv.CollectorError):
                c.persist_attempt()
            self.pump(c, p)
            count += 1
        self.assertGreater(count, 35)
        self.assertIs(c.state, lv.State.SAVEABLE)
        c.persist_attempt()
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])
        self.assertEqual(self.sidecar(root)["accepted"]["begin"]["decoded"]["trigger_kind"], 0)

    def test_fragmented_arm_records_queue_known_prefix_and_ack_separately(self):
        c, p, _, root = self.ready()
        p.write_limit, p.read_limit = 1, 1
        c.arm_next()
        self.pump(c, p)
        a = c.index["attempts"][0]
        self.assertEqual((a["arm_queued"], a["tx_known_bytes"], a["arm_ack"]), (True, 1, False))
        self.assertEqual(self.index(root)["attempts"][0]["tx_known_bytes"], 0)
        while not c.index["attempts"][0]["arm_ack"]:
            self.pump(c, p)
        p.read_limit = 4096
        p.window_frames()
        self.until(c, p, lv.State.SAVEABLE)
        c.persist_attempt()
        journal = self.sidecar(root)["journal"]
        self.assertTrue(journal["observation_only"])
        self.assertEqual(journal["raw_byte_count"], sum(len(r["data_hex"]) // 2 for r in journal["records"]))
        self.assertEqual(self.sidecar(root)["attempt"]["tx_known_bytes"], len(ref.encode(4, 4, 4, ref.u(1))))


class FailureEvidenceTests(Checks):
    def test_early_abort_without_begin_preserves_numeric_reason_no_fake_capture(self):
        for reason in (1, 2, 6):
            with self.subTest(reason=reason):
                c, p, _, root = self.armed()
                raw = p.early_abort(reason)
                body = self.fail_saved(c, p, root)
                self.assertIsNone(body["accepted"]["begin"])
                self.assertEqual(body["accepted"]["abort"]["decoded"]["reason"], reason)
                self.assertIsNone(body["capture_sha256"])
                self.assertEqual(list(root.glob("*.mtcap")), [])
                self.assertIn(raw, b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx"))
                self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])

    def test_numeric_status_error_reasons_survive_runtime_recovery(self):
        for reason in range(1, 7):
            with self.subTest(reason=reason):
                c, p, _, root = self.armed()
                raw = p.reply(134, ref.status(5, p.generation, 0, reason, observed=7, busy=3, dropped=2))
                body = self.fail_saved(c, p, root)
                rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
                self.assertIn(raw, rx)
                statuses = [f for f in ref.packets(rx) if f.kind == 134]
                self.assertEqual(statuses[-1].payload, ref.status(5, p.generation, 0, reason, observed=7, busy=3, dropped=2))
                self.assertEqual(body["reason"], "DEVICE_ERROR")
                self.assertIsNone(body["accepted"]["stop"])
                self.assertFalse(body["stop_matched"])

    def test_crc_bad_observed_bytes_retained_without_false_frame_admission(self):
        c, p, _, root = self.armed()
        original = ref.encode(147, p.sequence, payload=ref.u(p.generation) + ref.u(1) + ref.u(2, 2) + b"\0\0")
        decoded = ref.uncobs(original[:-1])
        corrupt = ref.cobs(decoded[:-1] + bytes((decoded[-1] ^ 1,))) + b"\0"
        p.rx.extend(corrupt)
        body = self.fail_saved(c, p, root)
        self.assertIn(corrupt, b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx"))
        self.assertIsNone(body["accepted"]["abort"])
        self.assertIsNone(body["accepted"]["begin"])
        self.assertEqual(list(root.glob("*.mtcap")), [])

    def test_missing_duplicate_and_wrong_end_crc_keep_unpadded_fragments(self):
        cases = ((dict(omit_offset=250), 250),
                 (dict(duplicate_offset=0, conflicting_duplicate=True), 250),
                 (dict(bad_sample_crc=True), 2048))
        for options, count in cases:
            with self.subTest(options=options):
                c, p, _, root = self.armed()
                expected = p.window_frames(**options)
                body = self.fail_saved(c, p, root)
                result = body["incomplete"]
                self.assertIsNotNone(result)
                actual = tuple(value for block in result["fragments"] for value in block["samples"])
                self.assertEqual(actual, expected[:count])
                self.assertEqual(result["missing_ranges"], [[count, 2048]] if count < 2048 else [])
                self.assertIsNone(body["capture_sha256"])
                self.assertEqual(list(root.glob("*.mtcap")), [])

    def test_disconnect_keeps_one_partial_window_and_cancels_remaining_attempts(self):
        c, p, _, root = self.armed()
        expected = p.window_frames(last_offset=500)
        self.pump(c, p)
        p.read_failure = OSError("Синтетический обрыв")
        body = self.fail_saved(c, p, root)
        fragments = body["incomplete"]["fragments"]
        self.assertEqual(tuple(v for b in fragments for v in b["samples"]), expected[:500])
        self.assertEqual(body["incomplete"]["missing_ranges"], [[500, 2048]])
        self.assertEqual(body["incomplete"]["reason"], "DISCONNECT")
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4])

    def test_bad_stop_and_stop_error_preserve_complete_capture_without_ack(self):
        for mode in ("bad_generation", "error"):
            with self.subTest(mode=mode):
                c, p, _, root = self.armed()
                p.stop_mode = mode
                expected = p.window_frames()
                body = self.fail_saved(c, p, root)
                self.assertFalse(body["stop_matched"])
                self.assertIsNone(body["accepted"]["stop"])
                self.assertEqual(load(root / "attempt-00.mtcap").samples, expected)
                self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])

    def test_stop_timeout_preserves_capture_and_never_retries_or_rearms(self):
        c, p, clock, root = self.armed()
        p.stop_mode = "silent"
        p.window_frames()
        self.until(c, p, lv.State.STOPPING)
        self.pump(c, p)
        clock.now += 1000
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["reason"], "TIMEOUT")
        self.assertFalse(body["stop_matched"])
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])


class StorageConformanceTests(Checks):
    def test_final_seal_readback_failure_with_rollback_disk_failure_is_not_writer_complete(self):
        c, p, _, root = self.final_stopped()
        atomic, read = lv.recording.atomic_write, lv._read
        complete_written = False
        fail_read = False

        def write(path, data, **kwargs):
            nonlocal complete_written, fail_read
            if Path(path).name == "index.json":
                state = json.loads(data)["body"]["state"]
                if complete_written and state != "sealed_unverified":
                    raise OSError("ENOSPC при rollback")
                result = atomic(path, data, **kwargs)
                if state == "sealed_unverified":
                    complete_written = fail_read = True
                return result
            return atomic(path, data, **kwargs)

        def failed_read(path, limit):
            nonlocal fail_read
            if Path(path).name == "index.json" and fail_read:
                fail_read = False
                raise OSError("Финальное чтение недоступно")
            return read(path, limit)

        capture = c.runtime.pending_capture
        with patch.object(lv.recording, "atomic_write", side_effect=write), patch.object(lv, "_read", side_effect=failed_read):
            with patch.object(c.runtime, "acknowledge", wraps=c.runtime.acknowledge) as ack:
                with self.assertRaises(lv.CollectorError):
                    c.persist_attempt()
        self.assertEqual(ack.call_count, 0)
        self.assertIs(c.runtime.pending_capture, capture)
        self.assertTrue(complete_written)
        try:
            reopened = lv.verify_run(root)
        except (lv.CollectorError, OSError):
            pass
        else:
            self.assertNotEqual(reopened["state"], "complete", "Двойной отказ оставил ложное полное завершение")
            self.assertEqual(reopened["prior_writer_outcome"], "UNKNOWN")
            self.assertEqual(reopened["artifact_integrity"], "RECOVERED_VERIFIED")

    def test_interrupt_after_seal_publication_before_readback_is_not_writer_complete(self):
        class SimulatedInterruption(BaseException):
            pass

        c, p, _, root = self.final_stopped()
        atomic, read = lv.recording.atomic_write, lv._read
        complete_written = False

        def write(path, data, **kwargs):
            nonlocal complete_written
            result = atomic(path, data, **kwargs)
            if Path(path).name == "index.json" and json.loads(data)["body"]["state"] == "sealed_unverified":
                complete_written = True
            return result

        def interrupted(path, limit):
            if Path(path).name == "index.json" and complete_written:
                raise SimulatedInterruption()
            return read(path, limit)

        with patch.object(lv.recording, "atomic_write", side_effect=write), patch.object(lv, "_read", side_effect=interrupted):
            with patch.object(c.runtime, "acknowledge", wraps=c.runtime.acknowledge) as ack:
                with self.assertRaises(SimulatedInterruption):
                    c.persist_attempt()
        self.assertEqual(ack.call_count, 0)
        self.assertIsNotNone(c.runtime.pending_capture)
        try:
            reopened = lv.verify_run(root)
        except (lv.CollectorError, OSError):
            pass
        else:
            self.assertNotEqual(reopened["state"], "complete", "Прерывание до reopen/ACK выдаётся за завершение")
            self.assertEqual(reopened["prior_writer_outcome"], "UNKNOWN")
            self.assertEqual(reopened["artifact_integrity"], "RECOVERED_VERIFIED")

    def stopped(self):
        c, p, clock, root = self.armed()
        p.window_frames()
        self.until(c, p, lv.State.SAVEABLE)
        return c, p, clock, root

    def expect_storage_failure(self, c, p, root):
        capture = c.runtime.pending_capture
        before = list(p.commands)
        with patch.object(c.runtime, "acknowledge", wraps=c.runtime.acknowledge) as ack:
            with self.assertRaises(lv.CollectorError):
                c.persist_attempt()
        ack.assert_not_called()
        self.assertIs(c.runtime.pending_capture, capture)
        self.assertIs(c.state, lv.State.FAILED)
        with self.assertRaises(lv.CollectorError):
            c.arm_next()
        self.assertEqual(p.commands, before)

    def test_existing_run_directory_is_never_reused_even_when_empty(self):
        c, p, _, root = self.make()
        root.mkdir()
        with self.assertRaises(lv.CollectorError):
            c.start()
        self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(p.calls, [])

    def test_start_disk_failure_has_no_handshake(self):
        c, p, _, root = self.make()
        with patch.object(lv.recording, "atomic_write", side_effect=OSError("ENOSPC")):
            with self.assertRaises(lv.CollectorError):
                c.start()
        self.assertEqual(p.calls, [])
        self.assertIs(c.state, lv.State.FAILED)
        self.assertFalse((root / "index.json").exists())

    def test_intent_disk_failure_has_no_arm_write(self):
        c, p, _, root = self.ready()
        previous = (root / "index.json").read_bytes()
        with patch.object(lv.recording, "atomic_write", side_effect=OSError("ENOSPC")):
            with self.assertRaises(lv.CollectorError):
                c.arm_next()
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3])
        self.assertEqual((root / "index.json").read_bytes(), previous)

    def test_old_capture_or_sidecar_is_preserved_without_ack(self):
        for suffix in ("mtcap", "json"):
            with self.subTest(suffix=suffix):
                c, p, _, root = self.stopped()
                old = root / f"attempt-00.{suffix}"
                old.write_bytes(b"old artifact")
                self.expect_storage_failure(c, p, root)
                self.assertEqual(old.read_bytes(), b"old artifact")
                self.assertNotEqual(lv.verify_run(root)["state"], "complete")

    def test_disk_failure_at_each_publication_boundary_retains_pending_object(self):
        for target in ("attempt-00.mtcap", "attempt-00.json", "index.json"):
            with self.subTest(target=target):
                c, p, _, root = self.stopped()
                atomic = lv.recording.atomic_write

                def fail(path, data, **kwargs):
                    if Path(path).name == target:
                        raise OSError("ENOSPC")
                    return atomic(path, data, **kwargs)

                with patch.object(lv.recording, "atomic_write", side_effect=fail):
                    self.expect_storage_failure(c, p, root)
                self.assertEqual(lv.verify_run(root)["state"], "incomplete")
                self.assertEqual(self.index(root)["attempts"][0]["outcome"], "pending")
                if target != "attempt-00.mtcap":
                    self.assertEqual(load(root / "attempt-00.mtcap"), c.runtime.pending_capture)

    def test_torn_readback_at_each_boundary_forbids_ack_and_next_arm(self):
        for target in ("attempt-00.mtcap", "attempt-00.json", "index.json"):
            with self.subTest(target=target):
                c, p, _, root = self.stopped()
                atomic = lv.recording.atomic_write

                def tear(path, data, **kwargs):
                    result = atomic(path, data, **kwargs)
                    if Path(path).name == target:
                        Path(path).write_bytes(data[:len(data) // 2])
                    return result

                with patch.object(lv.recording, "atomic_write", side_effect=tear):
                    self.expect_storage_failure(c, p, root)
                if target == "index.json":
                    with self.assertRaises(lv.CollectorError):
                        lv.verify_run(root)
                else:
                    self.assertEqual(lv.verify_run(root)["state"], "incomplete")

    def test_stale_index_detected_before_any_capture_or_sidecar_write(self):
        c, p, _, root = self.stopped()
        self.change_index(root, lambda body: body.update(reason="Внешняя правка"))
        changed = (root / "index.json").read_bytes()
        self.expect_storage_failure(c, p, root)
        self.assertEqual((root / "index.json").read_bytes(), changed)
        self.assertEqual(list(root.glob("*.mtcap")), [])
        self.assertEqual(list(root.glob("attempt-*.json")), [])

    def test_corrupt_capture_hash_and_sidecar_hash_are_rejected(self):
        for name in ("attempt-00.mtcap", "attempt-00.json"):
            with self.subTest(name=name):
                _, _, _, root = self.saved()
                path = root / name
                content = bytearray(path.read_bytes())
                content[len(content) // 2] ^= 1
                path.write_bytes(content)
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

    def test_file_symlink_is_not_followed(self):
        _, _, _, root = self.saved()
        side = root / "attempt-00.json"
        moved = root.parent / "external.json"
        side.rename(moved)
        side.symlink_to(moved)
        with self.assertRaises(lv.CollectorError):
            lv.verify_run(root)


class StrictSchemaTests(Checks):
    def test_unknown_index_fields_nested_and_noncanonical_json_are_rejected(self):
        mutations = (
            lambda b: b.update(unrequested=1),
            lambda b: b["identity"].update(unrequested=1),
            lambda b: b["attempts"][0].update(unrequested=1),
            lambda b: b["requested_config"].update(unrequested=1),
            lambda b: b["attempts"][0].update(number=False),
            lambda b: b.update(requested_fs_hz=50000.0),
            lambda b: b.update(attempts=b["attempts"] + [deepcopy(b["attempts"][0])]),
        )
        for n, change in enumerate(mutations):
            with self.subTest(mutation=n):
                _, _, _, root = self.saved()
                self.change_index(root, change)
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)
        _, _, _, root = self.saved()
        path = root / "index.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(lv.CollectorError):
            lv.verify_run(root)

    def test_unknown_sidecar_fields_are_rejected_with_recomputed_hashes(self):
        mutations = (
            lambda b: b.update(unrequested=1),
            lambda b: b["accepted"].update(unrequested=None),
            lambda b: b["accepted"]["begin"].update(unrequested=1),
            lambda b: b["accepted"]["begin"]["decoded"].update(unrequested=1),
            lambda b: b["journal"].update(unrequested=1),
            lambda b: b["journal"]["records"][0].update(unrequested=1),
        )
        for n, change in enumerate(mutations):
            with self.subTest(mutation=n):
                _, _, _, root = self.saved()
                self.change_sidecar(root, change)
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

    def test_bool_and_float_are_not_accepted_as_numeric_frame_fields(self):
        for name, field, bad in (("begin", "source", False), ("begin", "channel", False),
                                 ("begin", "edge", False), ("begin", "sample_count", 2048.0),
                                 ("caps", "profile", True), ("config", "mode", False),
                                 ("stop", "status", False)):
            with self.subTest(frame=name, field=field):
                _, _, _, root = self.saved()
                self.change_sidecar(root, lambda b: b["accepted"][name]["decoded"].update({field: bad}))
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

    def test_incomplete_metadata_is_strict_not_an_unchecked_json_bucket(self):
        mutations = (
            lambda r: r.update(complete="false"),
            lambda r: r.update(rail_observed=0),
            lambda r: r.update(abort_reason=True),
            lambda r: r.update(samples_crc32="CRC"),
            lambda r: r.update(deferred_checks={"anything": 1}),
            lambda r: r.update(metadata_issues=[False]),
            lambda r: r["begin"].update(unrequested=1),
            lambda r: r["begin"].update(source=False),
        )
        for n, change in enumerate(mutations):
            with self.subTest(mutation=n):
                c, p, _, root = self.armed()
                p.window_frames(last_offset=250)
                self.pump(c, p)
                p.read_failure = OSError("Обрыв")
                self.fail_saved(c, p, root)
                self.change_sidecar(root, lambda b: change(b["incomplete"]))
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for bad in (b'{"format":"mtlv-index","format":"mtlv-index"}', b'{"x":NaN}', b'[' * 2000 + b']' * 2000):
            with self.subTest(prefix=bad[:40]):
                _, _, _, root = self.saved()
                (root / "index.json").write_bytes(bad)
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

    def test_oversized_index_sidecar_and_mtcap_are_rejected_boundedly(self):
        for file, limit in (("index.json", 65536), ("attempt-00.json", 2 * 1024 * 1024),
                            ("attempt-00.mtcap", 16 + 16384 + 65536 * 2 + 32)):
            with self.subTest(file=file):
                _, _, _, root = self.saved()
                (root / file).write_bytes(b" " * (limit + 1))
                with self.assertRaises(lv.CollectorError):
                    lv.verify_run(root)

class AdditionalFailureEvidenceTests(Checks):
    def test_cancel_at_zero_first_penultimate_and_full_end_prefixes_on_real_emulator(self):
        for prefix in ("zero", "first", "penultimate", "full"):
            with self.subTest(prefix=prefix):
                c, p, clock, root = self.armed(emulator=True)
                clock.now += 100
                p.read_limit = 1
                pending = bytearray()
                got_last_data = False
                for _ in range(6000):
                    self.pump(c, p)
                    pending.extend(p.rx_log[-1])
                    if pending.endswith(b"\0"):
                        packet = ref.decode(bytes(pending))
                        pending.clear()
                        if packet.kind == 145:
                            offset = int.from_bytes(packet.payload[8:10], "little")
                            count = int.from_bytes(packet.payload[10:12], "little")
                            if offset + count == 2048:
                                got_last_data = True
                                break
                self.assertTrue(got_last_data)
                self.assertIsNone(c.runtime.pending_capture)
                self.assertIsNone(p.device.snapshot().terminal)
                generation = c.runtime.snapshot().generation
                expected = ref.codes(generation, 1)
                end_payload = ref.u(generation) + ref.u(1) + ref.u(2048, 2) + b"\0\0" + ref.u(ref.crc(ref.sample_bytes(expected)))
                end_packet = ref.encode(146, p.device.snapshot().next_tx_sequence, payload=end_payload)
                count = {"zero": 0, "first": 1, "penultimate": len(end_packet) - 1,
                         "full": len(end_packet)}[prefix]
                if count:
                    p.read_limit = count
                    self.pump(c, p)
                    self.assertEqual(p.rx_log[-1], end_packet[:count])
                if prefix == "full":
                    self.assertIsNotNone(c.runtime.pending_capture)
                    self.assertIs(c.state, lv.State.STOPPING)
                    already_queued_stop = c.runtime.snapshot().pending_stop_id
                else:
                    self.assertIsNone(c.runtime.pending_capture)
                    self.assertIs(c.state, lv.State.ACTIVE)
                    already_queued_stop = 0
                c.cancel()
                if already_queued_stop:
                    self.assertEqual(c.runtime.snapshot().pending_stop_id, already_queued_stop)
                p.read_limit = 1
                body = self.fail_saved(c, p, root)
                self.assertTrue(body["stop_matched"])
                self.assertEqual(body["attempt"]["outcome"], "cancelled")
                self.assertEqual([f.kind for f in ref.packets(b"".join(p.tx_log))], [1, 2, 3, 4, 5])
                if prefix == "zero":
                    self.assertIsNone(body["accepted"]["end"])
                    self.assertEqual(body["accepted"]["abort"]["decoded"]["reason"], 1)
                    self.assertIsNone(body["capture_sha256"])
                    fragments = body["incomplete"]["fragments"]
                    self.assertEqual(tuple(v for block in fragments for v in block["samples"]), expected)
                    self.assertEqual(body["incomplete"]["reason"], "ABORT")
                    self.assertEqual(list(root.glob("*.mtcap")), [])
                else:
                    self.assertEqual(body["accepted"]["end"]["payload_hex"], end_payload.hex())
                    self.assertIsNone(body["accepted"]["abort"])
                    self.assertIsNone(body["incomplete"])
                    self.assertEqual(load(root / "attempt-00.mtcap").samples, expected)

    def test_cancel_before_fragmented_arm_ack_keeps_real_ack_fact_and_stop_order(self):
        c, p, _, root = self.ready()
        p.write_limit = p.read_limit = 1
        c.arm_next()
        self.pump(c, p)
        self.assertFalse(c.index["attempts"][0]["arm_ack"])
        c.cancel()
        body = self.fail_saved(c, p, root)
        self.assertTrue(body["attempt"]["arm_ack"])
        self.assertTrue(body["attempt"]["arm_queued"])
        self.assertTrue(body["stop_matched"])
        self.assertEqual(body["attempt"]["outcome"], "cancelled")
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])
        self.assertEqual(body["attempt"]["tx_known_bytes"], len(ref.encode(4, 4, 4, ref.u(1))))

    def test_cancel_ready_after_warmup_preserves_previous_success_and_unused_attempts(self):
        c, p, _, root = self.saved()
        saved = (root / "attempt-00.mtcap").read_bytes()
        c.cancel()
        self.assertIs(c.state, lv.State.SAVEABLE)
        result = c.persist_attempt()
        self.assertEqual((result["number"], result["role"], result["outcome"]), (1, "series", "cancelled"))
        index = lv.verify_run(root)
        self.assertEqual(index["attempts"][0]["outcome"], "completed")
        self.assertTrue(all(a["outcome"] == "cancelled" for a in index["attempts"][1:]))
        self.assertFalse(result["intent_committed"])
        self.assertFalse(result["arm_queued"])
        self.assertFalse(result["arm_ack"])
        self.assertEqual((root / "attempt-00.mtcap").read_bytes(), saved)
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])

    def test_equal_samples_at_distinct_offsets_and_windows_are_valid(self):
        c, p, _, root = self.ready()
        for number in range(2):
            c.arm_next()
            while c.runtime.snapshot().phase is not Phase.ARMED:
                self.pump(c, p)
            p.window_frames(values_override=(1234,) * 2048)
            self.until(c, p, lv.State.SAVEABLE)
            c.persist_attempt()
            body = self.sidecar(root, number)
            self.assertEqual(body["attempt"]["outcome"], "completed")
            self.assertEqual(body["journal"]["identical_frame_replays"], 0)
            self.assertEqual(load(root / f"attempt-{number:02d}.mtcap").samples, (1234,) * 2048)
            self.assertEqual(body["accepted"]["begin"]["decoded"]["window_id"], number + 1)

    def test_cancel_during_fragmented_emulator_data_keeps_actual_terminal_and_missing_ranges(self):
        c, p, clock, root = self.armed(emulator=True)
        clock.now += 100
        p.read_limit = 700
        self.pump(c, p)
        self.assertIsNone(c.runtime.pending_capture)
        self.assertIs(c.state, lv.State.ACTIVE)
        c.cancel()
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["attempt"]["outcome"], "cancelled")
        self.assertIsNone(body["capture_sha256"])
        self.assertEqual(body["accepted"]["abort"]["decoded"]["reason"], 1)
        self.assertIsNone(body["accepted"]["end"])
        actual = body["incomplete"]
        count = sum(len(block["samples"]) for block in actual["fragments"])
        self.assertGreater(count, 0)
        self.assertLess(count, 2048)
        self.assertEqual(actual["missing_ranges"], [[count, 2048]])
        self.assertEqual(actual["reason"], "ABORT")
        self.assertEqual([f.kind for f in ref.packets(b"".join(p.tx_log))], [1, 2, 3, 4, 5])

    def test_identical_end_frame_replay_fails_lv_attempt_but_preserves_valid_capture(self):
        for read_limit in (1, 4096):
            with self.subTest(read_limit=read_limit):
                c, p, _, root = self.armed()
                values = p.window_frames()
                segments = [part + b"\0" for part in bytes(p.rx).split(b"\0") if part]
                repeated = next(part for part in segments if ref.decode(part).kind == 146)
                index = segments.index(repeated)
                segments.insert(index + 1, repeated)
                p.rx[:] = b"".join(segments)
                p.read_limit = read_limit
                body = self.fail_saved(c, p, root)
                self.assertEqual(load(root / "attempt-00.mtcap").samples, values)
                self.assertEqual(body["journal"]["identical_frame_replays"], 1)
                self.assertEqual(body["journal"]["repeated_data_blocks"], 0)
                self.assertEqual(body["attempt"]["outcome"], "failed")
                rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
                self.assertEqual(rx.count(repeated), 2)

    def test_same_offset_duplicate_block_fails_lv_but_preserves_valid_capture(self):
        c, p, _, root = self.armed()
        values = p.window_frames(duplicate_offset=0)
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["attempt"]["outcome"], "failed")
        self.assertEqual(body["journal"]["identical_frame_replays"], 0)
        self.assertEqual(body["journal"]["repeated_data_blocks"], 1)
        self.assertEqual(load(root / "attempt-00.mtcap").samples, values)
        rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
        blocks = [f for f in ref.packets(rx) if f.kind == 145]
        self.assertEqual(len(blocks), 10)
        self.assertEqual(blocks[0].payload, blocks[1].payload)
        self.assertNotEqual(blocks[0].sequence, blocks[1].sequence)

    def test_wait_release_expiry_preserves_actual_capture_and_error_status(self):
        c, p, clock, root = self.armed(emulator=True)
        clock.now += 100
        self.until(c, p, lv.State.STOPPING)
        capture = c.runtime.pending_capture
        p.write_limit = 0
        clock.now += 5000
        body = self.fail_saved(c, p, root)
        self.assertEqual(load(root / "attempt-00.mtcap"), capture)
        rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
        self.assertTrue(any(f.kind == 134 and f.payload[:2] == bytes((5, 3)) for f in ref.packets(rx)))
        self.assertFalse(body["stop_matched"])

    def test_deadline_crossing_read_is_observed_but_not_accepted_arm_ack(self):
        c, p, _, root = self.ready()
        c.arm_next()
        p.read_delay = 1000
        body = self.fail_saved(c, p, root)
        self.assertFalse(body["attempt"]["arm_ack"])
        rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
        self.assertTrue(any(f.kind == 132 for f in ref.packets(rx)))
        self.assertEqual(body["reason"], "TIMEOUT")

    def test_deadline_crossing_stop_ack_bytes_are_not_falsely_matched(self):
        c, p, _, root = self.armed()
        p.window_frames()
        self.until(c, p, lv.State.STOPPING)
        p.read_delay = 1000
        body = self.fail_saved(c, p, root)
        self.assertFalse(body["stop_matched"])
        self.assertIsNone(body["accepted"]["stop"])
        rx = b"".join(bytes.fromhex(r["data_hex"]) for r in body["journal"]["records"] if r["kind"] == "rx")
        self.assertTrue(any(f.kind == 133 for f in ref.packets(rx)))

    def test_unknown_arm_write_never_means_zero_or_known_accepted_prefix(self):
        c, p, _, root = self.ready()
        c.arm_next()
        p.unknown_prefix = 3
        body = self.fail_saved(c, p, root)
        a = body["attempt"]
        self.assertTrue(a["intent_committed"])
        self.assertTrue(a["arm_queued"])
        self.assertTrue(a["tx_unknown"])
        self.assertFalse(a["arm_ack"])
        self.assertEqual(a["tx_known_bytes"], 0)
        record = [r for r in body["journal"]["records"] if r["kind"] == "tx"][-1]
        self.assertEqual(record["outcome"], "unknown_write")
        self.assertIsNone(record["accepted"])
        self.assertGreater(len(record["data_hex"]), 6)
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3])

    def test_unknown_stop_write_retains_capture_and_marks_its_actual_command(self):
        c, p, _, root = self.armed()
        p.window_frames()
        self.until(c, p, lv.State.STOPPING)
        p.unknown_prefix = 3
        body = self.fail_saved(c, p, root)
        record = [r for r in body["journal"]["records"] if r["kind"] == "tx"][-1]
        self.assertEqual((record["command"], record["outcome"], record["accepted"]), ("stop", "unknown_write", None))
        self.assertFalse(body["attempt"]["tx_unknown"])
        self.assertFalse(body["stop_matched"])

    def test_explicit_cancel_waiting_records_cancelled_without_window(self):
        c, p, _, root = self.armed()
        c.cancel()
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["attempt"]["outcome"], "cancelled")
        self.assertEqual(list(root.glob("*.mtcap")), [])
        self.assertEqual([f.kind for f in p.commands], [1, 2, 3, 4, 5])


class ResourceAndInputTests(Checks):
    def test_recovered_integrity_never_promotes_failed_or_cancelled_final_capture(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                c, p, _, root = self.final_stopped(
                    final_options={} if cancelled else {"duplicate_offset": 0})
                if cancelled:
                    c.cancel()
                capture = c.runtime.pending_capture
                with patch.object(c.runtime, "acknowledge", wraps=c.runtime.acknowledge) as ack:
                    result = c.persist_attempt()
                self.assertEqual(ack.call_count, 0)
                self.assertIs(c.runtime.pending_capture, capture)
                self.assertEqual(result["outcome"], "cancelled" if cancelled else "failed")
                recovered = lv.verify_run(root)
                self.assertEqual(recovered["state"], "cancelled" if cancelled else "failed")
                self.assertEqual(recovered["artifact_integrity"], "PARTIAL_VERIFIED")
                self.assertEqual(recovered["prior_writer_outcome"], "UNKNOWN")
                self.assertEqual(len(list(root.glob("*.mtcap"))), 21)
                self.assertEqual(sum(a["outcome"] == "completed" for a in recovered["attempts"]), 20)

    def test_matching_config_rejection_terminates_instead_of_silent_negotiation_loop(self):
        c, p, _, root = self.make()
        command = p.command

        def rejected(frame):
            if frame.kind == 3:
                p.reply(255, bytes((3, 1, 0, 0)) + ref.u(0), frame.request)
            else:
                command(frame)

        with patch.object(p, "command", side_effect=rejected):
            c.start()
            for _ in range(5):
                if c.state not in (lv.State.NEGOTIATING, lv.State.ACTIVE, lv.State.STOPPING):
                    break
                self.pump(c, p)
        self.assertIs(c.state, lv.State.SAVEABLE)
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["reason"], "COMMAND_ERROR")
        commands = [f.kind for f in p.commands]
        self.assertIn(commands, ([1, 2, 3], [1, 2, 3, 5]))

    def test_final_index_verification_failure_cannot_leave_a_false_complete_run(self):
        c, p, _, root = self.ready()
        for number in range(21):
            c.arm_next()
            while c.runtime.snapshot().phase is not Phase.ARMED:
                self.pump(c, p)
            p.window_frames()
            self.until(c, p, lv.State.SAVEABLE)
            if number < 20:
                c.persist_attempt()
        pending = c.runtime.pending_capture
        with patch.object(lv, "verify_run", side_effect=OSError("Ошибка финального чтения индекса")):
            with patch.object(c.runtime, "acknowledge", wraps=c.runtime.acknowledge) as ack:
                with self.assertRaises(lv.CollectorError):
                    c.persist_attempt()
        ack.assert_not_called()
        self.assertIs(c.runtime.pending_capture, pending)
        self.assertIs(c.state, lv.State.FAILED)
        try:
            reopened = lv.verify_run(root)
        except (lv.CollectorError, OSError):
            pass  # Видимо недостоверное завершение разрешено; ложное complete нет.
        else:
            self.assertNotEqual(reopened["state"], "complete")

    def test_untrusted_artifact_names_never_escape_fixed_run_names(self):
        for field in ("sidecar_file", "mtcap_file"):
            for bad in ("../external.json", "/tmp/external.mtcap", "nested/attempt-00.json",
                        "attempt-01.json", "attempt-00.mtcap/extra"):
                with self.subTest(field=field, name=bad):
                    _, _, _, root = self.saved()
                    self.change_index(root, lambda b: b["attempts"][0].update({field: bad}))
                    with self.assertRaises(lv.CollectorError):
                        lv.verify_run(root)

    def test_hard_bounds_are_exact_and_not_configurable_to_unbounded_values(self):
        self.assertEqual((lv.ATTEMPTS, lv.MAX_JOURNAL_BYTES, lv.MAX_JOURNAL_RECORDS,
                          lv.MAX_SIDECAR_BYTES, lv.MAX_INDEX_BYTES),
                         (21, 131072, 8192, 2097152, 65536))

    def test_ten_thousand_empty_polls_are_bounded_and_coalesced(self):
        c, p, _, root = self.armed()
        initial = len(c.observed.records)
        for _ in range(10000):
            self.pump(c, p)
        self.assertLessEqual(len(c.observed.records), initial + 1)
        empty = [r for r in c.observed.records if r["outcome"] == "empty"]
        self.assertEqual(sum(r["count"] for r in empty), 10000)
        self.assertLess(c.observed.byte_count, 4096)
        c.cancel()
        body = self.fail_saved(c, p, root)
        self.assertIsNone(body["journal"]["failure"])

    def test_raw_journal_byte_overflow_fails_closed_without_replacement(self):
        c, p, _, root = self.armed()
        p.read_override = b"\0" * 4096
        self.until(c, p, lv.State.SAVEABLE, limit=40)
        self.assertLessEqual(c.observed.byte_count, 131072)
        self.assertLessEqual(len(c.observed.records), 8192)
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["journal"]["failure"], "JOURNAL_OVERFLOW")
        self.assertFalse(body["stop_matched"])

    def test_record_count_overflow_is_bounded_even_with_one_byte_observations(self):
        c, p, _, root = self.armed()
        p.read_override = b"\0"
        self.until(c, p, lv.State.SAVEABLE, limit=8200)
        self.assertLessEqual(len(c.observed.records), 8192)
        self.assertLess(c.observed.byte_count, 10000)
        body = self.fail_saved(c, p, root)
        self.assertEqual(body["journal"]["failure"], "JOURNAL_OVERFLOW")
        self.assertLessEqual((root / "attempt-00.json").stat().st_size, 2097152)

    def test_oversized_and_wrong_type_reads_are_rejected_before_hex_copy(self):
        for result in (b"X" * 1048576, bytearray(b"bad"), memoryview(b"bad"), "bad", [0], True):
            with self.subTest(type=type(result).__name__):
                c, p, _, root = self.armed()
                p.read_override = result
                body = self.fail_saved(c, p, root)
                self.assertEqual(body["journal"]["failure"], "INVALID_READ")
                record = body["journal"]["records"][-1]
                self.assertEqual((record["outcome"], record["data_hex"]), ("invalid_read", ""))
                self.assertLess(body["journal"]["raw_byte_count"], 4096)

    def test_invalid_write_result_is_unknown_not_coerced_to_prefix(self):
        for result in (True, False, -1, 1000000, 1.0, "1", None):
            with self.subTest(result=result):
                c, p, _, root = self.ready()
                c.arm_next()
                with patch.object(p, "write", return_value=result):
                    body = self.fail_saved(c, p, root)
                self.assertTrue(body["attempt"]["tx_unknown"])
                self.assertEqual(body["attempt"]["tx_known_bytes"], 0)
                self.assertIsNone(body["journal"]["records"][-1]["accepted"])

    def test_constructor_rejects_non_auto_profile_source_pretrigger_and_nonce_types(self):
        for changes in (dict(mode=1, auto_timeout_ms=0), dict(mode=2, auto_timeout_ms=0),
                        dict(profile=2), dict(source=1), dict(pretrigger_samples=512)):
            with self.subTest(changes=changes):
                with self.assertRaises(lv.CollectorError):
                    self.make(config=replace(Config(mode=0, auto_timeout_ms=100), **changes))
        c, p, clock, root = self.make()
        for nonce in (True, False, 0, -1, 2**64, 1.0, "1", None):
            with self.subTest(nonce=nonce), self.assertRaises(lv.CollectorError):
                lv.LVCollector(root, p, clock_ms=clock,
                    identity=lv.RunIdentity("simulator", ref.BASE, ref.TREE),
                    config=Config(mode=0, auto_timeout_ms=100), nonce=nonce)
        self.assertEqual(p.calls, [])

    def test_identity_rejects_huge_text_unknown_types_and_unproven_hashes(self):
        base = lv.RunIdentity("simulator", ref.BASE, ref.TREE)
        for changes in (dict(hardware_passport_id="X" * 1048576), dict(origin=True),
                        dict(vdda_measurement=3.3), dict(application_commit="unknown"),
                        dict(board_json_sha256=123), dict(schematic_id="bad\nname")):
            with self.subTest(changes={k: type(v).__name__ for k, v in changes.items()}):
                with self.assertRaises(lv.CollectorError):
                    replace(base, **changes)

    def test_caps_without_auto_never_queues_arm(self):
        c, p, _, root = self.make()
        p.hello = ref.HELLO_ACK[:-4] + ref.u(9)
        p.caps = ref.CAPS[:21] + bytes((2,)) + ref.CAPS[22:]
        c.start()
        for _ in range(10):
            if c.state is lv.State.SAVEABLE:
                break
            try:
                self.pump(c, p)
            except lv.CollectorError:
                break
        self.assertNotEqual(c.state, lv.State.READY)
        self.assertNotIn(4, [f.kind for f in p.commands])
        with self.assertRaises(lv.CollectorError):
            c.arm_next()
        self.assertEqual(self.index(root)["state"], "incomplete")

    def test_persist_releases_previous_capture_and_journal_not_a_20_window_buffer(self):
        c, p, _, root = self.armed()
        p.window_frames()
        self.until(c, p, lv.State.SAVEABLE)
        handle = weakref.ref(c.runtime.pending_capture)
        c.persist_attempt()
        gc.collect()
        self.assertIsNone(handle())
        self.assertEqual(c.observed.records, [])
        self.assertEqual(c.observed.byte_count, 0)
        self.assertIsNone(c.runtime.incomplete_result)
        self.assertEqual(len(lv.verify_run(root)["attempts"]), 21)

    def test_verify_cli_is_read_only_and_never_constructs_live_collector(self):
        _, _, _, root = self.saved()
        before = {p.name: sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
        with patch.object(lv, "LVCollector", side_effect=AssertionError("Live collector запрещён")):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(lv.main([str(root)]), 1)
        self.assertIn("0/20", out.getvalue())
        self.assertIn("прогрев отдельно", out.getvalue())
        self.assertEqual({p.name: sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
