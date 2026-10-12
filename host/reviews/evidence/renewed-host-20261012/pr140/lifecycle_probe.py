"""Fresh independent MainWindow/real bridge checks. Synthetic serial peer only."""
import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtCore,QtTest,QtWidgets
from motortester.gui import MainWindow
from motortester.acquisition import AcquisitionBridge
from motortester.transport import SerialTransport
from motortester.session import Phase
from motortester import recording
from test_acquisition_bridge import SerialPeer
APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([]);APP.setQuitOnLastWindowClosed(False)
class LifecycleProbe(unittest.TestCase):
 def setUp(self):
  self.controls={};self.calls=[];self.main_thread=threading.get_ident();self.acks=[];self.join=[]
  factory=lambda parent=None:AcquisitionBridge(parent,interval_ms=5,edge_delay_ms=15,serial_factory=lambda:SerialTransport(backend_factory=lambda **kw:SerialPeer(calls=self.calls,controls=self.controls,**kw)))
  self.patcher=patch('motortester.acquisition.AcquisitionBridge',factory);self.patcher.start()
  self.w=MainWindow();self.w.show();self.w.show_acquisition();self.b=self.w._acquisition;self.d=self.w._acquisition_dialog
  original=self.b.acknowledge
  def retained(c):
   self.assertIs(self.w.capture,c);self.assertEqual(threading.get_ident(),self.main_thread);self.acks.append(c);return original(c)
  self.b.acknowledge=retained
  self.b.finished.connect(lambda:self.join.append(self.b._thread is None or self.b._thread.wait(0)))
  APP.processEvents()
 def wait(self,predicate):
  deadline=time.monotonic()+4
  while not predicate():
   if time.monotonic()>deadline:self.fail('deadline: '+str(self.b.state)+' / '+self.d.status.text())
   QtTest.QTest.qWait(5)
  APP.processEvents()
 def ready(self):return bool(self.b.state.connected and self.b.state.snapshot and self.b.state.snapshot.phase is Phase.READY and not self.b.state.busy and not self.b.state.start_pending)
 def connect(self,device=False):
  if device:self.d.source.setCurrentIndex(1);self.d.port.setText('SYNTHETIC-ONLY')
  self.d.connect_button.click();self.d.connect_button.click();self.wait(self.ready)
 def stop(self):self.d.stop_button.click();self.wait(self.ready)
 def tearDown(self):
  self.controls['close_fail']=False
  for key in ['open_gate','read_gate']:
   if key in self.controls:self.controls[key].set()
  self.w.close();self.wait(lambda:self.b._closed and not self.b.running);self.wait(lambda:not self.w.isVisible());self.assertEqual(self.join,[True],'exactly one public finish after native join')
  self.w.deleteLater();QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete);APP.processEvents();self.patcher.stop()
 def test_connect_only_and_exact_single_retention(self):
  self.connect(True);self.assertEqual(self.controls['backend'].kinds,[1,2]);self.assertIsNone(self.w.capture)
  self.controls['trigger']=True;self.d.mode.setCurrentIndex(2);self.d.start_button.click();self.d.start_button.click();self.wait(lambda:self.w.capture is not None);self.wait(self.ready)
  current=self.w.capture;count=len(self.acks);QtTest.QTest.qWait(150);self.assertIs(self.w.capture,current);self.assertEqual(len(self.acks),count)
  self.assertIs(self.acks[0],current);self.assertEqual(self.controls['backend'].kinds.count(4),1)
  self.assertTrue(self.calls);self.assertTrue(all(tid!=self.main_thread for _,tid in self.calls))
 def test_all_modes_edges_real_capture_roundtrip(self):
  self.connect()
  with tempfile.TemporaryDirectory() as folder:
   for mode in range(3):
    for edge in range(2):
     with self.subTest(mode=mode,edge=edge):
      old=self.w.capture;self.d.mode.setCurrentIndex(mode);self.d.edge.setCurrentIndex(edge);self.d.start_button.click();self.wait(lambda:self.w.capture is not None and self.w.capture is not old)
      self.stop();capture=self.w.capture;self.assertEqual(capture.trigger_edge,['rising','falling'][edge]);self.assertEqual(capture.trigger_kind,'signal')
      path=Path(folder)/f'{mode}-{edge}.mtcap';self.w.save_path(path);self.wait(lambda:not self.w.busy)
      loaded=recording.load(path);self.assertEqual(loaded.samples,capture.samples);self.assertEqual(loaded.trigger_edge,capture.trigger_edge)
 def test_auto_timeout_and_stop(self):
  self.connect();self.d.mode.setCurrentIndex(0);self.d.timeout.setValue(100);self.d.simulate.setChecked(False);self.d.start_button.click();self.wait(lambda:self.w.capture is not None);self.stop()
  self.assertEqual(self.w.capture.trigger_kind,'auto_timeout');self.assertIsNone(self.w.capture.trigger_index)
 def test_normal_wait_hide_and_stop_no_restart(self):
  self.connect();self.d.simulate.setChecked(False);self.d.start_button.click();self.wait(lambda:self.b.state.snapshot.phase is Phase.ARMED);self.d.close();QtTest.QTest.qWait(150)
  self.assertFalse(self.d.isVisible());self.assertTrue(self.w.isVisible());self.assertIsNone(self.w.capture);self.assertEqual(self.b.state.snapshot.phase,Phase.ARMED)
  self.w.show_acquisition();self.stop();QtTest.QTest.qWait(100);self.assertEqual(self.b.state.snapshot.phase,Phase.READY);self.assertIsNone(self.w.capture)
 def test_reconnect_clears_current_caps_without_autostart(self):
  self.connect(True);old=self.b.state.snapshot.session;self.assertIn('CAPS приняты',self.d.device_details.text());self.d.disconnect_button.click();self.wait(lambda:not self.b.state.connected and not self.b.state.busy)
  self.assertNotIn('CAPS приняты',self.d.device_details.text());self.d.reset.setChecked(True);self.d.connect_button.click();self.wait(self.ready);self.assertNotEqual(self.b.state.snapshot.session,old);self.assertEqual(self.controls['backend'].kinds,[1,2]);self.assertIsNone(self.w.capture)
 def test_main_close_failure_can_retry(self):
  self.connect(True);self.controls['close_fail']=True;self.w.close();self.wait(lambda:self.b.state.close_failed);self.assertTrue(self.w.isVisible());self.assertFalse(self.b._closed)
  self.controls['close_fail']=False;self.w.close();self.wait(lambda:self.b._closed and not self.w.isVisible());self.assertTrue(all(self.join))
 def test_interrupted_handshake_is_async(self):
  self.controls.update(open_entered=threading.Event(),open_gate=threading.Event());self.d.source.setCurrentIndex(1);self.d.port.setText('SYNTHETIC-ONLY');self.d.connect_button.click();self.wait(self.controls['open_entered'].is_set)
  self.w.close();self.assertTrue(self.w.isVisible());fired=[];QtCore.QTimer.singleShot(10,lambda:fired.append(True));self.wait(lambda:bool(fired));self.controls['open_gate'].set();self.wait(lambda:self.b._closed and not self.w.isVisible());self.assertIsNone(self.w.capture)
if __name__=='__main__':unittest.main(verbosity=2)
