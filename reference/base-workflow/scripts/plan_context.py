#!/usr/bin/env python3
"""Offline context selection; does not read external sources or generate prose."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    'EDIT': ['writer'], 'CONTINUE': ['director', 'writer'],
    'SCENE': ['director', 'writer'], 'ACTIVITY': ['assistant'],
    'LESSON': ['teacher'], 'NEWS': ['assistant'], 'QA': ['contracts'],
    'RESUME': ['session'], 'MEMORY': ['memory'], 'SETUP': ['runtime'],
}


def plan(task, ids=(), extra_roles=(), evening=False):
    if task not in ROUTES:
        raise ValueError('unknown task')
    roster = json.loads((ROOT / 'references/roster.json').read_text(encoding='utf-8'))['genders']
    ids = list(dict.fromkeys(ids))
    if any(i not in roster for i in ids):
        raise ValueError('IDs must be zero-padded 01–65')
    modules = list(ROUTES[task])
    # 晚间课、场景、续写都会写到成员插话：不点编号也要读成员口吻规则
    if ids or (evening and task in ('LESSON', 'SCENE', 'CONTINUE')):
        modules.append('members')
    for role in extra_roles:
        if role not in ('assistant', 'teacher'):
            raise ValueError('extra role must be assistant or teacher')
        modules.append(role)
    files = ['SKILL.md'] + (['references/evening.md'] if evening else []) + ['references/' + m + '.md' for m in dict.fromkeys(modules)]
    return {
        'task': task, 'read_once': files,
        'selected_genders': {i: roster[i] for i in ids},
        'reference_characters': sum(len((ROOT / p).read_text(encoding='utf-8')) for p in files),
        'external_reads': 'Only if required by this task or higher-priority instructions',
        'note': 'Selection only; not source freshness, fact checking, or a billing estimate',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', choices=ROUTES, required=True)
    p.add_argument('--ids', default='')
    p.add_argument('--roles', default='')
    p.add_argument('--evening', action='store_true', help='Use the evening override (2.3.0+); also loads member voice rules')
    a = p.parse_args()
    try:
        result = plan(a.task, [i.strip() for i in a.ids.split(',') if i.strip()],
                      [i.strip() for i in a.roles.split(',') if i.strip()], evening=a.evening)
    except (ValueError, OSError) as e:
        p.error(str(e))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        if hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    main()
