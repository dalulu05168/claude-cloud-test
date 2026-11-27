#!/usr/bin/env python3
"""草稿与已采用稿对比（离线、只读）。用户采用并改过稿子后，用它找出自己的不足。

用法：
  python adoption_diff.py 草稿.txt --docjson read_doc保存的文件 --tab 2026-11-25 [--part 晚|上午|下午] [--json]

只输出差异和机械归类（被删外语词、被删引号、被删“有成员”、整句删除、整句新增），
不改文档、不写规则。归类后由人把确认的教训并入对应规则文件，并按 CLAUDE.md「规则维护」
同时删除被取代的旧规则。退出码：0=有差异，1=无差异，2=输入错误。
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

FOREIGN = re.compile(r'[A-Za-zĂÂÎȘȚăâîșț]{3,}')
QUOTES = '“”"「」'
PARTS = {'上午': '【上午', '下午': '【下午', '晚': '【晚'}


def tab_lines(doc, title):
    d = doc.get('content', doc)

    def walk(ts):
        for t in ts:
            yield t
            yield from walk(t.get('childTabs', []))
    for t in walk(d['tabs']):
        if t['tabProperties'].get('title') == title:
            out = []
            for el in t['documentTab']['body']['content']:
                if 'paragraph' in el:
                    out.append(''.join(r.get('textRun', {}).get('content', '')
                                       for r in el['paragraph']['elements']).rstrip('\n'))
            return out
    raise ValueError('找不到页签：' + title)


def cut(lines, part):
    marks = [(i, l.strip()) for i, l in enumerate(lines) if l.strip().startswith('【')]
    for i, m in marks:
        if m.startswith(PARTS[part]):
            nxt = [j for j, _ in marks if j > i]
            return lines[i + 1:(nxt[0] if nxt else len(lines))]
    return lines


def norm(ls):
    return [re.sub(r'\s+', '', x) for x in ls if x.strip()]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('draft')
    ap.add_argument('--docjson', required=True)
    ap.add_argument('--tab', required=True)
    ap.add_argument('--part', choices=list(PARTS), default='晚')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    try:
        draft = Path(a.draft).read_text(encoding='utf-8-sig').splitlines()
        doc = json.loads(Path(a.docjson).read_text(encoding='utf-8'))
        adopted = cut(tab_lines(doc, a.tab), a.part)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({'status': 'INPUT_ERROR', 'error': str(e)}, ensure_ascii=False))
        return 2
    d, o = norm(draft), norm(adopted)
    removed, added, changed = [], [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, d, o, autojunk=False).get_opcodes():
        if tag == 'delete':
            removed += d[i1:i2]
        elif tag == 'insert':
            added += o[j1:j2]
        elif tag == 'replace':
            for k in range(max(i2 - i1, j2 - j1)):
                x = d[i1 + k] if i1 + k < i2 else ''
                y = o[j1 + k] if j1 + k < j2 else ''
                changed.append((x, y)) if x and y else (removed if x else added).append(x or y)
    flags = []
    for x, y in changed:
        fx = set(FOREIGN.findall(x)) - set(FOREIGN.findall(y))
        if fx and not re.search(r'[A-Z]{2,5}\b', ' '.join(fx)):
            flags.append(('删外语词', ','.join(sorted(fx)), y))
        if any(q in x for q in QUOTES) and not any(q in y for q in QUOTES):
            flags.append(('删引号', x[:40], y[:40]))
        if '有成员' in x and '有成员' not in y:
            flags.append(('删“有成员”', x[:40], y[:40]))
    for x in removed:
        flags.append(('整句被删', x[:60], ''))
    for y in added:
        flags.append(('整句新增', '', y[:60]))
    res = {'status': 'DIFF' if (changed or removed or added) else 'SAME',
           'changed': len(changed), 'removed': len(removed), 'added': len(added),
           'flags': [dict(kind=k, draft=x, adopted=y) for k, x, y in flags]}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print('状态：%s｜改写 %d｜删除 %d｜新增 %d' % (res['status'], len(changed), len(removed), len(added)))
        for f in res['flags']:
            print('[%s] %s → %s' % (f['kind'], f['draft'], f['adopted']))
    return 0 if res['status'] == 'DIFF' else 1


if __name__ == '__main__':
    sys.exit(main())
