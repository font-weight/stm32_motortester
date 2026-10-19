#!/usr/bin/env python3
"""Replay one separately retained proof package; never build or accept firmware."""
import sys
if sys.flags.optimize != 0 or not sys.flags.isolated or not sys.flags.dont_write_bytecode:
    sys.stderr.write("REJECTED_INTERPRETER_POLICY: require python3 -I -B with assertions enabled; -O/-OO are forbidden\n")
    raise SystemExit(2)
import argparse
import hashlib
import json
import pathlib
import subprocess


def sha(path):
    if not path.is_file():
        raise ValueError(f"Required regular file is unavailable: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_hash(path, expected):
    if sha(path) != expected:
        raise ValueError(f"SHA256 mismatch: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proof-dir', required=True, type=pathlib.Path,
                        help='External extracted portable-proof directory; it receives replay outputs')
    parser.add_argument('--objdump', required=True, type=pathlib.Path)
    args = parser.parse_args()
    reference = json.loads(pathlib.Path(__file__).with_name('reference.json').read_text())
    proof = args.proof_dir.resolve(strict=True)
    objdump = args.objdump.resolve(strict=True)
    require_hash(proof / 'manifest.json', reference['proof_manifest_sha256'])
    require_hash(proof / 'verify_package.py', reference['proof_verifier_sha256'])
    require_hash(proof / 'REVIEW_RU.md', reference['proof_report_sha256'])
    require_hash(proof / 'image/DIAGNOSTIC_UNQUALIFIED_NEVER_FLASH.elf', reference['elf_sha256'])
    require_hash(objdump, reference['objdump_sha256'])
    manifest = json.loads((proof / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        rel = pathlib.PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('Invalid proof manifest path')
        require_hash(proof / name, expected)
    return subprocess.run([sys.executable, '-I', '-B', str(proof / 'verify_package.py'),
                           '--objdump', str(objdump)], check=False).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write(f'REJECTED_FROZEN_PROOF_REFERENCE: {exc}\n')
        raise SystemExit(1)
