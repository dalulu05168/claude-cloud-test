import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from plan_context import plan
from render_evening_chart import validate

class Evening(unittest.TestCase):
 def review(self,text,date='2026-11-12'):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'draft.txt';p.write_text('===== David先生 =====\n'+text,encoding='utf-8')
   r=subprocess.run([sys.executable,'-I',str(ROOT/'scripts/review_draft.py'),str(p),'--scope','evening','--fragment','--date',date,'--json'],capture_output=True,encoding='utf-8')
   return r.returncode,json.loads(r.stdout)
 def test_route_evening(self):
  self.assertIn('references/evening.md',plan('LESSON',evening=True)['read_once'])
  self.assertNotIn('references/evening.md',plan('NEWS')['read_once'])
 def test_evening_lesson_loads_member_voice(self):
  self.assertIn('references/members.md',plan('LESSON',evening=True)['read_once'])
  self.assertNotIn('references/members.md',plan('LESSON')['read_once'])
  self.assertNotIn('references/members.md',plan('QA',evening=True)['read_once'])
 def test_thursday_explicit_homework_fails(self):
  self.assertEqual(self.review('请完成课后练习。')[0],1)
 def test_thursday_disguised_task_fails(self):
  self.assertEqual(self.review('明天把观察图发给Gabriela。')[0],1)
 def test_tuesday_no_opening_still_fails(self):
  self.assertEqual(self.review('请完成作业。','2026-11-10')[0],1)
 def test_negated_homework_is_not_assignment(self):
  code,r=self.review('今晚不布置作业。')
  self.assertEqual(code,0);self.assertEqual(r['counts'],{'晚间':1})
 def test_friday_optional_task(self):
  self.assertEqual(self.review('请完成课后练习。','2026-11-13')[0],0)
 def test_intraclass_question(self):
  self.assertEqual(self.review('现在看图，触及边界和确认突破有什么不同？')[0],0)
 def test_bad_date_is_input_error(self):
  self.assertEqual(self.review('观察结构。','2026-99-99')[0],2)
 def test_ohlc_rejects_impossible_bars(self):
  bar={'o':10,'h':9,'l':8,'c':11,'v':1}
  with self.assertRaises(ValueError):validate({'title':'模拟','note':'示例','bars':[bar]*6})
 def test_ohlc_accepts_valid_sequence(self):
  bar={'o':10,'h':12,'l':9,'c':11,'v':1}
  self.assertEqual(len(validate({'title':'模拟','note':'示例','bars':[bar]*6})),6)
