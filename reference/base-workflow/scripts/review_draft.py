#!/usr/bin/env python3
"""Westcott 每日稿自动审稿（离线、只读）。

用法：
  python review_draft.py 稿件.txt [--date 2026-11-12] [--times times.json] [--json]
  python review_draft.py --docjson read_doc保存的文件 --tab 2026-11-12 [...]

检查：国内股民黑话/中式俚语与成语、渲染性用词、学习群式空表态、中国式涨跌颜色、
盘中具体时刻、成员编造持仓、先生说“答案是”、成员编号越界（默认01—52）、禁止一对一发言（先生/助理与成员交替）、成员发言重复、活动答题成员不选答案、先生助理点名回应成员、称呼“朋友”、各场条数、
晚间收尾三句、周二四晚间作业、新闻发布时间顺序（需 --times）。
晚场另查：成员台词不夹罗语口头词、不带引号、不用“你们两个”，先生助理不用“有成员问/认为”；给 --topic 时查交棒句不点课题。
2.3.1起：成员台词可写成“12男：台词”同行格式；另查称呼（成员称先生不说“老师”，
先生和助理统一称“成员”）、@编号、后台口吻、集体夸奖、排队发言、先生用股民口头说法。
退出码：0=PASS（可能有提醒），1=FAIL，2=输入错误。
脚本只做机器可查的项目；语义、事实和口吻是否自然仍需人工或审稿代理判断。
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HEADER = re.compile(r'^\s*(?:([0-9]{2})(男|女)[：:]|===== (资讯助理|David先生|[^=]{1,12}先生) =====)\s*$')
# 同行写法：“12男：台词”或“46男 Andrei：台词”，编号后可带罗马尼亚名字
INLINE = re.compile(r'^\s*([0-9]{2})(男|女)(?:\s*[A-Za-zĂÂÎȘȚăâîșț][A-Za-zĂÂÎȘȚăâîșț .\-]{0,30})?\s*[：:]\s*(.*)$')

# 成员台词禁用：国内股民黑话、中式网络梗（FAIL）；先生和助理出现时提醒改用专业术语
MEMBER_SLANG = ['抬杠', '杠精', '韭菜', '割肉', '躺平', '追高', '谁还敢追', '拉升', '说拉就拉', '拉盘',
                '砸盘', '卡死', '庄家', '洗盘', '吸筹', '抄底', '接盘侠', '接盘', '回血',
                '吃面', '吃肉', '满仓干', '梭哈', '套牢', '被套', '解套', '踏空', '上车', '下车',
                '牛市来了', '涨停', '跌停', '打板', '妖股', '龙头股', '题材股', '国家队', '大A', 'A股',
                '小散', '镰刀', '老铁', '家人们', '大佬', '小白', '翻车', '踩雷', '上岸', '起飞',
                '真香', '佛系', '内卷', '绝绝子', 'yyds', '破防', '摆烂', 'emo', '打工人']
# “稳了”是网络说法，但“站稳了/企稳了”是正常技术表述
#“主力合约/主力产品”“出货量”是正常说法，不算黑话
SLANG_RE = [(re.compile(r'(?<![站企走持守])稳了'), '稳了'),
            (re.compile(r'主力(?!合约|产品|业务|车型)'), '主力'),
            (re.compile(r'出货(?!量)'), '出货')]
# 中国成语、典故（FAIL）：罗马尼亚成员不会这样说
IDIOMS = ['事后诸葛亮', '马后炮', '落袋为安', '见好就收', '高开低走', '低开高走', '受益匪浅', '醍醐灌顶',
          '茅塞顿开', '守株待兔', '亡羊补牢', '塞翁失马', '纸上谈兵', '画蛇添足', '临时抱佛脚',
          '稳扎稳打', '一针见血', '胸有成竹', '未雨绸缪', '水涨船高', '顺藤摸瓜', '火中取栗',
          '不见兔子不撒鹰', '天上掉馅饼', '豁然开朗', '恍然大悟']
# 只在中国才有的生活和市场背景（提醒）
CN_CONTEXT = ['人民币', '微信', '支付宝', '余额宝', '红包', '春节', '国庆', '双十一', '茅台', '上证',
              '沪深', '创业板', '北向资金', '港股通']
# 夸先生、替机构说话（单句提醒，一场三句以上判为集体夸奖）
PRAISE = ['讲得太好', '讲得真好', '讲得好', '太厉害', '太专业', '太准了', '神了', '跟着先生', '多亏先生',
          '多亏了先生', '佩服', '膜拜', '大师', '先生说的都对', '听先生的准没错']
# 成员知道后台写作安排（FAIL）
BACKSTAGE = re.compile(r'开了没|数据还得等|等核对|核对完|稿子|后台')
AT_NUMBER = re.compile(r'@\s*[0-9]{2}\s*(?:男|女)')
STAFF_ADDRESS = re.compile(r'学员|同学|朋友')
POINT_OUT = re.compile(r'这位成员|那位成员|最后这位|刚才那位|几位成员|两位成员|三位成员|(?:问|说|提到|讲|回答)[^，。：；]{0,14}的成员')
NOT_PLAYING = re.compile(r'先不说|答案先|不告诉你们|心里有答案|等会再说')
# 较隐蔽的个人持仓、盈亏说法（提醒；持仓内容由用户加【持仓】占位）
HOLDING_SOFT = re.compile(r'我.{0,8}(?:买的|买进的|入手的?|拿着的?|卖早了|卖掉了)|我(?:手里|手上|仓里|账户里)(?:的|有|那)'
                          r'|我的(?:仓位|成本|持仓)|已经赚了|赚了不少|亏了不少|回本了')
LONG_LINE = 60
# 全稿禁用：渲染性用词（FAIL）
HYPE = ['出大事', '出了大事', '大事了', '炸了', '炸锅', '重磅', '血洗', '史诗级', '暴击', '王炸',
        '惊天', '震惊', '疯狂', '狂飙', '血亏', '暴雷']
# 学习群式空表态（每场超过2句提醒）
FILLER = ['学到了', '记下了', '原来是这样', '这个懂', '我懂了', '懂了', '谢谢先生', '回去试试',
          '再看一遍', '明天来听', '收藏了', '涨知识']
# 中国式红涨绿跌说法（提醒人工确认）
COLOR_CN = re.compile(r'飘绿|翻绿|翻红|红盘|绿盘|满屏红|满屏绿|一片红|一片绿|大红|绿油油|涨红|跌绿')
CLOSE_GREEN = re.compile(r'收绿|收红')
CLOCK = re.compile(r'截至\s*\d{1,2}[:：点]\d{0,2}|\d{1,2}[:：]\d{2}\s*(?:的|时)?\s*(?:行情|数据|报价|最新)')
HOLDING = re.compile(r'我(?:已经|刚|今天)?(?:买了|买入|加仓|建仓|满仓|持有|卖了|清仓)|赚了\s*\d|亏了\s*\d|收益\s*\d+%')
ANSWER = re.compile(r'答案是')
# 助理播报标题行统一（用户2026-11-25）：图标 类别｜具体内容；旧写法、不一致的连字符一律 FAIL
HEAD_OK = re.compile(r'^(?:🇺🇸 美股｜(?:昨晚|上周五|前一交易日)收盘回顾|🇷🇴 罗马尼亚财经新闻｜.+|🇺🇸 美国财经新闻｜.+|'
                     r'🇷🇴 罗马尼亚股市开盘｜.+|🇺🇸 美股开盘｜.+|📊 精选股票｜[A-Z0-9.]+|🏢 公司简介｜.+|📈 阶段止盈｜[A-Z0-9.]+)$')
HEAD_LIKE = re.compile(r'^(?:🇷🇴|🇺🇸|📊|📈|🏢)\s*.*(?:｜|止盈提醒)')
HEAD_OLD = re.compile(r'阶段止盈提醒|开盘播报')
NB_HYPHEN = '‑'
# 晚场新增机械检查（用户2026-11-25采用稿）：成员台词不夹罗语口头词、不用引号；先生和助理不用“有成员问/认为”引出回答
RO_WORDS = re.compile(r'\b(?:Păi|Pai|Mda|Gata|Deci|Adică|Adica|Serios|Hai|Bine|Exact|Mersi)\b')
QUOTE_CHARS = re.compile(r'[“”"「」]')
SOME_MEMBER = re.compile(r'有成员(?:问|认为|说|提到|觉得)')
YOU_TWO = re.compile(r'你们(?:两个|几个|几位|两位|俩)')
HOMEWORK = re.compile(r'作业|课后练习|练习题')
NEWS = re.compile(r'^(?:🇷🇴|🇺🇸|🇪🇺|🌍)\s*\S*?(?:新闻|股市|美股|开盘)')
SESSION = [('上午', re.compile(r'早上好')), ('下午', re.compile(r'下午好')), ('晚间', re.compile(r'晚上好'))]


def _load_roster():
    try:
        return json.loads((Path(__file__).resolve().parent.parent / 'references' / 'roster.json').read_text(encoding='utf-8')).get('genders', {})
    except Exception:
        return {}


ROSTER_GENDERS = _load_roster()


def load_lines(args):
    if args.docjson:
        d = json.loads(Path(args.docjson).read_text(encoding='utf-8'))
        d = d.get('content', d)

        def walk(tabs):
            for t in tabs:
                yield t
                yield from walk(t.get('childTabs', []))
        for t in walk(d['tabs']):
            if t['tabProperties']['title'] == args.tab:
                out = []
                for el in t['documentTab']['body']['content']:
                    if 'paragraph' in el:
                        s = ''.join(r.get('textRun', {}).get('content', '') for r in el['paragraph']['elements'])
                        out.append(s.rstrip('\n'))
                return out
        raise ValueError('找不到页签：' + str(args.tab))
    return Path(args.text).read_text(encoding='utf-8-sig').splitlines()


def blocks(lines):
    cur = None
    for i, raw in enumerate(lines, 1):
        line = raw.replace('\u000b', '\n')
        m = HEADER.match(line)
        inline = None if m else INLINE.match(line)
        if m:
            if cur:
                yield cur
            role = 'MEMBER' if m.group(1) else ('ASSISTANT' if m.group(3) == '资讯助理' else 'TEACHER')
            cur = {'role': role, 'id': m.group(1), 'label': line.strip(), 'line': i, 'body': []}
        elif inline:
            if cur:
                yield cur
            cur = {'role': 'MEMBER', 'id': inline.group(1), 'label': inline.group(1) + inline.group(2) + '：',
                   'line': i, 'body': []}
            if inline.group(3).strip():
                cur['body'].append((i, inline.group(3).strip()))
        elif cur is not None and line.strip():
            cur['body'].append((i, line.strip()))
    if cur:
        yield cur


def sentences(text):
    return [s for s in re.split(r'(?<=[。！？!?])', text) if re.search(r'[一-鿿A-Za-z0-9]', s)]


def assigned_homework(text):
    # Heuristic: remove clearly negated instructions, then detect explicit or disguised tasks.
    # 活动答题（答案/选择/字母/竞猜/判断题/趣味题）发给顾问不算作业
    cleaned = re.sub(r'[^。！？!?\n]*(?:答案|选择|字母|竞猜|判断题|趣味)[^。！？!?\n]*Gabriela[^。！？!?\n]*[。！？!?]?', '', text)
    cleaned = re.sub(r'(?:今晚|今天|本课)?(?:不|无需|不用|没有)(?:再|另)?(?:布置|安排|提交|交)?(?:课后练习|练习题|作业|打卡|观察任务)', '', cleaned)
    return bool(HOMEWORK.search(cleaned) or re.search(
        r'(?:明天|课后|下课后|下次课前).{0,30}(?:提交|交给|发给|上传|打卡)|'
        r'(?:请|需要|必须|记得).{0,12}(?:提交|上传|打卡)|'
        r'(?:把|将).{0,20}(?:交给|发给)\s*Gabriela|课后观察任务', cleaned))


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('text', nargs='?')
    ap.add_argument('--docjson')
    ap.add_argument('--tab')
    ap.add_argument('--date', help='YYYY-MM-DD，用于判断周几；默认取 --tab')
    ap.add_argument('--times', help='JSON：{"标题关键词": "HH:MM", ...} 新闻发布时间，按稿中出现顺序检查不回跳')
    ap.add_argument('--max-id', type=int, default=52)
    ap.add_argument('--topic', help='今晚课题名；给出后检查开场助理交棒块不得提前点出课题')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--scope', choices=['auto', 'evening'], default='auto')
    ap.add_argument('--fragment', action='store_true', help='Do not apply full-session counts/closing')
    a = ap.parse_args()
    if not a.text and not (a.docjson and a.tab):
        ap.error('需要 稿件.txt，或 --docjson 加 --tab')
    try:
        lines = load_lines(a)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        print(json.dumps({'status': 'INPUT_ERROR', 'error': str(e)}, ensure_ascii=False))
        return 2

    date_s = a.date or a.tab
    weekday = None
    if date_s:
        try:
            weekday = dt.date.fromisoformat(date_s).isoweekday()
        except ValueError:
            print(json.dumps({'status': 'INPUT_ERROR', 'error': 'invalid date'}, ensure_ascii=False))
            return 2

    fails, warns = [], []

    def add(kind, line, msg, text=''):
        (fails if kind == 'FAIL' else warns).append({'line': line, 'issue': msg, 'text': text[:80]})

    bl = list(blocks(lines))
    sessions, cur = {}, None
    for b in bl:
        body = ' '.join(t for _, t in b['body'])
        if b['role'] == 'ASSISTANT':
            for name, pat in SESSION:
                if pat.search(body) and '欢迎' in body:
                    cur = name
        b['session'] = cur or ('晚间' if a.scope == 'evening' else '未分场')
        sessions.setdefault(b['session'], []).append(b)

        for ln, t in b['body']:
            for w in HYPE:
                if w in t:
                    add('FAIL', ln, '渲染性用词「%s」' % w, t)
            if CLOCK.search(t) and b['role'] != 'MEMBER':
                add('WARN', ln, '盘中写了具体时刻（固定节点预告除外）', t)
            if COLOR_CN.search(t):
                add('WARN', ln, '中国式红绿说法，确认是否符合绿涨红跌', t)
            slang = [w for w in MEMBER_SLANG if w in t] + [n for p, n in SLANG_RE if p.search(t)]
            if AT_NUMBER.search(t):
                add('FAIL', ln, '@人用了编号，群里@某人写成员名字', t)
            if b['role'] == 'MEMBER':
                if b['session'] == '晚间':
                    if RO_WORDS.search(t):
                        add('FAIL', ln, '%s 晚场成员台词夹了罗语口头词，晚场不用' % b['label'], t)
                    if QUOTE_CHARS.search(t):
                        add('FAIL', ln, '%s 晚场成员台词带了引号，应去掉' % b['label'], t)
                    if YOU_TWO.search(t):
                        add('FAIL', ln, '%s 用“你们两个”这类点名式称呼回应其他成员' % b['label'], t)
                for w in slang:
                    add('FAIL', ln, '%s 用了国内股民/网络说法「%s」' % (b['label'], w), t)
                for w in IDIOMS:
                    if w in t:
                        add('FAIL', ln, '%s 用了中国成语/典故「%s」' % (b['label'], w), t)
                for w in CN_CONTEXT:
                    if w in t:
                        add('WARN', ln, '%s 提到中国特有背景「%s」，确认罗马尼亚成员是否会这样说' % (b['label'], w), t)
                if NOT_PLAYING.search(t):
                    add('FAIL', ln, '%s 活动答题里没有明确选答案（先不说/答案先不说）' % b['label'], t)
                if '老师' in t:
                    add('FAIL', ln, '%s 称呼先生用了“老师”，应为名字或姓氏加“先生”' % b['label'], t)
                if BACKSTAGE.search(t):
                    add('FAIL', ln, '%s 说了只有后台才知道的安排' % b['label'], t)
                if HOLDING.search(t):
                    add('FAIL', ln, '%s 疑似编造个人持仓或收益' % b['label'], t)
                elif HOLDING_SOFT.search(t):
                    add('WARN', ln, '%s 疑似提到个人持仓或盈亏，持仓内容应由用户加【持仓】占位' % b['label'], t)
                if any(w in t for w in PRAISE):
                    add('WARN', ln, '%s 在夸先生或替机构说话，确认是否必要' % b['label'], t)
                if len(re.sub(r'\s', '', t)) > LONG_LINE:
                    add('WARN', ln, '%s 台词很长，确认真实群聊里会不会这样发' % b['label'], t)
                if len(re.findall(r'\d[\d,.]*', t)) >= 2:
                    add('WARN', ln, '%s 一句里带了两个以上数字，像在读指标（用户2026-11-27），数字留给助理播报' % b['label'], t)
            else:
                for w in slang:
                    add('WARN', ln, '%s 用了股民口头说法「%s」，先生和助理应使用专业术语' % (b['label'], w), t)
                if SOME_MEMBER.search(t):
                    add('FAIL', ln, '%s 用“有成员问/认为”引出回答，直接讲道理' % b['label'], t)
                if POINT_OUT.search(t):
                    add('FAIL', ln, '%s 点名回应成员（“这位成员/问X的成员”），应合并成一般性讲解' % b['label'], t)
                if STAFF_ADDRESS.search(t):
                    add('FAIL', ln, '%s 称呼群里的人应统一为“成员”' % b['label'], t)
            if b['role'] == 'ASSISTANT':
                if HEAD_LIKE.match(t.strip()) and not HEAD_OK.match(t.strip()):
                    add('FAIL', ln, '播报标题行不符合统一版式（见 references/broadcast-format.md 播报版式总表）', t)
                elif HEAD_OLD.search(t) and t.strip()[:1] in '🇷🇺📈':
                    add('FAIL', ln, '播报标题用了旧写法（阶段止盈提醒/开盘播报）', t)
                if NB_HYPHEN in t:
                    add('FAIL', ln, '价格区间连接符统一用短横“–”，不用不换行连字符', t)
            if b['role'] == 'TEACHER' and ANSWER.search(t):
                add('FAIL', ln, '先生说了“答案是”', t)
        if b['role'] == 'MEMBER' and b['id'] and int(b['id']) > a.max_id:
            add('FAIL', b['line'], '成员编号超出 01—%02d' % a.max_id, b['label'])
        if b['role'] == 'MEMBER' and b['id']:
            gm = re.search(r'[男女]', b['label'])
            want = ROSTER_GENDERS.get(b['id'])
            if gm and want and gm.group(0) != want:
                add('FAIL', b['line'], '成员性别与编号表不符：%s 应为%s' % (b['id'], want), b['label'])

    limits = {'上午': (32, 35), '晚间': (32, 35), '下午': (35, 38)}  # 左右，不定死；±3 以内不提醒
    counts = {}
    for name, bs in sessions.items():
        n = len(bs)
        counts[name] = n
        lo, hi = limits.get(name, (0, 999))
        if not a.fragment and (n > hi + 3 or n < lo - 3):
            add('WARN', bs[0]['line'], '%s共 %d 条，默认范围 %d—%d（片段任务可忽略）' % (name, n, lo, hi))
        fill = [(ln, t) for b in bs if b['role'] == 'MEMBER' for ln, t in b['body']
                if any(w in t for w in FILLER)]
        if len(fill) > 2:
            add('WARN', fill[0][0], '%s学习群式空表态 %d 句（最多一两句）' % (name, len(fill)),
                ' / '.join(t for _, t in fill)[:80])
        praise = [(ln, t) for b in bs if b['role'] == 'MEMBER' for ln, t in b['body']
                  if any(w in t for w in PRAISE)]
        if len(praise) >= 3:
            add('FAIL', praise[0][0], '%s有 %d 句成员在夸先生，属于集体夸奖' % (name, len(praise)),
                ' / '.join(t for _, t in praise)[:80])
        roles = ['M' if b['role'] == 'MEMBER' else 'N' for b in bs]
        for i in range(len(roles) - 4):
            if roles[i:i + 5] == ['N', 'M', 'N', 'M', 'N']:
                add('FAIL', bs[i + 1]['line'], '%s先生/助理与成员一问一答交替（禁止一对一发言）' % name)
                break
        mq = [(b['role'] == 'MEMBER') and any(('？' in t2 or '?' in t2) for _, t2 in b['body']) for b in bs]
        grp = []
        for i, b in enumerate(bs + [{'role': 'END', 'line': 0}]):
            if b['role'] == 'MEMBER':
                grp.append(i)
                continue
            if len(grp) >= 3 and all(mq[j] for j in grp):
                add('WARN', bs[grp[0]]['line'], '%s这一组成员全是提问（用户2026-11-27），要有陈述、立场或不同意见' % name)
            grp = []
        member_n = sum(1 for b in bs if b['role'] == 'MEMBER')
        if member_n >= 6 and sum(mq) * 2 > member_n:
            add('WARN', bs[0]['line'], '%s成员问句过半（%d/%d），多数发言都在向先生提问' % (name, sum(mq), member_n))
        run_len = 0
        for b in bs:
            run_len = run_len + 1 if b['role'] == 'MEMBER' else 0
            if run_len == 7:
                add('FAIL', b['line'], '%s成员连着发言超过6位，要用助理新闻隔开（用户2026-11-25）' % name)
                break
        seen_lines = {}
        for b in bs:
            if b['role'] == 'MEMBER':
                for ln, t2 in b['body']:
                    key = re.sub(r'[\s，。！？!?,.~😄😁🙂]+', '', t2)
                    if len(key) >= 8 and key in seen_lines:
                        add('FAIL', ln, '%s成员发言重复（与第%d行相同）' % (name, seen_lines[key]), t2)
                    seen_lines.setdefault(key, ln)
        speakers = [b['id'] for b in bs if b['role'] == 'MEMBER']
        once = sum(1 for i in set(speakers) if speakers.count(i) == 1)
        if len(speakers) >= 8 and once >= 0.9 * len(speakers):
            add('WARN', bs[0]['line'], '%s成员几乎一人一句（%d 条里 %d 人只说一次），像排队发言' % (name, len(speakers), once))
        for b in bs:
            for ln, t in b['body']:
                if CLOSE_GREEN.search(t) and name != '晚间' and b['role'] == 'MEMBER':
                    add('WARN', ln, '盘中说了“收绿/收红”，未收盘应说“见绿/飘红”', t)

    ev = sessions.get('晚间')
    if ev:
        last = [b for b in ev if b['role'] == 'ASSISTANT']
        if not a.fragment and last and last[-1] is not ev[0]:
            body = ''.join(t for _, t in last[-1]['body'])
            if len(sentences(body)) != 3:
                add('WARN', last[-1]['line'], '晚间助理收尾不是三句（现 %d 句）' % len(sentences(body)), body)
        if weekday in (2, 4):
            for b in ev:
                for ln, t in b['body']:
                    if assigned_homework(t):
                        add('FAIL', ln, '周二四晚间禁止布置作业或变相提交任务', t)

    if ev and a.topic:
        first_t = next((i for i, b in enumerate(ev) if b['role'] == 'TEACHER'), None)
        pre = [b for b in (ev[:first_t] if first_t is not None else []) if b['role'] == 'ASSISTANT']
        if len(pre) > 1:
            for ln, t in pre[-1]['body']:
                if a.topic in t:
                    add('FAIL', ln, '交棒句点出了今晚课题「%s」，应保留悬念' % a.topic, t)

    if ev and weekday is None:
        add('WARN', ev[0]['line'], '未提供有效课程日期，周二四无作业规则待核')

    if a.times:
        try:
            times = json.loads(Path(a.times).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as e:
            print(json.dumps({'status': 'INPUT_ERROR', 'error': str(e)}, ensure_ascii=False))
            return 2
        seen = []
        for i, l in enumerate(lines, 1):
            for k, v in times.items():
                if k in l and NEWS.match(l.strip()):
                    seen.append((i, v, l.strip()))
        for (i0, t0, h0), (i1, t1, h1) in zip(seen, seen[1:]):
            if t1 < t0:
                add('FAIL', i1, '新闻时间往回跳：%s 排在 %s 之后' % (t1, t0), h1)

    status = 'FAIL' if fails else 'PASS'
    res = {'status': status, 'counts': counts, 'fail': fails, 'warn': warns,
           'not_verified': '事实与数字、语义口吻是否自然、配图来源、新闻时间（未给 --times 时）需人工或审稿代理核对'}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print('状态：%s　各场条数：%s' % (status, '、'.join('%s %d' % kv for kv in counts.items())))
        for tag, items in (('必须改', fails), ('请确认', warns)):
            for x in items:
                print('[%s] 第%s行 %s｜%s' % (tag, x['line'], x['issue'], x['text']))
        print('未检查：' + res['not_verified'])
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
