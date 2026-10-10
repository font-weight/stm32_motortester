"""Independent geometry/visibility regression: old and successor source bytes."""
from pathlib import Path
import json
import subprocess
import sys
import types

from PySide6 import QtWidgets, QtTest

ROOT = Path("/tmp/motortester-host-app-ui")
OUT = Path(__file__).resolve().parent
SOURCES = {"old988": "988f2cc094fa82c622f0b20276114e3159ae73d9",
           "fixedbd582": "bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed"}


def require(value, message):
    if not value:
        raise RuntimeError(message)


app = QtWidgets.QApplication([])
app.setQuitOnLastWindowClosed(False)
rows = []
for name, sha in SOURCES.items():
    source = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{sha}:host/motortester/gui.py"])
    module = types.ModuleType(f"motortester.sidebar_review_{name}")
    module.__file__ = str(ROOT / "host/motortester/gui.py")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    window = module.MainWindow()
    window.show()
    window.generate_demo()
    window.cursor_toggle.setChecked(True)
    style = window.styleSheet()
    for pixels in (13, 18, 22):
        window.setStyleSheet(style.replace("font-size: 13px", f"font-size: {pixels}px"))
        window.resize(1000, 710)
        QtTest.QTest.qWait(100)
        sidebar = window.findChild(QtWidgets.QFrame, "sidebar")
        labels = [{"text": w.text(), "height": w.height(), "width": w.width(),
                   "required_height": w.heightForWidth(w.width())}
                  for w in sidebar.findChildren(QtWidgets.QLabel)]
        buttons = [{"text": w.text(), "size": [w.width(), w.height()],
                    "minimum": [w.minimumSizeHint().width(), w.minimumSizeHint().height()]}
                   for w in sidebar.findChildren(QtWidgets.QPushButton)]
        clipped = [w for w in labels if w["height"] < w["required_height"]]
        undersized = [w for w in buttons if any(a < b for a, b in zip(w["size"], w["minimum"]))]
        row = dict(source=name, sha=sha, font_px=pixels, labels=labels, buttons=buttons,
                   clipped_labels=len(clipped), undersized_buttons=len(undersized),
                   window_size=[window.width(), window.height()])
        require(row["window_size"] == [1000, 710], "Window minimum unexpectedly expanded")
        if name == "fixedbd582":
            require(not clipped and not undersized, f"Sidebar still clips at {pixels}px")
            scroll = window.sidebar_scroll
            row["scroll_range"] = [scroll.horizontalScrollBar().maximum(), scroll.verticalScrollBar().maximum()]
            require(row["scroll_range"][1] > 0, "No scroll range at constrained height")
            # Every label and every button can be brought into the viewport, not just a subset.
            reached = []
            for w in sidebar.findChildren(QtWidgets.QWidget):
                if isinstance(w, (QtWidgets.QLabel, QtWidgets.QPushButton)):
                    scroll.ensureWidgetVisible(w, 0, 0)
                    app.processEvents()
                    point = w.mapTo(scroll.viewport(), w.rect().center())
                    require(scroll.viewport().rect().contains(point), f"Unreachable widget: {w.text()}")
                    reached.append(w.text())
            row["reachable_widgets"] = reached
            scroll.horizontalScrollBar().setValue(0)
            scroll.verticalScrollBar().setValue(0)
            app.processEvents()
            require(window.grab().save(str(OUT / f"sidebar-{pixels}px-top.png")), "Top screenshot failed")
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            app.processEvents()
            require(window.grab().save(str(OUT / f"sidebar-{pixels}px-bottom.png")), "Bottom screenshot failed")
        else:
            require(bool(clipped), "Old source should reproduce the concrete clipping failure")
        rows.append(row)
    if name == "fixedbd582":
        window.setStyleSheet(style)
        window.resize(1240, 900)
        QtTest.QTest.qWait(100)
        require(window.sidebar_scroll.verticalScrollBar().maximum() == 0, "Vertical scroll remains at restored size")
        require(window.sidebar_scroll.horizontalScrollBar().maximum() == 0, "Horizontal scroll remains at restored size")
        require(window.grab().save(str(OUT / "sidebar-restored-1240x900.png")), "Restored screenshot failed")
    window.close()
    window.deleteLater()
    app.processEvents()
(OUT / "sidebar-comparison.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
print("PASS: old988 reproduces clipping at 13/18/22px; bd582 has no clipped labels or undersized buttons, all widgets reachable, restored 1240x900 has no scrollbars.")
