"""Host-normalized indexed samples, not a wire protocol or physical evidence."""
from dataclasses import dataclass

from .stream_provenance import ProvenanceError, profile_for_identity

U64_MAX = (1 << 64) - 1
MAX_BLOCK_SAMPLES = 8192
MAX_READ_SAMPLES = 65536


class StreamError(ValueError):
    """Invalid normalized stream data or bounded recording."""


def require(condition, message):
    if not condition:
        raise StreamError(message)


def uint(value, maximum=U64_MAX, *, minimum=0):
    require(type(value) is int and minimum <= value <= maximum,
            "Целое значение вне допустимого диапазона")


def text(value, maximum):
    require(type(value) is str and 1 <= len(value) <= maximum,
            "Недопустимая длина текста")
    require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                    or ord(c) in (0xFFFE, 0xFFFF) for c in value),
            "Недопустимый символ в тексте")


@dataclass(frozen=True, slots=True)
class StreamIdentity:
    session_id: str
    generation: int
    config_id: int
    nominal_fs_hz: int
    origin: str
    protocol_revision: str | None = None
    firmware_version: str | None = None
    application_version: str | None = None

    def __post_init__(self):
        text(self.session_id, 128)
        uint(self.generation)
        uint(self.config_id)
        uint(self.nominal_fs_hz, 1_000_000, minimum=1)
        require(type(self.origin) is str and self.origin in ("synthetic", "device"),
                "Неизвестный заявленный источник")
        for value in (self.protocol_revision, self.firmware_version, self.application_version):
            if value is not None:
                text(value, 64)
        try:
            profile_for_identity(self)
        except ProvenanceError as exc:
            raise StreamError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class StreamStart:
    identity: StreamIdentity
    first_sample_index: int
    recording_id: str
    boundary_evidence: str = "unspecified"

    def __post_init__(self):
        require(type(self.identity) is StreamIdentity, "Неверный тип identity")
        self.identity.__post_init__()
        uint(self.first_sample_index)
        text(self.recording_id, 128)
        text(self.boundary_evidence, 128)


@dataclass(frozen=True, slots=True)
class StreamBlock:
    identity: StreamIdentity
    first_sample_index: int
    raw_u16le: bytes

    def __post_init__(self):
        require(type(self.identity) is StreamIdentity, "Неверный тип identity")
        self.identity.__post_init__()
        uint(self.first_sample_index)
        require(type(self.raw_u16le) is bytes and
                2 <= len(self.raw_u16le) <= 2 * MAX_BLOCK_SAMPLES and
                len(self.raw_u16le) % 2 == 0, "Ожидаются ограниченные uint16-le bytes")
        uint(self.end_sample_index)

    @property
    def sample_count(self):
        return len(self.raw_u16le) // 2

    @property
    def end_sample_index(self):
        return self.first_sample_index + self.sample_count


FAILURE_CODES = frozenset(("source_overrun", "ambiguous_dma", "transport_error",
                           "source_error", "host_io", "input_identity", "input_order",
                           "invalid_input", "invalid_terminal", "capacity_raw",
                           "capacity_blocks", "unknown"))


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    code: str
    detail: str
    operation: str | None = None
    request_id: int | None = None
    source_code: int | None = None

    def __post_init__(self):
        require(type(self.code) is str and self.code in FAILURE_CODES,
                "Неизвестная категория отказа")
        text(self.detail, 256)
        if self.operation is not None:
            text(self.operation, 64)
        for value in (self.request_id, self.source_code):
            if value is not None:
                uint(value)


@dataclass(frozen=True, slots=True)
class StreamTerminal:
    identity: StreamIdentity
    end_sample_index: int
    reason: str
    terminal_observed: bool
    device_generated_end_sample_index: int | None = None
    device_transmitted_end_sample_index: int | None = None
    loss_samples: int | None = None
    boundary_evidence: str = "unspecified"
    failure: FailureEvidence | None = None
    stop_requested: bool = False
    stop_request_id: int | None = None

    def __post_init__(self):
        require(type(self.identity) is StreamIdentity, "Неверный тип identity")
        self.identity.__post_init__()
        uint(self.end_sample_index)
        require(type(self.reason) is str and self.reason in
                ("stopped", "source_failure", "host_failure", "capacity"),
                "Неизвестная причина завершения")
        require(type(self.terminal_observed) is bool, "Неверный terminal_observed")
        require(type(self.stop_requested) is bool, "Неверный stop_requested")
        if self.stop_request_id is not None:
            uint(self.stop_request_id)
            require(self.stop_requested, "Stop request_id без запроса Stop")
        for value in (self.device_generated_end_sample_index,
                      self.device_transmitted_end_sample_index):
            if value is not None:
                uint(value, minimum=self.end_sample_index)
        if (self.device_generated_end_sample_index is not None and
                self.device_transmitted_end_sample_index is not None):
            require(self.device_generated_end_sample_index >=
                    self.device_transmitted_end_sample_index,
                    "Переданный предел превышает сгенерированный")
        if self.loss_samples is not None:
            uint(self.loss_samples)
        text(self.boundary_evidence, 128)
        if self.failure is not None:
            require(type(self.failure) is FailureEvidence, "Неверный тип failure")
            self.failure.__post_init__()
        require((self.reason == "stopped") == (self.failure is None),
                "Причина завершения и первый отказ не согласованы")

    @property
    def clean_stop_reported(self):
        """Concordant caller claims, never proof of physical Stop or durable I/O."""
        return (self.reason == "stopped" and self.stop_requested and self.terminal_observed and
                self.loss_samples == 0 and
                self.device_generated_end_sample_index == self.end_sample_index and
                self.device_transmitted_end_sample_index == self.end_sample_index)


@dataclass(frozen=True, slots=True)
class StreamPreparation:
    identity: StreamIdentity
    recording_id: str
    start_policy: str

    def __post_init__(self):
        require(type(self.identity) is StreamIdentity, "Неверный тип identity")
        self.identity.__post_init__()
        text(self.recording_id, 128)
        require(type(self.start_policy) is str and self.start_policy in
                ("next_block", "source_start"), "Неизвестное правило выбора начала")


@dataclass(frozen=True, slots=True)
class PreparedReadyToken:
    token_id: str
    preparation: StreamPreparation

    def __post_init__(self):
        require(type(self.token_id) is str and len(self.token_id) == 32 and
                all(c in "0123456789abcdef" for c in self.token_id),
                "Неверный идентификатор readiness")
        require(type(self.preparation) is StreamPreparation, "Неверная preparation")
        self.preparation.__post_init__()


@dataclass(frozen=True, slots=True)
class UnboundOutcome:
    reason: str
    failure: FailureEvidence | None = None
    terminal_observed: bool = False
    device_generated_end_sample_index: int | None = None
    device_transmitted_end_sample_index: int | None = None
    loss_samples: int | None = None
    boundary_evidence: str = "unspecified"
    stop_requested: bool = False
    stop_request_id: int | None = None

    def __post_init__(self):
        require(type(self.reason) is str and self.reason in
                ("ended_unbound", "cancelled_unbound", "failed_unbound"),
                "Неизвестный исход без выбранной границы")
        require(type(self.terminal_observed) is bool and type(self.stop_requested) is bool,
                "Неверный тип source/Stop evidence")
        for value in (self.device_generated_end_sample_index,
                      self.device_transmitted_end_sample_index, self.loss_samples,
                      self.stop_request_id):
            if value is not None:
                uint(value)
        if self.stop_request_id is not None:
            require(self.stop_requested, "Stop request_id без запроса Stop")
        if (self.device_generated_end_sample_index is not None and
                self.device_transmitted_end_sample_index is not None):
            require(self.device_generated_end_sample_index >= self.device_transmitted_end_sample_index,
                    "Переданный предел превышает сгенерированный")
        text(self.boundary_evidence, 128)
        if self.failure is not None:
            require(type(self.failure) is FailureEvidence, "Неверный тип failure")
            self.failure.__post_init__()
        require((self.reason == "failed_unbound") == (self.failure is not None),
                "Исход и первый отказ не согласованы")
