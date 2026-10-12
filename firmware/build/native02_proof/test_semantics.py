#!/usr/bin/env python3
"""Focused semantic controls, independent of outer input-hash refusal."""
import importlib.util, unittest
from pathlib import Path
HOME = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('proof', HOME / 'verify.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
projection = proof.module(HOME / 'projection.py')
relocations = proof.module(HOME / 'relocations.py')
R = '3-mt_control.o::.text.mt_control_receive'
H = '3-mt_control.o::.text.start_capture'
X = '1-mt_capture_link.o::.text.mt_capture_link_arm'
Y = '1-mt_capture_link.o::.text.mt_capture_link_record_now'
Z = '0-acquisition.o::.text.mt_acq_arm'
N = '0-acquisition.o::.text.mt_acq_record_now'

class ProjectionTests(unittest.TestCase):

    def setUp(self):
        self.oldnodes = {R, X, Z}
        self.old = {(R, X), (X, Z)}
        self.newnodes = {R, H, X, Y, Z, N}
        self.new = {(R, H), (H, X), (H, Y), (Y, N), (X, Z)}

    def check(self):
        return projection.project(self.oldnodes, self.old, self.newnodes, self.new)

    def test_valid_arm_and_record_paths(self):
        self.assertEqual(self.check()['collapsed_edges'], [(R, H)])

    def test_unknown_new_node(self):
        self.newnodes.add('unbound')
        self.assertRaises(ValueError, self.check)

    def test_uncovered_direct_edge(self):
        self.new.add((H, Z))
        self.assertRaises(ValueError, self.check)

    def test_helper_reentry(self):
        self.new.add((H, R))
        self.assertRaises(ValueError, self.check)

    def test_hidden_self_edge(self):
        self.new.add((H, H))
        self.assertRaises(ValueError, self.check)

    def test_missing_required_collapse(self):
        self.new.remove((R, H))
        self.assertRaises(ValueError, self.check)

    def test_new_cycle(self):
        self.new.add((N, R))
        self.assertRaises(ValueError, self.check)

class CallbackTests(unittest.TestCase):

    def body(self, kind='profile'):
        field = 528 if kind == 'profile' else 532
        return 'Disassembly of section .text.start_capture:\n' + (' 76: 4639 mov r1, r7\n' if kind == 'start' else '') + f' 78: f8d5 3210 ldr.w r3, [r5, #{field}]\n 7c: f8d5 021c ldr.w r0, [r5, #540]\n 80: 4798 blx r3\n'

    def test_profile(self):
        proof.callback_loads(self.body(), [('start_capture', 128, 'profile')])

    def test_start(self):
        proof.callback_loads(self.body('start'), [('start_capture', 128, 'start')])

    def test_wrong_field(self):
        self.assertRaises(ValueError, proof.callback_loads, self.body().replace('#528', '#532'), [('start_capture', 128, 'profile')])

    def test_wrong_context(self):
        self.assertRaises(ValueError, proof.callback_loads, self.body().replace('#540', '#544'), [('start_capture', 128, 'profile')])

    def test_wrong_call_register(self):
        self.assertRaises(ValueError, proof.callback_loads, self.body().replace('blx r3', 'blx r2'), [('start_capture', 128, 'profile')])

    def test_wrong_now(self):
        self.assertRaises(ValueError, proof.callback_loads, self.body('start').replace('r1, r7', 'r1, r6'), [('start_capture', 128, 'start')])

    def test_wrong_owner_base(self):
        self.assertRaises(ValueError, proof.callback_loads, self.body().replace('[r5,', '[r6,'), [('start_capture', 128, 'profile')])

class RelocationTests(unittest.TestCase):

    def test_call_addend(self):
        self.assertEqual(relocations.branch(bytes.fromhex('fff7feff'), 10), -4)

    def test_tail_addend(self):
        self.assertEqual(relocations.branch(bytes.fromhex('fff7febf'), 30), -4)

    def test_call_tail_swap(self):
        self.assertRaises(ValueError, relocations.branch, bytes.fromhex('fff7febf'), 10)

    def test_interworking_rejected(self):
        self.assertRaises(ValueError, relocations.branch, bytes.fromhex('fff7feef'), 10)

    def test_bad_first_half(self):
        self.assertRaises(ValueError, relocations.branch, bytes(4), 10)
if __name__ == '__main__':
    unittest.main(verbosity=2)
