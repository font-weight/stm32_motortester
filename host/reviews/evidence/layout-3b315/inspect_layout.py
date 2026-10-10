from pathlib import Path
from dataclasses import replace
import json,os,sys
from PySide6 import QtCore,QtGui,QtTest,QtWidgets
from motortester.gui import MainWindow
from motortester.recording import encode
root=Path(__file__).resolve().parent;app=QtWidgets.QApplication([]);w=MainWindow();w.show();w.generate_demo();app.processEvents();original_style=w.styleSheet()
raw=encode(w.capture);identity=w.capture;path=w.current_path;results=[]
for font in (13,18,22):
 w.setStyleSheet(original_style.replace('font-size: 13px',f'font-size: {font}px'))
 for width,height in ((1000,710),(1240,800),(1240,900),(1600,1000),(1000,710)):
  w.resize(width,height);w.cursor_toggle.setChecked(True);QtTest.QTest.qWait(100)
  plot=w.plot.geometry();trigger=w.trigger_label.geometry();cursor=w.cursor_label.geometry();scroll=w.plot_scroll
  assert plot.bottom()<trigger.top()<trigger.bottom()<cursor.top()
  assert w.plot.height()>=100 and w.width()==width and w.height()==height
  # Scan every vertical scroll position at this geometry; labels remain non-overlapping.
  bar=scroll.verticalScrollBar()
  for value in (0,bar.maximum()//2,bar.maximum()):bar.setValue(value);app.processEvents()
  scroll.ensureWidgetVisible(w.cursor_label);app.processEvents()
  bottom=w.cursor_label.mapTo(scroll.viewport(),w.cursor_label.rect().bottomLeft()).y()
  assert 0<=bottom<scroll.viewport().height()
  assert encode(w.capture)==raw and w.capture is identity and w.current_path==path
  results.append({'font_px':font,'size':[width,height],'plot_height':plot.height(),'plot_bottom':plot.bottom(),'trigger_top':trigger.top(),'trigger_bottom':trigger.bottom(),'cursor_top':cursor.top(),'scroll_max':bar.maximum(),'viewport':[scroll.viewport().width(),scroll.viewport().height()],'card_width':scroll.widget().width(),'cursor_bottom_in_viewport':bottom})
  if (width,height)==(1000,710):w.grab().save(str(root/f'min-font{font}-dpi{app.devicePixelRatio()}.png'))
  w.cursor_toggle.setChecked(False);app.processEvents();assert encode(w.capture)==raw
 w.cursor_toggle.setChecked(True)
w.setStyleSheet(original_style);w.resize(1240,900);QtTest.QTest.qWait(100)
assert w.plot_scroll.verticalScrollBar().maximum()==0
# Interactions after scroll/resize: cursor index remains tied to original samples.
w.cursor_lines[0].setValue(0);w.cursor_lines[1].setValue(1);app.processEvents()
assert w._cursor_indices==[512,562],w._cursor_indices
w.plot.setXRange(-2,5,padding=0);w.plot.setYRange(1500,2500,padding=0);app.processEvents()
assert encode(w.capture)==raw;w.reset_button.click();app.processEvents();assert encode(w.capture)==raw
w.grab().save(str(root/f'restored-dpi{app.devicePixelRatio()}.png'))
print(json.dumps({'status':'PASS','dpi':app.devicePixelRatio(),'geometry_cases':results,'raw_unchanged':True,'restored_scroll_max':w.plot_scroll.verticalScrollBar().maximum(),'cursor_indices':[512,562],'png_export_available':hasattr(w,'png_image')},indent=2))
w.close();app.processEvents()
