"""Qt-free LV-001 owner: one warm-up, twenty attempts, no physical admission.

Only the owner thread calls the unchanged collector/runtime/serial APIs.  The
small cancellation lock never covers I/O.  A retired owner is retained until
its entered operations settle; Python cannot cancel an entered OS syscall.
"""
from dataclasses import asdict, dataclass, replace
from enum import Enum
import os
import json
from pathlib import Path
import secrets
import stat
import threading
import time

from .device_emulator import DeviceEmulator
from .lv_collector import (ATTEMPTS, LVCollector, RunIdentity, State,
                           MAX_INDEX_BYTES, MAX_JOURNAL_BYTES, MAX_SIDECAR_BYTES)
from .recording import MAX_FILE
from .model import Capture
from .session import Config, EventKind
from .transport import SerialTransport, TransportState
from .wire import decode_frame, encode_frame
from .window import WindowResult


BASE_COMMIT = "1930f75b976be17a3df51da079b6d8ba275e3f73"
BASE_TREE = "c92973b11fbdf31e43bd8d709e20a4a1a03a9f62"
MAX_RUN_MS = 3_600_000
MAX_ATTEMPT_MS = 120_000
MAX_STORAGE_MS = 30_000
MAX_WAIT_MS = 1_000
MAX_PATH_CHARS = 1_024
MAX_OUTPUT_FILES = 2 * ATTEMPTS + 2  # index, 21 pairs, one in-flight temporary
MAX_OUTPUT_BYTES = MAX_INDEX_BYTES + ATTEMPTS * (MAX_FILE + MAX_SIDECAR_BYTES) + MAX_SIDECAR_BYTES
MAX_WIRE_BYTES = ATTEMPTS * MAX_JOURNAL_BYTES
MAX_IO_CALLS = 2_000_000
READ_TIMEOUT = 0.01
WRITE_TIMEOUT = 0.05
_OWNER_SLOT = threading.Lock()
_ACTIVE_HANDLE = None


class RunnerError(ValueError):
    """Bounded public error; never includes a backend exception or port data."""


class AdmissionError(RunnerError):
    """The exact board/operator admission interface is not available."""


class Status(str, Enum):
    RUNNING = "RUNNING"
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


def monotonic_ms():
    """Actual elapsed monotonic milliseconds, not a synthetic protocol clock."""
    return time.monotonic_ns() // 1_000_000


def _require(value, message="Некорректный запрос LV-001"):
    if not value:
        raise RunnerError(message)


def _integer(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)


def _path(value):
    _require(type(value) is str or isinstance(value, Path))
    text = str(value)
    _require(1 <= len(text) <= MAX_PATH_CHARS)
    _require(not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text))
    raw = Path(text)
    _require(raw.name not in ("", ".", "..") and ".." not in raw.parts)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}
    for component in raw.parts:
        if component in (raw.anchor, raw.drive):
            continue
        _require(component not in ("", ".", "..") and not component.endswith((".", " ")))
        _require(not any(c in '<>:"|?*' for c in component))
        _require(component.split(".")[0].upper() not in reserved)
    absolute = Path(os.path.abspath(raw))  # lexical only: no resolve()/I/O here
    _require(len(str(absolute)) <= MAX_PATH_CHARS and len(absolute.parts) <= 64)
    return absolute


@dataclass(frozen=True, slots=True)
class Limits:
    run_ms: int = 1_800_000
    attempt_ms: int = 90_000
    storage_ms: int = 10_000
    shutdown_ms: int = 1_000
    poll_ms: int = 2

    def __post_init__(self):
        for name, maximum in (("run_ms", MAX_RUN_MS), ("attempt_ms", MAX_ATTEMPT_MS),
                              ("storage_ms", MAX_STORAGE_MS), ("shutdown_ms", 1_000),
                              ("poll_ms", 20)):
            _integer(getattr(self, name), 1, maximum)


@dataclass(frozen=True, slots=True)
class RunRequest:
    directory: str | Path
    identity: RunIdentity
    config: Config
    limits: Limits = Limits()

    def __post_init__(self):
        _path(self.directory)
        _require(type(self.identity) is RunIdentity)
        self.identity.__post_init__()
        _require(self.identity.origin == "simulator", "Физический запуск LV-001 пока не допущен")
        _require(all(len(getattr(self.identity, n)) == 40 and
                     all(c in "0123456789abcdef" for c in getattr(self.identity, n))
                     for n in ("application_commit", "application_tree")),
                 "Нужны точные commit и tree запускаемой сборки")
        _require(type(self.config) is Config)
        self.config.__post_init__()
        _require((self.config.profile, self.config.mode, self.config.source,
                  self.config.pretrigger_samples) == (1, 0, 0, 0))
        _require(type(self.limits) is Limits)
        self.limits.__post_init__()


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    status: Status
    reason: str
    directory: str
    folder_created: bool = False
    operation: str = "STARTING"
    collector_state: str = "NOT_CREATED"
    collector_reason: str = "NOT_OBSERVED"
    warmup_completed: bool = False
    series_completed: int = 0
    matched_stops: int = 0
    last_stop_generation: int | None = None
    current_generation: int | None = None
    current_arm_request_id: int | None = None
    attempt_number: int | None = None
    storage_status: str = "NOT_OBSERVED"
    writer_operation: str = "NONE"
    writer_settlement: str = "NOT_OBSERVED"
    retained_capture: bool = False
    retained_fragments: bool = False
    transport_closed: bool = False
    owner_alive: bool = True
    fence_at_ms: int | None = None
    observed_at_ms: int = 0
    elapsed_ms: int = 0
    physical_verified: bool = False


@dataclass(frozen=True, slots=True)
class RetainedEvidence:
    """Detached current-result evidence in memory, not a durability receipt."""
    capture: Capture | None
    incomplete: WindowResult | None
    index_json: bytes
    journal_json: bytes
    durable: bool = False


def _bounded_json(value, limit):
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        _require(len(part) <= limit - len(result), "Превышен предел метаданных")
        raw = part.encode("utf-8")
        _require(len(raw) <= limit - len(result), "Превышен предел метаданных")
        result.extend(raw)
    return bytes(result)


class _Fence:
    def __init__(self, limits):
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.limits = limits
        self.started = monotonic_ms()
        self.run_deadline = self.started + limits.run_ms
        self.attempt_deadline = None
        self.operation_deadline = None
        self.operation_reason = "OPERATION_DEADLINE"
        self.reason = None
        self.at_ms = None
        self.finished = False
        self.signal_requested = False

    def _set(self, reason, now):
        if self.reason is None and not self.finished:
            self.reason, self.at_ms = reason, now
            self.changed.set()

    def _check(self, now):
        if self.finished:
            return self.reason
        if self.signal_requested:
            self._set("SIGNAL_STOP", now)
        deadlines = ((self.run_deadline, "RUN_DEADLINE"),
                     (self.attempt_deadline, "ATTEMPT_DEADLINE"),
                     (self.operation_deadline, self.operation_reason))
        for deadline, reason in sorted((d, r) for d, r in deadlines if d is not None):
            if now >= deadline:
                self._set(reason, now)
                break
        return self.reason

    def reason_now(self):
        with self.lock:
            return self._check(monotonic_ms())

    def stop(self, reason):
        with self.lock:
            now = monotonic_ms()
            self._check(now)
            self._set(reason, now)


class _FencedTransport:
    """Effect fence only; session/ACK/generation policy stays in the runtime.

    One complete outgoing frame or its exact known remaining suffix is held.
    The short locked check is the write-admission linearization point.  The
    lock is released before the backend call; an admitted call is irrevocable.
    """
    def __init__(self, serial, fence):
        self.serial, self.fence = serial, fence
        self.stop_identity = lambda: (0, 0)
        self.remaining = None
        self.frame = None
        self.uncertain = False
        self.bytes = self.calls = 0

    def _budget(self, size):
        _require(self.calls < MAX_IO_CALLS and self.bytes + size <= MAX_WIRE_BYTES,
                 "Исчерпан ограниченный бюджет транспорта")
        self.calls += 1

    def write(self, data):
        _require(type(data) is bytes and 1 <= len(data) <= 544)
        _require(not self.uncertain, "Исход предыдущей записи неизвестен")
        if self.remaining is None:
            _require(data.endswith(b"\0") and b"\0" not in data[:-1])
            frame = decode_frame(data[:-1])
            _require(encode_frame(frame) == data and frame.message_type != 7,
                     "RELEASE в серии LV-001 запрещён")
            self.frame, self.remaining = frame, data
        _require(data == self.remaining, "Не совпал сохранённый суффикс команды")
        self._budget(len(data))
        with self.fence.lock:
            now = monotonic_ms()
            reason = self.fence._check(now)
            if reason is not None:
                _require(now < self.fence.at_ms + self.fence.limits.shutdown_ms and
                         self.frame.message_type == 5 and
                         (self.frame.session, self.frame.request_id) == self.stop_identity(),
                         "После останова новая команда не допущена")
            # Admission is observed here, not at eventual OS/backend completion.
        try:
            count = self.serial.write(data)
        except BaseException:
            self.uncertain = True
            raise
        _require(type(count) is int and 0 <= count <= len(data))
        self.bytes += count
        self.remaining = data[count:] or None
        return count

    def read(self, size):
        _integer(size, 0, 4096)
        self._budget(size)
        with self.fence.lock:
            now = monotonic_ms()
            if self.fence._check(now) is not None:
                _require(now < self.fence.at_ms + self.fence.limits.shutdown_ms,
                         "Истёк бюджет завершения обмена")
        data = self.serial.read(size)
        self.bytes += len(data)
        return data


def _stat_directory(path):
    value = path.lstat()
    _require(stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode) and
             not (getattr(value, "st_file_attributes", 0) & 0x400),
             "Каталог-ссылка или reparse point не допускается")
    return value.st_dev, value.st_ino


class _Namespace:
    """Bounded boundary checks, not an adversarial filesystem sandbox."""
    def __init__(self, directory):
        self.directory = directory
        self.parents = ()
        self.created = None

    def prepare(self):
        parents = tuple(reversed(self.directory.parents))
        self.parents = tuple((p, _stat_directory(p)) for p in parents)
        _require(not os.path.lexists(self.directory), "Нужна новая папка опыта")

    def confirm_created(self):
        self.created = _stat_directory(self.directory)
        self.check()

    def check(self):
        for path, identity in self.parents:
            _require(_stat_directory(path) == identity, "Родительский каталог изменён")
        if self.created is None:
            return
        _require(_stat_directory(self.directory) == self.created, "Каталог опыта изменён")
        total = count = 0
        with os.scandir(self.directory) as entries:
            for entry in entries:
                count += 1
                _require(count <= MAX_OUTPUT_FILES, "Превышен предел файлов опыта")
                info = entry.stat(follow_symlinks=False)
                _require(stat.S_ISREG(info.st_mode) and not
                         (getattr(info, "st_file_attributes", 0) & 0x400),
                         "Файл-ссылка или reparse point не допускается")
                name = entry.name
                limit = MAX_SIDECAR_BYTES
                if name == "index.json":
                    limit = MAX_INDEX_BYTES
                elif name.startswith(".motortester-"):
                    _require(len(name) <= 128)
                else:
                    _require(any(name == f"attempt-{i:02d}.{suffix}"
                                 for i in range(ATTEMPTS) for suffix in ("json", "mtcap")),
                             "В каталоге опыта обнаружен посторонний файл")
                    if name.endswith(".mtcap"):
                        limit = MAX_FILE
                _require(0 <= info.st_size <= limit, "Превышен предел размера файла опыта")
                total += info.st_size
                _require(total <= MAX_OUTPUT_BYTES, "Превышен предел объёма опыта")


class _SyntheticSerial:
    """Closed inert serial-compatible backend; cannot enumerate/open a device."""
    def __init__(self, **settings):
        self.port = None
        self.is_open = False
        self.device = DeviceEmulator(all_analog_modes=True)

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def write(self, data):
        return self.device.write(data, monotonic_ms()).accepted_prefix

    def read(self, size):
        return self.device.read(size, monotonic_ms())


class RunHandle:
    """One bounded immutable status slot; no access to mutable collector state.

    stop() permanently fences new ARM. wait() only observes for at most one
    second, and its timeout does not cancel a run. retire() combines the two.
    A PENDING handle must be retained: its original owner may still be writing.
    """
    def __init__(self, request):
        self._request = request
        self._directory = _path(request.directory)
        self._fence = _Fence(request.limits)
        self._lock = threading.Lock()
        self._snapshot = RunSnapshot(Status.RUNNING, "NONE", str(self._directory),
                                     observed_at_ms=monotonic_ms())
        self._collector = None  # Retained private evidence, never used by caller.
        self._thread = threading.Thread(target=self._run, name="lv-001-owner", daemon=True)

    def snapshot(self):
        with self._fence.lock:
            now = monotonic_ms()
            reason = self._fence._check(now)
            fence_at = self._fence.at_ms
        with self._lock:
            result = self._snapshot
        alive = self._thread.is_alive()
        status = result.status
        if reason is not None:
            status = (Status.PENDING if alive else
                      Status.CANCELLED if reason in ("OPERATOR_STOP", "SIGNAL_STOP") else Status.FAILED)
        elif alive and status is Status.COMPLETE:
            status = Status.RUNNING
        return replace(result, status=status, reason=reason or result.reason,
                       owner_alive=alive, fence_at_ms=fence_at,
                       elapsed_ms=now - self._fence.started)

    def stop(self):
        # No I/O, collector call or join. Once terminal, do not rewrite history.
        if self._thread.is_alive():
            self._fence.stop("OPERATOR_STOP")
        return self.snapshot()

    def signal_stop(self):
        """Signal-handler-safe request: no lock, I/O, callback or collector call.

        The permanent flag is consumed by every subsequent admission/deadline
        check. An already-admitted operation remains irrevocable.
        """
        self._fence.signal_requested = True

    def wait(self, timeout_ms=MAX_WAIT_MS):
        _integer(timeout_ms, 0, MAX_WAIT_MS)
        deadline = monotonic_ms() + timeout_ms
        while self._thread.is_alive():
            self._fence.reason_now()
            remaining = deadline - monotonic_ms()
            if remaining <= 0:
                break
            self._thread.join(min(remaining, 20) / 1000)
        return self.snapshot()

    def retire(self, timeout_ms=250):
        _integer(timeout_ms, 0, MAX_WAIT_MS)
        self.stop()
        return self.wait(timeout_ms)

    def retained_evidence(self):
        """Read only after actual owner exit; never save, ACK, retry or repair.

        None means no collector was created. Full completed arrays are already
        released; only the current retained capture/fragments can be returned.
        Detached metadata bytes are capped independently of the status slot.
        """
        _require(not self._thread.is_alive(),
                 "Владелец ещё работает; чтение удержанных данных пока недоступно")
        c = self._collector
        if c is None:
            return None
        capture = c.runtime.pending_capture
        incomplete = c.runtime.incomplete_result
        observed = c.observed
        journal = dict(records=observed.records, raw_byte_count=observed.byte_count,
                       failure=observed.failure, observation_only=True,
                       identical_frame_replays=observed.identical_frame_replays,
                       repeated_data_blocks=observed.repeated_data_blocks)
        return RetainedEvidence(replace(capture) if capture is not None else None,
            replace(incomplete) if incomplete is not None else None,
            _bounded_json(c.index, MAX_INDEX_BYTES), _bounded_json(journal, MAX_SIDECAR_BYTES))

    def _publish(self, **fields):
        fields["observed_at_ms"] = monotonic_ms()
        with self._lock:
            self._snapshot = replace(self._snapshot, **fields)

    def _observe(self):
        c = self._collector
        if c is not None:
            completed = [a["outcome"] == "completed" for a in c.index["attempts"]]
            self._publish(collector_state=c.state.value, collector_reason=c.reason,
                          warmup_completed=completed[0],
                          series_completed=sum(completed[1:]), storage_status=c.storage_status,
                          current_generation=c.runtime.snapshot().generation,
                          retained_capture=c.runtime.pending_capture is not None,
                          retained_fragments=c.runtime.incomplete_result is not None)

    def _call(self, operation, action, *, storage=False, check_path=True):
        self._publish(operation=operation)
        with self._fence.lock:
            now = monotonic_ms()
            self._fence._check(now)
            self._fence.operation_deadline = now + (self._request.limits.storage_ms if storage else 1_000)
            self._fence.operation_reason = "STORAGE_DEADLINE" if storage else "OPERATION_DEADLINE"
        try:
            if check_path:
                self._namespace.check()
            if operation in ("START", "ARM_INTENT", "OPEN"):
                _require(self._fence.reason_now() is None, "Запуск уже остановлен")
            writing = operation in ("START", "ARM_INTENT", "PERSIST")
            if writing:
                self._publish(writer_operation=operation, writer_settlement="ENTERED")
            try:
                result = action()
            except BaseException:
                if writing:
                    self._publish(writer_settlement="RAISED_UNCONFIRMED")
                raise
            if writing:
                self._publish(writer_settlement="RETURNED_VERIFIED")
            if check_path:
                self._namespace.check()
            return result
        finally:
            with self._fence.lock:
                self._fence._check(monotonic_ms())
                self._fence.operation_deadline = None
            self._observe()

    def _run(self):
        serial = None
        self._namespace = _Namespace(self._directory)
        try:
            self._call("PATH_CHECK", self._namespace.prepare, storage=True, check_path=False)
            serial = SerialTransport(backend_factory=_SyntheticSerial)
            transport = _FencedTransport(serial, self._fence)
            c = self._collector = LVCollector(self._directory, transport,
                clock_ms=monotonic_ms, identity=self._request.identity,
                config=self._request.config, nonce=secrets.randbits(64) or 1)
            transport.stop_identity = lambda: (c.runtime.snapshot().session,
                                               c.runtime.snapshot().pending_stop_id)
            self._call("START", c.start, storage=True)
            self._call("PATH_CHECK", self._namespace.confirm_created, storage=True)
            self._publish(folder_created=True)
            if self._fence.reason_now() is None:
                self._call("OPEN", lambda: serial.open("LV-SYNTHETIC",
                    read_timeout=READ_TIMEOUT, write_timeout=WRITE_TIMEOUT))
            self._drive()
        except BaseException:
            self._fence.stop("OWNER_FAILURE")
            self._observe()
        finally:
            if serial is not None:
                try:
                    self._call("CLOSE", serial.close, check_path=False)
                    self._publish(transport_closed=serial.state is TransportState.CLOSED)
                except BaseException:
                    self._fence.stop("CLOSE_UNCONFIRMED")
            self._observe()
            with self._fence.lock:
                reason = self._fence._check(monotonic_ms())
                self._fence.finished = True
            self._publish(operation="SETTLED", status=(Status.COMPLETE if reason is None and
                          self._collector is not None and self._collector.state is State.COMPLETE
                          else Status.CANCELLED if reason in ("OPERATOR_STOP", "SIGNAL_STOP") else Status.FAILED),
                          reason=reason or "NONE")

    def _drive(self):
        c = self._collector
        cancelled = False
        last_stop = 0
        while c.state not in (State.COMPLETE, State.FAILED):
            fenced = self._fence.reason_now() is not None
            if fenced and not cancelled:
                self._call("STOP_REQUEST", c.cancel, check_path=False)
                cancelled = True
            if c.state is State.READY:
                _require(not fenced, "После останова ARM запрещён")
                number = next(a["number"] for a in c.index["attempts"] if a["outcome"] == "planned")
                self._publish(attempt_number=number,
                              current_arm_request_id=c.runtime.snapshot().next_request_id)
                with self._fence.lock:
                    self._fence.attempt_deadline = monotonic_ms() + self._request.limits.attempt_ms
                try:
                    self._call("ARM_INTENT", c.arm_next, storage=True)
                except RunnerError:
                    if c.state is not State.READY or self._fence.reason_now() is None:
                        raise
            elif c.state in (State.NEGOTIATING, State.ACTIVE, State.STOPPING):
                try:
                    events = self._call("PUMP", c.pump, check_path=False)
                    for event in events:
                        if event.kind is EventKind.STOPPED and event.request_id != last_stop:
                            last_stop = event.request_id
                            self._publish(matched_stops=self._snapshot.matched_stops + 1,
                                          last_stop_generation=event.generation)
                except Exception:
                    if c.state is not State.SAVEABLE:
                        raise
                    self._fence.stop("COLLECTOR_FAILED")
                if c.state in (State.NEGOTIATING, State.ACTIVE, State.STOPPING):
                    if fenced:
                        time.sleep(self._request.limits.poll_ms / 1000)
                    else:
                        self._fence.changed.wait(self._request.limits.poll_ms / 1000)
            elif c.state is State.SAVEABLE:
                self._call("PERSIST", c.persist_attempt, storage=True)
                with self._fence.lock:
                    self._fence._check(monotonic_ms())
                    self._fence.attempt_deadline = None
            else:
                raise RunnerError("Неизвестное состояние сборщика")
        if c.state is State.FAILED:
            self._fence.stop("COLLECTOR_FAILED")


def run_synthetic(request):
    """Start one explicit 21-attempt plan using only the built-in byte emulator."""
    _require(type(request) is RunRequest)
    request.__post_init__()
    global _ACTIVE_HANDLE
    with _OWNER_SLOT:
        _require(_ACTIVE_HANDLE is None or not _ACTIVE_HANDLE._thread.is_alive(),
                 "Предыдущий владелец LV-001 ещё не завершился; новая серия запрещена")
        # Copy the flat validated request before handing it to another thread.
        owned = replace(request, directory=_path(request.directory),
                        identity=RunIdentity(**asdict(request.identity)),
                        config=replace(request.config), limits=replace(request.limits))
        handle = RunHandle(owned)
        _ACTIVE_HANDLE = handle  # Retain an unresolved owner even if its caller leaves.
        handle._thread.start()
        return handle


def run_device(*args, **kwargs):
    """Fail closed before inspecting data, constructing serial, or opening I/O."""
    raise AdmissionError("Физический LV-001 заблокирован до согласования точного допуска платы, прошивки, USB и измерений")


def main(argv=None):
    import argparse
    import signal
    parser = argparse.ArgumentParser(description="Синтетическая серия LV-001 без Qt и без прибора")
    parser.add_argument("directory")
    parser.add_argument("--application-commit", required=True)
    parser.add_argument("--application-tree", required=True)
    parser.add_argument("--edge", type=int, choices=(0, 1), default=0)
    parser.add_argument("--level", type=int, default=2048)
    parser.add_argument("--timeout-ms", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        _require(threading.current_thread() is threading.main_thread(),
                 "CLI запускается только в главном потоке")
        handle = run_synthetic(RunRequest(args.directory,
            RunIdentity("simulator", args.application_commit, args.application_tree),
            Config(mode=0, source=0, profile=1, pretrigger_samples=0,
                   edge=args.edge, level_code=args.level, auto_timeout_ms=args.timeout_ms)))
        previous = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.signal(sig, lambda *_: handle.signal_stop())
            result = handle.wait()
            while result.owner_alive:
                result = handle.wait()
                if result.status is Status.PENDING:
                    result = handle.retire()
                    break
        except KeyboardInterrupt:
            result = handle.retire()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print(f"LV-001: {result.status.value}; причина {result.reason}; прогрев "
              f"{int(result.warmup_completed)}/1, серия {result.series_completed}/20. "
              f"Папка: {result.directory}. Владелец ещё работает: {result.owner_alive}. "
              "Только синтетическая проверка; физический опыт не выполнен.")
        return 0 if result.status is Status.COMPLETE else 2
    except (RunnerError, ValueError, TypeError):
        parser.exit(2, "Серия LV-001 не запущена: неверные параметры или незавершённый владелец\n")


if __name__ == "__main__":
    raise SystemExit(main())
