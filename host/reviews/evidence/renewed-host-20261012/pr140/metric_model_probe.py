from PySide6 import QtCore,QtGui,QtWidgets
from test_gui_acquisition import AcquisitionGuiTests,advertised_caps,panel_geometry
from motortester.session import Phase
import json
AcquisitionGuiTests.setUpClass();t=AcquisitionGuiTests();t.setUp()
try:
 t.window.setStyleSheet(t.window.styleSheet().replace("'Segoe UI', 'DejaVu Sans'", "'DejaVu Sans Mono'"))
 t.bridge.emit_state(Phase.READY,capabilities=advertised_caps())
 for _ in range(3):t.app.processEvents()
 for w in [t.dialog.connect_button,t.dialog.reset,t.dialog.simulate]:
  f=w.font();f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing,13-QtGui.QFontMetricsF(f).horizontalAdvance('M'));w.setFont(f)
 t.dialog.resize(420,360)
 for _ in range(3):t.app.processEvents()
 s=t.dialog.findChild(QtWidgets.QScrollArea)
 print('HMAX',s.horizontalScrollBar().maximum())
 for w in [t.dialog.connect_button,t.dialog.reset,t.dialog.simulate]:print(type(w).__name__,repr(w.text()),w.minimumSizeHint().toTuple(),w.font().toString(),w.fontMetrics().horizontalAdvance('M'),w.fontMetrics().horizontalAdvance(w.text()))
 print(panel_geometry(t.app,t.dialog,s))
finally:t.tearDown()
