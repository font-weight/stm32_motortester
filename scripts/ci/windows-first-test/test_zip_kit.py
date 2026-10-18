"""Inert envelope tests; no application, Windows, serial or upload execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
import zipfile
from unittest import mock

HERE = Path(__file__).parent

def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

z = module('zip_kit')
f = module('test_retain')

class ZipTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f.RetentionTests('test_exact_bytes_and_original_eight_checksum_preserved')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.receipt = self.fixture.invoke()
        self.output = self.fixture.root / 'kit.zip'

    def run_zip(self, **changes):
        args = dict(kit=str(self.fixture.output), output=str(self.output),
                    outer_manifest_sha256=self.receipt['outer_manifest']['sha256'])
        args.update(changes)
        return z.package(**args)

    def test_success_exact_members(self):
        result = self.run_zip()
        self.assertEqual(result['status'], 'KIT_ZIP_BYTES_VERIFIED_ONLY')
        self.assertFalse(result['physical_verified'])
        self.assertFalse(result['distribution_authorized_by_this_helper'])
        self.assertEqual(result['zip']['sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        with zipfile.ZipFile(self.output) as a:
            self.assertEqual(len(a.infolist()), self.receipt['payload_files'] + 2)
            self.assertEqual(a.read('build/source.zip'), self.fixture.payload['source.zip'])
            self.assertIsNone(a.testzip())

    def test_deterministic(self):
        self.run_zip()
        other = self.fixture.root / 'other.zip'
        self.run_zip(output=str(other))
        self.assertEqual(self.output.read_bytes(), other.read_bytes())

    def test_no_overwrite(self):
        self.output.write_bytes(b'KEEP')
        with self.assertRaises(ValueError): self.run_zip()
        self.assertEqual(self.output.read_bytes(), b'KEEP')

    def test_hash_mismatch(self):
        with self.assertRaises(ValueError): self.run_zip(outer_manifest_sha256='0'*64)
        self.assertFalse(self.output.exists())

    def test_payload_changed(self):
        (self.fixture.output/'build/source.zip').write_bytes(b'CHANGED')
        with self.assertRaises(ValueError): self.run_zip()
        self.assertFalse(self.output.exists())

    def test_extra_file(self):
        (self.fixture.output/'unlisted.txt').write_bytes(b'NO')
        with self.assertRaises(ValueError): self.run_zip()
        self.assertFalse(self.output.exists())

    def test_output_inside_kit(self):
        with self.assertRaises(ValueError): self.run_zip(output=str(self.fixture.output/'oops.zip'))

    def test_mode_mismatch(self):
        with self.assertRaises(ValueError): self.run_zip(mode='diagnostics')

    def test_symlink_member(self):
        p=self.fixture.output/'build/source.zip'
        target=self.fixture.root/'source-target'
        target.write_bytes(p.read_bytes());p.unlink();p.symlink_to(target)
        with self.assertRaises(ValueError): self.run_zip()

    def test_zip_cap(self):
        with mock.patch.object(z,'MAX_ZIP',20):
            with self.assertRaises(ValueError): self.run_zip()
        self.assertTrue(self.output.exists()) # partial evidence is preserved

    def test_helper_pin(self):
        with mock.patch.object(z,'RETAINER_SHA256','0'*64):
            with self.assertRaises(ValueError): self.run_zip()
        self.assertFalse(self.output.exists())

    def test_helper_does_not_execute_pyc(self):
        import importlib._bootstrap_external as boot
        import importlib.util
        import os
        helper_dir = self.fixture.root / 'helper-copy'
        helper_dir.mkdir()
        source = helper_dir / 'retain.py'
        source.write_bytes((HERE / 'retain.py').read_bytes())
        cached = Path(importlib.util.cache_from_source(str(source)))
        cached.parent.mkdir()
        st = source.stat()
        poisoned = compile("raise RuntimeError('INERT STALE CACHE EXECUTED')", str(source), 'exec')
        cached.write_bytes(boot._code_to_timestamp_pyc(poisoned, int(st.st_mtime), st.st_size))
        with mock.patch.object(z, '__file__', str(helper_dir / 'zip_kit.py')):
            self.assertEqual(z.helper().APP_COMMIT, f.r.APP_COMMIT)

    def test_fsync_failure(self):
        with mock.patch.object(z.os,'fsync',side_effect=OSError('fixture')):
            with self.assertRaises(OSError): self.run_zip()
        self.assertTrue(self.output.exists())

if __name__ == '__main__': unittest.main()
