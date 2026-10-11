# Windows onedir candidate. Build only with build_windows.py on Windows x64.
from pathlib import Path
import sys

if sys.platform != "win32":
    raise RuntimeError("Windows candidate must be built on Windows, not cross-compiled")

host = Path(SPECPATH).resolve().parent
a = Analysis(
    [str(host / "packaging" / "launch_gui.py")],
    pathex=[str(host)],
    binaries=[],
    datas=[(str(host / "docs"), "docs")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Motortester",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Motortester",
)
