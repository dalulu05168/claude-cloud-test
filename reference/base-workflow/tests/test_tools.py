import subprocess, sys, tempfile, unittest
from pathlib import Path
S = Path(__file__).resolve().parents[1] / 'scripts'


class T(unittest.TestCase):
    def test_redraw(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / 'a.png', Path(d) / 'b.png'
            Image.new('RGB', (2000, 1125), (10, 40, 68)).save(src)
            r = subprocess.run([sys.executable, str(S / 'redraw_bvb_card.py'), str(src), str(out),
                                'BET-FI|Fonduri|112.185,73|-0,10%'], capture_output=True)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(out.exists())

    def test_daily_prep_help(self):
        r = subprocess.run([sys.executable, str(S / 'daily_prep.py'), '--help'], capture_output=True)
        self.assertEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
