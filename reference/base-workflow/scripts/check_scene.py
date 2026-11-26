#!/usr/bin/env python3
"""Offline, fail-closed structural checks. Exit 0=PASS, 1=FAIL, 2=input error."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NOTICE = '【教学模拟｜非真实成员发言或交易记录】'
HEADER = re.compile(r'^(?:([0-9]{2})(男|女)[：:]|===== (资讯助理|David先生) =====)[ \t]*(.*)$')
SUSPECT = re.compile(r'^(?:[0-9０-９]{1,3}(?:男|女)|={2,})')
CLOSE = re.compile(r'闭群|今天(?:的交流)?就到这里|我们(?:下午|晚上|明天)见|祝(?:大家|各位)晚安')
NUMBERS = re.compile(r'(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?')
KEYS = {'mode', 'expected_count', 'existing_count', 'target_count', 'allowed_ids',
        'role_order', 'forbid_close', 'forbidden_literals', 'protected_literals',
        'simulation', 'original_text'}


MAX_SCENE_ID = 52  # 46男、47女 于 2026-11-10 起加入新场；48—52号男于用户2026-11-24确认加入

def load_roster():
    return json.loads((ROOT / 'references/roster.json').read_text(encoding='utf-8'))['genders']


def validate_contract(c, roster):
    if not isinstance(c, dict) or set(c) - KEYS:
        raise ValueError('contract must be an object with known keys')
    if c.get('mode', 'scene') not in ('scene', 'edit'):
        raise ValueError('mode must be scene or edit')
    for k in ('expected_count', 'existing_count', 'target_count'):
        if k in c and (type(c[k]) is not int or c[k] < 0):
            raise ValueError(k + ' must be a non-negative integer')
    if ('existing_count' in c) != ('target_count' in c):
        raise ValueError('existing_count and target_count are required together')
    if 'target_count' in c:
        delta = c['target_count'] - c['existing_count']
        if delta <= 0 or c.get('expected_count', delta) != delta:
            raise ValueError('count conflict; appending requires a positive delta')
    for k in ('forbid_close', 'simulation'):
        if k in c and type(c[k]) is not bool:
            raise ValueError(k + ' must be boolean')
    for k in ('allowed_ids', 'role_order', 'forbidden_literals', 'protected_literals'):
        if k in c and (not isinstance(c[k], list) or
                       any(not isinstance(x, str) or not x for x in c[k])):
            raise ValueError(k + ' must be a list of nonempty strings')
    if 'allowed_ids' in c and any(i not in roster for i in c['allowed_ids']):
        raise ValueError('unknown or non-padded allowed ID')
    if c.get('mode', 'scene') == 'scene' and any(
            int(i) > MAX_SCENE_ID for i in c.get('allowed_ids', []) + c.get('role_order', []) if i in roster):
        raise ValueError('IDs 53–65 are only permitted for explicitly selected local edits')
    if c.get('mode') == 'edit' and 'allowed_ids' not in c:
        raise ValueError('edit requires explicitly selected allowed_ids')
    for role in c.get('role_order', []):
        if role not in ('MEMBER', 'ASSISTANT', 'TEACHER') and role not in roster:
            raise ValueError('unknown role in role_order')
    if 'original_text' in c and (c.get('mode') != 'edit' or not isinstance(c['original_text'], str)):
        raise ValueError('original_text is a string for edit mode only')


def parse(text):
    blocks, errors = [], []
    for n, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        if not line.strip():
            continue
        if line == NOTICE and not blocks and not errors:
            continue
        m = HEADER.fullmatch(line)
        if m:
            ident, gender, staff, body = m.groups()
            role = ident or ('ASSISTANT' if staff == '资讯助理' else 'TEACHER')
            blocks.append({'role': role, 'gender': gender, 'body': body, 'line': n})
        elif SUSPECT.match(line.strip()):
            errors.append(f'line {n}: malformed or obsolete label')
        elif not blocks:
            errors.append(f'line {n}: text before first label')
        else:
            blocks[-1]['body'] += '\n' + line
    if not blocks:
        errors.append('no labeled messages')
    for b in blocks:
        if not b['body'].strip():
            errors.append(f"line {b['line']}: empty message")
    return blocks, errors


def check(text, contract):
    roster = load_roster()
    validate_contract(contract, roster)
    blocks, errors = parse(text)
    allowed = set(contract.get('allowed_ids', [f'{i:02}' for i in range(1, MAX_SCENE_ID + 1)]))
    has_members = any(b['gender'] is not None for b in blocks)
    simulation = contract.get('simulation', True)
    if (simulation or has_members) and not text.lstrip('\ufeff').startswith(NOTICE + '\n'):
        errors.append('missing first-line teaching simulation notice')
    if has_members and not simulation:
        errors.append('numbered fictional dialogue cannot be declared a real record')
    for b in blocks:
        ident = b['role']
        if b['gender'] is not None:
            if ident not in roster:
                errors.append('unknown ID: ' + ident)
            elif b['gender'] != roster[ident]:
                errors.append('gender mismatch: ' + ident)
            if ident not in allowed:
                errors.append('ID outside permitted scope: ' + ident)
    expected = contract.get('expected_count')
    if 'target_count' in contract:
        expected = contract['target_count'] - contract['existing_count']
    if expected is not None and len(blocks) != expected:
        errors.append(f'count mismatch: expected {expected}, got {len(blocks)}')
    actual = [b['role'] for b in blocks]
    if 'role_order' in contract:
        order = contract['role_order']
        if len(actual) != len(order) or any(
            want != got and not (want == 'MEMBER' and got in roster)
            for want, got in zip(order, actual)
        ):
            errors.append('role order mismatch')
    if '**' in text:
        errors.append('Markdown bold in copyable dialogue')
    if contract.get('forbid_close') and CLOSE.search(text):
        errors.append('possible closing beyond stop point; review the flagged wording')
    for literal in contract.get('forbidden_literals', []):
        if literal in text:
            errors.append('forbidden literal present: ' + literal)
    for literal in contract.get('protected_literals', []):
        if literal not in text:
            errors.append('protected literal missing: ' + literal)
    if 'original_text' in contract:
        original, original_errors = parse(contract['original_text'])
        if original_errors:
            raise ValueError('original_text must contain valid nonempty labeled messages')
        if [b['role'] for b in original] != actual:
            errors.append('edit changed selected speaker order')
        before = Counter(NUMBERS.findall('\n'.join(b['body'] for b in original)))
        after = Counter(NUMBERS.findall('\n'.join(b['body'] for b in blocks)))
        if before != after:
            errors.append('edit changed numeric literals')
    bodies = [' '.join(b['body'].split()) for b in blocks]
    warnings = ['repeated complete message; review'] if len(set(bodies)) != len(bodies) else []
    return {
        'status': 'FAIL' if errors else 'PASS', 'count': len(blocks),
        'errors': errors, 'warnings': warnings,
        'scope': 'structure and explicit contract only',
        'not_verified': ['facts', 'source authenticity/freshness', 'semantic meaning',
                         'all paraphrases of stop violations', 'naturalness', 'real-world actions'],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('text', type=Path)
    p.add_argument('--contract', type=Path, required=True)
    args = p.parse_args()
    try:
        result = check(args.text.read_text(encoding='utf-8-sig'),
                       json.loads(args.contract.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError, KeyError) as e:
        print(json.dumps({'status': 'INPUT_ERROR', 'error': str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        if hasattr(_s, 'reconfigure'):
            _s.reconfigure(encoding='utf-8')
    raise SystemExit(main())
