# Windows onedir candidate. Build only with build_windows.py on Windows x64.
from pathlib import Path
from runpy import run_path
import sys

if sys.platform != "win32":
    raise RuntimeError("Windows candidate must be built on Windows, not cross-compiled")

host = Path(SPECPATH).resolve().parent
a = Analysis(
    [str(host / "packaging" / "launch_gui.py")],
    pathex=[str(host)],
    binaries=[],
    datas=[(str(host / "docs"), "docs")],
    # pyserial selects its backend at runtime; do not rely on the host OS hook.
    # These modules import definitions only; the smoke never constructs a port.
    hiddenimports=["serial", "serial.serialutil", "serial.serialwin32", "serial.win32",
                   "motortester.transport"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy"],
    noarchive=False,
    optimize=0,
)
# Apply to the collected TOC before any build target or frozen feature smoke.
# Resolve from the spec itself, independent of the invoking working directory.
runtime_policy = run_path(str(host / "packaging" / "select_windows_runtime.py"))
a.binaries, removed_runtime = runtime_policy["select_windows_runtime"](a.binaries)
print(f"Windows 10 1809+ x64 runtime selection: {len(removed_runtime)} of "
      f"{len(runtime_policy['EXCLUDED_DESTINATIONS'])} approved destinations removed")
for destination in removed_runtime:
    print(f"  omitted: {destination}")
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
