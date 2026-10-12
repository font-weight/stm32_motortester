"""Hardware-free contract tests for HOST-009, including failure ambiguity."""
from array import array
import builtins
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from motortester.transport import (
    MAX_BAUDRATE, MAX_IO_BYTES, MAX_PORT_CHARS, MAX_TIMEOUT_SECONDS,
    SerialTransport, TransportArgumentError, TransportCloseError, TransportError,
    TransportOpenError, TransportReadError, TransportState, TransportStateError,
    TransportWriteError,
)


DEFAULT_RESULT = object()


class FakeSerial:
    """A closed fake; no import or operation can access an actual port."""

    def __init__(self):
        self.is_open = False
        self.port = None
        self.calls = []
        self.open_error = None
        self.close_error = None
        self.read_error = None
        self.write_error = None
        self.read_result = b""
        self.write_result = DEFAULT_RESULT
        self.confirm_open = True
        self.confirm_close = True
        self.write_hook = None
        self.accepted = b""

    def open(self):
        self.calls.append(("open", self.port))
        # A failed open may have acquired a handle before failing.
        self.is_open = self.confirm_open
        if self.open_error is not None:
            raise self.open_error

    def read(self, size):
        self.calls.append(("read", size))
        if self.read_error is not None:
            raise self.read_error
        return self.read_result

    def write(self, data):
        self.calls.append(("write", data))
        if self.write_hook is not None:
            self.write_hook(data)
        if self.write_error is not None:
            raise self.write_error
        return len(data) if self.write_result is DEFAULT_RESULT else self.write_result

    def close(self):
        self.calls.append(("close",))
        if self.close_error is not None:
            raise self.close_error
        if self.confirm_close:
            self.is_open = False


class Factory:
    def __init__(self, *backends):
        self.backends = list(backends)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.backends.pop(0)


class IntSubclass(int):
    pass


class BytesSubclass(bytes):
    pass


class StrSubclass(str):
    pass


class TransportTests(unittest.TestCase):
    def opened(self):
        backend = FakeSerial()
        factory = Factory(backend)
        transport = SerialTransport(backend_factory=factory)
        transport.open("COM17")
        return transport, backend, factory

    def assert_recovery(self, transport, backend):
        self.assertIs(transport.state, TransportState.RECOVERY)
        before = list(backend.calls)
        for operation, args in ((transport.read, (1,)), (transport.write, (b"x",)),
                                (transport.open, ("COM18",)),
                                (transport.read, (0,)), (transport.write, (b"",))):
            with self.assertRaises(TransportStateError):
                operation(*args)
        self.assertEqual(backend.calls, before)

    def test_import_and_constructor_are_lazy_with_no_optional_dependency(self):
        original_import = builtins.__import__
        serial_imports = []

        def guarded_import(name, *args, **kwargs):
            if name == "serial" or name.startswith("serial."):
                serial_imports.append(name)
                raise ImportError("pyserial intentionally absent")
            return original_import(name, *args, **kwargs)

        source = Path(__file__).parents[1] / "motortester" / "transport.py"
        spec = importlib.util.spec_from_file_location("isolated_transport", source)
        module = importlib.util.module_from_spec(spec)
        with patch("builtins.__import__", side_effect=guarded_import):
            spec.loader.exec_module(module)
            transport = module.SerialTransport()
            transport.close()
            with self.assertRaises(module.TransportArgumentError):
                transport.open(None)
            self.assertEqual(serial_imports, [])
            with self.assertRaises(module.TransportOpenError) as ctx:
                transport.open("COM17")
            self.assertIs(transport.state, module.TransportState.CLOSED)
            self.assertIsNone(transport.port)
            self.assertIsNone(ctx.exception.cleanup_error)
            self.assertIn("pyserial", str(ctx.exception))
        self.assertEqual(serial_imports, ["serial"])

    def test_default_backend_uses_serial_not_url_or_port_discovery(self):
        backend = FakeSerial()
        factory = Factory(backend)
        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=factory)}):
            transport = SerialTransport()
            self.assertEqual(factory.calls, [])
            transport.open("COM17")
        self.assertEqual(backend.calls, [("open", "COM17")])
        self.assertEqual(factory.calls, [{
            "port": None, "baudrate": 115200, "bytesize": 8, "parity": "N",
            "stopbits": 1, "timeout": 0.05, "write_timeout": 0.1,
            "xonxoff": False, "rtscts": False, "dsrdtr": False,
        }])
        self.assertIsNone(transport.close())

    def test_explicit_configuration_and_boundary_values(self):
        for read_timeout, write_timeout in ((0, 0.001), (1, 1), (0.25, 0.5)):
            with self.subTest(read=read_timeout, write=write_timeout):
                backend = FakeSerial()
                factory = Factory(backend)
                transport = SerialTransport(backend_factory=factory)
                transport.open("/dev/ttyACM0", baudrate=MAX_BAUDRATE,
                               read_timeout=read_timeout, write_timeout=write_timeout)
                config = factory.calls[0]
                self.assertIsNone(config["port"])
                self.assertEqual(config["baudrate"], MAX_BAUDRATE)
                self.assertEqual(config["timeout"], read_timeout)
                self.assertEqual(config["write_timeout"], write_timeout)
                self.assertIs(type(config["timeout"]), float)
                self.assertIs(type(config["write_timeout"]), float)
                self.assertLessEqual(config["timeout"], MAX_TIMEOUT_SECONDS)
                self.assertGreater(config["write_timeout"], 0)
                self.assertLessEqual(config["write_timeout"], MAX_TIMEOUT_SECONDS)
                self.assertEqual(transport.port, "/dev/ttyACM0")

    def test_no_io_before_explicit_open_and_idempotent_close(self):
        factory = Factory()
        transport = SerialTransport(backend_factory=factory)
        self.assertIs(transport.state, TransportState.CLOSED)
        for operation, args in ((transport.read, (1,)), (transport.read, (0,)),
                                (transport.write, (b"x",)), (transport.write, (b"",))):
            with self.assertRaises(TransportStateError):
                operation(*args)
        transport.close()
        transport.close()
        self.assertEqual(factory.calls, [])
        self.assertIsNone(transport.port)

    def test_repeated_identical_open_is_noop_changed_config_requires_close(self):
        transport, backend, factory = self.opened()
        transport.open("COM17")
        for port, kwargs in (("COM18", {}), ("COM17", {"baudrate": 9600}),
                             ("COM17", {"read_timeout": 0}),
                             ("COM17", {"write_timeout": 0.2})):
            with self.assertRaises(TransportStateError):
                transport.open(port, **kwargs)
        self.assertEqual(backend.calls, [("open", "COM17")])
        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(transport.port, "COM17")
        self.assertIs(transport.state, TransportState.OPEN)
        transport.close()
        transport.close()
        self.assertEqual(backend.calls[-1], ("close",))
        self.assertEqual(len(backend.calls), 2)

    def test_explicit_reopen_uses_fresh_backend_and_selected_port(self):
        first, second = FakeSerial(), FakeSerial()
        factory = Factory(first, second)
        transport = SerialTransport(backend_factory=factory)
        transport.open("COM17")
        transport.close()
        transport.open("COM19")
        self.assertEqual(first.calls, [("open", "COM17"), ("close",)])
        self.assertEqual(second.calls, [("open", "COM19")])
        self.assertEqual(transport.port, "COM19")
        self.assertEqual(len(factory.calls), 2)

    def test_read_empty_partial_and_full_are_not_disconnect(self):
        transport, backend, _ = self.opened()
        for data in (b"", b"a", bytes(range(256)) * 16):
            backend.read_result = data
            self.assertEqual(transport.read(), data)
            self.assertEqual(backend.calls[-1], ("read", MAX_IO_BYTES))
            self.assertIs(transport.state, TransportState.OPEN)
        before = list(backend.calls)
        self.assertEqual(transport.read(0), b"")
        self.assertEqual(backend.calls, before)

    def test_read_uses_requested_size_and_rejects_oversized_result(self):
        transport, backend, _ = self.opened()
        backend.read_result = b"ab"
        self.assertEqual(transport.read(2), b"ab")
        self.assertEqual(backend.calls[-1], ("read", 2))
        with self.assertRaises(TransportReadError):
            transport.read(1)
        self.assert_recovery(transport, backend)

    def test_read_malformed_results_fail_closed_without_coercion(self):
        for result in (None, "", 0, False, [], bytearray(b"a"), memoryview(b"a"),
                       BytesSubclass(b"a"), b"x" * (MAX_IO_BYTES + 1)):
            with self.subTest(result_type=type(result)):
                transport, backend, _ = self.opened()
                backend.read_result = result
                with self.assertRaises(TransportReadError):
                    transport.read()
                self.assert_recovery(transport, backend)

    def test_read_exception_preserves_cause_and_does_not_reconnect(self):
        for failure in (OSError("disconnected"), TimeoutError("backend fault"),
                        ValueError("broken backend")):
            transport, backend, factory = self.opened()
            backend.read_error = failure
            with self.assertRaises(TransportReadError) as ctx:
                transport.read(8)
            self.assertIs(ctx.exception.__cause__, failure)
            self.assertEqual(backend.calls, [("open", "COM17"), ("read", 8)])
            self.assertEqual(len(factory.calls), 1)
            self.assert_recovery(transport, backend)

    def test_write_known_zero_partial_and_full_prefix_never_loops(self):
        for count in (0, 1, 3, 4):
            transport, backend, _ = self.opened()
            backend.write_result = count
            self.assertEqual(transport.write(b"abcd"), count)
            self.assertEqual(backend.calls, [("open", "COM17"), ("write", b"abcd")])
            self.assertIs(transport.state, TransportState.OPEN)

    def test_caller_can_submit_only_known_unaccepted_suffix(self):
        transport, backend, _ = self.opened()
        backend.write_result = 2
        data = b"abcde"
        accepted = transport.write(data)
        backend.write_result = DEFAULT_RESULT
        self.assertEqual(transport.write(data[accepted:]), 3)
        self.assertEqual(backend.calls[1:], [("write", b"abcde"), ("write", b"cde")])

    def test_zero_length_writes_do_not_call_backend_and_maximum_is_exact(self):
        transport, backend, _ = self.opened()
        for payload in (b"", bytearray(), memoryview(b"")):
            self.assertEqual(transport.write(payload), 0)
        self.assertEqual(backend.calls, [("open", "COM17")])
        payload = bytes(range(256)) * 16
        self.assertEqual(transport.write(payload), MAX_IO_BYTES)
        self.assertEqual(backend.calls[-1], ("write", payload))

    def test_mutable_inputs_are_snapshotted_before_backend_write(self):
        for as_view in (False, True):
            transport, backend, _ = self.opened()
            source = bytearray(b"original")
            argument = memoryview(source) if as_view else source

            def mutate(data):
                source[:] = b"modified"
                self.assertIs(type(data), bytes)
                self.assertEqual(data, b"original")

            backend.write_hook = mutate
            self.assertEqual(transport.write(argument), 8)
            self.assertEqual(source, b"modified")
            self.assertEqual(backend.calls[-1], ("write", b"original"))

    def test_memoryview_byte_slice_and_signed_bytes_preserve_raw_content(self):
        transport, backend, _ = self.opened()
        for view, expected in ((memoryview(b"_abc_")[1:4], b"abc"),
                               (memoryview(array("b", [-1, 0, 1])), b"\xff\x00\x01")):
            self.assertEqual(transport.write(view), len(expected))
            self.assertEqual(backend.calls[-1], ("write", expected))

    def test_malformed_write_counts_are_unknown_not_zero(self):
        for count in (None, False, True, -1, 5, MAX_IO_BYTES + 1, 1.0, "1", [],
                      IntSubclass(1)):
            with self.subTest(count=count):
                transport, backend, _ = self.opened()
                backend.write_result = count
                with self.assertRaises(TransportWriteError) as ctx:
                    transport.write(b"abcd")
                self.assertIsNone(ctx.exception.accepted_bytes)
                self.assert_recovery(transport, backend)
                self.assertEqual(len(backend.calls), 2)

    def test_timeout_after_partial_acceptance_forces_recovery_without_retry(self):
        transport, backend, factory = self.opened()
        failure = TimeoutError("some bytes may already be in the device")

        def accept_prefix(data):
            backend.accepted += data[:2]

        backend.write_hook = accept_prefix
        backend.write_error = failure
        with self.assertRaises(TransportWriteError) as ctx:
            transport.write(b"command")
        self.assertIs(ctx.exception.__cause__, failure)
        self.assertIsNone(ctx.exception.accepted_bytes)
        self.assertEqual(backend.accepted, b"co")
        self.assert_recovery(transport, backend)
        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(len(backend.calls), 2)
        transport.close()
        self.assertEqual(backend.accepted, b"co")

    def test_write_other_exceptions_are_also_unknown_acceptance(self):
        for failure in (OSError("unplugged"), RuntimeError("unexpected driver error")):
            transport, backend, _ = self.opened()
            backend.write_error = failure
            with self.assertRaises(TransportWriteError) as ctx:
                transport.write(b"command")
            self.assertIs(ctx.exception.__cause__, failure)
            self.assertIsNone(ctx.exception.accepted_bytes)
            self.assert_recovery(transport, backend)

    def test_interrupts_propagate_but_leave_io_blocked_until_close(self):
        for operation in ("read", "write"):
            transport, backend, _ = self.opened()
            setattr(backend, operation + "_error", KeyboardInterrupt())
            with self.assertRaises(KeyboardInterrupt):
                getattr(transport, operation)(1 if operation == "read" else b"x")
            self.assert_recovery(transport, backend)
            transport.close()
            self.assertIs(transport.state, TransportState.CLOSED)

    def test_interrupted_open_and_close_keep_owned_handle_for_cleanup(self):
        for operation in ("open", "close"):
            backend = FakeSerial()
            transport = SerialTransport(backend_factory=Factory(backend))
            if operation == "close":
                transport.open("COM17")
            setattr(backend, operation + "_error", KeyboardInterrupt())
            with self.assertRaises(KeyboardInterrupt):
                if operation == "open":
                    transport.open("COM17")
                else:
                    transport.close()
            self.assert_recovery(transport, backend)
            self.assertEqual(transport.port, "COM17")
            backend.close_error = None
            transport.close()
            self.assertIs(transport.state, TransportState.CLOSED)

    def test_close_exception_after_releasing_handle_is_still_unconfirmed(self):
        class PartialClose(FakeSerial):
            def close(self):
                self.is_open = False
                super().close()

        backend = PartialClose()
        transport = SerialTransport(backend_factory=Factory(backend))
        transport.open("COM17")
        backend.close_error = OSError("final cleanup failed")
        with self.assertRaises(TransportCloseError):
            transport.close()
        self.assertFalse(backend.is_open)
        self.assert_recovery(transport, backend)
        backend.close_error = None
        transport.close()
        self.assertIs(transport.state, TransportState.CLOSED)

    def test_factory_failure_does_not_leave_open_state_or_retry(self):
        failure = OSError("factory failed before providing a handle")
        with patch("motortester.transport._serial_factory", side_effect=failure) as factory:
            transport = SerialTransport()
            with self.assertRaises(TransportOpenError) as ctx:
                transport.open("COM17")
            self.assertIs(ctx.exception.__cause__, failure)
            self.assertIsNone(ctx.exception.cleanup_error)
            self.assertIs(transport.state, TransportState.CLOSED)
            self.assertIsNone(transport.port)
            self.assertEqual(factory.call_count, 1)

    def test_open_failure_closes_partially_acquired_handle_once(self):
        backend = FakeSerial()
        failure = PermissionError("port busy")
        backend.open_error = failure
        factory = Factory(backend)
        transport = SerialTransport(backend_factory=factory)
        with self.assertRaises(TransportOpenError) as ctx:
            transport.open("COM17")
        self.assertIs(ctx.exception.__cause__, failure)
        self.assertIsNone(ctx.exception.cleanup_error)
        self.assertEqual(backend.calls, [("open", "COM17"), ("close",)])
        self.assertIs(transport.state, TransportState.CLOSED)
        transport.close()
        self.assertEqual(len(backend.calls), 2)

    def test_open_and_cleanup_failures_preserve_both_causes_and_handle(self):
        first, second = FakeSerial(), FakeSerial()
        first.open_error = OSError("cannot configure")
        first.close_error = OSError("cannot close")
        factory = Factory(first, second)
        transport = SerialTransport(backend_factory=factory)
        with self.assertRaises(TransportOpenError) as ctx:
            transport.open("COM17")
        self.assertIs(ctx.exception.__cause__, first.open_error)
        self.assertIsInstance(ctx.exception.cleanup_error, TransportCloseError)
        self.assertIs(ctx.exception.cleanup_error.__cause__, first.close_error)
        self.assert_recovery(transport, first)
        self.assertEqual(transport.port, "COM17")
        self.assertEqual(len(factory.calls), 1)
        first.close_error = None
        transport.close()
        transport.open("COM18")
        self.assertEqual(len(factory.calls), 2)
        self.assertEqual(first.calls, [("open", "COM17"), ("close",), ("close",)])
        self.assertEqual(second.calls, [("open", "COM18")])

    def test_open_requires_closed_factory_handle_and_true_confirmation(self):
        for initially_open, confirmed in ((True, True), (1, True), (False, False),
                                          (False, 1)):
            backend = FakeSerial()
            backend.is_open = initially_open
            backend.confirm_open = confirmed
            transport = SerialTransport(backend_factory=Factory(backend))
            with self.assertRaises(TransportOpenError):
                transport.open("COM17")
            self.assertIs(transport.state, TransportState.CLOSED)
            self.assertEqual(backend.calls[-1], ("close",))
            if initially_open is not False:
                self.assertEqual(backend.calls, [("close",)])

    def test_failed_port_assignment_is_open_error_and_cleans_up(self):
        class BadPort(FakeSerial):
            @property
            def port(self):
                return None

            @port.setter
            def port(self, value):
                if value is not None:
                    raise ValueError("unsupported port")

        backend = BadPort()
        transport = SerialTransport(backend_factory=Factory(backend))
        with self.assertRaises(TransportOpenError) as ctx:
            transport.open("COM17")
        self.assertIsInstance(ctx.exception.__cause__, ValueError)
        self.assertEqual(backend.calls, [("close",)])
        self.assertIs(transport.state, TransportState.CLOSED)

    def test_close_failure_retains_handle_and_blocks_reopen_until_explicit_close(self):
        transport, backend, factory = self.opened()
        failure = OSError("failed to release")
        backend.close_error = failure
        with self.assertRaises(TransportCloseError) as ctx:
            transport.close()
        self.assertIs(ctx.exception.__cause__, failure)
        self.assert_recovery(transport, backend)
        self.assertEqual(len(factory.calls), 1)
        backend.close_error = None
        transport.close()
        transport.close()
        self.assertEqual(backend.calls, [("open", "COM17"), ("close",), ("close",)])
        self.assertIsNone(transport.port)
        self.assertIs(transport.state, TransportState.CLOSED)

    def test_silent_failed_close_is_not_reported_as_success(self):
        transport, backend, _ = self.opened()
        backend.confirm_close = False
        with self.assertRaises(TransportCloseError):
            transport.close()
        self.assert_recovery(transport, backend)
        backend.confirm_close = True
        transport.close()
        self.assertIs(transport.state, TransportState.CLOSED)

    def test_invalid_open_arguments_do_not_change_state_or_call_backend(self):
        invalid = [(port, {}) for port in (
            None, 1, True, b"COM17", "", " COM17", "COM17 ", "COM\x0017",
            "COM\n17", "COM\x7f17", "loop://", "socket://localhost:7",
            "x" * (MAX_PORT_CHARS + 1), StrSubclass("COM17"),
        )]
        invalid += [("COM17", {"baudrate": value}) for value in (
            None, True, 0, -1, MAX_BAUDRATE + 1, 115200.0, "115200", IntSubclass(1),
        )]
        invalid += [("COM17", {name: value})
                    for name in ("read_timeout", "write_timeout")
                    for value in (None, True, -0.001, 1.001, float("nan"),
                                  float("inf"), float("-inf"), "0.1", 10 ** 1000)]
        invalid.append(("COM17", {"write_timeout": 0}))
        for opened in (False, True):
            backend = FakeSerial()
            factory = Factory(backend)
            transport = SerialTransport(backend_factory=factory)
            if opened:
                transport.open("COM17")
            before = (transport.state, transport.port, list(backend.calls), list(factory.calls))
            for port, kwargs in invalid:
                with self.subTest(opened=opened, argument_type=type(port), kwargs=kwargs):
                    with self.assertRaises(TransportArgumentError):
                        transport.open(port, **kwargs)
                    after = (transport.state, transport.port, backend.calls, factory.calls)
                    self.assertEqual(before, after)

    def test_invalid_read_arguments_are_side_effect_free(self):
        transport, backend, _ = self.opened()
        before = list(backend.calls)
        for size in (True, False, None, -1, MAX_IO_BYTES + 1, 1.0, "1", [], IntSubclass(1)):
            with self.assertRaises(TransportArgumentError):
                transport.read(size)
            self.assertIs(transport.state, TransportState.OPEN)
            self.assertEqual(backend.calls, before)

    def test_invalid_write_arguments_are_side_effect_free_and_not_coerced(self):
        class Coercible:
            def __bytes__(self):
                raise AssertionError("Invalid input must not be coerced")

        released = memoryview(b"x")
        released.release()
        arguments = [None, True, 3, "abc", [1, 2], Coercible(), BytesSubclass(b"abc"),
                     b"x" * (MAX_IO_BYTES + 1), bytearray(MAX_IO_BYTES + 1),
                     memoryview(b"x" * (MAX_IO_BYTES + 1)), released,
                     memoryview(b"abcd")[::2], memoryview(array("H", [1, 2])),
                     memoryview(b"abcd").cast("B", (2, 2))]
        transport, backend, _ = self.opened()
        before = list(backend.calls)
        for data in arguments:
            with self.subTest(argument_type=type(data)):
                with self.assertRaises(TransportArgumentError):
                    transport.write(data)
                self.assertIs(transport.state, TransportState.OPEN)
                self.assertEqual(backend.calls, before)

    def test_invalid_arguments_do_not_clear_recovery(self):
        transport, backend, _ = self.opened()
        backend.write_error = OSError("lost")
        with self.assertRaises(TransportWriteError):
            transport.write(b"x")
        before = list(backend.calls)
        for operation, args in ((transport.read, (True,)), (transport.write, (None,)),
                                (transport.open, (None,))):
            with self.assertRaises(TransportArgumentError):
                operation(*args)
            self.assertIs(transport.state, TransportState.RECOVERY)
            self.assertEqual(backend.calls, before)

    def test_errors_are_distinct_and_public_state_is_read_only(self):
        self.assertEqual(len({TransportOpenError, TransportReadError,
                              TransportWriteError, TransportCloseError}), 4)
        for error in (TransportArgumentError, TransportStateError, TransportOpenError,
                      TransportReadError, TransportWriteError, TransportCloseError):
            self.assertTrue(issubclass(error, TransportError))
        for value in (False, 0, "factory", object()):
            with self.assertRaises(TransportArgumentError):
                SerialTransport(backend_factory=value)
        transport = SerialTransport(backend_factory=Factory())
        self.assertFalse(hasattr(transport, "__dict__"))
        for name, value in (("state", TransportState.OPEN), ("port", "COM18")):
            with self.assertRaises(AttributeError):
                setattr(transport, name, value)


if __name__ == "__main__":
    unittest.main()
