from PySide6 import QtCore,QtWidgets,QtTest
from motortester.gui import MainWindow
from motortester.recording import encode
import json
app=QtWidgets.QApplication([]);w=MainWindow();w.show();w.generate_demo();w.cursor_toggle.setChecked(True);w.setStyleSheet(w.styleSheet().replace('font-size: 13px','font-size: 22px'));w.resize(1000,710);QtTest.QTest.qWait(100)
scroll=w.plot_scroll;label=w.cursor_label;raw=encode(w.capture);results=[]
def snapshot():
 return {'viewport_height':scroll.viewport().height(),'label_height':label.height(),'top':label.mapTo(scroll.viewport(),label.rect().topLeft()).y(),'bottom':label.mapTo(scroll.viewport(),label.rect().bottomLeft()).y(),'scroll_value':scroll.verticalScrollBar().value(),'scroll_max':scroll.verticalScrollBar().maximum()}
for scenario,forcedheight,viewport in [('natural',None,x) for x in (181,100,79,78,77,76,50)]+[('oversized101',101,100),('oversized150',150,100)]:
 if forcedheight is not None:label.setFixedHeight(forcedheight)
 scroll.setFixedHeight(viewport);QtTest.QTest.qWait(50)
 assert w.plot.geometry().bottom()<w.trigger_label.geometry().top() and w.trigger_label.geometry().bottom()<label.geometry().top()
 for action in ('default_ensure','bottom_point_margin0','bottom_point_margin1','top_point_margin1','scroll_max','scroll_to_top','scroll_min'):
  if action=='default_ensure':scroll.ensureWidgetVisible(label)
  elif action in ('bottom_point_margin0','bottom_point_margin1'):
   p=label.mapTo(scroll.widget(),label.rect().bottomLeft());scroll.ensureVisible(p.x(),p.y(),1,int(action.endswith('1')))
  elif action=='top_point_margin1':
   p=label.mapTo(scroll.widget(),label.rect().topLeft());scroll.ensureVisible(p.x(),p.y(),1,1)
  elif action=='scroll_max':scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
  elif action=='scroll_to_top':scroll.verticalScrollBar().setValue(label.mapTo(scroll.widget(),label.rect().topLeft()).y())
  else:scroll.verticalScrollBar().setValue(0)
  app.processEvents();d={'scenario':scenario,'requested_viewport':viewport,'action':action,**snapshot()};results.append(d)
  if action in ('bottom_point_margin1','scroll_max'):assert 0<=d['bottom']<d['viewport_height'],d
  if action in ('top_point_margin1','scroll_to_top'):assert 0<=d['top']<d['viewport_height'],d
 assert encode(w.capture)==raw
print(json.dumps({'source':'3b315ac4e1184c4c7e9216ce5594cff0f9beff33','qt_version':QtCore.qVersion(),'raw_unchanged':True,'results':results},indent=2));w.close()
