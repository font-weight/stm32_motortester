"""Independent PR139 real-event, retained-data and finite-navigation probes.
Run from the frozen source tree, with its host on PYTHONPATH.
MUTANT=baseline loads prior gui.py in memory; MUTANT=no_limits suppresses new
limits; MUTANT=no_axis consumes bottom-axis wheels. No source file is changed.
"""
import os,sys,math,time,subprocess,types,json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
import numpy as np
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W, QtTest as T
from motortester import recording
from motortester.emulator import demo_message,from_message
from motortester.model import MAX_SAMPLES
import motortester.gui as gui
OUT=Path(__file__).resolve().parent
MUTANT=os.environ.get('MUTANT','none')
if MUTANT=='baseline':
 code=subprocess.check_output(['git','show','HEAD^:host/motortester/gui.py']).decode()
 mod=types.ModuleType('motortester.review_gui');mod.__file__=gui.__file__;sys.modules[mod.__name__]=mod
 exec(compile(code,gui.__file__,'exec'),mod.__dict__);gui=mod

class IndependentZoom(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.app=W.QApplication.instance() or W.QApplication([]);cls.app.setQuitOnLastWindowClosed(False)
 def setUp(self):
  self.w=gui.MainWindow();self.w.show();self.app.processEvents();self.v=self.w.plot.getViewBox();self.p=self.w.plot
  self.c=replace(from_message(demo_message()),nominal_fs_hz=33333.3333333333)
  if MUTANT=='no_limits':
   self.v.setLimits(xMin=None,xMax=None,yMin=None,yMax=None,minXRange=None,maxXRange=None,minYRange=None,maxYRange=None)
   self.v.setLimits=lambda **kw:None
  if MUTANT=='no_axis':self.p.getAxis('bottom').wheelEvent=lambda event:None
 def tearDown(self):
  self.wait(lambda:not self.w.busy);self.w.close()
  if self.w._acquisition is not None:
   self.wait(lambda:not self.w._acquisition.running)
   self.assertTrue(self.w._acquisition._thread.wait(0))
  self.w.deleteLater();self.app.sendPostedEvents(None,C.QEvent.Type.DeferredDelete);self.app.processEvents()
 def wait(self,condition):
  deadline=time.monotonic()+6
  while not condition():
   if time.monotonic()>deadline:self.fail('asynchronous deadline')
   T.QTest.qWait(5)
  self.app.processEvents()
 def show(self,c=None):
  self.w.show_capture(c or self.c);self.app.processEvents()
 def pos(self,area):
  item=self.v if area=='plot' else self.p.getAxis(area)
  pt=self.p.mapFromScene(item.mapToScene(item.rect().center()))
  self.assertTrue(self.p.viewport().rect().contains(pt))
  if area!='plot':self.assertFalse(self.v.sceneBoundingRect().contains(self.p.mapToScene(pt)))
  return pt
 def wheel(self,area,delta):
  pt=self.pos(area);vp=self.p.viewport()
  event=G.QWheelEvent(C.QPointF(pt),C.QPointF(vp.mapToGlobal(pt)),C.QPoint(),C.QPoint(0,delta),C.Qt.NoButton,C.Qt.NoModifier,C.Qt.NoScrollPhase,False)
  self.app.sendEvent(vp,event);self.app.processEvents()
 def drag(self,pt,dx,dy):
  vp=self.p.viewport();T.QTest.mouseMove(vp,pt);T.QTest.mousePress(vp,C.Qt.LeftButton,pos=pt)
  for fraction in (.2,.4,.6,.8,1):
   q=pt+C.QPoint(round(dx*fraction),round(dy*fraction))
   self.app.sendEvent(vp,G.QMouseEvent(C.QEvent.MouseMove,C.QPointF(q),C.QPointF(vp.mapToGlobal(q)),C.Qt.NoButton,C.Qt.LeftButton,C.Qt.NoModifier));T.QTest.qWait(8)
  T.QTest.mouseRelease(vp,C.Qt.LeftButton,pos=q);self.app.processEvents()
 def limits(self,c):
  if c is None:x0,x1,period=0.,1.,1.
  else:
   period=1000./c.nominal_fs_hz;zero=c.trigger_index if c.trigger_index is not None else 0
   x0,x1=-zero*period,(len(c.samples)-1-zero)*period
   if len(c.samples)==1:x0-=period/2;x1+=period/2
  pad=(x1-x0)/40
  return np.array([[x0-pad,x1+pad],[-4095*.035,4095*1.035]]),np.array([period,1.])
 def bounded(self,c,overview=False):
  bounds,minimum=self.limits(c);r=np.array(self.v.viewRange());span=np.diff(r,axis=1)[:,0]
  self.assertTrue(np.isfinite(r).all());self.assertTrue((span>0).all())
  tolerance=np.maximum(1e-10,np.diff(bounds,axis=1)[:,0]*1e-11)
  self.assertTrue((r[:,0]>=bounds[:,0]-tolerance).all(),(r,bounds));self.assertTrue((r[:,1]<=bounds[:,1]+tolerance).all(),(r,bounds))
  self.assertTrue((span>=minimum-tolerance).all(),(span,minimum))
  if overview:np.testing.assert_allclose(r,bounds,rtol=1e-11,atol=1e-10)
 def test_01_actual_axis_and_plot_gestures(self):
  self.show()
  for area,changed in [('bottom',{0}),('left',{1}),('plot',{0,1})]:
   self.w.reset_view();self.wheel('plot',120);self.wheel('plot',120)
   before=np.array(self.v.viewRange());self.wheel(area,120);zoom=np.array(self.v.viewRange())
   for axis in (0,1):
    if axis in changed:self.assertLess(np.ptp(zoom[axis]),np.ptp(before[axis]))
    else:np.testing.assert_array_equal(zoom[axis],before[axis])
   self.drag(self.pos(area),27,19);pan=np.array(self.v.viewRange())
   for axis in (0,1):
    if axis in changed:self.assertNotEqual(pan[axis,0],zoom[axis,0])
    else:np.testing.assert_array_equal(pan[axis],zoom[axis])
   np.testing.assert_allclose(np.ptp(pan,axis=1),np.ptp(zoom,axis=1),rtol=1e-10);self.bounded(self.c)
 def test_02_limits_extreme_fractional_rates_and_replacement(self):
  self.bounded(None,True)
  cases=[(1,1.00000001,None),(2,999999.99999,1),(7,3.7,6),(MAX_SAMPLES,1.,MAX_SAMPLES-1),(MAX_SAMPLES,1000000.,0)]
  for n,rate,trigger in cases:
   with self.subTest(n=n,rate=rate,trigger=trigger):
    c=replace(self.c,samples=tuple((i*997)%4096 for i in range(n)),nominal_fs_hz=rate,trigger_index=trigger,pretrigger_samples=trigger or 0,trigger_kind='signal' if trigger is not None else 'auto_timeout')
    self.show(c);self.bounded(c,True)
    for sign in (1,-1):
     for k in range(90):
      self.wheel('bottom',sign*120);self.wheel('left',sign*120);self.bounded(c)
     if sign==1:np.testing.assert_allclose(np.ptp(self.v.viewRange(),axis=1),self.limits(c)[1],rtol=1e-5,atol=1e-10)
     else:self.bounded(c,True)
    self.wheel('plot',240)
    for axis,area in enumerate(('bottom','left')):
     for sign in (-1,1):
      for _ in range(8):self.drag(self.pos(area),sign*80 if axis==0 else 0,sign*60 if axis else 0);self.bounded(c)
    self.w.reset_view();self.bounded(c,True)
 def test_03_cursors_bytes_home_and_png(self):
  self.show();self.w.cursor_toggle.setChecked(True);self.app.processEvents()
  blob=recording.encode(self.c);data=tuple(x.copy() for x in self.w.curve.getData());indices=self.w._cursor_indices[:];positions=[x.value() for x in self.w.cursor_lines]
  for _ in range(4):self.wheel('bottom',120);self.wheel('left',120)
  self.assertEqual(self.w._cursor_indices,indices);self.assertEqual([x.value() for x in self.w.cursor_lines],positions)
  for old,new in zip(data,self.w.curve.getData()):np.testing.assert_array_equal(old,new)
  self.assertEqual(recording.encode(self.w.capture),blob)
  # Cursor move is a scene mouse drag after independent X/Y zoom.
  # Return to a view containing the original cursor before targeting its hit area.
  self.w.reset_view();self.wheel("bottom",120);self.wheel("left",120)
  line=self.w.cursor_lines[0];mid=sum(self.v.viewRange()[1])/2
  pt=self.p.mapFromScene(self.v.mapViewToScene(C.QPointF(line.value(),mid)))
  self.assertTrue(self.v.sceneBoundingRect().contains(self.p.mapToScene(pt)))
  self.drag(pt,31,0);self.assertNotEqual(indices[0],self.w._cursor_indices[0])
  for i,line in zip(self.w._cursor_indices,self.w.cursor_lines):
   self.assertAlmostEqual(line.value(),(i-(self.c.trigger_index or 0))*1000/self.c.nominal_fs_hz,places=9)
   self.assertIn(f'{self.c.samples[i]} код',self.w.cursor_label.text())
  self.assertEqual(recording.encode(self.w.capture),blob)
  self.w.activateWindow();self.w.setFocus();T.QTest.keyClick(self.w,C.Qt.Key_Home)
  self.wait(lambda:np.allclose(self.v.viewRange(),self.limits(self.c)[0]));self.bounded(self.c,True)
  self.assertTrue(self.w.grab().save(str(OUT/f'capture-scale-{os.environ.get("QT_SCALE_FACTOR","1")}.png')))
 def test_04_file_and_live_replacement(self):
  self.show();self.wheel('plot',240)
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'fractional-частота.mtcap';short=replace(self.c,samples=(0,4095),nominal_fs_hz=1.25,trigger_index=1,pretrigger_samples=1)
   recording.save(p,short);before=p.read_bytes();self.w.open_path(p);self.wait(lambda:not self.w.busy)
   self.assertEqual(self.w.capture,short);self.bounded(short,True);self.wheel('left',120);r=np.array(self.v.viewRange())
   self.w.open_path(Path(folder)/'missing');self.wait(lambda:not self.w.busy);np.testing.assert_array_equal(r,self.v.viewRange());self.assertEqual(recording.encode(self.w.capture),before)
  retained=self.w.capture
  self.w.show_acquisition();d=self.w._acquisition_dialog;d.connect_button.click();self.wait(d.start_button.isEnabled)
  self.assertIs(self.w.capture,retained);self.assertFalse(d.stop_button.isEnabled())
  d.mode.setCurrentIndex(2);d.start_button.click();self.wait(lambda:self.w.capture is not retained and d.start_button.isEnabled());first=self.w.capture;raw=recording.encode(first);self.bounded(first,True)
  self.wheel('plot',240);d.start_button.click();self.wait(lambda:self.w.capture is not first and d.start_button.isEnabled());self.bounded(self.w.capture,True);self.assertEqual(recording.encode(first),raw)
 def test_05_guidance_reachable_and_rendered(self):
  self.show();self.w.cursor_toggle.setChecked(True);style=self.w.styleSheet();rows=[]
  for px in (13,18,22):
   self.w.setStyleSheet(style.replace('font-size: 13px',f'font-size: {px}px'));self.w.resize(1000,710);T.QTest.qWait(40)
   label=self.w.zoom_help;scroll=self.w.plot_scroll
   self.assertEqual(self.w.size(),C.QSize(1000,710));self.assertGreaterEqual(label.height(),label.heightForWidth(label.width()));self.assertGreaterEqual(self.w.plot.height(),100)
   self.assertLess(self.w.trigger_label.geometry().bottom(),label.geometry().top());self.assertLess(label.geometry().bottom(),self.w.cursor_label.geometry().top())
   for pt in (label.rect().topLeft(),label.rect().bottomRight()):
    target=label.mapTo(scroll.widget(),pt);scroll.ensureVisible(target.x(),target.y(),1,1);self.app.processEvents();self.assertTrue(scroll.viewport().rect().contains(label.mapTo(scroll.viewport(),pt)))
   rows.append({'px':px,'guide':[label.width(),label.height()],'plot_height':self.w.plot.height(),'hscroll':scroll.horizontalScrollBar().maximum()})
   self.assertEqual(scroll.horizontalScrollBar().maximum(),0)
   self.assertTrue(self.w.grab().save(str(OUT/f'guide-{px}-scale-{os.environ.get("QT_SCALE_FACTOR","1")}.png')))
  (OUT/f'layout-scale-{os.environ.get("QT_SCALE_FACTOR","1")}.json').write_text(json.dumps(rows,indent=2)+'\n')
  self.w.show_help();plain=self.w._help_dialog.findChild(W.QTextBrowser,'helpText').toPlainText()
  for term in ('нижняя ось','левая ось','одним номинальным интервалом','одним кодом','очередное окно захвата'):self.assertIn(term,plain)

if __name__=='__main__':unittest.main(verbosity=2)
