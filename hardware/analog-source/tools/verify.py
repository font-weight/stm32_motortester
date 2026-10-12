#!/usr/bin/env python3
"""Complete offline export into a new directory; never update editable inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ('option-a-dc', 'option-b-ac')
DIAGRAMS = ('structural', 'functional')
SOURCES = tuple('schematics/' + name for name in (
    'option-a-dc.kicad_sch', 'option-a-dc.kicad_pro',
    'option-b-ac.kicad_sch', 'option-b-ac.kicad_pro',
    'AnalogDraft.kicad_sym', 'sym-lib-table')) + tuple(v + '-bom.csv' for v in VARIANTS)
TOOLS = ('calculate.py', 'generate_schematics.py', 'generate_diagrams.py', 'check_netlists.py')
REQUIRED = tuple('exports/' + name + ext for name in VARIANTS + DIAGRAMS for ext in ('.svg', '.pdf')) + tuple('evidence/' + v + ext for v in VARIANTS for ext in ('.net', '-erc.rpt')) + ('evidence/calculations.json',)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12+ required by the unchanged diagram generator")
    if not args.out.is_absolute():
        parser.error('--out must be absolute')
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents or out in ROOT.parents:
        parser.error('--out must be outside the source tree')
    if args.out.exists() or args.out.is_symlink() or not out.parent.is_dir():
        parser.error('--out must not exist and its parent must exist')
    programs = {name: shutil.which(name) for name in ('kicad-cli', 'inkscape')}
    missing = [name for name, path in programs.items() if not path]
    if missing:
        parser.error('missing required exporters: ' + ', '.join(missing))
    inputs = SOURCES + tuple('tools/' + t for t in TOOLS) + ('tools/verify.py', 'tools/verify.sh')
    for name in inputs:
        if not (ROOT / name).is_file() or (ROOT / name).is_symlink():
            parser.error('missing or symlink input: ' + name)
    before = {name: digest(ROOT / name) for name in inputs}
    out.mkdir()  # Exclusive creation; existing runs are never replaced.
    result = {'status': 'FAILED', 'hardware_validated': False, 'source_sha256': before,
              'required_artifacts': list(REQUIRED), 'commands': []}
    stage = out / 'candidate'
    env = dict(os.environ, TZ='UTC', PYTHONOPTIMIZE='')
    for key in ('XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME'):
        path = out / key.lower()
        path.mkdir()
        env[key] = str(path)
    def run(argv):
        number = len(result['commands'])
        record = {'argv': [str(x) for x in argv], 'log': f'{number:02d}.log'}
        result['commands'].append(record)
        with (out / record['log']).open('wb') as log:
            completed = subprocess.run(record['argv'], cwd=stage, env=env, stdout=log, stderr=subprocess.STDOUT)
        record['returncode'] = completed.returncode
        if completed.returncode:
            raise RuntimeError('command failed; see ' + record['log'])
    try:
        for name in inputs:
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        for name in ('evidence', 'exports'):
            (stage / name).mkdir()
        run([sys.executable, '--version'])
        run([programs['kicad-cli'], 'version'])
        run([programs['inkscape'], '--version'])
        run([sys.executable, stage / 'tools/calculate.py'])
        generated = out / 'regenerated'
        (generated / 'tools').mkdir(parents=True)
        shutil.copyfile(stage / 'tools/generate_schematics.py', generated / 'tools/generate_schematics.py')
        run([sys.executable, generated / 'tools/generate_schematics.py'])
        for name in SOURCES:
            if (stage / name).read_bytes() != (generated / name).read_bytes():
                raise RuntimeError('source/generator drift: ' + name)
        run([sys.executable, stage / 'tools/generate_diagrams.py'])
        for variant in VARIANTS:
            sch = stage / 'schematics' / (variant + '.kicad_sch')
            run([programs['kicad-cli'], 'sch', 'erc', sch, '--severity-all', '--exit-code-violations', '-o', stage / 'evidence' / (variant + '-erc.rpt')])
            run([programs['kicad-cli'], 'sch', 'export', 'netlist', sch, '-o', stage / 'evidence' / (variant + '.net')])
            run([programs['kicad-cli'], 'sch', 'export', 'svg', sch, '-o', str(stage / 'exports') + '/'])
            run([programs['kicad-cli'], 'sch', 'export', 'pdf', sch, '--exclude-pdf-property-popups', '--exclude-pdf-hierarchical-links', '-o', stage / 'exports' / (variant + '.pdf')])
        run([sys.executable, stage / 'tools/check_netlists.py'])
        for name in DIAGRAMS:
            run([programs['inkscape'], stage / 'exports' / (name + '.svg'), '--export-type=pdf', '--export-filename=' + str(stage / 'exports' / (name + '.pdf'))])
        artifacts = {}
        for name in REQUIRED:
            path = stage / name
            if not path.is_file() or path.is_symlink() or not path.stat().st_size:
                raise RuntimeError('missing/empty required artifact: ' + name)
            if path.suffix == '.svg' and ET.parse(path).getroot().tag != '{http://www.w3.org/2000/svg}svg':
                raise RuntimeError('invalid SVG root: ' + name)
            if path.suffix == '.pdf' and not path.read_bytes().startswith(b'%PDF-'):
                raise RuntimeError('invalid PDF header: ' + name)
            if path.suffix == '.json':
                json.loads(path.read_text())
            artifacts[name] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        if before != {name: digest(ROOT / name) for name in inputs}:
            raise RuntimeError('source changed during verification')
        result.update(status='COMPLETE_EXPORT_CHECKS_PASS', artifacts=artifacts)
    except Exception as error:
        result['error'] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        (out / 'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(out / 'RESULTS.json')
    return 0 if result['status'] == 'COMPLETE_EXPORT_CHECKS_PASS' else 1

if __name__ == '__main__':
    sys.exit(main())
