"""Read-only CA-CTRL-4 host observation on exact HOST-006 477057aa.
Loads Critic-B's earlier helper only; no production or product tests are edited.
"""
import importlib.util,inspect,ast,struct,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('critic_helpers','/workspace/scratch/d2a4dc03255e/motortester/host/reviews/evidence/session-477/critic-probes.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
from motortester.session import SessionController,Config,SessionError,FaultCode

class StopBarrierProbes(unittest.TestCase):
 def reject_new(self,c):
  before=c.snapshot();ticket=c.prepare_tx(); before=c.snapshot()
  for method,args in [(c.configure,(Config(),0)),(c.arm,(0,)),(c.stop,(0,)),(c.start,(h.NONCE+1,0))]:
   with self.assertRaises(SessionError):method(*args)
   self.assertEqual(c.snapshot(),before);self.assertIs(c.prepare_tx(),ticket)
 def test_older_replies_consumed_barrier_until_ack(self):
  for stage in ['CAPS','CONFIG','ARM']:
   for accepted in [0,1,544]:
    c=h.at(stage);t=c.prepare_tx();c.written(t,min(accepted,len(t.encoded)),0);c.stop(0)
    self.reject_new(c)
    if c.prepare_tx().frame.message_type!=5:ordinary=h.send(c)
    else:ordinary=t.frame
    stop=h.send(c)
    self.reject_new(c)
    payload={'CAPS':h.caps(),'CONFIG':struct.pack('<I',1)+ordinary.payload,'ARM':struct.pack('<II',ordinary.request_id,1)}[stage]
    events=h.rx(c,{'CAPS':130,'CONFIG':131,'ARM':132}[stage],payload,ordinary.request_id)
    if stage=='ARM':self.assertEqual(events,())
    self.assertFalse(c.snapshot().pending_request_id);self.assertTrue(c.snapshot().stop_barrier)
    self.reject_new(c)
    g=c.snapshot().generation
    self.assertEqual(h.kinds(h.rx(c,133,struct.pack('<III',g,0,0),stop.request_id)),('STOPPED',))
    self.assertEqual(c.configure(Config(),0),())
 def test_barrier_while_only_stop_is_queued_partial_or_sent(self):
  for count in [0,1,544]:
   c=h.at('ARMED');c.stop(0);t=c.prepare_tx();c.written(t,min(count,len(t.encoded)),0)
   self.reject_new(c)
 def test_fresh_hello_recovery_after_pending_stop(self):
  for mode in ['timeout','transport_lost','unknown_write','downstream']:
   c=h.at('STOP');old=c.prepare_tx();c.written(old,len(old.encoded),0)
   if mode=='timeout':e=c.tick(1000)
   elif mode=='downstream':e=c.protocol_fault(FaultCode.DOWNSTREAM,1000)
   else:e=getattr(c,mode)(1000)
   self.assertEqual(h.kinds(e),('RECOVERY',));self.assertIsNone(c.prepare_tx())
   self.assertTrue(e[0].reset_transport)
   self.assertEqual(c.start(h.NONCE+1,1000),())
   t=c.prepare_tx();self.assertEqual((t.frame.message_type,t.frame.session,t.frame.sequence,t.frame.request_id),(1,0,1,1))
   self.assertEqual(t.frame.payload,struct.pack('<Q',h.NONCE+1))
   with self.assertRaises(SessionError):c.written(old,0,1000)
 def test_public_scheduling_surface_and_get_caps_origin(self):
  public=sorted(n for n,m in inspect.getmembers(SessionController,inspect.isfunction) if not n.startswith('_'))
  self.assertEqual(public,['arm','configure','prepare_tx','protocol_fault','receive','snapshot','start','stop','tick','transport_lost','unknown_write','written'])
  tree=ast.parse(inspect.getsource(SessionController))
  origins=[]
  for method in tree.body[0].body:
   if isinstance(method,ast.FunctionDef):
    for node in ast.walk(method):
     if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='_queue' and node.args and isinstance(node.args[0],ast.Constant):
      origins.append((method.name,node.args[0].value))
  self.assertEqual(sorted(origins),[('_reply',2),('arm',4),('configure',3),('stop',5)])
  print('Public API:',public,'; _queue origins:',origins)

unittest.main(verbosity=2)
