"""Internal synthetic source. This is NOT the STM32 USB wire protocol."""
from datetime import datetime, timezone
import math
from uuid import uuid4
from .model import Capture, CaptureError

DEMO_COUNT = 2048
DEMO_FS_HZ = 50_000


def demo_message(sequence: int = 0, session_id: str | None = None) -> dict:
    """A complete internal event; deterministic waveform, explicit provenance."""
    trigger = DEMO_COUNT // 4
    samples = tuple(round(2048 + 1000 * math.sin(2 * math.pi * (i - trigger) / 100))
                    for i in range(DEMO_COUNT))
    return dict(event_version=1, samples=samples, session_id=str(uuid4()) if session_id is None else session_id,
                sequence=sequence, nominal_fs_hz=DEMO_FS_HZ,
                received_at_utc=datetime.now(timezone.utc).isoformat(), origin="simulator",
                trigger_kind="signal", trigger_index=trigger, pretrigger_samples=trigger,
                trigger_edge="rising", trigger_level_code=2048, sensor="synthetic sine; no sensor")


def from_message(message: dict) -> Capture:
    """Replaceable host adapter; never silently coerce floats to ADC codes."""
    if type(message) is not dict or type(message.get("event_version")) is not int or message["event_version"] != 1:
        raise CaptureError("Неизвестная версия внутреннего события эмулятора")
    values = dict(message)
    values.pop("event_version")
    try:
        return Capture(**values)
    except TypeError as exc:
        raise CaptureError("Неполное или неизвестное внутреннее событие") from exc
