#!/usr/bin/env python3
"""每天写稿前的一次性准备（离线，只读）：读 read_doc 保存的文件，输出
本日页签是否已有内容、最近两个稿日、今天必须出场的成员、各成员最近一次发言日。
用法：python daily_prep.py --docjson 文件 --tab 2026-11-25 [稿件.txt ...] [--max-id 52]"""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from silence_check import tab_texts, must_speak, LINE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('texts', nargs='*')
    ap.add_argument('--docjson', required=True)
    ap.add_argument('--tab', required=True)
    ap.add_argument('--max-id', type=int, default=52)
    a = ap.parse_args()
    tabs = tab_texts(json.loads(Path(a.docjson).read_text(encoding='utf-8')))
    extra = '\n'.join(Path(t).read_text(encoding='utf-8') for t in a.texts)
    prev, need = must_speak(tabs, a.tab, extra, a.max_id)
    today = tabs.get(a.tab, '')
    last = {}
    for k in sorted(tabs):
        if k < a.tab:
            for i in set(LINE.findall(tabs[k])):
                last[i] = k
    print('今日页签字数：%d（%s）' % (len(today.strip()), '已有内容' if len(today.strip()) > 1 else '空'))
    print('参照稿日：' + '、'.join(prev))
    for k in prev:
        print('  %s 出场 %d 人' % (k, len(set(LINE.findall(tabs[k])))))
    print('今天必须出场：' + (' '.join(need) or '无'))
    ids = ['%02d' % i for i in range(1, a.max_id + 1)]
    print('最近发言日：' + ' '.join('%s:%s' % (i, last.get(i, '无')[5:]) for i in ids))


if __name__ == '__main__':
    main()
