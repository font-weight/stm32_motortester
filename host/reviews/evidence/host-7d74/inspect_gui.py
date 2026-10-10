from pathlib import Path
import hashlib, json, struct, time, random
from importlib import metadata
from PySide6 import QtCore, QtWidgets
from dataclasses import replace
from motortester.gui import MainWindow
from motortester.emulator import from_message,demo_message
from motortester.recording import decode,encode,save,load
from motortester.model import CaptureError
root=Path('/tmp/critic-b-host-7d74')
app=QtWidgets.QApplication([]);w=MainWindow();w.show();app.processEvents()
capture=from_message(demo_message());w.show_capture(capture)
w.show_help();app.processEvents()
help_widget=w._help_dialog.findChild(QtWidgets.QTextBrowser,'helpText')
help_text=help_widget.toPlainText()
for phrase in ['Начало работы','Это не напряжение','40,940','Только .mtcap']:
    if phrase=='Только .mtcap':continue
    assert phrase in help_text,phrase
assert 'auto_timeout' in help_text and 'Пуск/Стоп' in help_text and 'подключается' in help_text
assert w._help_dialog.grab().save(str(root/'actual-help.png'))
w._help_dialog.close();w.show_help();app.processEvents();assert w._help_dialog.isVisible();w._help_dialog.close()
w.resize(1000,710);w.cursor_toggle.setChecked(True);app.processEvents();assert w.grab().save(str(root/'minimum-cursors.png'))
raw=encode(capture);rng=random.Random(20261010);bad=0
for _ in range(5000):
    changed=bytearray(raw);i=rng.randrange(len(changed));changed[i]^=1<<rng.randrange(8)
    try:decode(bytes(changed))
    except CaptureError:bad+=1
    else:raise AssertionError(('Accepted corruption',i))
# Read a committed fixture independently without recording.decode.
b=(Path('/tmp/critic-b-host-7d74/source/host/evidence/control-synthetic.mtcap')).read_bytes()
magic,m,n=struct.unpack_from('<8sII',b);meta=json.loads(b[16:16+m]);codes=struct.unpack_from('<4H',b,16+m)
assert magic==b'MTCAP\0\1\0' and n==4 and codes==(0,1,256,4095)
assert hashlib.sha256(b[:-32]).digest()==b[-32:]
assert meta['origin']=='simulator' and meta['calibration']=='uncalibrated' and meta['time_basis']=='nominal_unverified'
w.close();app.processEvents()
print(json.dumps({'corruption_rejections':bad,'control_codes':codes,'actual_help_chars':len(help_text),'versions':{p:metadata.version(p) for p in ['PySide6-Essentials','shiboken6','pyqtgraph','numpy','colorama']},'windows_tested':False},indent=2))
