"""Reviewer-only check: fill side effects and failure branch survive -O."""
import importlib.util
import pathlib
import sys
import types
import unittest
spec=importlib.util.spec_from_file_location('candidate_test_window',pathlib.Path(sys.argv[1])/'host/tests/test_window.py')
candidate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
sys.argv=sys.argv[:1]
class HelperDeltaProbe(unittest.TestCase):
    def test_fill_executes_nine_frames_and_keeps_explicit_failure_branch(self):
        calls=[]
        fake=types.SimpleNamespace(feed=lambda frame,now: calls.append((frame,now)))
        candidate.fill(fake)
        self.assertEqual(len(calls),9)
        self.assertEqual([now for _,now in calls],list(range(1,10)))
        self.assertEqual(sum((len(frame.payload)-12)//2 for frame,_ in calls),2048)
        failed=types.SimpleNamespace(feed=lambda *args: types.SimpleNamespace(reason='expected-probe'))
        with self.assertRaisesRegex(AssertionError,'expected-probe'):
            candidate.fill(failed)
if __name__=='__main__':
    unittest.main(verbosity=2)
