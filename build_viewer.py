#!/usr/bin/env python3
"""
download_jma.py で取得した日別値CSVを月別・年別に集計し、
ブラウザで開くだけで見られる HTML ビューアを作る（Kibana に入れる前の簡易確認用）。

使い方:
    python build_viewer.py                       # data/raw → viewer/weather.html
    python build_viewer.py data/raw viewer/weather.html

できた viewer/weather.html をダブルクリックで開く（グラフ描画ライブラリを CDN から読むためネット接続は必要）。

集計のルール:
    - 品質情報が 5 以上（8=正常値, 5=準正常値）の値だけを使う
    - 月の値は、その月の日数の 8 割以上の値がそろった月だけ出す（足りない月は空）
    - 年の値は、12か月そろった年だけ出す（気温は月平均の平均、降水量は月合計の合計）

標準ライブラリのみで動作する。CSV の読み込みは convert_jma.py の parse_file をそのまま使う。
"""
import calendar
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from convert_jma import STATION_TO_PREF, parse_file

QUALITY_OK = 5      # これ以上の品質情報の値だけ使う
MIN_RATIO = 0.8     # 月の値を出すのに必要な日数の割合

# 出力キー → convert_jma の項目名
FIELDS = {"t": "temp_avg", "tx": "temp_max", "tn": "temp_min", "p": "precip_total"}

# 都道府県の並び（北から順）と地方の区切り
PREF_ORDER = list(dict.fromkeys(STATION_TO_PREF.values()))
REGIONS = [("北海道", 1), ("東北", 6), ("関東", 7), ("中部", 9),
           ("近畿", 7), ("中国", 5), ("四国", 4), ("九州・沖縄", 8)]
PREF_REGION = {}
_i = 0
for _region, _n in REGIONS:
    for _pref in PREF_ORDER[_i:_i + _n]:
        PREF_REGION[_pref] = _region
    _i += _n

TEMPLATE = Path(__file__).with_name("viewer_template.html")


def value(rec, key):
    v = rec.get(key, "")
    if v == "":
        return None
    q = rec.get(f"{key}_quality", "")
    if q and int(q) < QUALITY_OK:
        return None
    return float(v)


def aggregate(files):
    # (都道府県, 地点) → (年, 月) → 項目 → [合計, 日数]
    sums = defaultdict(lambda: defaultdict(lambda: {f: [0.0, 0] for f in FIELDS}))
    for path in files:
        print(f"読み込み中: {path.name}")
        for rec in parse_file(path):
            cell = sums[(rec["prefecture"], rec["station"])][(rec["year"], rec["month"])]
            for f, key in FIELDS.items():
                v = value(rec, key)
                if v is not None:
                    cell[f][0] += v
                    cell[f][1] += 1

    stations = []
    for (pref, station), months in sums.items():
        years = sorted({y for y, _ in months})
        y0, y1 = years[0], years[-1]
        monthly = {f: [] for f in FIELDS}
        annual = {f: [] for f in FIELDS}
        for y in range(y0, y1 + 1):
            row = {f: [] for f in FIELDS}
            for m in range(1, 13):
                need = calendar.monthrange(y, m)[1] * MIN_RATIO
                cell = months.get((y, m))
                for f in FIELDS:
                    total, n = cell[f] if cell else (0.0, 0)
                    if n < need:
                        row[f].append(None)
                    elif f == "p":
                        row[f].append(round(total, 1))
                    else:
                        row[f].append(round(total / n, 1))
            for f in FIELDS:
                monthly[f].append(row[f])
                vals = row[f]
                if None in vals:
                    annual[f].append(None)
                elif f == "p":
                    annual[f].append(round(sum(vals), 1))
                else:
                    annual[f].append(round(sum(vals) / 12, 1))
        stations.append({
            "key": pref or station, "pref": pref, "station": station,
            "region": PREF_REGION.get(pref, "その他"),
            "y0": y0, "y1": y1, "m": monthly, "a": annual,
        })

    order = {p: i for i, p in enumerate(PREF_ORDER)}
    stations.sort(key=lambda s: (order.get(s["pref"], len(order)), s["station"]))
    keys = [s["key"] for s in stations]
    for s in stations:  # 同じ都道府県に複数地点があれば地点名で区別する
        if keys.count(s["key"]) > 1:
            s["key"] = f'{s["pref"]} {s["station"]}'
    return stations


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "viewer/weather.html")
    files = sorted(src.glob("*.csv"))
    if not files:
        sys.exit(f"{src} に CSV がありません。先に download_jma.py を実行してください")

    stations = aggregate(files)
    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regions": [r for r, _ in REGIONS],
        "stations": stations,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload, 1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html, encoding="utf-8")

    print(f"\n{len(stations)} 地点を集計しました")
    for s in stations:
        n = sum(v is not None for v in s["a"]["t"])
        print(f"  {s['pref']}\t{s['station']}\t{s['y0']}〜{s['y1']}年（年平均気温がそろった年: {n}）")
    print(f"\n→ {dst}（{dst.stat().st_size / 1024:,.0f} KB）をブラウザで開いてください")


if __name__ == "__main__":
    main()
