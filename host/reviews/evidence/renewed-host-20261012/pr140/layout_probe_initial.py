"""Independent geometry, multiline painting bounds and native interaction oracle."""
from pathlib import Path
from unittest.mock import patch
import json,os,sys,unittest
from PySide6 import QtCore,QtGui,QtTest,QtWidgets
from test_gui_acquisition import AcquisitionGuiTests,advertised_caps
from motortester.session import Phase
OUT=Path(__file__).resolve().parent;SCALE=os.getenv('QT_SCALE_FACTOR','1')
WORDS={'connect_button':'Подключить · без запуска захвата','reset':'Подтверждаю физический сброс','simulate':'Событие запуска в эмуляторе'}
def norm(text):return ' '.join(text.split())
def pressure(fixture):
 fixture.window.setStyleSheet(fixture.window.styleSheet().replace("'Segoe UI', 'DejaVu Sans'", "'DejaVu Sans Mono'"))
 for _ in range(3):fixture.app.processEvents()
 for name in WORDS:
  widget=getattr(fixture.dialog,name);font=widget.font();font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing,13-QtGui.QFontMetricsF(font).horizontalAdvance('M'));widget.setFont(font)
class LayoutProbe(unittest.TestCase):
 @classmethod
 def setUpClass(cls):AcquisitionGuiTests.setUpClass();cls.app=AcquisitionGuiTests.app
 def pump(self):
  for _ in range(3):self.app.processEvents()
 def panel(self,fixture,size,tag):
  d=fixture.dialog;d.resize(*size);self.pump();scroll=d.findChild(QtWidgets.QScrollArea);content=scroll.widget();v=scroll.viewport()
  record={'tag':tag,'scale':SCALE,'dialog':[d.width(),d.height()],'viewport':[v.width(),v.height()],'content':[content.width(),content.height()],'hmax':scroll.horizontalScrollBar().maximum(),'vmax':scroll.verticalScrollBar().maximum(),'target_hints':{name:getattr(d,name).sizeHint().toTuple() for name in WORDS}}
  print('GEOMETRY',json.dumps(record),flush=True);self.assertEqual(d.size(),QtCore.QSize(*size));self.assertEqual(record['hmax'],0,record);self.assertLessEqual(content.width(),v.width());self.assertEqual(scroll.horizontalScrollBarPolicy(),QtCore.Qt.ScrollBarAsNeeded)
  widgets=content.findChildren(QtWidgets.QLabel)+[getattr(d,name) for name in ['source','mode','edge','level','timeout','port','reset','simulate','connect_button','start_button','stop_button','disconnect_button']]
  for w in widgets:
   with self.subTest(widget=w.objectName() or type(w).__name__,tag=tag,size=size):
    self.assertGreaterEqual(w.width(),w.minimumSizeHint().width());self.assertGreaterEqual(w.height(),w.heightForWidth(w.width()) if isinstance(w,QtWidgets.QLabel) and w.wordWrap() else w.minimumSizeHint().height())
    if isinstance(w,QtWidgets.QAbstractButton):
     opt=QtWidgets.QStyleOptionButton();w.initStyleOption(opt);element=QtWidgets.QStyle.SE_CheckBoxContents if isinstance(w,QtWidgets.QCheckBox) else QtWidgets.QStyle.SE_PushButtonContents
     area=w.style().subElementRect(element,opt,w);need=w.fontMetrics().size(QtCore.Qt.TextShowMnemonic,w.text())
     self.assertGreaterEqual(area.width(),need.width(),('text_width',w.text(),area,need));self.assertGreaterEqual(area.height(),need.height(),('text_height',w.text(),area,need))
    if isinstance(w,QtWidgets.QComboBox):
     opt=QtWidgets.QStyleOptionComboBox();w.initStyleOption(opt);area=w.style().subControlRect(QtWidgets.QStyle.CC_ComboBox,opt,QtWidgets.QStyle.SC_ComboBoxEditField,w)
     self.assertGreaterEqual(area.width(),max(w.fontMetrics().horizontalAdvance(w.itemText(i)) for i in range(w.count())));self.assertGreaterEqual(area.height(),w.fontMetrics().height())
    for point in [w.rect().topLeft(),w.rect().topRight(),w.rect().bottomLeft(),w.rect().bottomRight()]:
     local=w.mapTo(content,point);scroll.ensureVisible(local.x(),local.y(),1,1);self.pump();self.assertTrue(v.rect().contains(w.mapTo(v,point)),('corner',point))
    if w.height()<=v.height():
     scroll.verticalScrollBar().setValue(w.mapTo(content,QtCore.QPoint(0,0)).y()-5);self.pump();self.assertTrue(v.rect().contains(QtCore.QRect(w.mapTo(v,QtCore.QPoint(0,0)),w.size())),('wholewidget',w))
  for name,expected in WORDS.items():self.assertEqual(norm(getattr(d,name).text()),expected)
  self.assertEqual(fixture.bridge.calls,[])
 def test_native_and_measured_width_matrix(self):
  original_font=QtGui.QFont(self.app.font());original_style=self.app.style().objectName();count=0
  try:
   for style in ['Fusion','Windows']:
    self.assertIn(style,QtWidgets.QStyleFactory.keys());self.app.setStyle(style)
    for native in [9,22]:
     self.app.setFont(QtGui.QFont('DejaVu Sans',native))
     for model in [False,True]:
      t=AcquisitionGuiTests();t.setUp()
      try:
       if model:pressure(t)
       for phase in [Phase.IDLE,Phase.READY,Phase.RECOVERY]:
        t.bridge.emit_state(phase,capabilities=advertised_caps() if phase is Phase.READY else None)
        for size in [(560,650),(420,360),(360,300)]:
         with self.subTest(style=style,native=native,model=model,phase=phase.name,size=size):
          tag=f'{style}-native{native}-model{int(model)}-{phase.name}';self.panel(t,size,tag);count+=1
          if native==22 and phase is Phase.IDLE and size==(360,300):
           for name in ['connect_button','reset','simulate']:
            w=getattr(t.dialog,name);scroll=t.dialog.findChild(QtWidgets.QScrollArea);scroll.verticalScrollBar().setValue(w.mapTo(scroll.widget(),QtCore.QPoint(0,0)).y()-15);self.pump();self.assertTrue(t.dialog.grab().save(str(OUT/f'render-{tag}-scale{SCALE}-{name}.png')))
      finally:t.tearDown()
  finally:self.app.setFont(original_font);self.app.setStyle(original_style)
  print('LAYOUT_CASES',count,'scale',SCALE,flush=True)
 def test_multiline_checkboxes_keep_native_mouse_keyboard_and_names(self):
  t=AcquisitionGuiTests();t.setUp()
  try:
   pressure(t);t.bridge.emit_state(Phase.IDLE);t.dialog.resize(360,300);self.pump()
   for name in ['reset','simulate']:
    t.dialog.source.setCurrentIndex(1 if name=='reset' else 0);self.pump();w=getattr(t.dialog,name);self.assertTrue(w.isEnabled());self.assertEqual(norm(w.text()),WORDS[name]);self.assertIn('\n',w.text())
    iface=QtGui.QAccessible.queryAccessibleInterface(w);self.assertIsNotNone(iface);self.assertEqual(norm(iface.text(QtGui.QAccessible.Name)),WORDS[name])
    scroll=t.dialog.findChild(QtWidgets.QScrollArea);scroll.verticalScrollBar().setValue(w.mapTo(scroll.widget(),QtCore.QPoint(0,0)).y()-10);self.pump()
    opt=QtWidgets.QStyleOptionButton();w.initStyleOption(opt);area=w.style().subElementRect(QtWidgets.QStyle.SE_CheckBoxContents,opt,w)
    point=QtCore.QPoint(area.left()+4,area.bottom()-w.fontMetrics().height()//2);self.assertTrue(w.rect().contains(point));values=[];w.clicked.connect(values.append);initial=w.isChecked();QtTest.QTest.mouseClick(w,QtCore.Qt.LeftButton,pos=point);self.pump();self.assertEqual(w.isChecked(),not initial);self.assertEqual(values,[not initial])
    w.setFocus();self.assertTrue(w.hasFocus());QtTest.QTest.keyClick(w,QtCore.Qt.Key_Space);self.pump();self.assertEqual(w.isChecked(),initial);self.assertEqual(values,[not initial,initial]);self.assertEqual(t.bridge.calls,[])
  finally:t.tearDown()
if __name__=='__main__':unittest.main(verbosity=2)
