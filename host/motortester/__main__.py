"""Run: python -m motortester demo OUTPUT_DIRECTORY."""
import argparse
from pathlib import Path
import sys
from .emulator import demo_message, from_message
from .model import CaptureError
from .recording import save, load, export_csv
from .plotting import save_svg


def main():
    parser = argparse.ArgumentParser(description="Мотортестер: программная демонстрация без прибора")
    parser.add_argument("command", choices=("demo",))
    parser.add_argument("output", type=Path, help="Новая папка результатов; существующие файлы не перезаписываются")
    args = parser.parse_args()
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        original = from_message(demo_message())
        save(args.output / "demo.mtcap", original)
        reopened = load(args.output / "demo.mtcap")
        if original != reopened:
            raise CaptureError("Повторно открытая запись отличается от исходной")
        export_csv(args.output / "demo.csv", reopened)
        save_svg(args.output / "demo.svg", reopened)
        print(f"ДЕМОНСТРАЦИЯ: {len(reopened.samples)} отсчётов; побитный round-trip проверен.")
        print(f"Файл, CSV и график: {args.output.resolve()}")
        print("USB, Windows 10 и реальный прибор не проверены.")
        return 0
    except (OSError, CaptureError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
