#!/usr/bin/env python3
"""Verify the delivered E2 package and replay it without modifying source files."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

import fitz
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'hardware/functional-diagram'
PDF = 'Мотортестер_функциональная_схема_Э2.pdf'
SVG = 'Мотортестер_функциональная_схема_Э2.svg'
DOCX = 'Описание_функциональной_схемы_мотортестера.docx'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_files():
    require(PACKAGE.is_dir() and not PACKAGE.is_symlink() and
            not PACKAGE.parent.is_symlink(), 'package must be a real directory')
    files = {}
    for path in PACKAGE.rglob('*'):
        require(not path.is_symlink(), 'package symlink is forbidden: ' + str(path))
        if path.is_dir():
            continue
        require(path.is_file(), 'non-regular package entry: ' + str(path))
        files[str(path.relative_to(PACKAGE))] = path
    return files


def main():
    before = {name: digest(path) for name, path in package_files().items()}
    manifest = json.loads((PACKAGE / 'DELIVERY_SHA256.json').read_text(encoding='utf-8'))
    require(manifest['scope'] == 'project-functional-diagram-E2', 'unexpected manifest scope')
    require(manifest['hardware_validated'] is False, 'hardware claim is not validated')
    entries = manifest['files']
    require(set(entries) == set(before) - {'README_RU.md', 'DELIVERY_SHA256.json'},
            'manifest coverage does not equal delivered package inventory')
    for name, expected in entries.items():
        require(before[name] == expected, 'hash mismatch: ' + name)
    with tempfile.TemporaryDirectory(prefix='functional diagram replay ') as temp:
        replay = Path(temp) / 'clean source package'
        replay.mkdir()
        for name, path in package_files().items():
            if name.startswith('output/'):
                continue
            target = replay / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        require(not (replay / 'output').exists(), 'replay output must start absent')
        require(not (replay / 'qa').exists(), 'replay qa must start absent')
        # Deliberately run outside the package to verify source-relative paths.
        for script in ('build_scheme.py', 'build_description.py'):
            subprocess.run([sys.executable, str(replay / script)], cwd=temp, check=True,
                           timeout=120)
        original, rebuilt = PACKAGE / 'output', replay / 'output'
        require((original / SVG).read_bytes() == (rebuilt / SVG).read_bytes(), 'SVG bytes differ')
        old_pdf, new_pdf = PdfReader(original / PDF, strict=True), PdfReader(rebuilt / PDF, strict=True)
        require(len(old_pdf.pages) == len(new_pdf.pages) == 1, 'PDF must have exactly one page')
        for doc in (old_pdf, new_pdf):
            page = doc.pages[0]
            require(abs(float(page.mediabox.width) * 25.4 / 72 - 420) < .01 and
                    abs(float(page.mediabox.height) * 25.4 / 72 - 297) < .01, 'PDF is not A3 landscape')
        require(old_pdf.pages[0].get_contents().get_data() == new_pdf.pages[0].get_contents().get_data(),
                'PDF page content differs')
        with fitz.open(original / PDF) as left, fitz.open(rebuilt / PDF) as right:
            lp = left[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            rp = right[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            require((lp.width, lp.height, lp.n) == (rp.width, rp.height, rp.n), 'PDF raster geometry differs')
            require(lp.samples == rp.samples, 'PDF rendered pixels differ')
        with zipfile.ZipFile(original / DOCX) as left, zipfile.ZipFile(rebuilt / DOCX) as right:
            require(left.testzip() is None and right.testzip() is None, 'DOCX ZIP CRC failure')
            require(len(left.namelist()) == len(set(left.namelist())) and
                    len(right.namelist()) == len(set(right.namelist())), 'duplicate DOCX members')
            require(set(left.namelist()) == set(right.namelist()), 'DOCX members differ')
            for name in left.namelist():
                require(left.read(name) == right.read(name), 'DOCX content differs: ' + name)
    after = {name: digest(path) for name, path in package_files().items()}
    require(before == after, 'source package changed during checking')
    print(json.dumps({'status': 'PASS_DOCUMENT_PACKAGE_REPLAY_ONLY',
                      'manifest_files': len(entries), 'svg_bytes_equal': True,
                      'pdf_content_and_raster_equal': True, 'docx_member_payloads_equal': True,
                      'source_unchanged': True, 'hardware_or_software_acceptance': False}))


if __name__ == '__main__':
    main()
