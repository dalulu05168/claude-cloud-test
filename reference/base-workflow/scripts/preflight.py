#!/usr/bin/env python3
"""Check local bundle capability without claiming platform permissions."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(self_test=False):
    errors = []
    if sys.version_info < (3, 9):
        errors.append('Python 3.9+ required')
    required = ['SKILL.md', 'references/roster.json', 'references/memory.md',
                'references/runtime.md', 'references/writing-examples.md',
                'scripts/check_scene.py', 'scripts/plan_context.py', 'scripts/memory_store.py',
                'scripts/project_session.py', 'scripts/bundle_doctor.py', 'references/session.md',
                'references/acceptance.md', 'manifest.json']
    for f in required:
        if not (ROOT / f).is_file():
            errors.append('missing: ' + f)
    for f in ROOT.rglob('*.md'):
        for link in re.findall(r'\]\(([^)]+)\)', f.read_text(encoding='utf-8')):
            if '://' not in link and not link.startswith('#'):
                target = (f.parent / link.split('#')[0]).resolve()
                if ROOT not in target.parents or not target.is_file():
                    errors.append('invalid bundled reference: ' + link)
    for f in (ROOT / 'scripts').glob('*.py'):
        try:
            ast.parse(f.read_text(encoding='utf-8'))
        except SyntaxError:
            errors.append('invalid Python syntax: ' + f.name)
    if not errors:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from bundle_doctor import verify
        errors.extend(verify(ROOT)['errors'])
    tests = 'NOT_RUN'
    if self_test and not errors:
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-m', 'unittest', 'discover',
                                 '-s', str(ROOT / 'tests'), '-v'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        tests = {'exit_code': result.returncode, 'log': (result.stdout + result.stderr)[-12000:]}
        if result.returncode:
            errors.append('self-test failed')
    return {'status': 'LOCAL_READY' if not errors else 'LOCAL_BLOCKED',
            'python': sys.version.split()[0], 'errors': errors, 'self_test': tests,
            'platform_permissions': 'NOT_TESTED', 'persistent_storage': 'NOT_TESTED',
            'claude_model_execution': 'NOT_TESTED',
            'note': 'Run once after install/update, not for each short edit.'}


if __name__ == '__main__':
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        if hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    result = run(args.self_test)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status'] == 'LOCAL_READY' else 1)
