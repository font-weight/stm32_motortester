"""Synthetic producer declaration -> real journal -> fresh-process reopen.

No serial open, physical firmware, Qt, image, installation or native build.
The encoded-peer fixtures are independent of the production wire encoder.
"""
from dataclasses import asdict, FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import zlib

from motortester.stream_bridge import StreamBridge
from motortester.stream_controller import StreamController
from motortester.stream_model import StreamError, StreamIdentity, StreamStart
from motortester.stream_provenance import (DMA_DOMAIN, DMA_REVISION, ProvenanceError,
    SYNTHETIC_DMA_PROFILE as PROFILE, admit_profile, profile_for_identity, provenance_text)
from motortester.stream_recording import StreamWriter, recover, recover_attempt
from motortester.stream_serial import SerialSettings, SerialStreamPeer
import test_stream_bridge as bridge_fixture
import test_stream_controller as controller_fixture
from test_stream_bridge import Peer
from test_stream_controller import CONFIG, EncodedPeer


def dma_identity():
    return StreamIdentity("synthetic-declared-dma", 3, 1, 50000, "synthetic", *PROFILE.identity_metadata)


def fresh_read(path):
    script = """
from dataclasses import asdict
import hashlib, json, sys
from motortester.stream_recording import recover_attempt
from motortester.stream_provenance import profile_for_identity
a = recover_attempt(sys.argv[1])
r = a.recording
p = profile_for_identity(a.preparation.identity)
t = r.terminal if r is not None else a.unbound_outcome
print(json.dumps(dict(identity=asdict(a.preparation.identity),
 profile=None if p is None else asdict(p), selected=a.selected_start is not None,
 recording=r is not None, clean=None if r is None else r.clean_stop_reported,
 terminal=None if t is None else asdict(t),
 raw_sha=None if r is None else hashlib.sha256(b''.join(b.raw_u16le for b in r.iter_raw())).hexdigest())))
"""
    args = [sys.executable] + (["-O"] if sys.flags.optimize else [])
    result = subprocess.run(args + ["-c", script, str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


class ProfileTests(unittest.TestCase):
    def test_exact_fixture_association_and_existing_text_bounds(self):
        artifact = Path(__file__).with_name("test_stream_bridge.py").read_bytes()
        self.assertEqual(hashlib.sha256(artifact).hexdigest(), PROFILE.artifact_sha256)
        self.assertEqual(PROFILE.source_commit, "e86759a011b93a12a42542cfc9ba8149c673ff86")
        self.assertEqual([len(s) for s in PROFILE.identity_metadata], [48, 64, 63])
        self.assertEqual(profile_for_identity(dma_identity()), PROFILE)
        with self.assertRaises(FrozenInstanceError):
            PROFILE.accounting_domain = "something-else"

    def test_every_profile_component_is_exact_and_no_registration(self):
        for name, value in asdict(PROFILE).items():
            wrong = value + "x" if type(value) is str else value + 1
            with self.subTest(field=name), self.assertRaises(ProvenanceError):
                StreamBridge("wrong", producer_profile=replace(PROFILE, **{name: wrong}))
        for value in (None, "syn1", asdict(PROFILE), 0x1234):
            with self.subTest(value=value), self.assertRaises(ProvenanceError):
                admit_profile(value)
        with self.assertRaises(ProvenanceError):
            admit_profile(replace(PROFILE, config_id=True))

    def test_missing_unknown_mixed_domain_source_artifact_build_are_rejected(self):
        identity = dma_identity()
        changes = [dict(protocol_revision=None), dict(protocol_revision=DMA_REVISION),
                   dict(protocol_revision=DMA_REVISION + "|completed-dma-v2"),
                   dict(protocol_revision="0.2-draft-081721"),
                   dict(firmware_version=None), dict(firmware_version="0" * 64),
                   dict(application_version=None),
                   dict(application_version=identity.application_version.replace("syn1", "syn2")),
                   dict(application_version=identity.application_version.replace(PROFILE.source_commit, "0" * 40)),
                   dict(application_version=identity.application_version[:-8] + "00001235"),
                   dict(origin="device"), dict(config_id=2), dict(nominal_fs_hz=49999)]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(StreamError):
                replace(identity, **change)

    def test_revision_or_hello_build_alone_does_not_select_domain(self):
        legacy = StreamIdentity("old", 3, 1, 50000, "synthetic", "0.2-draft-081721",
                                "build:00001234", "isolated-stream-bridge")
        self.assertIsNone(profile_for_identity(legacy))
        self.assertNotIn(DMA_DOMAIN, provenance_text(legacy))
        with self.assertRaises(StreamError):
            replace(legacy, protocol_revision=DMA_REVISION)
        with self.assertRaises(StreamError):
            replace(legacy, protocol_revision=PROFILE.identity_metadata[0])

    def test_synthetic_declaration_cannot_enable_managed_or_direct_serial_peer(self):
        peer = SerialStreamPeer(SerialSettings("TEST_ONLY"), backend_factory=lambda: self.fail("Physical I/O"))
        for managed in (False, True):
            with self.subTest(managed=managed), self.assertRaises(ProvenanceError):
                StreamController(peer, event_config=CONFIG, producer_profile=PROFILE, managed_peer=managed)
        self.assertEqual(peer.initial_status.state, "closed")


class ProvenanceBridgeTests(unittest.TestCase):
    # Reuse finite scheduling helpers, not the inherited aggregate of tests.
    setUp = bridge_fixture.BridgeTests.setUp
    shutdown = bridge_fixture.BridgeTests.shutdown
    disk = bridge_fixture.BridgeTests.disk
    direct = bridge_fixture.BridgeTests.direct
    clean = bridge_fixture.BridgeTests.clean

    def ready(self, **kwargs):
        bridge = StreamBridge("attempt", producer_profile=PROFILE, **kwargs)
        self.addCleanup(lambda: self.shutdown(bridge))
        peer = Peer(bridge)
        peer.negotiate()
        return bridge, peer

    def test_known_clean_counter_scope_actual_journal_and_fresh_reader(self):
        bridge, peer = self.direct()
        peer.data()
        recording = self.clean(bridge, peer)
        self.assertEqual(profile_for_identity(recording.identity), PROFILE)
        result = fresh_read(self.path)
        self.assertEqual(result["profile"], asdict(PROFILE))
        self.assertEqual(result["identity"], asdict(recording.identity))
        self.assertEqual(result["raw_sha"], hashlib.sha256(peer.raw).hexdigest())
        self.assertTrue(result["clean"])
        self.assertEqual(result["terminal"]["loss_samples"], 0)
        self.assertIn(DMA_DOMAIN.encode(), self.path.read_bytes())

    def test_pre_ack_fixture_values_and_nonzero_record_keep_source_indices_crc_stop(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        # Synthetic values exist before ACK, but DATA is serialized after ACK.
        # This checks mapping, not physical DMA writes or ADC drain behavior.
        pre_ack = bridge_fixture.samples(seed=9)
        peer.started(); peer.data(pre_ack)
        bridge.record(self.path, "selected-after-preview", 0)
        bridge.dispatch_storage(0)
        peer.data(bridge_fixture.samples(seed=19))
        bridge.storage_tick(0)
        selected = bridge_fixture.samples(seed=29)
        peer.data(selected)
        recording = self.clean(bridge, peer)
        self.assertEqual(recording.first_sample_index, 256)
        self.assertEqual(recording.end_sample_index, 384)
        self.assertEqual(recording.read_raw(256, 128), selected)
        self.assertEqual(bridge.snapshot().source_prefix_crc32, zlib.crc32(peer.raw))
        self.assertEqual(recording.terminal.stop_request_id, peer.stop_request)
        self.assertEqual(recording.terminal.device_generated_end_sample_index, 384)
        saved = self.path.read_bytes()
        # A different active legacy attempt cannot supply semantics to reopen;
        # the reader also has no declaration-override parameter.
        other = StreamBridge("different-active-attempt")
        other_peer = Peer(other, nonce=42); other_peer.negotiate(); other.preview(0)
        self.assertIsNone(profile_for_identity(other.snapshot().identity))
        other.close(0)
        result = fresh_read(self.path)
        self.assertEqual(result["profile"], asdict(PROFILE))
        self.assertEqual(result["raw_sha"], hashlib.sha256(selected).hexdigest())
        with self.assertRaises(TypeError):
            recover_attempt(self.path, producer_profile=replace(PROFILE, profile_id="bad"))
        self.assertEqual(self.path.read_bytes(), saved)

    def test_known_loss_unknown_end_and_equal_count_fault_remain_distinct(self):
        for flags, produced, expected_loss in ((2, 141, 13), (3, 0, None), (2, 128, 0)):
            with self.subTest(flags=flags, produced=produced):
                self.path = Path(self.temp.name) / f"terminal-{flags}-{produced}.mtstream"
                bridge, peer = self.direct()
                peer.data()
                peer.end(reason=1, flags=flags, produced=produced, stop=0)
                peer.tx(); peer.ack(); self.disk(bridge)
                first = bridge.snapshot().session.first_failure
                result = fresh_read(self.path)
                terminal = result["terminal"]
                self.assertEqual(terminal["device_generated_end_sample_index"], None if flags == 3 else produced)
                self.assertEqual(terminal["loss_samples"], expected_loss)
                self.assertEqual(terminal["failure"], asdict(first))
                self.assertFalse(result["clean"])
                self.assertEqual(result["profile"], asdict(PROFILE))
                # A later observation cannot replace the first source failure.
                bridge.transport_fault(0)
                self.assertEqual(bridge.snapshot().session.first_failure, first)

    def test_empty_direct_recording_retains_profile_without_inventing_samples(self):
        bridge, peer = self.direct()
        recording = self.clean(bridge, peer)
        self.assertEqual(recording.first_sample_index, recording.end_sample_index)
        result = fresh_read(self.path)
        self.assertTrue(result["recording"])
        self.assertEqual(result["profile"], asdict(PROFILE))
        self.assertEqual(result["terminal"]["device_generated_end_sample_index"], 0)

    def test_known_equal_end_without_stop_ack_cannot_clear_quiescence_failure(self):
        bridge, peer = self.direct()
        peer.data(); peer.stop(); peer.end()
        bridge.tick(2000); self.disk(bridge)
        result = fresh_read(self.path)
        self.assertFalse(result["clean"])
        self.assertEqual(result["terminal"]["loss_samples"], 0)
        self.assertIsNotNone(result["terminal"]["failure"])
        self.assertEqual(result["terminal"]["failure"]["operation"], "STOP")
        self.assertEqual(result["profile"], asdict(PROFILE))

    def test_no_r_known_unknown_and_preparation_only_preserve_declaration(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                self.path = Path(self.temp.name) / f"no-r-{unknown}.mtstream"
                bridge, peer = self.ready()
                bridge.preview(0); peer.started(); peer.data()
                bridge.record(self.path, "no-r", 0)
                bridge.dispatch_storage(0)
                peer.stop()
                peer.end(reason=1 if unknown else 0, flags=3 if unknown else 0)
                peer.ack(); bridge.storage_tick(0); self.disk(bridge)
                result = fresh_read(self.path)
                self.assertFalse(result["selected"])
                self.assertFalse(result["recording"])
                self.assertEqual(result["profile"], asdict(PROFILE))
                self.assertEqual(result["terminal"]["loss_samples"], None if unknown else 0)
                self.assertEqual(result["terminal"]["device_generated_end_sample_index"], None if unknown else 128)
        self.path = Path(self.temp.name) / "preparation-only.mtstream"
        bridge, peer = self.ready()
        bridge.record(self.path, "header", 0)
        bridge.dispatch_storage(0); bridge.disk_executor.execute_one()
        # Never accept prepare and therefore never issue START.
        self.shutdown(bridge)
        result = fresh_read(self.path)
        self.assertFalse(result["selected"])
        self.assertIsNone(result["terminal"])
        self.assertEqual(result["profile"], asdict(PROFILE))

    def test_build_mismatch_rejects_before_plan_start_or_file(self):
        bridge = StreamBridge("wrong-build", producer_profile=PROFILE)
        self.addCleanup(lambda: self.shutdown(bridge))
        peer = Peer(bridge)
        original = peer.rx
        def rx(kind, payload, **kwargs):
            if kind == 129:
                payload = payload[:8] + struct.pack("<I", 0x1235) + payload[12:]
            return original(kind, payload, **kwargs)
        peer.rx = rx
        peer.negotiate()
        with self.assertRaisesRegex(ProvenanceError, "build ID"):
            bridge.record(self.path, "no-file", 0)
        self.assertIsNone(bridge.snapshot().identity)
        self.assertIsNone(bridge.snapshot().session)
        self.assertIsNone(bridge.disk_executor)
        self.assertFalse(self.path.exists())
        self.assertEqual(peer.tx(), [])

    def test_rehashed_archive_header_cannot_bypass_declaration_validation(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "header", 0)
        bridge.dispatch_storage(0); bridge.disk_executor.execute_one()
        self.shutdown(bridge)
        original = self.path.read_bytes()
        magic, length = struct.unpack("<8sI", original[:12])
        obj = json.loads(original[12:12 + length])
        for key, bad in (("protocol_revision", DMA_REVISION),
                         ("firmware_version", "0" * 64),
                         ("application_version", "producer:unknown"),
                         ("origin", "device")):
            with self.subTest(field=key):
                altered = json.loads(json.dumps(obj))
                altered["preparation"]["identity"][key] = bad
                metadata = json.dumps(altered, separators=(",", ":")).encode()
                prefix = struct.pack("<8sI", magic, len(metadata))
                path = Path(self.temp.name) / f"rehashed-{key}.mtstream"
                path.write_bytes(prefix + metadata + hashlib.sha256(prefix + metadata).digest())
                with self.assertRaisesRegex(StreamError, "декларация"):
                    recover_attempt(path)

    def test_legacy_v1_v2_archives_keep_original_identity_and_meaning(self):
        legacy = StreamIdentity("old", 1, 7, 50000, "synthetic", "old-revision", "old-fw", "old-app")
        writer = StreamWriter.create(self.path, StreamStart(legacy, 0, "legacy"))
        writer.close()
        self.assertEqual(recover(self.path).identity, legacy)
        self.assertIsNone(profile_for_identity(recover(self.path).identity))
        self.path = Path(self.temp.name) / "old-v2.mtstream"
        bridge = StreamBridge("old-v2")
        self.addCleanup(lambda: self.shutdown(bridge))
        peer = Peer(bridge); peer.negotiate()
        bridge.record(self.path, "old-v2", 0); self.disk(bridge); peer.started(); self.disk(bridge)
        old_identity = bridge.snapshot().identity
        self.clean(bridge, peer)
        result = fresh_read(self.path)
        self.assertEqual(result["identity"], asdict(old_identity))
        self.assertEqual(old_identity.protocol_revision, "0.2-draft-081721")
        self.assertEqual(old_identity.firmware_version, "build:00001234")
        self.assertEqual(old_identity.application_version, "isolated-stream-bridge")
        self.assertIsNone(result["profile"])


class ProvenanceControllerTests(unittest.TestCase):
    setUp = controller_fixture.ControllerTests.setUp
    tearDown = controller_fixture.ControllerTests.tearDown
    make = controller_fixture.ControllerTests.make
    step = controller_fixture.ControllerTests.step
    until = controller_fixture.ControllerTests.until
    record = controller_fixture.ControllerTests.record
    finish = controller_fixture.ControllerTests.finish

    def test_selected_profile_reaches_actual_controller_journal(self):
        controller, peer = self.make(producer_profile=PROFILE)
        self.record(controller)
        peer.data()
        self.until(controller, lambda s: s.validated_samples == 128)
        self.finish(controller)
        result = fresh_read(self.path)
        self.assertTrue(result["clean"])
        self.assertEqual(result["profile"], asdict(PROFILE))
        self.assertEqual(result["raw_sha"], hashlib.sha256(peer.raw).hexdigest())

    def test_wrong_build_fences_controller_and_keeps_admission_cause(self):
        peer = EncodedPeer()
        original = peer._rx
        def rx(kind, payload, request=0):
            if kind == 129:
                payload = payload[:8] + struct.pack("<I", 0x1235) + payload[12:]
            return original(kind, payload, request)
        peer._rx = rx
        controller, peer = self.make(peer, producer_profile=PROFILE)
        self.assertTrue(controller.record(self.path))
        status = self.until(controller, lambda s: s.error is not None)
        self.assertIn("build ID", status.error)
        self.assertIsNone(status.bridge.session)
        self.assertFalse(status.can_preview)
        self.assertFalse(status.can_record)
        self.assertFalse(Path(self.path).exists())
        self.assertNotIn(8, [cmd[0] for cmd in peer.commands])
        self.step(controller)
        self.assertEqual(controller.snapshot().error, status.error)


if __name__ == "__main__":
    unittest.main()
