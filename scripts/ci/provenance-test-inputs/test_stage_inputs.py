"""Tests for exact-byte staging, using explicitly supplied pinned input data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('stager', HERE / 'stage_inputs.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ROOT = Path(os.environ['STAGING_TEST_EXTERNAL_ROOT']).resolve()
CHECKOUT = HERE.parents[2]
MANIFEST = json.loads((HERE / 'inputs.json').read_text())


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        values = {'checkout': str(CHECKOUT), 'output': str(self.base / 'staged')}
        values.update({row['input']: str(ROOT / row['destination'])
                       for row in MANIFEST['files'] if row['input'] != 'checkout'})
        self.args = argparse.Namespace(**values)

    def test_exact_staging(self):
        receipt = m.stage(self.args)
        self.assertEqual(receipt['file_count'], 23)
        self.assertEqual(receipt['payload_bytes'], 1789828)
        self.assertIs(receipt['tests_executed'], False)
        for row in MANIFEST['files']:
            data = (Path(self.args.output) / row['destination']).read_bytes()
            self.assertEqual(m.digest(data), row['sha256'])

    def test_wrong_hash_leaves_no_output(self):
        p = self.base / 'wrong.py'
        p.write_bytes(b'x' * 6418)
        self.args.original_init = str(p)
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            m.stage(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_wrong_size_leaves_no_output(self):
        p = self.base / 'short.py'
        p.write_bytes(b'x')
        self.args.original_init = str(p)
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            m.stage(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_existing_output_untouched(self):
        out = Path(self.args.output)
        out.mkdir()
        (out / 'keep').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'already exist'):
            m.stage(self.args)
        self.assertEqual((out / 'keep').read_bytes(), b'keep')

    def test_missing_input(self):
        self.args.observer = str(self.base / 'missing')
        with self.assertRaises(OSError):
            m.stage(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_manifest_pin(self):
        with patch.object(m, 'MANIFEST_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'unrecognized input manifest'):
                m.stage(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_symlink_input(self):
        p = self.base / 'link.py'
        try:
            p.symlink_to(self.args.original_init)
        except OSError as error:
            self.skipTest('symlink unavailable: ' + str(error))
        self.args.original_init = str(p)
        with self.assertRaisesRegex(ValueError, 'symlink or reparse'):
            m.stage(self.args)

    def test_symlink_output_parent(self):
        p = self.base / 'alias'
        try:
            p.symlink_to(self.base, target_is_directory=True)
        except OSError as error:
            self.skipTest('symlink unavailable: ' + str(error))
        self.args.output = str(p / 'staged')
        with self.assertRaisesRegex(ValueError, 'canonical'):
            m.stage(self.args)


if __name__ == '__main__':
    unittest.main()
