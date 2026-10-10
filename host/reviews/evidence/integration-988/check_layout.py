"""Read-only comparison of pinned viewer variants at the declared minimum size."""
from pathlib import Path
import json
import subprocess
import sys
import types

from PySide6 import QtWidgets, QtTest

ROOT = Path("/tmp/motortester-host-app")
OUT = Path(__file__).resolve().parent
SOURCES = {"core2376": "2376d325d573b1aa3da6feaf8fddf05d2e1cb601",
           "png2cf": "2cf1fb83cf7d0dae98656cd6a63a5eea47c7e5c4",
           "layout3b315": "3b315ac4e1184c4c7e9216ce5594cff0f9beff33",
           "candidate988": "988f2cc094fa82c622f0b20276114e3159ae73d9"}
app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
rows = []
for label, sha in SOURCES.items():
    source = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{sha}:host/motortester/gui.py"])
    module = types.ModuleType(f"motortester.review_{label}")
    module.__file__ = str(ROOT / "host/motortester/gui.py")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    window = module.MainWindow()
    window.show()
    window.generate_demo()
    window.cursor_toggle.setChecked(True)
    original_style = window.styleSheet()
    for pixels in (13, 18, 22):
        window.setStyleSheet(original_style.replace("font-size: 13px", f"font-size: {pixels}px"))
        window.resize(1000, 710)
        QtTest.QTest.qWait(100)
        sidebar = window.findChild(QtWidgets.QFrame, "sidebar")
        labels = []
        for widget in sidebar.findChildren(QtWidgets.QLabel):
            labels.append({"text": widget.text(), "height": widget.height(),
                           "width": widget.width(), "size_hint_height": widget.sizeHint().height(),
                           "height_for_width": widget.heightForWidth(widget.width()),
                           "word_wrap": widget.wordWrap()})
        row = {"source": label, "sha": sha, "font_px": pixels,
               "window_size": [window.width(), window.height()], "sidebar_labels": labels,
               "sidebar_buttons": [{"text": widget.text(), "height": widget.height(),
                                    "size_hint_height": widget.sizeHint().height()}
                                   for widget in sidebar.findChildren(QtWidgets.QPushButton)],
               "plot_bottom": window.plot.geometry().bottom(),
               "trigger_top": window.trigger_label.geometry().top()}
        if hasattr(window, "plot_scroll"):
            row["plot_scroll_max"] = window.plot_scroll.verticalScrollBar().maximum()
        if pixels == 13 or label == "candidate988":
            window.grab().save(str(OUT / f"layout-{label}-{pixels}px.png"))
        rows.append(row)
    window.close()
    window.deleteLater()
    app.processEvents()
(OUT / "layout-comparison.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
for row in rows:
    clipped = [item for item in row["sidebar_labels"]
               if item["word_wrap"] and item["height"] < item["height_for_width"]]
    print(row["source"], row["font_px"], "clipped_wrapped_labels=", len(clipped),
          [(item["text"][:30], item["height"], item["height_for_width"]) for item in clipped])
