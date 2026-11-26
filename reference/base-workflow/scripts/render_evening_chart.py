#!/usr/bin/env python3
"""Render an explicitly simulated teaching chart. Requires Pillow, no network.

python render_evening_chart.py --data chart.json --font /path/to/chinese-font --out chart.png
Data keys: title, note, bars [{o,h,l,c,v}], levels [{price,label}], focus (optional).
Default Logo is the unmodified user-provided bundled asset. Does not generate real market data.
"""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(data):
    bars = data.get('bars', [])
    if not isinstance(bars, list) or not 6 <= len(bars) <= 100:
        raise ValueError('bars must contain 6..100 simulated candles')
    for b in bars:
        vals = [b[k] for k in ('o', 'h', 'l', 'c', 'v')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vals):
            raise ValueError('OHLCV must be finite numbers')
        if b['h'] < max(b['o'], b['c']) or b['l'] > min(b['o'], b['c']) or b['v'] < 0:
            raise ValueError('invalid OHLC or volume')
    for level in data.get('levels', []):
        if not isinstance(level['price'], (int, float)) or not math.isfinite(level['price']):
            raise ValueError('invalid level')
    if not data.get('title') or not data.get('note'):
        raise ValueError('title and note required')
    return bars


def render(data, font_path, out, logo=None):
    bars = validate(data)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RuntimeError('Chart rendering requires Pillow; core workflow scripts do not.') from e
    logo = Path(logo) if logo else ROOT / 'assets/westcott-logo-original.png'
    image = Image.new('RGB', (3840, 2160), 'white')
    draw = ImageDraw.Draw(image)
    font = lambda size: ImageFont.truetype(str(font_path), size)
    brand = Image.open(logo).convert('RGBA')
    if brand.getchannel('A').getextrema()[0] != 0:
        raise ValueError('Logo has no fully transparent background pixels')
    brand.thumbnail((442, 442), Image.Resampling.LANCZOS)
    image.paste(brand, (96, 96), brand)
    draw.text((660, 130), data['title'], fill='#252525', font=font(82))
    draw.text((660, 260), '教学模拟示意  非真实行情  不构成买卖指令', fill='#657384', font=font(42))
    x0, x1, y0, y1 = 250, 3550, 610, 1550
    prices = [b[k] for b in bars for k in ('h', 'l')] + [v['price'] for v in data.get('levels', [])]
    low, high = min(prices), max(prices)
    pad = max((high-low)*0.15, 0.5)
    low -= pad; high += pad
    y = lambda price: y1 - (price-low)/(high-low)*(y1-y0)
    n = max(len(bars), 32)
    step = (x1-x0)/n
    x = lambda i: x0 + step*(i+0.5)
    for j in range(5):
        price = low + (high-low)*j/4
        py = y(price)
        draw.line((x0, py, x1, py), fill='#E4E9EE', width=2)
        draw.text((70, py-25), f'{price:.1f}', fill='#657384', font=font(38))
    for level in data.get('levels', []):
        py = y(level['price'])
        for px in range(x0, x1, 38):
            draw.line((px, py, min(px+22, x1), py), fill='#E58B2A', width=5)
        draw.text((x0+30, py-65), level['label'], fill='#995B16', font=font(44))
    vmax = max(max(b['v'] for b in bars), 1)
    for i, b in enumerate(bars):
        color = '#168A55' if b['c']>b['o'] else '#D64545' if b['c']<b['o'] else '#718096'
        pale = '#A6D8BE' if b['c']>b['o'] else '#EAB1B1' if b['c']<b['o'] else '#B7C0CA'
        px=x(i); width=step*0.55
        draw.line((px,y(b['h']),px,y(b['l'])),fill=color,width=5)
        top,bottom=sorted((y(b['o']),y(b['c'])))
        draw.rectangle((px-width/2,top,px+width/2,max(bottom,top+4)), fill=color)
        draw.rectangle((px-width/2,1890-b['v']/vmax*220,px+width/2,1890),fill=pale)
    draw.text((70, 1660),'量',fill='#657384',font=font(38))
    draw.line((x0, 1910, x1, 1910), fill='#9AABBC', width=3)
    draw.text((x0,1940),'观察顺序  →    K线绿涨红跌按收盘价对开盘价判定',fill='#657384',font=font(38))
    note=data['note']
    if draw.textbbox((0,0),note,font=font(48))[2] > 3300:
        raise ValueError('note too long for a single-line teaching point')
    draw.rounded_rectangle((210,420,3590,540),radius=20,fill='#F6F1E7')
    draw.text((250,450),note,fill='#252525',font=font(48))
    draw.text((250,2050),'相对价格与模拟成交量  ·  同一图组沿用基础走势  ·  等待确认并保留失效情景',fill='#657384',font=font(36))
    out=Path(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        raise ValueError('output already exists; choose a new filename')
    image.save(out, format='PNG')
    return {'width':3840,'height':2160,'mode':'RGB','bars':len(bars),'logo_source':logo.name,'simulation':True}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',required=True,type=Path)
    parser.add_argument('--font',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--logo',type=Path)
    args=parser.parse_args()
    try:
        result=render(json.loads(args.data.read_text(encoding='utf-8')),args.font,args.out,args.logo)
    except (OSError,ValueError,KeyError,RuntimeError) as error:
        parser.exit(2,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))
