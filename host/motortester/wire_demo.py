"""Synthetic draft byte-stream integration; never opens or controls a device."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
from uuid import UUID
import zlib

from .emulator import demo_message
from .model import Capture, CaptureError
from .plotting import save_svg
from .recording import atomic_write, export_csv, load, save
from .wire import StreamDecoder, Frame, encode_frame
from .window import ExpectedContext, WindowReceiver, PROTOCOL_SOURCE_SHA

CONTEXT = ExpectedContext(0x53494D0000000001, 2, 1, 1, 0, 0, 2048, 512)
FRAMES_PER_WINDOW = 11  # BEGIN + nine DATA blocks + END.
MAX_TRANSCRIPT_BYTES = 16384


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise CaptureError(f"Недопустимое значение: {label}")


def synthetic_window(window_id=1, first_sequence=1):
    """Return deterministic raw codes and framed bytes for the fixed demo context."""
    _integer(window_id, 1, 20, "номер синтетического окна")
    _integer(first_sequence, 1, 0xFFFFFFFE - FRAMES_PER_WINDOW + 1, "sequence")
    codes = list(demo_message()["samples"])
    codes[:4] = (0, window_id, 256, 4095)  # Explicit byte-order/control prefix.
    samples = tuple(codes)
    raw = struct.pack("<2048H", *samples)
    c = CONTEXT
    payloads = [(144, struct.pack("<IIIHHHHBBBBHHIHHI", c.generation,
        window_id, c.config_id, 1, 2048, 512, 512, 0, 0, 0, 0, 2048, 2,
        72000000, 71, 19, 0))]
    for offset in range(0, 2048, 250):
        block = samples[offset:offset + 250]
        payloads.append((145, struct.pack("<IIHH", c.generation, window_id,
            offset, len(block)) + struct.pack(f"<{len(block)}H", *block)))
    payloads.append((146, struct.pack("<IIHHI", c.generation, window_id,
                                    2048, 0, zlib.crc32(raw))))
    data = b"".join(encode_frame(Frame(kind, c.session, first_sequence + i, 0, payload))
                    for i, (kind, payload) in enumerate(payloads))
    return samples, data


def capture_from_synthetic_stream(data, *, first_sequence=1, chunk_size=97,
                                  received_at_utc=None):
    """Strict, bounded replay harness, not a general USB session dispatcher.

    Every error rejects the entire transcript before any recording is written.
    Virtual time advances one millisecond per 64 chunks; this tests chunking,
    not transport timing. Timeout behavior is tested in the receiver separately.
    """
    if type(data) is not bytes or not 1 <= len(data) <= MAX_TRANSCRIPT_BYTES:
        raise CaptureError("Синтетический поток должен содержать 1…16384 байт")
    _integer(chunk_size, 1, 4096, "размер порции")
    _integer(first_sequence, 1, 0xFFFFFFFE - FRAMES_PER_WINDOW + 1, "sequence")
    decoder, receiver = StreamDecoder(), WindowReceiver(CONTEXT)
    expected_sequence = first_sequence
    terminal = None
    now = 0
    for number, offset in enumerate(range(0, len(data), chunk_size)):
        now = number // 64
        batch = decoder.feed(data[offset:offset + chunk_size], now)
        if batch.diagnostics:
            raise CaptureError("Повреждённый или несовместимый синтетический поток")
        for frame in batch.frames:
            if terminal is not None or frame.sequence != expected_sequence:
                raise CaptureError("Повтор, пропуск или лишний кадр синтетического окна")
            expected_sequence += 1
            result = receiver.feed(frame, now)
            if result is not None:
                if not result.semantic_checks_complete:
                    raise CaptureError(f"Синтетическое окно не подтверждено: {result.reason.value}")
                terminal = result
    if decoder.tick(now + 3000).diagnostics or terminal is None:
        raise CaptureError("Синтетический поток оборван или не содержит полного окна")
    if expected_sequence != first_sequence + FRAMES_PER_WINDOW:
        raise CaptureError("Неожиданное число кадров синтетического окна")
    begin = terminal.begin
    # This bridge deliberately handles only its normal/analog demo contract.
    if begin.trigger_kind != 0 or begin.quality_flags != 2:
        raise CaptureError("Окно не соответствует профилю синтетического примера")
    stamp = datetime.now(timezone.utc).isoformat() if received_at_utc is None else received_at_utc
    return Capture(samples=terminal.samples, session_id=str(UUID(int=CONTEXT.session)),
        sequence=begin.window_id, nominal_fs_hz=begin.timer_clock_hz /
        ((begin.timer_psc + 1) * (begin.timer_arr + 1)), received_at_utc=stamp,
        origin="simulator", trigger_kind="signal", trigger_index=begin.trigger_index,
        trigger_edge="rising", trigger_level_code=begin.level_code,
        pretrigger_samples=begin.pretrigger_samples, firmware_version=None,
        protocol_version=f"draft-0.1@{PROTOCOL_SOURCE_SHA}",
        sensor="Синтетический wire-поток; прибор и датчик не подключены",
        quality_flags=("rail_observed", "draft_protocol", "nominal_timing_unverified"))


def save_synthetic_stream(path, data, **options):
    """Decode completely before invoking non-overwriting atomic recording save."""
    capture = capture_from_synthetic_stream(data, **options)
    save(path, capture)
    reopened = load(path)
    if reopened != capture:
        raise CaptureError("Повторное чтение синтетической записи отличается")
    return reopened


def run_demo(output, *, windows=20, chunk_size=97):
    _integer(windows, 1, 20, "число окон")
    _integer(chunk_size, 1, 4096, "размер порции")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index in range(windows):
        sequence = index * FRAMES_PER_WINDOW + 1
        expected, wire = synthetic_window(index + 1, sequence)
        path = output / f"synthetic-{index + 1:02d}.mtcap"
        capture = save_synthetic_stream(path, wire, first_sequence=sequence,
                                       chunk_size=chunk_size)
        if capture.samples != expected:
            raise CaptureError("Исходные коды изменились в сквозном пути")
        export_csv(path.with_suffix(".csv"), capture)
        save_svg(path.with_suffix(".svg"), capture)
        records.append(dict(file=path.name, samples=len(capture.samples),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest = dict(origin="simulator", scope="draft_wire_window_to_recording",
        protocol_source_sha=PROTOCOL_SOURCE_SHA, chunk_size=chunk_size, records=records,
        limitations=["No USB or handshake", "No Windows verification", "No hardware measurements"])
    atomic_write(output / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Синтетический wire → файл → график без прибора")
    parser.add_argument("output", type=Path, help="Новая папка, без перезаписи")
    parser.add_argument("--windows", type=int, default=20)
    parser.add_argument("--chunk-size", type=int, default=97)
    args = parser.parse_args(argv)
    try:
        manifest = run_demo(args.output, windows=args.windows, chunk_size=args.chunk_size)
    except (CaptureError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"СИНТЕТИКА: {len(manifest['records'])} окон; исходные коды совпали после wire/файла.")
    print("USB, handshake, Windows и аппаратные измерения не проверялись.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
