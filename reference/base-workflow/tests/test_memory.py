import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import memory_store as m


def change(text='说明使用中文。', key='rule.language', version=0, status='confirmed', kind='rule'):
    return {'key':key,'expected_record_version':version,'record':{'kind':kind,'status':status,'text':text,
            'source':{'ref':'test user message','revision':'1'},'confirmation_ref':'explicit test request' if status=='confirmed' else ''}}


def patch(c=None, op='op-1'):
    return {'project_id':'demo','operation_id':op,'changes':[c or change()]}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'state.json'
        m.init(self.path,'demo')
    def tearDown(self):self.tmp.cleanup()
    def test_init_no_overwrite(self):
        with self.assertRaises(ValueError):m.init(self.path,'demo')
    def test_apply_query(self):
        result=m.apply(self.path,'demo',patch(),0)
        self.assertEqual(result['status'],'SAVED_LOCAL')
        self.assertEqual(result['persistent_cloud_storage'],'NOT_VERIFIED')
        self.assertEqual(m.query(self.path,'demo')['records']['rule.language']['text'],'说明使用中文。')
    def test_duplicate_retry_is_noop(self):
        m.apply(self.path,'demo',patch(),0);before=self.path.read_bytes()
        self.assertEqual(m.apply(self.path,'demo',patch(),0)['status'],'ALREADY_APPLIED')
        self.assertEqual(self.path.read_bytes(),before)
    def test_operation_reuse_rejected(self):
        m.apply(self.path,'demo',patch(),0)
        with self.assertRaises(ValueError):m.apply(self.path,'demo',patch(change('changed')),1)
    def test_stale_global_revision(self):
        m.apply(self.path,'demo',patch(),0)
        with self.assertRaises(ValueError):m.apply(self.path,'demo',patch(change('new',version=1),'op-2'),0)
    def test_stale_record_revision(self):
        m.apply(self.path,'demo',patch(),0)
        with self.assertRaises(ValueError):m.apply(self.path,'demo',patch(change('new'),'op-2'),1)
    def test_history_preserved(self):
        m.apply(self.path,'demo',patch(),0);m.apply(self.path,'demo',patch(change('改为简短中文。',version=1),'op-2'),1)
        s=m.load(self.path,'demo');self.assertEqual(s['records']['rule.language']['version'],2)
        self.assertEqual(s['history'][0]['record']['text'],'说明使用中文。')
    def test_batch_all_or_nothing(self):
        before=self.path.read_bytes();p=patch();p['changes'].append(change(key='rule.other',version=9))
        with self.assertRaises(ValueError):m.apply(self.path,'demo',p,0)
        self.assertEqual(self.path.read_bytes(),before)
    def test_duplicate_keys_rejected(self):
        p=patch();p['changes'].append(change())
        with self.assertRaises(ValueError):m.apply(self.path,'demo',p,0)
    def test_confirmation_required(self):
        p=patch();p['changes'][0]['record']['confirmation_ref']=''
        with self.assertRaises(ValueError):m.apply(self.path,'demo',p,0)
    def test_candidate_not_read_as_confirmed(self):
        m.apply(self.path,'demo',patch(change(status='candidate')),0)
        self.assertEqual(m.query(self.path,'demo')['matched'],0)
        self.assertEqual(m.query(self.path,'demo',status='candidate')['matched'],1)
    def test_adopted_needs_confirmation(self):
        with self.assertRaises(ValueError):m.apply(self.path,'demo',patch(change(kind='adopted',status='candidate')),0)
    def test_project_mismatch(self):
        with self.assertRaises(ValueError):m.query(self.path,'other')
        p=patch();p['project_id']='other'
        with self.assertRaises(ValueError):m.apply(self.path,'demo',p,0)
    def test_query_filter_and_budget(self):
        p=patch();p['changes'].append(change('A'*1000,key='rule.long'));m.apply(self.path,'demo',p,0)
        q=m.query(self.path,'demo',limit=1);self.assertEqual(q['omitted'],1)
        q=m.query(self.path,'demo',keys=['rule.long'],max_chars=100);self.assertEqual(q['returned'],0);self.assertEqual(q['omitted'],1)
    def test_snapshot_round_trip(self):
        m.apply(self.path,'demo',patch(),0);snap=self.root/'snapshot.json';target=self.root/'restored/state.json'
        m.export_snapshot(self.path,'demo',snap);m.restore(snap,target,'demo')
        self.assertEqual(m.load(self.path,'demo'),m.load(target,'demo'))
    def test_tamper_detection(self):
        snap=self.root/'snapshot.json';m.export_snapshot(self.path,'demo',snap);v=m.read_json(snap);v['data']['project_id']='altered';snap.write_text(json.dumps(v))
        with self.assertRaises(ValueError):m.restore(snap,self.root/'new.json','altered')
    def test_restore_and_export_no_overwrite(self):
        snap=self.root/'snapshot.json';m.export_snapshot(self.path,'demo',snap)
        with self.assertRaises(ValueError):m.export_snapshot(self.path,'demo',snap)
        with self.assertRaises(ValueError):m.restore(snap,self.path,'demo')
    def test_schema_and_corruption(self):
        state=m.read_json(self.path);state['schema_version']=99;self.path.write_text(json.dumps(state))
        with self.assertRaises(ValueError):m.query(self.path,'demo')
        self.path.write_text('broken')
        with self.assertRaises(ValueError):m.query(self.path,'demo')
    def test_lock_prevents_write(self):
        self.path.with_name('state.json.lock').write_text('busy')
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):m.apply(self.path,'demo',patch(),0)
        self.assertEqual(self.path.read_bytes(),before)
    def test_cli_from_other_directory(self):
        result=subprocess.run([sys.executable,'-I','-S','-B',str(ROOT/'scripts/memory_store.py'),'read','--file',str(self.path),'--project','demo'],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0);self.assertEqual(json.loads(result.stdout)['revision'],0)
    def test_patch_unknown_field_rejected(self):
        p=patch();p['extra']='bad'
        with self.assertRaises(ValueError):m.apply(self.path,'demo',p,0)
    def test_error_does_not_leave_lock(self):
        with self.assertRaises(ValueError):m.apply(self.path,'demo',patch(),9)
        self.assertFalse(self.path.with_name('state.json.lock').exists())

    def test_temporary_corruption_preserves_original(self):
        before=self.path.read_bytes()
        real_read=m.read_json
        def corrupt(path):
            if path != self.path:
                path.write_text('{}',encoding='utf-8')
            return real_read(path)
        with mock_patch.object(m,'read_json',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'temporary file verification failed'):
                m.apply(self.path,'demo',patch(),0)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['state.json'])

    def make_history(self):
        m.apply(self.path,'demo',patch(),0)
        m.apply(self.path,'demo',patch(change('updated',version=1),'op-2'),1)

    def test_archive_roundtrip_and_retry_receipts(self):
        self.make_history();old=m.load(self.path,'demo');snap=self.root/'archive.json'
        receipt=m.archive_history(self.path,'demo',snap,2)
        current=m.load(self.path,'demo')
        self.assertEqual(receipt['status'],'ARCHIVED_LOCAL')
        self.assertEqual(current['revision'],3)
        self.assertEqual(current['history'],[])
        self.assertEqual(current['records'],old['records'])
        self.assertEqual(current['operations'],old['operations'])
        m.restore(snap,self.root/'restored.json','demo')
        self.assertEqual(m.load(self.root/'restored.json','demo'),old)
        self.assertEqual(m.apply(self.path,'demo',patch(),0)['status'],'ALREADY_APPLIED')
        m.apply(self.path,'demo',patch(change('next',version=2),'op-3'),3)
        self.assertEqual(len(m.load(self.path,'demo')['history']),1)

    def test_archive_conflict_and_no_overwrite(self):
        self.make_history();before=self.path.read_bytes();snap=self.root/'archive.json'
        with self.assertRaises(ValueError):m.archive_history(self.path,'demo',snap,1)
        self.assertFalse(snap.exists())
        snap.write_text('keep')
        with self.assertRaises(ValueError):m.archive_history(self.path,'demo',snap,2)
        self.assertEqual(snap.read_text(),'keep')
        self.assertEqual(self.path.read_bytes(),before)

    def test_archive_snapshot_failure_keeps_store(self):
        self.make_history();before=self.path.read_bytes()
        with mock_patch.object(m,'export_snapshot',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):m.archive_history(self.path,'demo',self.root/'archive.json',2)
        self.assertEqual(self.path.read_bytes(),before)

    def test_archive_update_failure_keeps_snapshot(self):
        self.make_history();before=self.path.read_bytes();snap=self.root/'archive.json'
        real_write=m.write_atomic
        def fail_active(path,value):
            if path == self.path:raise OSError('active write failure')
            return real_write(path,value)
        with mock_patch.object(m,'write_atomic',side_effect=fail_active):
            with self.assertRaises(OSError):m.archive_history(self.path,'demo',snap,2)
        self.assertEqual(self.path.read_bytes(),before)
        m.restore(snap,self.root/'recovered.json','demo')
        self.assertEqual(m.load(self.root/'recovered.json','demo'),m.load(self.path,'demo'))

    def test_archive_cli(self):
        self.make_history()
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/memory_store.py'),'archive',
            '--file',str(self.path),'--project','demo','--snapshot',str(self.root/'cli.json'),
            '--expected-revision','2'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'],'ARCHIVED_LOCAL')

if __name__=='__main__':unittest.main()
