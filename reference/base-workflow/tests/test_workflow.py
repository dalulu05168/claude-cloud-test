import sys, unittest, json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_scene import check, NOTICE
from plan_context import plan

def scene(body):return NOTICE+'\n'+body
class Checks(unittest.TestCase):
 def test_valid(self):self.assertEqual(check(scene('14男：看看条件。'),{'expected_count':1})['status'],'PASS')
 def test_rejections(self):
  cases=[('',{}),('无标签',{}),('14男：句子',{}),(scene('10男：句子'),{}),(scene('66男：句子'),{}),(scene('53男：句子'),{}),(scene('===== 先生 =====\n句子'),{}),(scene('14男：'),{}),(scene('14男：句子'),{'expected_count':2}),(scene('14男：我们下午见'),{'forbid_close':True}),(scene('14男：答案是甲'),{'forbidden_literals':['答案是']}),(scene('14男：句子'),{'protected_literals':['SNN']}),(scene('14男：**句子**'),{}),(scene('14男：句子'),{'role_order':['TEACHER']}),(scene('14男：句子'),{'simulation':False}),(scene('14男：句子'),{'allowed_ids':['02']}),(scene('14男：句子\n1男：坏标签'),{}),(scene('14男：句子\n===== 顾问 =====\n未知'),{})]
  for text,c in cases:
   with self.subTest(text=text,c=c):self.assertEqual(check(text,c)['status'],'FAIL')
 def test_contract_errors(self):
  for c in [{'unexpected':1},{'expected_count':True},{'existing_count':21},{'existing_count':21,'target_count':35,'expected_count':13},{'allowed_ids':['1']},{'mode':'edit'},{'simulation':'false'},{'role_order':['BAD']},{'mode':'bad'}]:
   with self.subTest(c=c),self.assertRaises(ValueError):check(scene('14男：句子'),c)
 def test_append(self):
  self.assertEqual(check(scene('\n'.join('14男：第'+str(i)+'句' for i in range(14))),{'existing_count':21,'target_count':35})['status'],'PASS')
 def test_edit_archive(self):
  from check_scene import load_roster
  g=load_roster()['53']
  self.assertEqual(check(scene('53'+g+'：句子'),{'mode':'edit','allowed_ids':['53']})['status'],'PASS')
 def test_scene_cannot_expand_to_archived_ids(self):
  from check_scene import load_roster
  for ident in ('53', '65'):
   for mode in ({}, {'mode':'scene'}):
    for field in ('allowed_ids', 'role_order'):
     with self.subTest(ident=ident,mode=mode,field=field), self.assertRaises(ValueError):
      check(scene(ident+load_roster()[ident]+'：句子'),dict(mode, **{field:[ident]}))
  self.assertEqual(check(scene('45男：句子'),{'mode':'scene','allowed_ids':['45']})['status'],'PASS')
  self.assertEqual(check(scene('46男：句子'+chr(10)+'47女：句子'),{'mode':'scene','allowed_ids':['46','47']})['status'],'PASS')
 def test_edit_numbers(self):
  c={'mode':'edit','allowed_ids':['14'],'original_text':'14男：价格120，增长50%。'}
  self.assertEqual(check(scene('14男：增长50%，价格120。'),c)['status'],'PASS')
  self.assertEqual(check(scene('14男：增长60%，价格120。'),c)['status'],'FAIL')
 def test_staff_news(self):self.assertEqual(check('===== 资讯助理 =====\n待核新闻。',{'simulation':False})['status'],'PASS')
 def test_empty_between_headers(self):self.assertEqual(check(scene('14男：\n02女：句子'),{})['status'],'FAIL')
 def test_no_hidden_fact_pass(self):self.assertIn('facts',check(scene('14男：句子'),{})['not_verified'])
 def test_route(self):
  self.assertEqual(plan('EDIT',['14'])['read_once'],['SKILL.md','references/writer.md','references/members.md'])
  self.assertEqual(plan('NEWS')['read_once'],['SKILL.md','references/assistant.md'])
  self.assertEqual(plan('RESUME')['read_once'],['SKILL.md','references/session.md'])
  self.assertEqual(set(plan('EDIT',['14'])['selected_genders']),{'14'})
 def test_route_errors(self):
  for args in [('BAD',),('EDIT',['1']),('EDIT',[],['unknown'])]:
   with self.subTest(args=args),self.assertRaises(ValueError):plan(*args)
 def test_cli_exit(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'t.txt').write_text('无标签',encoding='utf-8');(p/'c.json').write_text('{}',encoding='utf-8')
   result=subprocess.run([sys.executable,str(ROOT/'scripts/check_scene.py'),str(p/'t.txt'),'--contract',str(p/'c.json')],capture_output=True,text=True,encoding='utf-8')
   self.assertEqual(result.returncode,1);self.assertEqual(json.loads(result.stdout)['status'],'FAIL')
if __name__=='__main__':unittest.main()
