"""Runtime-only negative control. Source files are never modified."""
import sys, unittest
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtWidgets
source=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(source/'host'/'tests'))
from test_gui import ViewerTests
suite=unittest.TestSuite([ViewerTests('test_annotation_taller_than_viewport_is_fully_reachable_by_scrolling')])
with patch.object(QtWidgets.QScrollArea,'ensureVisible',lambda *args,**kwargs:None):
 result=unittest.TextTestRunner(verbosity=2).run(suite)
if result.testsRun!=1 or len(result.failures)!=1 or result.errors or result.skipped:
 raise RuntimeError('Negative control did not produce the required assertion failure')
print('EXPECTED_NEGATIVE_CONTROL: the new regression rejects a no-op scroll helper.')
