"""Read-only component reversions in Qt objects; never writes candidate files."""
import json,sys
from PySide6 import QtWidgets
from test_gui_acquisition import AcquisitionGuiTests,advertised_caps
from motortester.session import Phase
from layout_probe import LayoutProbe,pressure
AcquisitionGuiTests.setUpClass();app=AcquisitionGuiTests.app;app.setStyle(sys.argv[2] if len(sys.argv)>2 else 'Fusion');t=AcquisitionGuiTests();t.setUp()
try:
 mode=sys.argv[1];names=['connect_button','reset','simulate'] if mode=='all' else [mode]
 for name in names:
  w=getattr(t.dialog,name);w.setText(w.text().replace('\n',' '))
 pressure(t);t.bridge.emit_state(Phase.READY,capabilities=advertised_caps());checker=LayoutProbe();checker.app=app
 checker.panel(t,(420,360),'mutant-'+mode)
finally:t.tearDown()
