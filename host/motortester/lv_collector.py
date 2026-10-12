"""Ограниченный LV-001 collector: Stop → диск → проверка → локальный ACK.

Только библиотека с внедрённым транспортом и CLI проверки сохранённого опыта.
Физическое подключение, сброс и открытие/закрытие транспорта принадлежат caller.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import struct
from uuid import uuid4
import zlib

from . import recording
from .runtime import AcquisitionRuntime
from .model import Capture
from .session import Config, EventKind, Phase, SOURCE_SHA as SESSION_PIN
from .window import PROTOCOL_SOURCE_SHA as WINDOW_PIN
from .wire import Frame, StreamDecoder, encode_frame

ATTEMPTS = 21
MAX_JOURNAL_BYTES = 131072
MAX_JOURNAL_RECORDS = 8192
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 65536
MAX_POLL_COUNT = 0xFFFFFFFF
MAX_TIME = (1 << 64) - 1
PROPOSAL_SHA = "db09c263c022c27723fd9de9e4a79905097767c6"
PROVENANCE = {
    "proposal_commit": PROPOSAL_SHA,
    "local_base_commit": "33696f8a41d70196d7870a92b24d01def44a1eb2",
    "published_base_commit": "1af7c7c27e7eaa6559138b055ed40c7e3114abb1",
    "base_tree": "2ed75f6d5d9993af47a79cbfa0eb1e6b3568b2e9",
    "session_module_pin": SESSION_PIN, "window_module_pin": WINDOW_PIN,
    "codec_module_pin": "124b3f32e1e7f7282bce283baa4c9406d4d72950",
    "shared_semantics_accepted": False,
}


class CollectorError(ValueError):
    """Фиксированное безопасное сообщение; данные остаются у владельца."""


class State(str, Enum):
    NEW = "NEW"
    NEGOTIATING = "NEGOTIATING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    SAVEABLE = "SAVEABLE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


def _require(condition, message="Некорректная запись опыта LV-001"):
    if not condition:
        raise CollectorError(message)


def _uint(value, maximum=MAX_TIME):
    _require(type(value) is int and 0 <= value <= maximum)


def _keys(value, names):
    _require(type(value) is dict and set(value) == set(names.split()))


def _text(value, maximum=512):
    _require(type(value) is str and 1 <= len(value) <= maximum)
    _require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _sha(value, *, tbd=False):
    _require(type(value) is str)
    _require((tbd and value == "TBD") or (len(value) in (40, 64)
             and all(c in "0123456789abcdef" for c in value)))


@dataclass(frozen=True)
class RunIdentity:
    origin: str
    application_commit: str
    application_tree: str
    application_build: str = "TBD"
    firmware_commit: str = "TBD"
    firmware_build_identity: str = "TBD"
    board_json_sha256: str = "TBD"
    hardware_passport_id: str = "TBD"
    schematic_id: str = "TBD"
    vdda_measurement: str = "TBD"
    adc_in_measurement: str = "TBD"
    meter_and_uncertainty: str = "TBD"
    independent_timebase: str = "TBD"
    measured_fs_and_uncertainty: str = "TBD"

    def __post_init__(self):
        _require(type(self.origin) is str and self.origin in ("simulator", "device"))
        for name in self.__dataclass_fields__:
            _text(getattr(self, name))
        for name in ("application_commit", "application_tree", "firmware_commit",
                     "board_json_sha256"):
            _sha(getattr(self, name), tbd=True)
            value = getattr(self, name)
            _require(value == "TBD" or len(value) == (64 if name == "board_json_sha256" else 40))


def _config(config):
    _require(type(config) is Config, "Нужны настройки Config")
    config.__post_init__()
    _require((config.profile, config.mode, config.source, config.pretrigger_samples)
             == (1, 0, 0, 0), "LV-001 поддерживает только Auto/analog/profile 1/pretrigger 0")


def _json(value, limit=MAX_SIDECAR_BYTES):
    # Ограничиваем накопление во время сериализации, а не после готовой копии.
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        _require(len(part) <= limit - len(result), "Превышен предел JSON опыта")
        encoded = part.encode("utf-8")
        _require(len(encoded) <= limit - len(result), "Превышен предел JSON опыта")
        result.extend(encoded)
    return bytes(result)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _envelope(kind, body, limit):
    raw = _json({"format": kind, "version": 1, "body": body, "sha256": _hash(_json(body, limit))}, limit)
    _require(len(raw) <= limit, "Превышен предел размера файла опыта")
    return raw


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Повторяющийся ключ JSON")
        result[key] = value
    return result


def _read(path, limit):
    _require(not path.is_symlink() and path.is_file(), "Файл отсутствует или заменён ссылкой")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _require(len(data) <= limit, "Превышен предел размера файла опыта")
    return data


def _decode(raw, kind, limit):
    _require(type(raw) is bytes and len(raw) <= limit)
    try:
        value = json.loads(raw, object_pairs_hook=_object,
                           parse_constant=lambda _: _require(False))
        _keys(value, "format version body sha256")
        _require(value["format"] == kind and type(value["version"]) is int and value["version"] == 1)
        _require(type(value["sha256"]) is str and _hash(_json(value["body"])) == value["sha256"])
        _require(_json(value) == raw, "Файл опыта имеет неканонический JSON")
        return value["body"]
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise CollectorError("Повреждён или не поддерживается JSON опыта") from exc


_LAYOUTS = {
    129: ("<BBBBHHII", "major minor channels adc_bits max_payload max_window firmware_build capability_bits"),
    130: ("<BBHIHHIHHBBBBHHH", "profile_count reserved profile timer_clock_hz timer_psc timer_arr adc_clock_hz adc_sample_cycles_x2 window_samples pre_count mode_bits source_bits edge_bits pre0 pre1 pre2"),
    131: ("<IHBBBBHHHI", "config_id profile mode source edge reserved0 level_code pretrigger_samples reserved1 auto_timeout_ms"),
    144: ("<IIIHHHHBBBBHHIHHI", "generation window_id config_id profile sample_count pretrigger_samples trigger_index trigger_kind source edge channel level_code quality_flags timer_clock_hz timer_psc timer_arr external_edge_id"),
    146: ("<IIHHI", "generation window_id sample_count reserved samples_crc32"),
    147: ("<IIHH", "generation window_id reason reserved"),
    133: ("<IIBBBB", "generation last_window_id status reserved0 reserved1 reserved2"),
}


def _frame(frame):
    fmt, names = _LAYOUTS[frame.message_type]
    return dict(message_type=frame.message_type, session=frame.session,
                sequence=frame.sequence, request_id=frame.request_id,
                payload_hex=frame.payload.hex(),
                decoded=dict(zip(names.split(), struct.unpack(fmt, frame.payload))))


def _check_frame(value, kind):
    if value is None:
        return
    _keys(value, "message_type session sequence request_id payload_hex decoded")
    _require(type(value["payload_hex"]) is str and len(value["payload_hex"]) <= 1024)
    for name in ("message_type", "session", "sequence", "request_id"):
        _uint(value[name])
    _require(value["message_type"] == kind)
    _keys(value["decoded"], _LAYOUTS[kind][1])
    for field in value["decoded"].values():
        _uint(field, 0xFFFFFFFF)
    try:
        f = Frame(kind, value["session"], value["sequence"], value["request_id"],
                  bytes.fromhex(value["payload_hex"]))
        _require(_frame(f) == value)
    except (ValueError, KeyError, struct.error) as exc:
        raise CollectorError("Повреждены исходные метаданные кадра") from exc


def _observe_replay(frame, previous, blocks):
    identical = int(frame == previous)
    repeated = 0
    if frame.message_type == 145 and len(frame.payload) >= 14:
        generation, window, offset, count = struct.unpack_from("<IIHH", frame.payload)
        if 1 <= count <= 250 and len(frame.payload) == 12 + count * 2 and offset + count <= 2048:
            key = (frame.session, generation, window, offset)
            if key in blocks:
                repeated = int(blocks[key] == frame.payload)
            else:
                _require(len(blocks) < 2048, "Превышен предел наблюдаемых блоков")
                blocks[key] = frame.payload
    return identical, repeated


class ObservedTransport:
    """Наблюдение перед runtime; RX не означает допуск прошивкой/контроллером.

    Предварительное резервирование консервативно: read может быть запрещён,
    даже если backend вернул бы меньше запрошенных байтов. Никакого disk I/O.
    """
    def __init__(self, transport, clock_ms):
        _require(callable(getattr(transport, "read", None)) and
                 callable(getattr(transport, "write", None)) and callable(clock_ms))
        self.transport, self.clock = transport, clock_ms
        self.records = []
        self.byte_count = 0
        self.failure = None
        self.command = "other"
        self._decoder = StreamDecoder()
        self._last_frame = None
        self.identical_frame_replays = 0
        self.repeated_data_blocks = 0
        self._data_blocks = {}

    def _reserve(self, size):
        if (self.failure is not None or self.byte_count + size > MAX_JOURNAL_BYTES
                or len(self.records) >= MAX_JOURNAL_RECORDS):
            self.failure = self.failure or "JOURNAL_OVERFLOW"
            raise CollectorError("Журнал переполнен; продолжение запрещено")

    def _record(self, kind):
        now = self.clock()
        _uint(now)
        record = dict(kind=kind, at_ms=now, data_hex="", accepted=None,
                      outcome="pending", count=1, command=self.command)
        self.records.append(record)
        return record

    def write(self, data):
        _require(type(data) is bytes and len(data) <= 4096)
        self._reserve(len(data))
        record = self._record("tx")
        record["data_hex"] = data.hex()
        self.byte_count += len(data)
        # Намерение сохранено до вызова: исключение не означает нулевую запись.
        try:
            count = self.transport.write(data)
        except BaseException:
            record["outcome"] = "unknown_write"
            raise
        if type(count) is not int or not 0 <= count <= len(data):
            record["outcome"] = "unknown_write"
            raise CollectorError("Транспорт не подтвердил число принятых байтов")
        record.update(accepted=count, outcome="accepted_prefix")
        return count

    def read(self, size):
        _uint(size, 4096)
        self._reserve(size)
        record = self._record("rx")
        try:
            data = self.transport.read(size)
        except BaseException:
            record["outcome"] = "read_error"
            raise
        if type(data) is not bytes or len(data) > size:
            record["outcome"] = "invalid_read"
            self.failure = "INVALID_READ"
            raise CollectorError("Транспорт вернул недопустимую порцию; байты не копировались")
        self.byte_count += len(data)
        record.update(data_hex=data.hex(), outcome="observed")
        # Только наблюдение: не передаём эти frames в controller и не заменяем
        # его допуск. Дополнительная память: один decoder и один прошлый Frame.
        for frame in self._decoder.feed(data, record["at_ms"]).frames:
            try:
                identical, repeated = _observe_replay(frame, self._last_frame, self._data_blocks)
            except CollectorError:
                self.failure = "JOURNAL_OVERFLOW"
                raise
            self.identical_frame_replays += identical
            self.repeated_data_blocks += repeated
            self._last_frame = frame
        if not data:
            record["outcome"] = "empty"
            if len(self.records) > 1 and self.records[-2]["outcome"] == "empty":
                previous = self.records[-2]
                if previous["count"] == MAX_POLL_COUNT:
                    self.failure = "JOURNAL_OVERFLOW"
                    raise CollectorError("Счётчик пустых чтений переполнен")
                previous["count"] += 1
                self.records.pop()
        return data

    def clear(self):
        self.identical_frame_replays = 0
        self.repeated_data_blocks = 0
        self._data_blocks = {}
        self.records.clear()
        self.byte_count = 0
        self.failure = None


def _attempt(number):
    return dict(number=number, role="warmup" if number == 0 else "series",
                intended=True, intent_committed=False, arm_request_id=0,
                arm_queued=False, arm_ack=False, tx_known_bytes=0, tx_unknown=False,
                outcome="planned", sidecar_file=None, sidecar_sha256=None,
                mtcap_file=None, mtcap_sha256=None)


def _check_attempt(a, number):
    _keys(a, "number role intended intent_committed arm_request_id arm_queued arm_ack tx_known_bytes tx_unknown outcome sidecar_file sidecar_sha256 mtcap_file mtcap_sha256")
    _uint(a["number"], ATTEMPTS - 1)
    _require(a["number"] == number)
    _require(a["role"] == ("warmup" if number == 0 else "series") and a["intended"] is True)
    for name in ("intent_committed", "arm_ack", "tx_unknown"):
        _require(type(a[name]) is bool)
    _require(a["arm_queued"] is None or type(a["arm_queued"]) is bool)
    _uint(a["arm_request_id"], 0xFFFFFFFE)
    _uint(a["tx_known_bytes"], 544)
    _require(type(a["outcome"]) is str and a["outcome"] in
             ("planned", "pending", "completed", "failed", "cancelled"))
    if a["outcome"] == "completed":
        _require(a["intent_committed"] and a["arm_queued"] is True and a["arm_ack"] and not a["tx_unknown"])
        _require(a["mtcap_file"] is not None)
    if a["outcome"] == "planned":
        _require(not a["intent_committed"] and a["arm_request_id"] == 0 and a["arm_queued"] is False
                 and not a["arm_ack"] and a["tx_known_bytes"] == 0 and not a["tx_unknown"])
        _require(a["mtcap_file"] is None and a["sidecar_file"] is None)
    for field, suffix in (("sidecar", "json"), ("mtcap", "mtcap")):
        name, digest = a[field + "_file"], a[field + "_sha256"]
        if name is None:
            _require(digest is None)
        else:
            _require(name == f"attempt-{number:02d}.{suffix}")
            _sha(digest)
            _require(len(digest) == 64)


def _check_index(body):
    _keys(body, "run_id state reason identity implementation requested_config requested_fs_hz attempts")
    _text(body["run_id"], 36)
    _require(body["state"] in ("incomplete", "sealed_unverified", "failed", "cancelled"))
    _text(body["reason"])
    _require(type(body["identity"]) is dict and set(body["identity"]) == set(RunIdentity.__dataclass_fields__))
    RunIdentity(**body["identity"])
    _keys(body["implementation"], " ".join(PROVENANCE))
    _require(type(body["implementation"]["shared_semantics_accepted"]) is bool)
    _require(body["implementation"] == PROVENANCE)
    _require(type(body["requested_config"]) is dict and set(body["requested_config"]) == set(Config.__dataclass_fields__))
    _config(Config(**body["requested_config"]))
    _require(type(body["requested_fs_hz"]) is int and body["requested_fs_hz"] == 50000)
    _require(type(body["attempts"]) is list and len(body["attempts"]) == ATTEMPTS)
    for number, attempt in enumerate(body["attempts"]):
        _check_attempt(attempt, number)
    if body["state"] == "sealed_unverified":
        _require(all(a["outcome"] == "completed" and a["sidecar_file"] and a["mtcap_file"]
                     for a in body["attempts"]))


def _check_journal(journal):
    _keys(journal, "records raw_byte_count failure observation_only identical_frame_replays repeated_data_blocks")
    _uint(journal["repeated_data_blocks"], MAX_JOURNAL_BYTES // 28)
    _uint(journal["identical_frame_replays"], MAX_JOURNAL_BYTES // 28)
    _require(journal["observation_only"] is True)
    _require(journal["failure"] in (None, "JOURNAL_OVERFLOW", "INVALID_READ"))
    _require(type(journal["records"]) is list and len(journal["records"]) <= MAX_JOURNAL_RECORDS)
    total = 0
    for record in journal["records"]:
        _keys(record, "kind at_ms data_hex accepted outcome count command")
        _require(record["kind"] in ("tx", "rx") and record["command"] in ("other", "arm", "stop"))
        _uint(record["at_ms"])
        _uint(record["count"], MAX_POLL_COUNT)
        _require(record["count"] > 0)
        raw = record["data_hex"]
        _require(type(raw) is str and len(raw) <= 8192 and len(raw) % 2 == 0)
        _require(all(c in "0123456789abcdef" for c in raw))
        total += len(raw) // 2
        _require(total <= MAX_JOURNAL_BYTES)
        if record["kind"] == "tx":
            _require(record["count"] == 1 and record["outcome"] in ("accepted_prefix", "unknown_write"))
            if record["outcome"] == "accepted_prefix":
                _uint(record["accepted"], len(raw) // 2)
            else:
                _require(record["accepted"] is None)
        else:
            _require(record["accepted"] is None and record["outcome"] in
                     ("observed", "empty", "read_error", "invalid_read"))
            _require(record["outcome"] == "observed" or not raw)
            _require(record["outcome"] == "empty" or record["count"] == 1)
    _require(type(journal["raw_byte_count"]) is int and journal["raw_byte_count"] == total)


def _fragments(result):
    if result is None:
        return None
    from .window import WindowResult, SampleBlock, BeginMetadata
    _require(type(result) is WindowResult and type(result.fragments) is tuple and len(result.fragments) <= 2048)
    total = 0
    for block in result.fragments:
        _require(type(block) is SampleBlock and type(block.samples) is tuple and 1 <= len(block.samples) <= 250)
        total += len(block.samples)
        _require(total <= 2048)
        for code in block.samples:
            _uint(code, 4095)
    _require(result.begin is None or type(result.begin) is BeginMetadata)
    for value in (result.missing_ranges, result.deferred_checks, result.metadata_issues):
        _require(type(value) is tuple and len(value) <= 16)
    # Размеры проверены до создания JSON-списков; нулевое дополнение запрещено.
    return dict(reason=result.reason.value, begin=asdict(result.begin) if result.begin else None,
                fragments=[dict(offset=b.offset, samples=list(b.samples)) for b in result.fragments],
                missing_ranges=[list(r) for r in result.missing_ranges],
                abort_reason=result.abort_reason, deferred_checks=list(result.deferred_checks),
                metadata_issues=list(result.metadata_issues), rail_observed=result.rail_observed,
                complete=result.complete, samples_crc32=result.samples_crc32)


def _check_sidecar(body):
    _keys(body, "run_id attempt reason identity implementation requested_fs_hz accepted journal incomplete capture_sha256 stop_request_id stop_matched")
    _text(body["run_id"], 36)
    _text(body["reason"])
    _require(type(body["attempt"]) is dict and "number" in body["attempt"])
    _check_attempt(body["attempt"], body["attempt"]["number"])
    _require(type(body["identity"]) is dict and set(body["identity"]) == set(RunIdentity.__dataclass_fields__))
    RunIdentity(**body["identity"])
    _keys(body["implementation"], " ".join(PROVENANCE))
    _require(body["implementation"] == PROVENANCE and body["implementation"]["shared_semantics_accepted"] is False)
    _require(type(body["requested_fs_hz"]) is int and body["requested_fs_hz"] == 50000)
    _keys(body["accepted"], "hello caps config begin end abort stop")
    for name, kind in (("hello", 129), ("caps", 130), ("config", 131), ("begin", 144),
                       ("end", 146), ("abort", 147), ("stop", 133)):
        _check_frame(body["accepted"][name], kind)
    _check_journal(body["journal"])
    _uint(body["stop_request_id"], 0xFFFFFFFE)
    _require(type(body["stop_matched"]) is bool)
    if body["stop_matched"]:
        _require(body["accepted"]["stop"] is not None and
                 body["accepted"]["stop"]["request_id"] == body["stop_request_id"])
    if body["capture_sha256"] is not None:
        _sha(body["capture_sha256"])
    result = body["incomplete"]
    if result is not None:
        _keys(result, "reason begin fragments missing_ranges abort_reason deferred_checks metadata_issues rail_observed complete samples_crc32")
        from .window import WindowCode, BeginMetadata
        _require(type(result["reason"]) is str and result["reason"] in {v.value for v in WindowCode})
        for field in ("complete", "rail_observed"):
            _require(type(result[field]) is bool)
        for field in ("deferred_checks", "metadata_issues"):
            _require(type(result[field]) is list and len(result[field]) <= 16)
            for value in result[field]:
                _text(value, 64)
        if result["begin"] is not None:
            _keys(result["begin"], " ".join(BeginMetadata.__dataclass_fields__))
            for value in result["begin"].values():
                _uint(value, 0xFFFFFFFF)
        for field in ("abort_reason", "samples_crc32"):
            if result[field] is not None:
                _uint(result[field], 6 if field == "abort_reason" else 0xFFFFFFFF)
        _require(type(result["missing_ranges"]) is list and len(result["missing_ranges"]) <= 1)
        for pair in result["missing_ranges"]:
            _require(type(pair) is list and len(pair) == 2)
            for value in pair:
                _uint(value, 2048)
        _require(type(result["fragments"]) is list and len(result["fragments"]) <= 2048)
        count = 0
        for block in result["fragments"]:
            _keys(block, "offset samples")
            _require(type(block["offset"]) is int and block["offset"] == count)
            _require(type(block["samples"]) is list and 1 <= len(block["samples"]) <= 250)
            count += len(block["samples"])
            _require(count <= 2048)
            for code in block["samples"]:
                _uint(code, 4095)
        _require(result["missing_ranges"] == ([[count, 2048]] if count < 2048 else []))


def _check_capture(capture, body):
    _require(type(capture) is Capture and type(capture.samples) is tuple and len(capture.samples) == 2048)
    capture.__post_init__()
    accepted = body["accepted"]
    _require(all(accepted[n] is not None for n in ("hello", "caps", "config", "begin", "end")))
    begin, end = accepted["begin"]["decoded"], accepted["end"]["decoded"]
    config, caps = accepted["config"]["decoded"], accepted["caps"]["decoded"]
    from uuid import UUID
    _require(int(UUID(capture.session_id)) == accepted["begin"]["session"])
    _require(capture.origin == body["identity"]["origin"])
    _require(all(accepted[name]["session"] == accepted["begin"]["session"]
                 for name in ("hello", "caps", "config", "end")))
    _require(capture.firmware_version == str(accepted["hello"]["decoded"]["firmware_build"]))
    _require(capture.protocol_version == "draft-0.1@" + SESSION_PIN)
    _require((config["profile"], config["mode"], config["source"], config["pretrigger_samples"])
             == (1, 0, 0, 0) and 100 <= config["auto_timeout_ms"] <= 60000)
    _require((caps["profile"], caps["window_samples"], caps["timer_clock_hz"], caps["timer_psc"],
              caps["timer_arr"], caps["adc_clock_hz"], caps["adc_sample_cycles_x2"])
             == (1, 2048, 72000000, 71, 19, 12000000, 143))
    _require((begin["channel"], begin["external_edge_id"]) == (0, 0))
    _require(begin["trigger_kind"] in (0, 2) and begin["quality_flags"] & ~7 == 0)
    _require(bool(begin["quality_flags"] & 1) == (begin["trigger_kind"] == 2))
    _require(begin["trigger_index"] == (65535 if begin["trigger_kind"] == 2 else 0))
    flags = ["draft_protocol", "nominal_timing_unverified"]
    if 0 in capture.samples or 4095 in capture.samples:
        flags.append("rail_observed")
    _require(bool(begin["quality_flags"] & 2) == ("rail_observed" in flags))
    if begin["quality_flags"] & 4:
        flags.append("wire_quality_bit2")
    _require(capture.quality_flags == tuple(flags))
    _require((begin["generation"], begin["window_id"], begin["sample_count"]) ==
             (end["generation"], end["window_id"], end["sample_count"]) ==
             (body["attempt"]["arm_request_id"], capture.sequence, 2048))
    _require(begin["config_id"] == config["config_id"])
    for name in ("profile", "source", "edge", "level_code", "pretrigger_samples"):
        _require(begin[name] == config[name])
    for name in ("timer_clock_hz", "timer_psc", "timer_arr"):
        _require(begin[name] == caps[name])
    _require(capture.nominal_fs_hz == begin["timer_clock_hz"] / ((begin["timer_psc"] + 1) * (begin["timer_arr"] + 1)))
    _require((capture.trigger_kind, capture.trigger_index) ==
             (("auto_timeout", None) if begin["trigger_kind"] == 2 else ("signal", begin["trigger_index"])))
    _require(capture.trigger_edge == ("rising" if begin["edge"] == 0 else "falling"))
    _require(capture.trigger_level_code == begin["level_code"] and capture.pretrigger_samples == begin["pretrigger_samples"])
    _require(end["samples_crc32"] == zlib.crc32(struct.pack("<2048H", *capture.samples)))
    if body["stop_matched"]:
        stop = accepted["stop"]
        _require(stop["session"] == accepted["begin"]["session"] and
                 (stop["decoded"]["generation"], stop["decoded"]["last_window_id"], stop["decoded"]["status"])
                 == (begin["generation"], begin["window_id"], 0))
    if body["attempt"]["outcome"] == "completed":
        _require(body["stop_matched"] and body["incomplete"] is None and body["journal"]["failure"] is None
                 and body["journal"]["identical_frame_replays"] == 0
                 and body["journal"]["repeated_data_blocks"] == 0)


def verify_run(directory):
    """Вернуть индекс + artifact_integrity/prior_writer_outcome, не 21 Capture."""
    _require(type(directory) is str or isinstance(directory, Path))
    _require(len(str(directory)) <= 4096)
    directory = Path(directory)
    index = _decode(_read(directory / "index.json", MAX_INDEX_BYTES), "mtlv-index", MAX_INDEX_BYTES)
    _check_index(index)
    windows = set()
    setup = None
    observer = StreamDecoder()
    previous_frame = None
    for attempt in index["attempts"]:
        if attempt["sidecar_file"] is None:
            continue
        raw = _read(directory / attempt["sidecar_file"], MAX_SIDECAR_BYTES)
        _require(_hash(raw) == attempt["sidecar_sha256"])
        body = _decode(raw, "mtlv-sidecar", MAX_SIDECAR_BYTES)
        _check_sidecar(body)
        # Проверяем связь accepted metadata с наблюдавшимся RX, не объявляя
        # другие RX-кадры допущенными. Parser и последний frame ограничены.
        wanted = {name for name, value in body["accepted"].items() if value is not None
                  and (setup is None or name not in ("hello", "caps", "config"))}
        replays = repeated_blocks = 0
        blocks = {}
        observer_full = False
        for record in body["journal"]["records"]:
            if record["kind"] != "rx" or observer_full:
                continue
            for frame in observer.feed(bytes.fromhex(record["data_hex"]), record["at_ms"]).frames:
                try:
                    identical, repeated = _observe_replay(frame, previous_frame, blocks)
                except CollectorError:
                    _require(body["journal"]["failure"] == "JOURNAL_OVERFLOW")
                    observer_full = True
                    break
                replays += identical
                repeated_blocks += repeated
                previous_frame = frame
                for name in tuple(wanted):
                    stored = body["accepted"][name]
                    if (frame.message_type, frame.session, frame.sequence, frame.request_id, frame.payload.hex()) == (
                            stored["message_type"], stored["session"], stored["sequence"], stored["request_id"], stored["payload_hex"]):
                        wanted.remove(name)
        _require(not wanted, "Принятые метаданные не связаны с RX-журналом")
        _require(replays == body["journal"]["identical_frame_replays"] and
                 repeated_blocks == body["journal"]["repeated_data_blocks"])
        _require(body["run_id"] == index["run_id"] and body["identity"] == index["identity"])
        current_setup = {name: body["accepted"][name] for name in ("hello", "caps", "config")}
        if setup is None:
            setup = current_setup
        else:
            _require(current_setup == setup)
        if body["accepted"]["config"] is not None:
            config = body["accepted"]["config"]["decoded"]
            _require(all(config[name] == value for name, value in index["requested_config"].items()))
        # Sidecar не содержит собственную контрольную сумму: связь однонаправленная.
        expected = dict(attempt, sidecar_file=None, sidecar_sha256=None)
        _require(body["attempt"] == expected)
        if attempt["mtcap_file"] is not None:
            raw = _read(directory / attempt["mtcap_file"], recording.MAX_FILE)
            _require(_hash(raw) == attempt["mtcap_sha256"] == body["capture_sha256"])
            capture = recording.decode(raw)
            _check_capture(capture, body)
            key = (capture.session_id, capture.sequence)
            _require(key not in windows, "Номер окна повторяется")
            windows.add(key)
        else:
            _require(body["capture_sha256"] is None and attempt["outcome"] != "completed")
    # Чтение устанавливает целостность артефактов сейчас, не подтверждает
    # исход прошлого writer-процесса или отправку локального consumer ACK.
    return dict(index, artifact_integrity=("RECOVERED_VERIFIED" if index["state"] == "sealed_unverified"
                                          else "PARTIAL_VERIFIED"), prior_writer_outcome="UNKNOWN")


class LVCollector:
    """Один владелец. start/pump/arm_next/persist_attempt/cancel без auto-ARM."""
    def __init__(self, directory, transport, *, clock_ms, identity, config, nonce, utc_now=None):
        _require(type(identity) is RunIdentity)
        identity.__post_init__()
        _config(config)
        _uint(nonce)
        _require(nonce > 0)
        _require(type(directory) in (str, Path) or isinstance(directory, Path))
        _require(len(str(directory)) <= 4096)
        self.directory = Path(directory)
        self.observed = ObservedTransport(transport, clock_ms)
        self.runtime = AcquisitionRuntime(self.observed, origin=identity.origin, clock_ms=clock_ms, utc_now=utc_now)
        self.config, self.nonce = config, nonce
        self.state, self.reason = State.NEW, "TBD"
        self.index = dict(run_id=str(uuid4()), state="incomplete", reason="TBD",
                          identity=asdict(identity), implementation=dict(PROVENANCE),
                          requested_config=asdict(config), requested_fs_hz=50000,
                          attempts=[_attempt(i) for i in range(ATTEMPTS)])
        self._index_hash = None
        self._index_candidate_hash = None
        self.storage_status = "TBD"
        self._current = None
        self._accepted = dict.fromkeys(("hello", "caps", "config", "begin", "end", "abort", "stop"))
        self._stop_request = 0
        self._stop_matched = False
        self._cancelled = False
        self._arm_length = 0

    def _write_index(self):
        path = self.directory / "index.json"
        if self._index_hash is not None:
            _require(_hash(_read(path, MAX_INDEX_BYTES)) == self._index_hash,
                     "Индекс изменён извне; продолжение запрещено")
        _check_index(self.index)
        raw = _envelope("mtlv-index", self.index, MAX_INDEX_BYTES)
        self._index_candidate_hash = _hash(raw)
        recording.atomic_write(path, raw, overwrite=self._index_hash is not None)
        _require(_read(path, MAX_INDEX_BYTES) == raw, "Повторное чтение индекса не совпало")
        self._index_hash = _hash(raw)

    def start(self):
        _require(self.state is State.NEW, "Опыт уже начат")
        try:
            self.directory.mkdir()  # Не принимает даже пустую старую папку.
            self._write_index()
            self.runtime.start(self.nonce)
            self.state = State.NEGOTIATING
        except Exception as exc:
            self.state, self.reason = State.FAILED, "STORAGE_OR_START"
            raise CollectorError("Не удалось начать опыт; прежние файлы не заменены") from exc

    def arm_next(self):
        _require(self.state is State.READY, "Новое ARM запрещено: предыдущий результат не сохранён")
        number = next((a["number"] for a in self.index["attempts"] if a["outcome"] == "planned"), None)
        _require(number is not None)
        attempt = self.index["attempts"][number]
        request = self.runtime.snapshot().next_request_id
        attempt.update(intent_committed=True, arm_request_id=request, arm_queued=None, outcome="pending")
        try:
            self._write_index()  # Сначала intent; после crash факт выдачи пока неизвестен.
        except Exception as exc:
            self.state, self.reason = State.FAILED, "STORAGE"
            raise CollectorError("Намерение ARM не сохранено; команда не выдана") from exc
        self._current = number
        self._arm_length = len(encode_frame(Frame(4, self.nonce, self.runtime.snapshot().next_tx_sequence,
                                                 request, struct.pack("<I", self.runtime.snapshot().config_id))))
        self._accepted.update(begin=None, end=None, abort=None, stop=None)
        self._stop_request, self._stop_matched = 0, False
        self.reason = "TBD"
        try:
            events = self.runtime.arm()
            attempt["arm_queued"] = True
            self.state = State.ACTIVE
            self._events(events)
        except Exception as exc:
            self.reason, self.state = "ARM_REJECTED", State.SAVEABLE
            raise CollectorError("ARM не поставлен в очередь; сохраните отказ") from exc

    def _events(self, events):
        names = {EventKind.HELLO_ACCEPTED: "hello", EventKind.CAPABILITIES: "caps",
                 EventKind.CONFIGURED: "config", EventKind.WINDOW_BEGIN: "begin",
                 EventKind.WINDOW_END_ADMITTED: "end", EventKind.WINDOW_ABORT: "abort",
                 EventKind.STOPPED: "stop"}
        for event in events:
            if event.kind in names and event.frame is not None:
                self._accepted[names[event.kind]] = _frame(event.frame)
            if event.kind is EventKind.CAPABILITIES and self.state is State.NEGOTIATING:
                self.runtime.configure(self.config)
            if event.kind is EventKind.CONFIGURED:
                self.state = State.READY
            if event.kind is EventKind.ARMED and self._current is not None:
                self.index["attempts"][self._current]["arm_ack"] = True
            if event.kind is EventKind.STOPPED:
                if event.request_id == self._stop_request and self._stop_request:
                    self._stop_matched = True
                    self.state = State.SAVEABLE
            if event.kind in (EventKind.WINDOW_ABORT, EventKind.COMMAND_ERROR, EventKind.RECOVERY):
                self.reason = "ABORT" if event.kind is EventKind.WINDOW_ABORT else (
                    "COMMAND_ERROR" if event.kind is EventKind.COMMAND_ERROR else str(event.reason.value))
                if event.kind is EventKind.RECOVERY:
                    self.state = State.SAVEABLE

    def _stop(self):
        events = self.runtime.stop()
        self._stop_request = self.runtime.snapshot().pending_stop_id
        self.state = State.STOPPING
        self._events(events)

    def pump(self):
        _require(self.state in (State.NEGOTIATING, State.ACTIVE, State.STOPPING),
                 "Сейчас нужен явный ARM, сохранение или завершение")
        attempt = self.index["attempts"][self._current] if self._current is not None else None
        self.observed.command = ("arm" if attempt and attempt["tx_known_bytes"] < self._arm_length and not attempt["tx_unknown"]
                                 else "stop" if self._stop_request else "other")
        before = len(self.observed.records)
        try:
            events = self.runtime.pump()
            self._events(events)
        except Exception as exc:
            self.reason = "RUNTIME_ERROR"
            self.state = State.SAVEABLE
            try:
                self.runtime.disconnect()
            except Exception:
                pass
            raise CollectorError("Сеанс прерван; сохраните доступные данные") from exc
        finally:
            if attempt is not None:
                for record in self.observed.records[before:]:
                    if record["kind"] == "tx" and record["command"] == "arm":
                        attempt["tx_known_bytes"] += record["accepted"] or 0
                        attempt["tx_unknown"] |= record["outcome"] == "unknown_write"
        if attempt is not None and self.runtime.snapshot().generation == attempt["arm_request_id"]:
            # Session подавляет ARMED event при уже ожидающем Stop, но его
            # generation меняется только после принятого matching ARM_ACK.
            attempt["arm_ack"] = True
        if self.observed.failure:
            self.reason, self.state = self.observed.failure, State.SAVEABLE
        if self.runtime.snapshot().phase is Phase.RECOVERY:
            self.state = State.SAVEABLE
        elif ((self.state is State.ACTIVE and self.runtime.pending_capture is not None)
              or (self.state in (State.ACTIVE, State.NEGOTIATING) and self.reason != "TBD")):
            self._stop()  # Без диска между verified pending_capture и Stop.
        return events

    def cancel(self):
        _require(self.state not in (State.NEW, State.COMPLETE, State.FAILED))
        self._cancelled, self.reason = True, "CANCELLED"
        if self.state is State.ACTIVE:
            self._stop()
        elif self.state in (State.READY, State.NEGOTIATING):
            if self.state is State.READY:
                self._current = next(a["number"] for a in self.index["attempts"] if a["outcome"] == "planned")
                self._accepted.update(begin=None, end=None, abort=None, stop=None)
                self._stop_request, self._stop_matched = 0, False
            self.runtime.disconnect()
            self.state = State.SAVEABLE

    def persist_attempt(self):
        _require(self.state is State.SAVEABLE, "Сначала дождитесь подходящего STOP_ACK или завершения отказом")
        snapshot = self.runtime.snapshot()
        _require(snapshot.phase is Phase.RECOVERY or (self._stop_matched and snapshot.phase is Phase.READY
                 and not snapshot.stop_barrier and not snapshot.window_barrier),
                 "Stop не подтверждён; сохранение в активном сеансе запрещено")
        capture = self.runtime.pending_capture
        if self.reason == "TBD":
            if self.observed.repeated_data_blocks:
                self.reason = "REPEATED_DATA_BLOCK"
            elif self.observed.identical_frame_replays:
                self.reason = "IDENTICAL_FRAME_REPLAY"
        good = capture is not None and self.reason == "TBD" and self._stop_matched and not self._cancelled
        number = self._current if self._current is not None else 0
        attempt = self.index["attempts"][number]
        attempt["outcome"] = "completed" if good else ("cancelled" if self._cancelled else "failed")
        name = f"attempt-{number:02d}"
        rollback = None
        try:
            # Проверяем stale index до создания каких-либо новых файлов.
            rollback = _read(self.directory / "index.json", MAX_INDEX_BYTES)
            _require(_hash(rollback) == self._index_hash,
                     "Индекс изменён извне; данные остаются в памяти")
            if capture is not None:
                _require(type(capture) is Capture and type(capture.samples) is tuple and len(capture.samples) == 2048)
                capture.__post_init__()
            capture_raw = recording.encode(capture) if capture is not None else None
            if capture_raw is not None:
                _require(len(capture.samples) == 2048)
                attempt.update(mtcap_file=name + ".mtcap", mtcap_sha256=_hash(capture_raw))
            body = dict(run_id=self.index["run_id"], attempt=dict(attempt), reason=self.reason,
                        identity=self.index["identity"], implementation=dict(PROVENANCE), requested_fs_hz=50000,
                        accepted=dict(self._accepted),
                        journal=dict(records=self.observed.records, raw_byte_count=self.observed.byte_count,
                                     failure=self.observed.failure, observation_only=True,
                                     identical_frame_replays=self.observed.identical_frame_replays,
                                     repeated_data_blocks=self.observed.repeated_data_blocks),
                        incomplete=_fragments(self.runtime.incomplete_result),
                        capture_sha256=attempt["mtcap_sha256"], stop_request_id=self._stop_request,
                        stop_matched=self._stop_matched)
            _check_sidecar(body)
            if capture is not None:
                _check_capture(capture, body)
            raw = _envelope("mtlv-sidecar", body, MAX_SIDECAR_BYTES)
            if capture_raw is not None:
                recording.atomic_write(self.directory / attempt["mtcap_file"], capture_raw)
                reopened = recording.decode(_read(self.directory / attempt["mtcap_file"], recording.MAX_FILE))
                _require(reopened == capture and recording.encode(reopened) == capture_raw,
                         "Исходная и открытая запись различаются")
            recording.atomic_write(self.directory / (name + ".json"), raw)
            reread = _read(self.directory / (name + ".json"), MAX_SIDECAR_BYTES)
            _require(reread == raw and _decode(reread, "mtlv-sidecar", MAX_SIDECAR_BYTES) == body,
                     "Исходные метаданные после чтения различаются")
            attempt.update(sidecar_file=name + ".json", sidecar_sha256=_hash(raw))
            if not good:
                self.index.update(state="cancelled" if self._cancelled else "failed", reason=self.reason)
                for remaining in self.index["attempts"]:
                    if remaining["outcome"] == "planned":
                        remaining["outcome"] = "cancelled"
            if good and number == ATTEMPTS - 1:
                self.index.update(state="sealed_unverified", reason="21/21")
            self._write_index()
            verified = verify_run(self.directory)
            _require(all(verified[name] == value for name, value in self.index.items()))
            self.storage_status = "VERIFIED"
            if good:
                # Только тот же объект, только READY после конкретного Stop.
                self.runtime.acknowledge(capture)
                self.observed.clear()
                self._current = None
                self.state = State.COMPLETE if number == ATTEMPTS - 1 else State.READY
            else:
                self.state = State.FAILED  # Capture/fragments остаются доступны.
            return dict(attempt)
        except Exception as exc:
            self.state, self.reason = State.FAILED, "STORAGE_OR_READBACK"
            self.storage_status = "UNKNOWN"
            # Если последнее изменение принадлежит нам, возвращаем проверенный
            # incomplete intent. Чужой stale index никогда не перезаписываем.
            if rollback is not None:
                try:
                    path = self.directory / "index.json"
                    current = _read(path, MAX_INDEX_BYTES)
                    if _hash(current) in (_hash(rollback), self._index_hash, self._index_candidate_hash):
                        if current != rollback:
                            recording.atomic_write(path, rollback, overwrite=True)
                        _require(_read(path, MAX_INDEX_BYTES) == rollback)
                        self._index_hash = _hash(rollback)
                        self.index = _decode(rollback, "mtlv-index", MAX_INDEX_BYTES)
                        self.storage_status = "INCOMPLETE"
                except Exception:
                    pass
            raise CollectorError("Сохранение не подтверждено: ACK и следующий ARM запрещены; данные остаются в памяти") from exc


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Проверка сохранённого опыта LV-001 без подключения прибора")
    parser.add_argument("directory", help="Папка сохранённого опыта")
    args = parser.parse_args(argv)
    try:
        result = verify_run(args.directory)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, "Проверка не пройдена: файл отсутствует, повреждён или не поддерживается\n")
    completed = sum(a["outcome"] == "completed" for a in result["attempts"][1:])
    print(f"Состояние индекса: {result['state']}; основная серия {completed}/20; прогрев отдельно. "
          f"Целостность артефактов: {result['artifact_integrity']}. "
          "Исход прежнего процесса и consumer ACK: UNKNOWN. Физическая приёмка не подтверждена.")
    return 0 if result["artifact_integrity"] == "RECOVERED_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
