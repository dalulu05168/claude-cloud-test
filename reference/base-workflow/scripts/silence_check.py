#!/usr/bin/env python3
"""成员连续未发言检查（用户2026-11-25规则：01—52号任何成员不得连续三个稿日没发言）。
用法：
  python silence_check.py --docjson read_doc保存的文件 --tab 2026-11-25 [稿件.txt ...] [--max-id 52] [--json]
统计文档里本日之前最近两个有内容的日期页签；这两天都没发言的成员，本日必须出场。
本日已出场 = 文档本日页签 + 额外给的稿件文本。输出仍需出场的编号（退出码1），没有则退出码0。"""
import argparse, json, re, sys
from pathlib import Path

LINE = re.compile(r'(?m)^\s*(\d\d)\s*[男女]')


def tab_texts(doc):
    out = {}

    def walk(tabs):
        for t in tabs:
            title = t['tabProperties']['title'].strip()
            txt = ''
            for e in t.get('documentTab', {}).get('body', {}).get('content', []):
                for pe in e.get('paragraph', {}).get('elements', []):
                    txt += pe.get('textRun', {}).get('content', '')
            if re.fullmatch(r'\d{4}-\d\d-\d\d', title):
                out[title] = txt
            walk(t.get('childTabs', []))
    walk(doc['content']['tabs'])
    return out


def must_speak(tabs, day, extra_text='', max_id=52, window=2):
    prev = [k for k in sorted(tabs) if k < day and len(LINE.findall(tabs[k])) > 0][-window:]
    spoke_prev = set()
    for k in prev:
        spoke_prev |= set(LINE.findall(tabs[k]))
    today = set(LINE.findall(tabs.get(day, '') + '\n' + extra_text))
    allids = ['%02d' % i for i in range(1, max_id + 1)]
    return prev, [i for i in allids if i not in spoke_prev and i not in today]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('texts', nargs='*')
    ap.add_argument('--docjson', required=True)
    ap.add_argument('--tab', required=True)
    ap.add_argument('--max-id', type=int, default=52)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    doc = json.loads(Path(a.docjson).read_text(encoding='utf-8'))
    extra = '\n'.join(Path(t).read_text(encoding='utf-8') for t in a.texts)
    prev, need = must_speak(tab_texts(doc), a.tab, extra, a.max_id)
    res = {'previous_days': prev, 'must_speak_today': need}
    print(json.dumps(res, ensure_ascii=False) if a.json else
          '参照日：%s　今天还必须出场：%s' % ('、'.join(prev), ' '.join(need) or '无'))
    return 1 if need else 0


if __name__ == '__main__':
    sys.exit(main())
