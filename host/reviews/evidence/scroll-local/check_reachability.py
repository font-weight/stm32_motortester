import json
from PySide6 import QtCore, QtTest, QtWidgets
from motortester.gui import MainWindow
from motortester.recording import encode
app=QtWidgets.QApplication([]);app.setQuitOnLastWindowClosed(False)
rows=[];old_failures=0
for font in (13,22):
 for viewport in (50,100,181):
  for height in (101,180,400):
   w=MainWindow();w.show();w.generate_demo();w.cursor_toggle.setChecked(True)
   w.setStyleSheet(w.styleSheet().replace('font-size: 13px',f'font-size: {font}px'));w.resize(1000,710)
   scroll=w.plot_scroll;label=w.cursor_label;label.setFixedHeight(height);scroll.setFixedHeight(viewport);QtTest.QTest.qWait(40)
   raw=encode(w.capture);capture=w.capture
   if not(w.plot.geometry().bottom()<w.trigger_label.geometry().top()<w.trigger_label.geometry().bottom()<label.geometry().top()):raise RuntimeError('overlap')
   scroll.ensureWidgetVisible(label);app.processEvents()
   old_bottom=label.mapTo(scroll.viewport(),label.rect().bottomLeft()).y();old_pass=old_bottom<scroll.viewport().height()
   old_failures+=not old_pass
   scroll.verticalScrollBar().setValue(0);positions=[]
   for end,point in [('top',label.rect().topLeft()),('bottom',label.rect().bottomLeft())]:
    target=label.mapTo(scroll.widget(),point);scroll.ensureVisible(target.x(),target.y(),1,1);app.processEvents()
    actual=label.mapTo(scroll.viewport(),point)
    if not scroll.viewport().rect().contains(actual):raise RuntimeError((font,viewport,height,end,actual))
    positions.append({'end':end,'x':actual.x(),'y':actual.y(),'scroll':scroll.verticalScrollBar().value()})
   if w.capture is not capture or encode(w.capture)!=raw:raise RuntimeError('source data changed')
   rows.append({'font_px':font,'viewport_height':scroll.viewport().height(),'label_height':label.height(),'old_bottom':old_bottom,'old_oracle_pass':old_pass,'endpoints':positions,'raw_unchanged':True})
   w.close();w.deleteLater();app.processEvents()
if not old_failures:raise RuntimeError('Old oracle did not fail')
print(json.dumps({'qt':QtCore.qVersion(),'candidate_tree':'89ee762ea583e587924ceb15bf7507b9d9afe29b','cases':len(rows),'endpoint_checks':len(rows)*2,'old_oracle_failures':old_failures,'rows':rows},indent=2))
