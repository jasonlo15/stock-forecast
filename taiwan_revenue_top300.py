#!/usr/bin/env python3
"""台股今年累計營收排名（上市＋上櫃，不含興櫃/ETF）。Python 3.9+，無外部套件。
執行：python3 taiwan_revenue_top300.py
選項：--top 300 --output ranking.csv --exclude-finance
離線：--listed-json /path/L.json --otc-csv /path/O.csv
資料為官方最新快照，非歷史查詢。相同年度月份才排名；資料更新不一致則停止。
營收單位為新台幣千元；億元 = 千元 / 100000。直接使用本年累計欄位，勿再加總。
官方來源：https://data.gov.tw/dataset/18420 、https://data.gov.tw/dataset/56510
"""
import argparse
import csv
import io
import json
import re
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCES = {
    '上市': 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
    '上櫃': 'https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv',
}
REVENUE = '累計營業收入-當月累計營收'

def download(url):
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent': 'TaiwanRevenueRanking/1.0'})
            with urlopen(req, timeout=60) as response:
                return response.read()
        except Exception as exc:
            if attempt == 2:
                raise RuntimeError(f'下載失敗：{url}；{exc}') from exc
            time.sleep(1 + attempt)

def decode_csv(raw):
    for encoding in ('utf-8-sig', 'cp950'):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(encoding))))
        except UnicodeDecodeError:
            pass
    raise ValueError('CSV 編碼無法辨識')

def period(value):
    parts = re.findall(r'\d+', str(value))
    if len(parts) == 2:
        year, month = map(int, parts)
    elif len(parts) == 1 and len(parts[0]) in (5, 6):
        year, month = int(parts[0][:-2]), int(parts[0][-2:])
    else:
        raise ValueError(f'資料年月格式無法辨識：{value!r}')
    if year < 1911:
        year += 1911
    if not 1 <= month <= 12:
        raise ValueError(f'月份無效：{value!r}')
    return year, month

def number(value):
    s = str(value).strip().replace(',', '')
    try:
        result = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f'營收不是有效數值：{value!r}') from exc
    if not result.is_finite():
        raise ValueError(f'營收不是有限數值：{value!r}')
    return result

def rank(datasets, year, top=300, exclude_finance=False):
    rows, dates, seen = [], set(), set()
    for market, data in datasets.items():
        if not isinstance(data, list) or not data:
            raise ValueError(f'{market}資料為空或格式錯誤')
        for raw in data:
            r = {str(k).strip().lstrip('\ufeff'): v for k, v in raw.items()}
            p = period(r['資料年月'])
            dates.add(p)
            if p[0] != year:
                raise ValueError(f'{market}資料是 {p[0]} 年，並非今年 {year} 年；請等今年資料公布')
            code = str(r['公司代號']).strip()
            if not code or code in seen:
                raise ValueError(f'公司代號缺漏或重複：{code}')
            seen.add(code)
            industry = str(r['產業別']).strip()
            revenue = number(r[REVENUE])
            if exclude_finance and any(x in industry for x in ('金融', '保險', '證券')):
                continue
            rows.append({'股票代號': code, '公司名稱': r['公司名稱'], '市場': market,
                         '產業別': industry, '資料年月': f'{p[0]}-{p[1]:02d}',
                         '累計營收_千元': revenue,
                         '累計營收_億元': f'{revenue / Decimal(100000):.4f}',
                         '累計營收年增率_%': r.get('累計營業收入-前期比較增減(%)', ''),
                         '出表日期_民國': r.get('出表日期', ''), '備註': r.get('備註', '')})
    if len(dates) != 1:
        raise ValueError(f'資料月份不一致：{sorted(dates)}；停止排名，請待兩市場更新至相同月份再試')
    if len(rows) < top:
        raise ValueError(f'只有 {len(rows)} 家可用公司，不足前 {top} 名；停止輸出')
    rows.sort(key=lambda r: (-r['累計營收_千元'], r['股票代號']))
    return [dict(排名=i, **r) for i, r in enumerate(rows[:top], 1)]

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--top', type=int, default=300)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--exclude-finance', action='store_true', help='排除金融、保險、證券產業')
    parser.add_argument('--listed-json', type=Path)
    parser.add_argument('--otc-csv', type=Path)
    args = parser.parse_args()
    if args.top <= 0:
        parser.error('--top 必須大於 0')
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    try:
        listed = args.listed_json.read_bytes() if args.listed_json else download(SOURCES['上市'])
        otc = args.otc_csv.read_bytes() if args.otc_csv else download(SOURCES['上櫃'])
        rows = rank({'上市': json.loads(listed.decode('utf-8-sig')), '上櫃': decode_csv(otc)},
                    now.year, args.top, args.exclude_finance)
        output = args.output or Path(f'tw_revenue_top{args.top}_{rows[0]["資料年月"]}.csv')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f'{now.year} 年 1 月至 {rows[0]["資料年月"]} 累計營收前 {len(rows)} 名')
        print('排名  股票代號  公司名稱  累計營收（億元）')
        for r in rows[:20]:
            print(f'{r["排名"]:>3}  {r["股票代號"]}  {r["公司名稱"]}  {r["累計營收_億元"]}')
        print(f'完整排名：{output.resolve()}')
        print('範圍：官方快照內的上市、上櫃公司。未申報或官方尚未更新者可能不在資料中。')
        print('這是已公布累計營收，並非全年預估；排名無法保證涵蓋尚未申報公司。')
    except (Exception,) as exc:
        print(f'錯誤：{exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
