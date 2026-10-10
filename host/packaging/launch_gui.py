"""PyInstaller entry point; source runs use python -m motortester.gui."""
from motortester.gui import main


if __name__ == "__main__":
    raise SystemExit(main())
