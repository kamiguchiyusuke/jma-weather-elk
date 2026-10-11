# 都道府県の気象データ（気象庁）を集めて可視化する

気象庁「過去の気象データ・ダウンロード」から、47都道府県の気象台の日別値（平均・最高・最低気温、降水量）を 1976〜2025 年分集め、
HTML ビューアと Elasticsearch + Kibana で可視化します。作業の流れは [qiita/article.md](qiita/article.md) にまとめています。

## 構成

| パス | 内容 |
|---|---|
| `download_jma.py` | 気象庁のサイトから日別値の CSV を 47 地点分ダウンロード（→ `data/raw/`） |
| `build_viewer.py` / `viewer_template.html` | CSV を集計して HTML ビューアを作る（→ `viewer/weather.html`） |
| `convert_jma.py` | CSV を「1行 = 1地点 × 1日」に整形（Python で前処理する場合用） |
| `elk/` | WSL2 + Docker で動かす Elasticsearch / Kibana / Logstash（9.5.5） |
| `elk/logstash/` | 気象庁の CSV を Python なしで取り込む Logstash の設定とインデックステンプレート |
| `elk/kibana/jma-dashboard.json` | Kibana ダッシュボードの定義（Dashboards API 用） |
| `elk/kibana/export_static.ps1` | ダッシュボードを静的な HTML / PDF に書き出す |
| `qiita/` | 記事の原稿と画像 |

`data/` はリポジトリに含めていません。下の手順でダウンロードしてください。

## 使い方

### 1. データをダウンロードして HTML ビューアで見る

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python download_jma.py          # 47地点 × 1976〜2025年（約10分。2秒間隔で順番に取得）
.venv\Scripts\python build_viewer.py          # → viewer/weather.html をブラウザで開く
```

### 2. Kibana で見る（WSL の Ubuntu で）

```bash
cd elk
docker compose up -d      # Logstash が data/raw の CSV を jma-daily に取り込む（約1分半）

# ダッシュボードを作る
cd kibana
curl -X PUT 'localhost:5601/api/dashboards/jma-weather' \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -H 'Elastic-Api-Version: 2023-10-31' --data-binary @jma-dashboard.json
```

http://localhost:5601/app/dashboards#/view/jma-weather を開きます。

## 注意

- `elk/` はローカル検証用です。認証（`xpack.security`）を無効にしており、ポートは `127.0.0.1` にだけ公開しています。
- 気象庁のサーバーに負荷をかけないよう、ダウンロードは並列にせず間隔を空けています。

## 出典

気象庁「過去の気象データ・ダウンロード」の日別値をもとに作成・集計しています。
