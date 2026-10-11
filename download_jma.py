#!/usr/bin/env python3
"""
気象庁「過去の気象データ・ダウンロード」から、47都道府県の気象台の日別値CSVを
自動で順番にダウンロードする（2026年10月時点の新しい画面に対応）。

画面からは1回のダウンロード量に上限があるため、地点ごと・期間ごとに分けて取得する。
上限に引っかかった場合は、期間を自動で半分にして取り直す。

使い方:
    pip install requests
    # まず1地点・1年で試す
    python download_jma.py --start 2024 --end 2024 --stations 東京
    # 47地点すべて
    python download_jma.py --start 1976 --end 2025

    # サイトから取得した地点一覧を確認する（調査用）
    python download_jma.py --list-stations

出力: data/raw/jma_<都道府県>_<地点>_<開始年>-<終了年>.csv
    気象庁からは Shift_JIS で届くが、VS Code や Excel で読みやすいよう UTF-8（BOM付き）に変換して保存する。
    以前 Shift_JIS のまま保存したファイルも、このスクリプトを再実行すると UTF-8 に変換される。
このあと convert_jma.py でそのまま変換できる。

仕組み（ブラウザが裏で行っている通信と同じ）:
    1. POST top/station  pd=00         → 地域の一覧（prid）
    2. POST top/station  pd=<prid>     → 各地域の地点一覧（stid、例: 東京 = s47662）
    3. POST show/table   条件一式      → CSV

注意:
    - 気象庁のサーバーに負荷をかけないよう、リクエストの間隔を空け（既定2秒）、並列にはしない
    - 取得済みのファイルはスキップするので、途中で止まっても再実行すれば続きから取れる
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests が必要です: pip install requests")

BASE = "https://www.data.jma.go.jp/risk/obsdl/"

# 各都道府県の気象台（管区気象台・地方気象台など）
TARGET_STATIONS = [
    ("北海道", "札幌"), ("青森県", "青森"), ("岩手県", "盛岡"), ("宮城県", "仙台"),
    ("秋田県", "秋田"), ("山形県", "山形"), ("福島県", "福島"), ("茨城県", "水戸"),
    ("栃木県", "宇都宮"), ("群馬県", "前橋"), ("埼玉県", "熊谷"), ("千葉県", "千葉"),
    ("東京都", "東京"), ("神奈川県", "横浜"), ("新潟県", "新潟"), ("富山県", "富山"),
    ("石川県", "金沢"), ("福井県", "福井"), ("山梨県", "甲府"), ("長野県", "長野"),
    ("岐阜県", "岐阜"), ("静岡県", "静岡"), ("愛知県", "名古屋"), ("三重県", "津"),
    ("滋賀県", "彦根"), ("京都府", "京都"), ("大阪府", "大阪"), ("兵庫県", "神戸"),
    ("奈良県", "奈良"), ("和歌山県", "和歌山"), ("鳥取県", "鳥取"), ("島根県", "松江"),
    ("岡山県", "岡山"), ("広島県", "広島"), ("山口県", "下関"), ("徳島県", "徳島"),
    ("香川県", "高松"), ("愛媛県", "松山"), ("高知県", "高知"), ("福岡県", "福岡"),
    ("佐賀県", "佐賀"), ("長崎県", "長崎"), ("熊本県", "熊本"), ("大分県", "大分"),
    ("宮崎県", "宮崎"), ("鹿児島県", "鹿児島"), ("沖縄県", "那覇"),
]

# 日別値の項目コード（日平均気温, 日最高気温, 日最低気温, 降水量の日合計）
DEFAULT_ELEMENTS = "201,202,203,101"
# 返ってきたCSVに含まれているはずの項目名（コードが合っているかの確認用）
EXPECTED_ITEMS = ["平均気温", "最高気温", "最低気温", "降水量"]


class Client:
    def __init__(self, sleep: float):
        self.sleep = sleep
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (jma-weather-elk; educational data analysis)",
            "Referer": BASE + "index.php",
        })
        self._req("GET", "index.php")  # ブラウザと同じく、最初にトップページを開いておく

    def _req(self, method, path, data=None, accept=None):
        headers = {"Accept": accept} if accept else {}
        r = self.s.request(method, BASE + path, data=data, headers=headers, timeout=180)
        time.sleep(self.sleep)
        r.raise_for_status()
        return r

    # ---- 地点一覧 ----
    def stations(self):
        """{地点名: [stid, ...]} を作る。地域ごとに問い合わせる"""
        top = self._req("POST", "top/station", {"pd": "00", "format": "json"}).json()
        prids = []

        def walk(o):
            if isinstance(o, dict):
                if o.get("type") == "pref" and o.get("prid"):
                    prids.append(str(o["prid"]))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(top.get("prefTable", top))
        prids = list(dict.fromkeys(prids))
        if not prids:
            raise RuntimeError("地域の一覧が取得できませんでした（サイトの構造が変わった可能性）")

        result = {}
        for n, prid in enumerate(prids, 1):
            print(f"  地点一覧を取得中 {n}/{len(prids)}", end="\r", file=sys.stderr)
            data = self._req("POST", "top/station", {"pd": prid, "format": "json"}).json()
            for m in data.get("markers", []):
                if m.get("kind") != "station" or not m.get("stid"):
                    continue
                ids = result.setdefault(m.get("stname", ""), [])
                if m["stid"] not in ids:
                    ids.append(m["stid"])
        print(file=sys.stderr)
        return result

    # ---- CSV ダウンロード ----
    def download(self, stid, element_codes, y1, y2):
        data = {
            "stationNumList": json.dumps([stid]),
            "aggrgPeriod": "1",                                          # 日別値
            "elementNumList": json.dumps([[c, ""] for c in element_codes]),
            "interAnnualType": "1",                                      # 連続した期間
            "ymdList": json.dumps([str(y1), str(y2), "1", "12", "1", "31"]),
            "option": json.dumps({"list": [], "nyear": 0}),
            "downloadFlag": "true",
            "rmkFlag": "1",          # 利用上注意が必要なデータも格納
            "disconnectFlag": "1",   # 観測環境の変化にかかわらず全期間を格納
            "youbiFlag": "0",
            "fukenFlag": "1",        # 都道府県名を格納
            "kijiFlag": "0",
            "csvFlag": "1",          # すべて数値で格納
            "jikantaiFlag": "0",
            "jikantaiList": "[]",
            "ymdLiteral": "1",
        }
        r = self._req("POST", "show/table", data, accept="text/html,application/xhtml+xml")
        text = r.content.decode("cp932", errors="replace")
        return text.lstrip().startswith("ダウンロードした時刻"), r.content, text


def to_utf8_bytes(content: bytes) -> bytes:
    """Shift_JIS のCSVを UTF-8（BOM付き）に変換する。BOM付きにすると Excel でも文字化けしない"""
    if content.startswith(b"\xef\xbb\xbf"):
        return content
    try:
        content.decode("utf-8")
        return b"\xef\xbb\xbf" + content  # もともと UTF-8 なら BOM を付けるだけ
    except UnicodeDecodeError:
        pass
    return content.decode("cp932").encode("utf-8-sig")


def to_utf8_file(path: Path) -> bool:
    """取得済みのファイルが Shift_JIS なら UTF-8 に変換する。変換したら True"""
    raw = path.read_bytes()
    new = to_utf8_bytes(raw)
    if new != raw:
        path.write_bytes(new)
        return True
    return False


def items_in_csv(text):
    for line in text.splitlines():
        if line.startswith("年月日"):
            return sorted(set(c for c in line.split(",")[1:] if c))
    return []


def resolve_stations(client, cache: Path, names_filter):
    if cache.exists():
        table = json.loads(cache.read_text(encoding="utf-8"))
    else:
        table = client.stations()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    targets = []
    for pref, name in TARGET_STATIONS:
        if names_filter and name not in names_filter:
            continue
        ids = [i for i in table.get(name, []) if i.startswith("s")]  # s で始まるのが気象台などの官署
        if not ids:
            print(f"[注意] {pref} {name} の地点IDが見つかりません。スキップします", file=sys.stderr)
            continue
        targets.append((pref, name, ids[0]))
    return targets


class Fetcher:
    def __init__(self, client, codes, out: Path, chunk: int):
        self.client, self.codes, self.out, self.chunk = client, codes, out, chunk
        self.checked = False

    def check_items(self, text):
        """最初の1回だけ、返ってきた項目名が期待どおりかを確認する"""
        if self.checked:
            return
        self.checked = True
        items = items_in_csv(text)
        print(f"  取得できた項目: {', '.join(items) or '(見つからず)'}")
        missing = [e for e in EXPECTED_ITEMS if not any(e in i for i in items)]
        if missing:
            print(f"[注意] 期待した項目 {missing} が含まれていません。"
                  "--elements で項目コードを指定し直してください", file=sys.stderr)

    def station(self, pref, name, stid, start, end):
        y = start
        while y <= end:
            span = min(self.chunk, end - y + 1)
            while True:
                y2 = y + span - 1
                path = self.out / f"jma_{pref}_{name}_{y}-{y2}.csv"
                if path.exists():
                    note = "、UTF-8に変換" if to_utf8_file(path) else ""
                    print(f"  スキップ（取得済み{note}）: {path.name}")
                    break
                ok, content, text = self.client.download(stid, self.codes, y, y2)
                if ok:
                    path.write_bytes(to_utf8_bytes(content))
                    print(f"  保存: {path.name}（{len(content) / 1024:,.0f} KB）")
                    self.check_items(text)
                    break
                if span == 1:
                    err = self.out / f"_error_{name}_{y}.html"
                    err.write_bytes(content)
                    head = re.sub(r"<[^>]+>|\s+", " ", text)[:300]
                    raise RuntimeError(f"{name} {y}年を取得できませんでした。応答を {err} に保存しました\n応答の先頭: {head}")
                span = max(1, span // 2)
                self.chunk = span  # 以降もこの年数で取る（無駄なリクエストを減らす）
                print(f"  上限に達したため期間を {span} 年に分けて取り直します")
            y += span


def main():
    ap = argparse.ArgumentParser(description="気象庁 過去の気象データ（日別値）を47地点分ダウンロード")
    ap.add_argument("--start", type=int, default=1976, help="開始年（既定 1976）")
    ap.add_argument("--end", type=int, default=2025, help="終了年（既定 2025）")
    ap.add_argument("--out", default="data/raw", help="保存先フォルダ（既定 data/raw）")
    ap.add_argument("--chunk-years", type=int, default=10, help="1回に取る年数（既定 10）")
    ap.add_argument("--sleep", type=float, default=2.0, help="リクエスト間隔の秒数（既定 2）")
    ap.add_argument("--stations", default="", help="地点名をカンマ区切りで指定（例: 東京,大阪）")
    ap.add_argument("--elements", default=DEFAULT_ELEMENTS, help=f"項目コード（既定 {DEFAULT_ELEMENTS}）")
    ap.add_argument("--station-cache", default="data/jma_stations.json", help="地点一覧のキャッシュ")
    ap.add_argument("--list-stations", action="store_true", help="サイトの地点一覧を表示して終了")
    a = ap.parse_args()

    client = Client(a.sleep)

    if a.list_stations:
        for name, ids in sorted(client.stations().items()):
            print(f"{name}\t{', '.join(ids)}")
        return

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    codes = [c.strip() for c in a.elements.split(",") if c.strip()]
    names_filter = {s.strip() for s in a.stations.split(",") if s.strip()}
    targets = resolve_stations(client, Path(a.station_cache), names_filter)
    print(f"対象 {len(targets)} 地点、{a.start}〜{a.end}年、項目コード {codes}\n")

    f = Fetcher(client, codes, out, a.chunk_years)
    for n, (pref, name, stid) in enumerate(targets, 1):
        print(f"[{n}/{len(targets)}] {pref} {name}（{stid}）")
        f.station(pref, name, stid, a.start, a.end)

    print(f"\n完了。次は: python convert_jma.py {out} data/processed")


if __name__ == "__main__":
    main()
