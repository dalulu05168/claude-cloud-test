#!/usr/bin/env python3
"""Read-only bundle integrity, duplicate entrypoint and snapshot inspection."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_store as m

ROOT = Path(__file__).resolve().parents[1]
NAMES = {'westcott-efficient-workflow', 'westcott-control', 'westcott-director',
         'westcott-writer', 'westcott-members', 'westcott-assistant', 'westcott-teacher',
         'westcott-scene-writing', 'westcott-role-craft', 'independent-writing',
         'westcott-evening-control', 'westcott-evening-courseware', 'westcott-evening-teacher',
         'westcott-evening-interaction', 'westcott-evening-review'}


def verify(root=ROOT):
    manifest = m.read_json(root / 'manifest.json')
    errors = []
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            errors.append('missing/invalid path: ' + name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append('changed: ' + name)
    return {'status': 'BUNDLE_OK' if not errors else 'BUNDLE_CHANGED',
            'version': manifest['version'], 'root': str(root.resolve()), 'errors': errors,
            'note': 'Checks packaged files, not enabled status or actual model invocation; hashes are not signatures.'}


def scan(directory):
    rows = []
    for path in sorted(directory.rglob('SKILL.md')):
        match = re.search(r'^name:\s*[\"\x27]?([a-z0-9-]+)', path.read_text(encoding='utf-8-sig'), re.M)
        if match and match[1] in NAMES:
            rows.append({'name': match[1], 'file': str(path.resolve()), 'enabled': 'NOT_VERIFIED'})
    return rows


def snapshots(directory):
    rows = []
    for path in sorted(directory.glob('*.json')):
        try:
            env = m.read_json(path)
            m.require(isinstance(env, dict) and env.get('format') == 'westcott-memory-v1', 'not a memory snapshot')
            state = m.validate_store(env['data'])
            m.require(env['sha256'] == m.digest(state), 'checksum mismatch')
            rows.append({'file': str(path.resolve()), 'project_id': state['project_id'],
                         'revision': state['revision'], 'history_entries': len(state['history']), 'status': 'VALID'})
        except (OSError, ValueError, TypeError, KeyError) as e:
            rows.append({'file': str(path.resolve()), 'status': 'INVALID_OR_OTHER', 'error': str(e)})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scan-root', type=Path, help='Only scan an explicitly selected skill directory')
    p.add_argument('--snapshots', type=Path, help='List JSON snapshots in this directory without reading their text aloud')
    a = p.parse_args()
    try:
        result = verify()
        if a.scan_root is not None:
            m.require(a.scan_root.is_dir(), 'scan root does not exist')
            result['entrypoints'] = scan(a.scan_root)
        if a.snapshots is not None:
            m.require(a.snapshots.is_dir(), 'snapshot directory does not exist')
            result['snapshots'] = snapshots(a.snapshots)
    except (OSError, ValueError, TypeError, KeyError) as e:
        print(json.dumps({'status': 'ERROR', 'error': str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'BUNDLE_OK' else 1


if __name__ == '__main__':
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        if hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    raise SystemExit(main())
