"""PyInstaller entry point; ordinary source runs use python -m motortester.gui."""
import sys


def main() -> int:
    # Keep this branch before any Qt/application import. No other CLI is changed.
    if sys.argv[1:2] == ["--serial-import-smoke"]:
        if len(sys.argv) != 3:
            if sys.stderr is not None:
                print("Usage: --serial-import-smoke OUTPUT.json", file=sys.stderr)
            return 2
        from serial_import_smoke import run
        return run(sys.argv[2])
    from motortester.gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
