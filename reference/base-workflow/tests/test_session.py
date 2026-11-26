import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import memory_store as m
import project_session as s
import bundle_doctor as d


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = s.project_file(self.root, 'demo')
        m.init(self.file, 'demo')
        self.payload = m.read_json(ROOT / 'references/session-example.json')

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/project_session.py'),
                               *args, '--data-dir', str(self.root)], cwd=self.root,
                              capture_output=True, text=True, encoding='utf-8')

    def test_new_process_resume_preserves_candidate_and_stop(self):
        payload = self.root / 'input.json'
        payload.write_text(json.dumps(self.payload), encoding='utf-8')
        first = self.cli('checkpoint', '--project', 'demo', '--input', str(payload))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.cli('resume', '--project', 'demo')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        loaded = json.loads(second.stdout)
        self.assertEqual(loaded['session'], self.payload['session'])
        self.assertEqual(loaded['session']['adopted_text'], '')
        self.assertTrue(loaded['session']['candidate_text'])
        self.assertIn('publication', loaded['not_verified'])

    def test_two_windows_conflict_does_not_overwrite(self):
        s.checkpoint(self.root, 'demo', self.payload)
        stale = copy.deepcopy(self.payload)
        stale['operation_id'] = 'window-2'
        stale['session']['next_step'] = 'stale change'
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            s.checkpoint(self.root, 'demo', stale)
        self.assertEqual(self.file.read_bytes(), before)

    def test_preview_does_not_write(self):
        before = self.file.read_bytes()
        result = s.preview(self.root, 'demo', self.payload)
        self.assertEqual(result['status'], 'PREVIEW_ONLY')
        self.assertIn('candidate_text', result['changes'])
        self.assertEqual(self.file.read_bytes(), before)

    def test_record_conflict_and_retry(self):
        s.checkpoint(self.root, 'demo', self.payload)
        self.assertEqual(s.checkpoint(self.root, 'demo', self.payload)['status'], 'ALREADY_APPLIED')
        self.assertEqual(s.preview(self.root, 'demo', self.payload)['status'], 'ALREADY_APPLIED')
        bad = copy.deepcopy(self.payload)
        bad.update(operation_id='new-op', expected_revision=1)
        with self.assertRaisesRegex(ValueError, 'record version conflict'):
            s.checkpoint(self.root, 'demo', bad)

    def test_adoption_requires_evidence(self):
        self.payload['session']['adopted_text'] = 'unconfirmed'
        with self.assertRaisesRegex(ValueError, 'adoption evidence'):
            s.checkpoint(self.root, 'demo', self.payload)

    def test_save_requires_authorization(self):
        self.payload['save_authorization_ref'] = ''
        with self.assertRaises(ValueError):
            s.checkpoint(self.root, 'demo', self.payload)

    def test_valid_adoption_and_candidate_stay_separate(self):
        self.payload['session'].update(adopted_text='confirmed excerpt', adoption_ref='user approval', completed_count=1)
        s.checkpoint(self.root, 'demo', self.payload)
        result = s.resume(self.root, 'demo')['session']
        self.assertEqual(result['adopted_text'], 'confirmed excerpt')
        self.assertEqual(result['candidate_text'], self.payload['session']['candidate_text'])

    def test_missing_project_does_not_initialize(self):
        with self.assertRaises(FileNotFoundError):
            s.resume(self.root, 'missing')
        self.assertFalse(s.project_file(self.root, 'missing').exists())

    def test_corruption_fails_without_overwrite(self):
        self.file.write_text('broken')
        with self.assertRaises(ValueError):
            s.resume(self.root, 'demo')
        self.assertEqual(self.file.read_text(), 'broken')

    def test_project_isolation_and_listing(self):
        other = s.project_file(self.root, 'other')
        m.init(other, 'other')
        s.checkpoint(self.root, 'demo', self.payload)
        self.assertEqual(s.resume(self.root, 'other')['status'], 'NO_CHECKPOINT')
        result = s.list_projects(self.root)
        self.assertEqual({x['project_id'] for x in result['projects']}, {'demo', 'other'})
        self.assertFalse(result['auto_selected'])

    def test_project_id_not_a_path(self):
        path = s.project_file(self.root, 'a/../../elsewhere')
        self.assertEqual(path.parent.parent, self.root)
        self.assertEqual(len(path.parent.name), 64)

    def test_fixed_root_across_working_directories(self):
        with patch.dict(os.environ, {'WESTCOTT_DATA_HOME': str(self.root)}):
            self.assertEqual(s.data_root(), self.root.resolve())
            self.assertEqual(s.project_file(None, 'demo'), self.file)
        with patch.dict(os.environ, {'WESTCOTT_DATA_HOME': 'relative/path'}):
            with self.assertRaises(ValueError): s.data_root()

    def test_inspect_maintenance(self):
        s.checkpoint(self.root, 'demo', self.payload)
        result = s.inspect(self.root, 'demo')
        self.assertEqual(result['bytes'], self.file.stat().st_size)
        self.assertEqual(result['operation_count'], 1)
        self.assertFalse(result['automatic_cleanup'])

    def test_invalid_session_fields(self):
        for key, value in [('completed_count', True), ('selected_ids', ['66']), ('source_revision', ''), ('candidate_text', 'x' * 10000)]:
            candidate = copy.deepcopy(self.payload)
            candidate['session'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                s.checkpoint(self.root, 'demo', candidate)

    def test_snapshot_lookup(self):
        snapdir = self.root / 'snapshots'
        m.export_snapshot(self.file, 'demo', snapdir / 'one.json')
        result = d.snapshots(snapdir)
        self.assertEqual(result[0]['project_id'], 'demo')
        self.assertEqual(result[0]['status'], 'VALID')
        env = m.read_json(snapdir / 'one.json'); env['sha256'] = '0' * 64
        (snapdir / 'one.json').write_text(json.dumps(env))
        self.assertEqual(d.snapshots(snapdir)[0]['status'], 'INVALID_OR_OTHER')

    def test_manifest_tamper_detection(self):
        bundle = self.root / 'bundle'; bundle.mkdir()
        (bundle / 'SKILL.md').write_text('original')
        manifest = {'version': 'test', 'files': {'SKILL.md': hashlib.sha256(b'original').hexdigest()}}
        (bundle / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(d.verify(bundle)['status'], 'BUNDLE_OK')
        (bundle / 'SKILL.md').write_text('changed')
        self.assertEqual(d.verify(bundle)['status'], 'BUNDLE_CHANGED')

    def test_duplicate_scan_does_not_claim_enabled(self):
        directory = self.root / 'old'; directory.mkdir()
        (directory / 'SKILL.md').write_text('---\nname: westcott-control\n---\n')
        result = d.scan(self.root)
        self.assertEqual(result[0]['name'], 'westcott-control')
        self.assertEqual(result[0]['enabled'], 'NOT_VERIFIED')


if __name__ == '__main__':
    unittest.main()
