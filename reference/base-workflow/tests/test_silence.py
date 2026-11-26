import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import silence_check as s


class T(unittest.TestCase):
    def test_must_speak(self):
        tabs = {'2026-11-23': '01男：\n嗨\n02女：\n嗨\n', '2026-11-24': '03男：\n嗨\n',
                '2026-11-25': '04男：\n早\n'}
        prev, need = s.must_speak(tabs, '2026-11-25', '05女：\n早', max_id=6)
        self.assertEqual(prev, ['2026-11-23', '2026-11-24'])
        self.assertEqual(need, ['06'])

    def test_older_silence_counts(self):
        tabs = {'2026-11-20': '01男：\n嗨\n', '2026-11-23': '02女：\n嗨\n', '2026-11-24': '02女：\n嗨\n'}
        prev, need = s.must_speak(tabs, '2026-11-25', '', max_id=3)
        self.assertEqual(need, ['01', '03'])


if __name__ == '__main__':
    unittest.main()
