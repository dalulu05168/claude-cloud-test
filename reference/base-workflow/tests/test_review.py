import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'review_draft.py'


def run(text, *extra):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'c.txt'
        p.write_text(text, encoding='utf-8')
        args = [sys.executable, '-I', str(SCRIPT), str(p), '--json']
        for e in extra:
            if isinstance(e, dict):
                q = Path(d) / 't.json'
                q.write_text(json.dumps(e, ensure_ascii=False), encoding='utf-8')
                args += ['--times', str(q)]
            else:
                args.append(e)
        r = subprocess.run(args, capture_output=True, encoding='utf-8')
        return r.returncode, json.loads(r.stdout)


OK = '===== 资讯助理 =====\n🌙大家晚上好！欢迎回来！\n\n47女：\n原来我昨天问的就是宽度的事😄\n\n24男：\nSNP今天见绿了。\n'


class Review(unittest.TestCase):
    def test_clean_passes(self):
        code, res = run(OK, '--date', '2026-11-12')
        self.assertEqual((code, res['status']), (0, 'PASS'))
        self.assertEqual(res['fail'], [])

    def test_member_slang_fails(self):
        for w in ('那我昨天那句不算抬杠', 'RRC谁还敢追？', 'RRC说拉就拉', 'RRC卡死了'):
            with self.subTest(w=w):
                code, res = run('47女：\n' + w + '\n')
                self.assertEqual((code, res['status']), (1, 'FAIL'))

    def test_confirmed_colour_words_allowed(self):
        code, res = run('10女：\nPRSN也见绿了👀\n\n29男：\n纳指一开盘就飘红了。\n')
        self.assertEqual(res['status'], 'PASS')

    def test_hype_holding_answer(self):
        for t in ('===== 资讯助理 =====\n英伟达出大事了。\n', '12男：\n我已经买了SNP。\n',
                  '===== David先生 =====\n答案是周期股。\n'):
            with self.subTest(t=t):
                self.assertEqual(run(t)[1]['status'], 'FAIL')

    def test_news_time_order(self):
        text = '===== 资讯助理 =====\n🇺🇸 美国财经新闻｜英伟达\n\n===== 资讯助理 =====\n🇷🇴 罗马尼亚财经新闻｜核电站\n'
        self.assertEqual(run(text, {'英伟达': '09:00', '核电站': '07:59'})[1]['status'], 'FAIL')
        self.assertEqual(run(text, {'英伟达': '07:59', '核电站': '09:00'})[1]['status'], 'PASS')

    def test_input_error(self):
        r = subprocess.run([sys.executable, '-I', str(SCRIPT), str(ROOT / 'nope.txt')], capture_output=True, encoding='utf-8')
        self.assertEqual(r.returncode, 2)


def issues(res, kind):
    return ' | '.join(x['issue'] for x in res[kind])


class Voice231(unittest.TestCase):
    """2.3.1：成员口吻漏检修补。"""

    def test_inline_member_line_is_checked(self):
        code, res = run('12男：这波拉升太猛了\n')
        self.assertEqual((code, res['status']), (1, 'FAIL'))
        code, res = run('46男 Andrei：\n庄家在出货\n')
        self.assertEqual(res['status'], 'FAIL')

    def test_inline_clean_line_passes_and_counts(self):
        code, res = run('===== 资讯助理 =====\n🌙大家晚上好！欢迎回来！\n12男：SNP今天见绿了👀\n14男: 那派息还算数吗？\n')
        self.assertEqual((code, res['status']), (0, 'PASS'))
        self.assertEqual(res['counts'], {'晚间': 3})

    def test_more_slang_and_idioms_fail(self):
        for w in ('主力在洗盘', '又被套了', '现在可以上车吗', '先落袋为安吧', '今天高开低走', '这把稳了',
                  '听完醍醐灌顶', '真是马后炮'):
            with self.subTest(w=w):
                self.assertEqual(run('14男：\n' + w + '\n')[1]['status'], 'FAIL')

    def test_technical_wording_not_flagged(self):
        code, res = run('14男：\nSNP在这个位置站稳了吗？\n\n22男：\nBET企稳了再看吧\n')
        self.assertEqual((code, res['status'], res['warn']), (0, 'PASS', []))

    def test_member_calls_teacher_laoshi_fails(self):
        self.assertIn('老师', issues(run('10女：\n老师，这样算确认吗？\n')[1], 'fail'))
        self.assertEqual(run('10女：\nDavid先生，这样算确认吗？\n')[1]['status'], 'PASS')

    def test_staff_must_say_member(self):
        for t in ('===== David先生 =====\n各位学员晚上好。\n', '===== 资讯助理 =====\n同学们下午好。\n'):
            with self.subTest(t=t):
                self.assertEqual(run(t)[1]['status'], 'FAIL')
        self.assertEqual(run('===== David先生 =====\n各位成员晚上好。\n')[1]['status'], 'PASS')

    def test_teacher_slang_is_warning_only(self):
        code, res = run('===== David先生 =====\n急跌时不要急着抄底。\n')
        self.assertEqual((code, res['status']), (0, 'PASS'))
        self.assertIn('专业术语', issues(res, 'warn'))

    def test_soft_holding_warns(self):
        for t in ('我上周买的TLV已经赚了不少', '我手里的H2O要不要先卖'):
            with self.subTest(t=t):
                code, res = run('07女：\n' + t + '\n')
                self.assertIn('持仓', issues(res, 'warn') + issues(res, 'fail'))

    def test_backstage_and_at_number_fail(self):
        self.assertEqual(run('14男：\n开了没？数据还得等一会儿吧\n')[1]['status'], 'FAIL')
        self.assertEqual(run('12男：\n@46男 你说的对\n')[1]['status'], 'FAIL')
        self.assertEqual(run('12男：\n@Andrei 你说的对\n')[1]['status'], 'PASS')

    def test_praise_chorus(self):
        one = '10女：\n先生讲得真好\n'
        self.assertEqual(run(one)[1]['status'], 'PASS')
        self.assertIn('夸', issues(run(one)[1], 'warn'))
        three = one + '12男：\n太厉害了\n14男：\n跟着先生准没错\n'
        self.assertEqual(run(three)[1]['status'], 'FAIL')

    def test_queue_pattern_warns(self):
        queue = ''.join('%02d男：\nSNP见绿了吗？\n' % i for i in (1, 3, 4, 5, 6, 8, 9, 11))
        self.assertIn('排队', issues(run(queue)[1], 'warn'))
        mixed = queue + '01男：\n我再问一句，派息呢？\n03男：\n我也想知道\n04男：\n先看BNR吧\n'
        self.assertNotIn('排队', issues(run(mixed)[1], 'warn'))


if __name__ == '__main__':
    unittest.main()


class EveningRules(unittest.TestCase):
    HEAD = '===== 资讯助理 =====\n🌤 大家晚上好！欢迎回来！\n\n'

    def test_evening_member_romanian_fails_but_morning_ok(self):
        code, res = run(self.HEAD + '12男：\nPăi，SNP今天见绿了。\n', '--fragment')
        self.assertEqual(code, 1)
        code, res = run('===== 资讯助理 =====\n🌤 大家早上好！欢迎回来！\n\n12男：\nPăi，SNP今天见绿了。\n', '--fragment')
        self.assertEqual(res['status'], 'PASS')

    def test_evening_member_quotes_and_you_two_fail(self):
        for t in ('那句话里的“总算”听着像松一口气', '你们两个都在猜'):
            with self.subTest(t=t):
                code, res = run(self.HEAD + '12男：\n' + t + '\n', '--fragment')
                self.assertEqual(code, 1)

    def test_some_member_phrase_fails_for_staff(self):
        code, res = run('===== David先生 =====\n\n有成员问这是不是W底：是候选。\n', '--fragment')
        self.assertEqual(code, 1)

    def test_topic_in_handoff_fails(self):
        txt = (self.HEAD + '12男：\nA\n\n===== 资讯助理 =====\n\n带着各自的选择，请David先生开讲三重底。\n\n'
               '===== David先生 =====\n\n各位晚上好。\n')
        code, res = run(txt, '--fragment', '--topic', '三重底')
        self.assertEqual(code, 1)
        code, res = run(txt.replace('请David先生开讲三重底', '看看David先生的看法'), '--fragment', '--topic', '三重底')
        self.assertEqual(res['fail'], [])


class BroadcastHeaders(unittest.TestCase):
    A = '===== 资讯助理 =====\n\n'

    def test_unified_headers_pass(self):
        for h in ('🇺🇸 美股｜昨晚收盘回顾', '🇷🇴 罗马尼亚财经新闻｜某公司公布业绩', '🇺🇸 美国财经新闻｜油价回落',
                  '🇷🇴 罗马尼亚股市开盘｜BET小幅走低', '🇺🇸 美股开盘｜科技股领跌', '📊 精选股票｜EVER',
                  '🏢 公司简介｜Evergent（EVER）', '📈 阶段止盈｜TENB'):
            with self.subTest(h=h):
                code, res = run(self.A + h + '\n\n正文。\n', '--fragment')
                self.assertEqual(res['fail'], [])

    def test_old_or_odd_headers_fail(self):
        for h in ('🇺🇸 美股｜开盘播报', '📈 GIS 阶段止盈提醒', '🇷🇴 罗马尼亚股市｜开盘', '📊 精选股票｜ev er'):
            with self.subTest(h=h):
                code, res = run(self.A + h + '\n\n正文。\n', '--fragment')
                self.assertEqual(code, 1)

    def test_non_breaking_hyphen_fails(self):
        code, res = run(self.A + '🎯 目标价位：3.68‑3.78 RON\n', '--fragment')
        self.assertEqual(code, 1)
        code, res = run(self.A + '🎯 目标价位：3.68–3.78 RON\n', '--fragment')
        self.assertEqual(res['fail'], [])


class GenderTest(unittest.TestCase):
    def test_gender_mismatch_fails(self):
        code, res = run('===== 资讯助理 =====\n🌤大家早上好！欢迎回到 ELLERY WESTCOTT 社群！\n\n37男：\n早\n')
        self.assertEqual(code, 1)
        self.assertTrue(any('性别' in str(x) for x in res.get('findings', res.get('issues', []))) or '性别' in str(res))
