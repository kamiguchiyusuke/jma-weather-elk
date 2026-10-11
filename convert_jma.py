#!/usr/bin/env python3
"""
気象庁「過去の気象データ・ダウンロード」の日別値CSVを、
Logstash で取り込みやすい「1行 = 1地点 × 1日」の縦持ちCSVに変換する。

使い方:
    python3 scripts/convert_jma.py data/raw data/processed

前提（ダウンロード時の表示オプション）:
    - データの種類: 日別値
    - 「すべて数値で格納」を選ぶ（記号付きの値は扱わない）
    - 「都道府県名を格納」を選ぶと都道府県が自動で入る（推奨）
      選ばなかった場合は STATION_TO_PREF で地点名から補完する

標準ライブラリのみで動作する。
"""
import csv
import io
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 出力列（Logstash の csv フィルタの columns と同じ順番にすること）
ITEMS = ["temp_avg", "temp_max", "temp_min", "precip_total"]
OUT_COLUMNS = ["date", "year", "month", "day", "prefecture", "station"]
for _item in ITEMS:
    OUT_COLUMNS += [_item, f"{_item}_quality", f"{_item}_homogeneity"]
OUT_COLUMNS += ["precip_total_nophenomenon"]

# CSV の項目名 → 出力フィールド名（部分一致で判定）
def item_key(name: str):
    n = name.replace(" ", "")
    if "平均気温" in n:
        return "temp_avg"
    if "最高気温" in n and "起時" not in n:
        return "temp_max"
    if "最低気温" in n and "起時" not in n:
        return "temp_min"
    if "降水量" in n and "合計" in n:
        return "precip_total"
    return None

# サブ見出し（第5行）→ 値の種類
SUB_KEYS = {
    "": "value",
    "品質情報": "quality",
    "均質番号": "homogeneity",
    "現象なし情報": "nophenomenon",
}

# 「都道府県名を格納」を選ばなかった場合の補完用（都道府県の気象台など）
STATION_TO_PREF = {
    "札幌": "北海道", "青森": "青森県", "盛岡": "岩手県", "仙台": "宮城県",
    "秋田": "秋田県", "山形": "山形県", "福島": "福島県", "水戸": "茨城県",
    "宇都宮": "栃木県", "前橋": "群馬県", "熊谷": "埼玉県", "さいたま": "埼玉県",
    "千葉": "千葉県", "東京": "東京都", "横浜": "神奈川県", "新潟": "新潟県",
    "富山": "富山県", "金沢": "石川県", "福井": "福井県", "甲府": "山梨県",
    "長野": "長野県", "岐阜": "岐阜県", "静岡": "静岡県", "名古屋": "愛知県",
    "津": "三重県", "彦根": "滋賀県", "京都": "京都府", "大阪": "大阪府",
    "神戸": "兵庫県", "奈良": "奈良県", "和歌山": "和歌山県", "鳥取": "鳥取県",
    "松江": "島根県", "岡山": "岡山県", "広島": "広島県", "下関": "山口県",
    "山口": "山口県", "徳島": "徳島県", "高松": "香川県", "松山": "愛媛県",
    "高知": "高知県", "福岡": "福岡県", "佐賀": "佐賀県", "長崎": "長崎県",
    "熊本": "熊本県", "大分": "大分県", "宮崎": "宮崎県", "鹿児島": "鹿児島県",
    "那覇": "沖縄県",
}

NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    # UTF-8 を先に試す（Shift_JIS のファイルを UTF-8 として読むと必ずエラーになるので判定できる）
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: 文字コードを判定できません（Shift_JIS / UTF-8 以外）")


def looks_like_pref_row(row) -> bool:
    cells = [c.strip() for c in row[1:] if c.strip()]
    return bool(cells) and all(c[-1] in "都道府県" for c in cells)


def parse_file(path: Path):
    rows = list(csv.reader(io.StringIO(read_text(path))))

    # 「年月日」で始まる行（項目名の行）を探す
    h = next((i for i, r in enumerate(rows) if r and r[0].strip() == "年月日"), None)
    if h is None:
        if any(r and r[0].strip() == "年月日時" for r in rows):
            raise ValueError(f"{path}: 時別値のCSVです。このスクリプトは日別値のみ対応しています")
        raise ValueError(f"{path}: 「年月日」の見出し行が見つかりません。日別値のCSVか確認してください")

    item_row = rows[h]
    station_row = rows[h - 1]
    pref_row = rows[h - 2] if h >= 2 and looks_like_pref_row(rows[h - 2]) else None

    # 項目名の行とデータの間には、空の行（,,,,）と「品質情報・均質番号」の見出し行が入ることがある。
    # 先頭セルが空の行を見ていき、何か書いてある行を見出し行として使う。
    data_start = h + 1
    sub_row = None
    while data_start < len(rows) and (not rows[data_start] or rows[data_start][0].strip() == ""):
        if any(c.strip() for c in rows[data_start]):
            sub_row = rows[data_start]
        data_start += 1

    # 列番号 → (地点, 都道府県, 項目, 値の種類)
    colmap = {}
    skipped_items = set()
    for j in range(1, len(item_row)):
        station = station_row[j].strip() if j < len(station_row) else ""
        key = item_key(item_row[j])
        if not station:
            continue
        if key is None:
            skipped_items.add(item_row[j].strip())
            continue
        sub = sub_row[j].strip() if sub_row and j < len(sub_row) else ""
        kind = SUB_KEYS.get(sub)
        if kind is None:
            continue
        pref = pref_row[j].strip() if pref_row and j < len(pref_row) else STATION_TO_PREF.get(station, "")
        colmap[j] = (station, pref, key, kind)

    if not colmap:
        raise ValueError(f"{path}: 対応する項目（平均・最高・最低気温、降水量の合計）が見つかりません")
    if skipped_items:
        print(f"  [注意] 対象外の項目をスキップ: {', '.join(sorted(skipped_items))}", file=sys.stderr)

    out = []
    for r in rows[data_start:]:
        if not r or not r[0].strip():
            continue
        y, m, d = (int(x) for x in r[0].strip().replace("-", "/").split("/")[:3])
        recs = defaultdict(dict)
        for j, (station, pref, key, kind) in colmap.items():
            v = r[j].strip() if j < len(r) else ""
            if v and not NUMERIC.match(v):
                raise ValueError(
                    f"{path}: 数値でない値 '{v}' があります（{r[0]} {station}）。"
                    "ダウンロード時に「すべて数値で格納」を選んでください"
                )
            rec = recs[(station, pref)]
            col = key if kind == "value" else f"{key}_{kind}"
            rec[col] = v
        for (station, pref), rec in recs.items():
            rec.update({
                "date": f"{y:04d}-{m:02d}-{d:02d}", "year": y, "month": m, "day": d,
                "prefecture": pref, "station": station,
            })
            out.append(rec)
    return out


def main():
    if len(sys.argv) != 3:
        print("使い方: python3 scripts/convert_jma.py <入力フォルダ> <出力フォルダ>", file=sys.stderr)
        sys.exit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.csv"))
    if not files:
        print(f"{src} に CSV がありません", file=sys.stderr)
        sys.exit(1)

    total = Counter()
    missing_pref = set()
    for f in files:
        print(f"変換中: {f.name}")
        recs = parse_file(f)
        out_path = dst / f"{f.stem}_tidy.csv"
        with out_path.open("w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=OUT_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for rec in recs:
                w.writerow(rec)
                total[(rec["prefecture"], rec["station"])] += 1
                if not rec["prefecture"]:
                    missing_pref.add(rec["station"])
        print(f"  → {out_path}（{len(recs):,} 行）")

    print("\n地点ごとの行数（取り込み後に Kibana の件数と突き合わせる）")
    for (pref, station), n in sorted(total.items()):
        print(f"  {pref or '(都道府県不明)'}\t{station}\t{n:,}")
    print(f"合計: {sum(total.values()):,} 行")
    if missing_pref:
        print(f"\n[注意] 都道府県が空の地点: {', '.join(sorted(missing_pref))}"
              "（「都道府県名を格納」を選ぶか STATION_TO_PREF に追加）", file=sys.stderr)


if __name__ == "__main__":
    main()
