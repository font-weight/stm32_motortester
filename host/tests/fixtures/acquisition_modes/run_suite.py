"""Воспроизводимый unittest runner: отсутствие GUI и любой skip — ошибка."""
import argparse
from pathlib import Path
import platform
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--pattern', default='test_acquisition_modes_conformance.py')
parser.add_argument('--require-gui', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
print('Python:', sys.version, flush=True)
print('Platform:', platform.platform(), flush=True)
print('Optimization:', sys.flags.optimize, flush=True)
print('Pattern:', args.pattern, flush=True)
if args.require_gui:
    import PySide6
    import numpy
    import pyqtgraph
    for module in (PySide6, numpy, pyqtgraph):
        print(module.__name__, module.__version__, module.__file__, flush=True)
suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern=args.pattern)
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('CHECK_RESULT', {'tests': result.testsRun, 'failures': len(result.failures),
                       'errors': len(result.errors), 'skips': len(result.skipped)}, flush=True)
sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
