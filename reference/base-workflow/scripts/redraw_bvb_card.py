#!/usr/bin/env python3
"""重画 BVB 开盘卡右下“Alți indici BVB”框（2000x1125 卡片版式）。数字必须是已核实的。
用法：python redraw_bvb_card.py 原图 输出.png "BET-FI|Fonduri de investiții|112.185,73|-0,10%" "BET-NG|Energie|2.648,71|-0,47%"
最多4行；负数红、正数绿；先清空整个框再画，不改卡片其他位置。需要 Pillow。"""
import sys
from PIL import Image, ImageDraw, ImageFont

ROWS = [(666, 741), (752, 826), (838, 913), (924, 998)]
BG, RED, GREEN, WHITE = (10, 40, 68), (240, 98, 108), (60, 200, 150), (222, 235, 245)


def font(size):
    for f in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main(src, out, rows):
    im = Image.open(src).convert('RGB')
    d = ImageDraw.Draw(im)
    f, fs = font(19), font(16)
    for y0, y1 in ROWS:
        d.rectangle((1390, y0 + 3, 1888, y1 - 3), fill=BG)
    for (y0, y1), row in zip(ROWS, rows):
        code, name, val, var = row.split('|')
        cy, col = (y0 + y1) // 2, (RED if var.strip().startswith('-') else GREEN)
        d.text((1402, cy - 20), code, font=f, fill=WHITE)
        d.text((1402, cy + 2), name, font=fs, fill=WHITE)
        for text, x in ((val, 1645), (var, 1870)):
            d.text((x - d.textlength(text, font=f) / 2, cy - 12), text, font=f, fill=col)
    im.save(out)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:7])
