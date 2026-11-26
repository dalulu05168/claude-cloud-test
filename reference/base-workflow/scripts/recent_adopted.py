#!/usr/bin/env python3
"""最近几晚已采用稿摘要（离线、只读）。写晚场前先读它，不必整份读谷歌文档。

用法：
  python recent_adopted.py --docjson read_doc保存的文件 --tab 2026-11-25 [--days 2] [--json]

从 --tab 之前、有晚场内容的日期页签里取最近 --days 晚，每晚列出：
开场句、开场题、交棒句、David 块数、成员条数、收尾块（先生最后一块与助理收尾）。
只摘录文档里已有的原文，不判断好坏，也不改文档。
退出码：0=找到，1=一晚也没找到，2=输入错误。
"""
import argparse
import json
import re
import sys
from pathlib import Path

LABEL = re.compile(r'^\s*===== (资讯助理|[^=]{1,12}先生) =====\s*$')
MEMBER = re.compile(r'^\s*[0-9]{2}\s*(?:男|女)(?:\s*[A-Za-z]\S*)?\s*[：:]\s*(.*)$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def tabs_of(doc):
    d = doc.get('content', doc)

    def walk(ts):
        for t in ts:
            yield t
            yield from walk(t.get('childTabs', []))
    return list(walk(d['tabs']))


def tab_lines(t):
    out = []
    for el in t.get('documentTab', {}).get('body', {}).get('content', []):
        if 'paragraph' in el:
            s = ''.join(r.get('textRun', {}).get('content', '') for r in el['paragraph']['elements'])
            out.append(s.rstrip('\n'))
    return out


def evening_part(lines):
    for i, l in enumerate(lines):
        if l.strip().startswith('【晚'):
            return lines[i + 1:]
    for i, l in enumerate(lines):
        if '晚上好' in l and '欢迎' in l:
            start = i
            while start > 0 and not LABEL.match(lines[start]):
                start -= 1
            return lines[start:]
    return []


def blocks(lines):
    out, cur = [], None
    for l in lines:
        m = LABEL.match(l)
        mm = None if m else MEMBER.match(l)
        if m:
            cur = {'role': 'ASSISTANT' if m.group(1) == '资讯助理' else 'TEACHER', 'text': []}
            out.append(cur)
        elif mm:
            cur = {'role': 'MEMBER', 'text': [mm.group(1)] if mm.group(1).strip() else []}
            out.append(cur)
        elif cur is not None and l.strip():
            cur['text'].append(l.strip())
    return out


def parse(lines):
    bl = blocks(lines)
    first_t = next((i for i, b in enumerate(bl) if b['role'] == 'TEACHER'), None)
    head = bl[:first_t] if first_t is not None else bl
    asst_head = [b for b in head if b['role'] == 'ASSISTANT']
    teachers = [b for b in bl if b['role'] == 'TEACHER']
    asst = [b for b in bl if b['role'] == 'ASSISTANT']
    j = lambda b: chr(10).join(b['text']) if b else ''
    closing = asst[-1] if asst and teachers and asst[-1] is not asst_head[-1] else None
    return {
        'opening': j(asst_head[0]) if asst_head else '',
        'handoff': j(asst_head[-1]) if len(asst_head) > 1 else '',
        'david_blocks': len(teachers),
        'member_lines': sum(1 for b in bl if b['role'] == 'MEMBER'),
        'last_david_block': j(teachers[-1]) if teachers else '',
        'closing': j(closing),
    }


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--docjson', required=True)
    ap.add_argument('--tab', required=True)
    ap.add_argument('--days', type=int, default=2)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    try:
        doc = json.loads(Path(a.docjson).read_text(encoding='utf-8'))
        tabs = tabs_of(doc)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({'status': 'INPUT_ERROR', 'error': str(e)}, ensure_ascii=False))
        return 2
    days = sorted((t['tabProperties']['title'], t) for t in tabs
                  if DATE.match(t['tabProperties'].get('title', '')) and t['tabProperties']['title'] < a.tab)
    found = []
    for title, t in reversed(days):
        ev = evening_part(tab_lines(t))
        if any(l.strip() for l in ev):
            found.append(dict(date=title, **parse(ev)))
        if len(found) >= a.days:
            break
    if a.json:
        print(json.dumps({'status': 'OK' if found else 'NONE', 'nights': found}, ensure_ascii=False, indent=1))
    else:
        for n in found:
            print('=== %s ===' % n['date'])
            print('成员条数 %d｜David块 %d' % (n['member_lines'], n['david_blocks']))
            print('[开场]\n' + n['opening'])
            print('[交棒]\n' + n['handoff'])
            print('[先生最后一块]\n' + n['last_david_block'])
            print('[助理收尾]\n' + n['closing'] + '\n')
        if not found:
            print('没有找到 %s 之前有晚场内容的页签' % a.tab)
    return 0 if found else 1


if __name__ == '__main__':
    sys.exit(main())
