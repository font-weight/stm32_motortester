#!/usr/bin/env python3
"""Tooling-only regressions; do not alter the native register/test semantics."""
import json
from pathlib import Path
import tempfile
from run import require_unchanged, snapshot_sources, validate_output_path

with tempfile.TemporaryDirectory(prefix='fsdev-tooling-') as td:
    root=Path(td)
    for name in ['inputs','src','tools']:
        (root/name).mkdir()
        (root/name/'sample').write_text(name)
    candidate=root/'candidate.c';candidate.write_text('candidate')
    rejected=0
    for name in ['inputs','src','tools']:
        for path in [root/name,root/name/'not-created'/'nested']:
            try: validate_output_path(path,root)
            except ValueError: rejected+=1
            else: raise AssertionError('protected output accepted')
            assert not (root/name/'not-created').exists()
    (root/'alias').symlink_to(root/'src',target_is_directory=True)
    try: validate_output_path(root/'alias'/'nested',root)
    except ValueError: rejected+=1
    else: raise AssertionError('symlink output accepted')
    assert validate_output_path(root/'evidence'/'fresh',root)==root/'evidence'/'fresh'
    before=snapshot_sources(candidate,root);require_unchanged(before,snapshot_sources(candidate,root))
    (root/'src'/'sample').write_text('changed')
    try: require_unchanged(before,snapshot_sources(candidate,root))
    except RuntimeError: pass
    else: raise AssertionError('source mutation undetected')
    (root/'src'/'sample').write_text('src');candidate.write_text('changed')
    try: require_unchanged(before,snapshot_sources(candidate,root))
    except RuntimeError: pass
    else: raise AssertionError('candidate mutation undetected')
    print(json.dumps({'protected_paths_rejected_before_mkdir':rejected,'no_source_dirs_created':True,
        'symlink_resolved':True,'source_and_candidate_mutations_detected':True,'unchanged_allowed':True}))
