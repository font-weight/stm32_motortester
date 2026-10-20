"""Measure inherited large-font header overflow separately from new guide."""
import json,os,subprocess,sys,types
from pathlib import Path
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W,QtTest as T
import motortester.gui as gui
from motortester.emulator import demo_message,from_message
BASE=os.environ.get('HEADER_BASELINE')=='1';O=Path(__file__).resolve().parent
if BASE:
 mod=types.ModuleType('motortester.review_header_baseline');mod.__file__=gui.__file__;sys.modules[mod.__name__]=mod
 exec(compile(subprocess.check_output(['git','show','HEAD^:host/motortester/gui.py']).decode(),gui.__file__,'exec'),mod.__dict__);gui=mod
app=W.QApplication([]);app.setQuitOnLastWindowClosed(False);w=gui.MainWindow();w.show();w.show_capture(from_message(demo_message()));original=w.styleSheet();records=[]
for px in (13,18,22):
 w.setStyleSheet(original.replace('font-size: 13px',f'font-size: {px}px'));w.resize(1000,710);T.QTest.qWait(60)
 widgets={'subtitle':w.findChild(W.QLabel,'subtitle'),'origin_badge':w.origin_badge,'acquisition_button':w.acquisition_button};row={'pixels':px,'window':[w.width(),w.height()],'dpr':w.devicePixelRatioF(),'widgets':{}}
 for name,item in widgets.items():
  c=item.contentsRect();fm=G.QFontMetricsF(item.font());row['widgets'][name]={'text':item.text(),'geometry':[item.x(),item.y(),item.width(),item.height()],'contents':[c.x(),c.y(),c.width(),c.height()],'minimumHint':[item.minimumSizeHint().width(),item.minimumSizeHint().height()],'sizeHint':[item.sizeHint().width(),item.sizeHint().height()],'textAdvance':fm.horizontalAdvance(item.text()),'font':item.font().toString(),'wordWrap':item.wordWrap() if isinstance(item,W.QLabel) else None}
 records.append(row);w.grab().save(str(O/f'header-{"baseline" if BASE else "candidate"}-{px}.png'))
w.close();w.deleteLater();app.processEvents();name='baseline' if BASE else 'candidate';(O/f'header-{name}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n');print(json.dumps(records,ensure_ascii=False))
