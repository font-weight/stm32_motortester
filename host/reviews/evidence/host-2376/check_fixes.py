from pathlib import Path
from unittest.mock import patch
from PySide6 import QtCore,QtWidgets,QtTest
from motortester.gui import MainWindow
from motortester.recording import load
from motortester.model import CaptureError
import time,json
root=Path('/tmp/critic-b-host-2376')
for name in ('utf-16','utf-32'):
    try:load(Path('/tmp/critic-b-host-7d74')/(name+'.mtcap'))
    except CaptureError:print(name,'controlled rejection: PASS')
    else:raise AssertionError(name+' still accepted')
app=QtWidgets.QApplication([]);w=MainWindow();w.show();w.generate_demo();app.processEvents()
original=w.capture;target=root/'существующая.mtcap';target.write_bytes(b'KEEP')
results=[]
for action in ('enter','escape','close','replace'):
    info={}
    def dismiss():
        d=app.activeModalWidget();assert isinstance(d,QtWidgets.QMessageBox)
        info['labels']=[b.text() for b in d.buttons()];info['default']=d.defaultButton().text();info['escape']=d.escapeButton().text()
        assert set(info['labels'])=={'Заменить','Отмена'} and info['default']==info['escape']=='Отмена'
        if action=='enter':QtTest.QTest.keyClick(d,QtCore.Qt.Key.Key_Return)
        elif action=='escape':QtTest.QTest.keyClick(d,QtCore.Qt.Key.Key_Escape)
        elif action=='close':d.close()
        else:
            assert d.grab().save(str(root/'localized-overwrite.png'))
            QtTest.QTest.mouseClick(next(b for b in d.buttons() if b.text()=='Заменить'),QtCore.Qt.MouseButton.LeftButton)
    QtCore.QTimer.singleShot(100,dismiss)
    with patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(target),'')):w.save_dialog()
    deadline=time.monotonic()+5
    while w.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.002)
    assert not w.busy
    if action=='replace':assert load(target)==original
    else:assert target.read_bytes()==b'KEEP'
    results.append({'action':action,**info,'raw_file_preserved_or_correctly_replaced':True})
w.close();app.processEvents();print(json.dumps(results,ensure_ascii=False,indent=2))
