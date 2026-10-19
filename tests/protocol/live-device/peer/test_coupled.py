"""Actual C endpoint against the exact immutable PR195 HOST, never a HOST model."""
import ctypes as c
import hashlib
import itertools
import json
import os
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parent
if not os.environ.get("HOST_ROOT"):
    raise RuntimeError("Set HOST_ROOT to the exact PR195 host directory")
PEER = Path(os.environ["HOST_ROOT"]).resolve() / "motortester"
for filename, expected in {
    "__init__.py": "a9e7bc3ad035367282bcc6f940c95cf1cde2c6c1cee0bb4c3ead1f6eae845645",
    "stream_endpoint.py": "fed7113f3c5570f888c1bb4852da80224849a89977517dd5b99ccf94e6f1d67e",
    "stream_wire.py": "62c7dabe9a73a3f7d486f5e350131438a2ead7e126d6cd2d891ebb13485a9cce",
}.items():
    if hashlib.sha256((PEER / filename).read_bytes()).hexdigest() != expected:
        raise RuntimeError("HOST source does not match exact PR195: " + filename)
from motortester import stream_endpoint as e
from motortester import stream_wire as w
if Path(e.__file__).resolve() != PEER / "stream_endpoint.py":
    raise RuntimeError("wrong HOST endpoint imported")
if Path(w.__file__).resolve() != PEER / "stream_wire.py":
    raise RuntimeError("wrong HOST codec imported")
U8, U16 = c.POINTER(c.c_uint8), c.POINTER(c.c_uint16)
LIB = c.CDLL(os.environ.get("BRIDGE_LIBRARY", str(ROOT / "bridge.so")))
SIGNATURES = {
    "reset": (c.c_int, []), "time": (None, [c.c_uint64]),
    "accept": (None, [c.c_size_t]), "start_result": (None, [c.c_int]),
    "snapshot": (None, [c.c_uint, c.c_uint, c.c_uint64, c.c_uint16]),
    "tail": (c.c_int, [c.c_uint64, U16, c.c_uint16]),
    "receive": (c.c_int, [U8, c.c_size_t]), "step": (c.c_int, []),
    "offer": (c.c_int, [c.c_uint64, U16, c.c_uint16]),
    "fault": (None, [c.c_uint16]), "drain": (c.c_size_t, [U8, c.c_size_t]),
    "get": (c.c_uint64, [c.c_uint]),
}
for name, (result, arguments) in SIGNATURES.items():
    function = getattr(LIB, "bridge_" + name)
    function.restype, function.argtypes = result, arguments


def codes(first, count):
    return tuple((i * 37 + 11) % 4096 for i in range(first, first + count))


class Coupled:
    def __init__(self, name, host_accept=(544,), device_accept=(64,), rx_split=544):
        if LIB.bridge_reset() != 1:
            raise AssertionError("C init rejected")
        self.name, self.host, self.now = name, e.Endpoint(), 0
        self.host_accept = itertools.cycle(host_accept)
        self.device_accept = itertools.cycle(device_accept)
        self.rx_split = rx_split
        self.events, self.trace, self.device_bytes = [], [], bytearray()
        self.host_bytes = bytearray()
        self.scalar_snapshot = None

    def get(self, key):
        return LIB.bridge_get(key)

    def observe(self, events):
        self.events.extend(events)
        if events:
            self.trace.append({"at_ms": self.now, "events": [repr(v) for v in events]})
        return events

    def command(self, name, *args, **kwargs):
        result = getattr(self.host, name)(*args, **kwargs)
        return self.observe(result)

    def advance(self, value):
        self.now = value
        LIB.bridge_time(value)
        self.observe(self.host.tick(value))

    def send(self):
        for _ in range(2000):
            events = self.host.next_write(self.now)
            if not events:
                return
            if len(events) != 1 or not isinstance(events[0], e.WriteOffer):
                self.observe(events)
                return
            offer = events[0]
            accepted = min(next(self.host_accept), len(offer.data))
            sent = offer.data[:accepted]
            self.trace.append({"at_ms": self.now, "host_offer_token": offer.token,
                               "offered_hex": offer.data.hex(), "accepted": accepted})
            # Irrevocable synchronous FIFO acceptance observed before any C reply.
            self.observe(self.host.observe_write(offer.token, accepted, self.now))
            self.host_bytes.extend(sent)
            for position in range(0, len(sent), self.rx_split):
                chunk = sent[position:position + self.rx_split]
                buffer = (c.c_uint8 * len(chunk)).from_buffer_copy(chunk)
                if LIB.bridge_receive(buffer, len(chunk)) != 1:
                    raise AssertionError("C receive rejected bounded chunk")
        raise AssertionError("unbounded HOST TX pump")

    def step(self, accept=None, allow_fault=False):
        LIB.bridge_accept(next(self.device_accept) if accept is None else accept)
        before = self.get(16)
        result = LIB.bridge_step()
        buffer = (c.c_uint8 * 544)()
        length = LIB.bridge_drain(buffer, 544)
        raw = bytes(buffer[:length])
        self.device_bytes.extend(raw)
        if self.get(16) != before:
            self.trace.append({"at_ms": self.now, "device_accepted_hex": raw.hex(),
                               "device_state": self.get(0), "tx_offset": self.get(9)})
        for position in range(0, len(raw), self.rx_split):
            self.observe(self.host.receive(raw[position:position + self.rx_split], self.now))
        if not result and not allow_fault:
            raise AssertionError("C fault: local=%d reason=%d" % (self.get(6), self.get(5)))
        return self.get(16) != before

    def pump(self, allow_fault=False):
        for _ in range(20000):
            if not self.step(allow_fault=allow_fault):
                return
        raise AssertionError("unbounded C TX pump")

    def ready(self, start=True, pump_start=True):
        self.command("hello", 0x0102030405060708, self.now)
        self.send(); self.pump()
        self.command("get_caps", self.now, action_id=22)
        self.send(); self.pump()
        if start:
            self.command("start", self.now, action_id=33)
            self.send()
            if pump_start:
                self.pump()
        return self

    def offer(self, first, count=128):
        values = codes(first, count)
        buffer = (c.c_uint16 * count)(*values)
        return LIB.bridge_offer(first, buffer, count)

    def snapshot(self, end, *, known=True, off=True, reason=0, tails=()):
        LIB.bridge_snapshot(int(off), int(known), end, reason)
        for first, count in tails:
            buffer = (c.c_uint16 * count)(*codes(first, count))
            if LIB.bridge_tail(first, buffer, count) != 1:
                raise AssertionError("invalid synthetic tail setup")

    def stop(self, pump=True):
        self.command("stop", self.now, action_id=44)
        self.send()
        if pump:
            self.pump()

    def faults(self):
        return [x for x in self.events if isinstance(x, (e.Fault, e.RecoveryRequired, e.ResetRequired))]

    def data(self):
        return [x for x in self.events if isinstance(x, e.ValidatedData)]

    def ends(self):
        return [x for x in self.events if isinstance(x, e.ValidatedEnd)]

    def acks(self):
        return [x for x in self.events if isinstance(x, e.StopAcknowledged)]

    def persist(self):
        destination = Path(os.environ.get("TRACE_DIR", str(ROOT / "traces")))
        destination.mkdir(parents=True, exist_ok=True)
        if self.scalar_snapshot is None:
            self.scalar_snapshot = {"device_state": self.get(0),
                                    "callback_max_write": self.get(17),
                                    "invalid_callback": self.get(18)}
        value = {"scenario": self.name, "host_module": str(e.__file__),
                 "host_bytes_hex": self.host_bytes.hex(), "device_bytes_hex": self.device_bytes.hex(),
                 "host_state": self.host.state.value, **self.scalar_snapshot,
                 "trace": self.trace}
        (destination / (self.name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


class CoupledTests(unittest.TestCase):
    def setUp(self):
        self.links = []

    def link(self, suffix="", **kwargs):
        if self.links:
            self.links[-1].persist()
        item = Coupled(self._testMethodName + suffix, **kwargs)
        self.links.append(item)
        return item

    def tearDown(self):
        # Earlier links cached their final bridge snapshot before the next reset.
        for item in self.links:
            item.persist()
        if self.links:
            self.assertEqual(self.links[-1].get(18), 0)
            self.assertLessEqual(self.links[-1].get(17), 64)

    def assert_terminal(self, link, prefix, produced, reason=0, flags=0, stop_id=4):
        self.assertEqual(len(link.ends()), 1, link.faults())
        terminal = link.ends()[0].terminal
        self.assertEqual((terminal.prefix_end, terminal.produced_end, terminal.reason,
                          terminal.flags, terminal.stop_request_id),
                         (prefix, produced, reason, flags, stop_id))
        samples = tuple(v for event in link.data() for v in event.block.samples)
        self.assertEqual(samples, codes(0, prefix))
        packed = struct.pack("<" + "H" * len(samples), *samples)
        self.assertEqual(terminal.prefix_crc32, zlib.crc32(packed))
        self.assertEqual(link.host.received_end, prefix)
        self.assertEqual(link.get(2), prefix)
        if not reason and not flags:
            self.assertEqual(link.faults(), [])
        return terminal

    def assert_ack(self, link):
        self.assertEqual(len(link.acks()), 1, link.faults())
        ack = link.acks()[0]
        self.assertIs(ack.terminal, link.ends()[0])
        self.assertEqual(ack.request.action_id, 44)
        self.assertEqual(link.host.state, e.State.NEEDS_HELLO)
        self.assertEqual(link.get(0), 7)

    def test_clean_full_and_final_partial(self):
        link = self.link().ready()
        for first in range(0, 384, 128):
            self.assertEqual(link.offer(first), 1)
        link.snapshot(391, tails=((384, 7),))
        link.stop()
        self.assert_terminal(link, 391, 391)
        self.assert_ack(link)
        self.assertEqual([len(x.block.samples) for x in link.data()], [128, 128, 128, 7])

    def test_empty_stop(self):
        link = self.link().ready()
        link.stop()
        self.assert_terminal(link, 0, 0)
        self.assert_ack(link)

    def test_two_certified_tail_blocks(self):
        link = self.link().ready()
        link.snapshot(131, tails=((0, 128), (128, 3)))
        link.stop()
        self.assert_terminal(link, 131, 131)
        self.assert_ack(link)

    def test_stop_before_start_ack(self):
        link = self.link().ready(pump_start=False)
        link.stop()
        self.assert_terminal(link, 0, 0)
        self.assert_ack(link)
        ordered = [type(v) for v in link.events]
        self.assertLess(ordered.index(e.Started), ordered.index(e.ValidatedEnd))
        self.assertEqual(link.get(13), 1)

    def test_stop_during_partial_start_ack(self):
        link = self.link(device_accept=(1, 3, 64)).ready(pump_start=False)
        link.step(accept=1)
        self.assertEqual(link.get(8), 136)
        self.assertEqual(link.get(9), 1)
        link.stop()
        self.assert_terminal(link, 0, 0)
        self.assert_ack(link)

    def test_fault_then_late_stop_completed_end(self):
        link = self.link().ready()
        self.assertEqual(link.offer(0), 1)
        link.snapshot(128)
        LIB.bridge_fault(2)
        link.pump()
        self.assert_terminal(link, 128, 128, reason=2, stop_id=0)
        first_end = link.ends()[0]
        link.stop()
        self.assert_ack(link)
        self.assertIs(link.ends()[0], first_end)
        self.assertEqual(link.host.first_fault.code, "device_2")

    def test_late_stop_during_frozen_end(self):
        link = self.link().ready()
        LIB.bridge_fault(2)
        link.step(accept=1)
        self.assertEqual((link.get(8), link.get(11)), (153, 1))
        link.stop()
        self.assert_terminal(link, 0, 0, reason=2, stop_id=0)
        self.assert_ack(link)

    def test_stop_joins_unstarted_failure_end(self):
        link = self.link().ready()
        LIB.bridge_fault(2)
        link.step(accept=0)
        self.assertEqual((link.get(8), link.get(11)), (153, 0))
        link.stop()
        self.assert_terminal(link, 0, 0, reason=2)
        self.assert_ack(link)

    def test_overrun_known_tail(self):
        link = self.link().ready()
        link.snapshot(2176)
        for first in range(0, 2048, 128):
            self.assertEqual(link.offer(first), 1)
        self.assertEqual(link.offer(2048), 0)
        link.stop()
        self.assert_terminal(link, 2048, 2176, reason=1, flags=2)
        self.assert_ack(link)

    def test_overrun_unknown_tail(self):
        link = self.link().ready()
        link.snapshot(0, known=False)
        for first in range(0, 2048, 128):
            self.assertEqual(link.offer(first), 1)
        self.assertEqual(link.offer(2048), 0)
        link.stop()
        self.assert_terminal(link, 2048, None, reason=1, flags=3)
        self.assert_ack(link)

    def test_ambiguous_snapshot_is_unknown(self):
        link = self.link().ready()
        self.assertEqual(link.offer(0), 1)
        link.snapshot(0)  # Impossible known produced count below certified block.
        link.stop()
        self.assert_terminal(link, 128, None, reason=2, flags=3)
        self.assert_ack(link)

    def test_failed_quiescence_forbids_ack(self):
        link = self.link().ready()
        link.snapshot(0, off=False)
        link.stop()
        self.assert_terminal(link, 0, None, reason=5, flags=7)
        self.assertEqual(link.acks(), [])
        self.assertEqual(link.host.state, e.State.RECOVERING)
        self.assertEqual(link.host.last_recovery.terminal, link.ends()[0])

    def test_failed_start_exact_causal_request_with_stop_pending(self):
        link = self.link().ready(start=False)
        LIB.bridge_start_result(0)
        link.command("start", link.now, action_id=333)
        link.send()
        link.command("stop", link.now, action_id=444)
        link.send(); link.pump()
        self.assertEqual(link.host.state, e.State.RECOVERING)
        self.assertEqual(link.host.first_fault.code, "device_error_4")
        self.assertEqual(link.host.first_fault.causal_request.action_id, 333)
        self.assertEqual(link.host.last_recovery.stop.action_id, 444)
        self.assertEqual(link.data() + link.ends() + link.acks(), [])

    def test_fragmentation_and_zero_acceptance_matrix(self):
        for i, (ha, da, split) in enumerate(itertools.product(
                ((544,), (0, 1, 7, 544)), ((64,), (0, 1, 3, 64)), (1, 7, 544))):
            with self.subTest(host=ha, device=da, split=split):
                link = self.link("_%02d" % i, host_accept=ha, device_accept=da, rx_split=split).ready()
                self.assertEqual(link.offer(0), 1)
                link.snapshot(133, tails=((128, 5),))
                link.stop()
                self.assert_terminal(link, 133, 133)
                self.assert_ack(link)

    def test_host_timer_independent_of_device_io(self):
        link = self.link().ready()
        self.assertEqual(link.host.next_deadline_ms, 1000)
        link.now = 1000
        link.observe(link.host.tick(link.now))
        self.assertEqual(link.host.state, e.State.RECOVERING)
        self.assertEqual(link.host.first_fault.code, "data_timeout")
        self.assertEqual(link.get(0), 4)  # HOST timeout does not attest C hardware off.

    def test_clean_recovery_delimiter_and_fresh_session(self):
        link = self.link(host_accept=(0, 1, 7, 544), device_accept=(0, 1, 64), rx_split=1).ready()
        link.stop()
        self.assert_terminal(link, 0, 0)
        self.assert_ack(link)
        link.command("recover", link.now)
        self.assertEqual(link.host.state, e.State.RECOVERING)
        # This synchronous FIFO has no retained pointer or unresolved old write.
        link.command("isolation_complete", 0x1112131415161718, link.now)
        previous = len(link.host_bytes)
        link.send(); link.pump()
        self.assertEqual(link.host_bytes[previous], 0)
        self.assertEqual(link.host.state, e.State.STOPPED)
        self.assertIsNone(link.host.generation)
        link.command("get_caps", link.now)
        link.send(); link.pump()
        link.command("start", link.now, action_id=55)
        link.send(); link.pump()
        link.stop()
        starts = [x for x in link.events if isinstance(x, e.Started)]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0].nonce, starts[1].nonce)
        self.assertEqual((starts[0].generation, starts[1].generation), (3, 3))
        self.assertEqual(len(link.ends()), 2)
        self.assertEqual(len(link.acks()), 2)
        self.assertEqual(link.host.state, e.State.NEEDS_HELLO)
        self.assertEqual(link.get(13), 2)

    def test_start_ack_stop_every_byte_boundary(self):
        probe = self.link("_probe").ready(pump_start=False)
        probe.step(accept=0)
        length = probe.get(10)
        self.assertGreater(length, 1)
        for cut in range(length):
            with self.subTest(cut=cut, length=length):
                link = self.link("_%03d" % cut).ready(pump_start=False)
                if cut:
                    link.step(accept=cut)
                link.stop()
                self.assert_terminal(link, 0, 0)
                self.assert_ack(link)

    def test_late_stop_every_end_byte_boundary(self):
        probe = self.link("_probe").ready()
        LIB.bridge_fault(2)
        probe.step(accept=0)
        length = probe.get(10)
        self.assertGreater(length, 1)
        for cut in range(length + 1):
            with self.subTest(cut=cut, length=length):
                link = self.link("_%03d" % cut).ready()
                LIB.bridge_fault(2)
                remaining = cut
                while remaining:
                    accepted = min(remaining, 64)
                    link.step(accept=accepted)
                    remaining -= accepted
                link.stop()
                self.assert_terminal(link, 0, 0, reason=2, stop_id=4 if cut == 0 else 0)
                self.assert_ack(link)

    def test_stop_during_partial_data_preserves_started_frame(self):
        link = self.link(host_accept=(1, 544), device_accept=(7, 64), rx_split=3).ready()
        self.assertEqual(link.offer(0), 1)
        link.advance(10)
        link.step(accept=1)
        self.assertEqual((link.get(8), link.get(2), link.host.received_end), (152, 0, 0))
        link.snapshot(137, tails=((128, 9),))
        link.advance(17)
        link.stop()
        self.assert_terminal(link, 137, 137)
        self.assert_ack(link)
        self.assertEqual(link.get(19), 517)

    def test_samples_queued_while_start_ack_pending(self):
        link = self.link().ready(pump_start=False)
        self.assertEqual(link.offer(0), 1)
        link.snapshot(128)
        link.stop()
        self.assert_terminal(link, 128, 128)
        self.assert_ack(link)
        ordered = [type(v) for v in link.events]
        self.assertLess(ordered.index(e.Started), ordered.index(e.ValidatedData))

    def test_tail_capacity_overrun_retains_certified_prefix(self):
        link = self.link().ready()
        for first in range(0, 1920, 128):
            self.assertEqual(link.offer(first), 1)
        link.snapshot(2055, tails=((1920, 128), (2048, 7)))
        link.stop()
        self.assert_terminal(link, 2048, 2055, reason=1, flags=2)
        self.assert_ack(link)

    def test_first_device_fault_survives_later_drain_fault(self):
        link = self.link().ready()
        LIB.bridge_fault(2)
        LIB.bridge_fault(3)
        link.stop()
        self.assert_terminal(link, 0, 0, reason=2)
        self.assert_ack(link)
        self.assertEqual(link.host.first_fault.code, "device_2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
