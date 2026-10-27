#!/usr/bin/env bash
# fetch_sources.sh — 一次データの取得（冪等・再取得可能）。
# data/ は .gitignore 済みなので、別PC/VPSではこれを流して素材を揃える。
#
# ここで落とすのは「集約サイトの索引」と「政府オープンデータ」。
# 個々の自治体サイトのクロールではない（死活監視=probe.py は別。VPSで実行）。
set -euo pipefail
cd "$(dirname "$0")/../.."   # リポジトリルート
UA="Mozilla/5.0 (TDF47-bot; +https://tdf47.com/; data ingest; contact: https://tdf47.com/contact/)"

get() { # url dest
  mkdir -p "$(dirname "$2")"
  echo "  GET $1"
  curl -sSL -A "$UA" -o "$2" "$1"
}

echo "== 総務省 全国地方公共団体コード =="
get "https://www.soumu.go.jp/main_content/000925835.xlsx" data/soumu/municipality.xlsx
get "https://www.soumu.go.jp/main_content/000892696.xlsx" data/soumu/union_etc.xlsx

echo "== e-Stat 住民基本台帳 人口・世帯数（市区町村別・総計 R8.1.1） =="
get "https://www.soumu.go.jp/main_content/000892952.xlsx" data/estat/population.xlsx

echo "== 総務省 主要財政指標一覧・市町村決算カード・類似団体比較カード（最新年度を自動で解決） =="
python3 src/ingest/fetch_soumu.py

echo "== 国土地理院 全国都道府県市区町村別面積調（最新CSV） =="
get "https://www.gsi.go.jp/KOKUJYOHO/MENCHO/backnumber/R8_07_mencho.csv" data/gsi/mencho.csv

echo "== 国土数値情報 N03 行政区域（★583MB・隣接算出用。重い。VPS推奨） =="
get "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_GML.zip" data/ksj/N03-2024.zip
( cd data/ksj && unzip -o N03-2024.zip >/dev/null )

echo "== 日本郵便 郵便番号データ（郡名の補完） =="
get "https://www.post.japanpost.jp/service/search/zipcode/download/utf/zip/utf_ken_all.zip" data/jp_post/utf_ken_all.zip
( cd data/jp_post && unzip -o utf_ken_all.zip >/dev/null )

echo "== RILG 全国自治体例規集（1ページに全自治体の例規URL） =="
get "https://www.rilg.or.jp/htdocs/main/zenkoku_reiki/zenkoku_link.html" data/rilg/zenkoku_link.html

echo "== uub.jp 公式メディア（都道府県別47ページ・1.5秒間隔） =="
prefs=(hokkaido aomori iwate miyagi akita yamagata fukushima ibaraki tochigi gunma \
  saitama chiba tokyo kanagawa niigata toyama ishikawa fukui yamanashi nagano gifu \
  shizuoka aichi mie shiga kyoto osaka hyogo nara wakayama tottori shimane okayama \
  hiroshima yamaguchi tokushima kagawa ehime kochi fukuoka saga nagasaki kumamoto \
  oita miyazaki kagoshima okinawa)
for p in "${prefs[@]}"; do
  get "https://uub.jp/opm/$p.html" "data/uub/$p.html"
  sleep 1.5
done

echo "== app-mints 会議録検索MAP（都道府県別50ページ・北海道は4地域・1.2秒間隔） =="
mints=(hokkaido1 hokkaido2 hokkaido3 hokkaido4 aomori iwate miyagi akita yamagata \
  fukushima ibaraki tochigi gunma saitama chiba tokyo kanagawa niigata toyama ishikawa \
  fukui yamanashi nagano gifu shizuoka aichi mie shiga kyoto osaka hyogo nara wakayama \
  tottori shimane okayama hiroshima yamaguchi tokushima kagawa ehime kochi fukuoka saga \
  nagasaki kumamoto oita miyazaki kagoshima okinawa)
for s in "${mints[@]}"; do
  get "https://app-mints.com/kaigiroku/lg/$s" "data/mints/$s.html"
  sleep 1.2
done

echo "== GKP マンホールカード（都道府県別47ページ・JISコード・1.2秒間隔） =="
for n in $(seq -w 1 47); do
  get "https://www.gk-p.jp/mhcard/?pref=$n" "data/gkp/pref$n.html"
  sleep 1.2
done

echo "== 国土交通省 ダムカード配布場所一覧（XLSX） =="
get "https://www.mlit.go.jp/river/kankyo/campaign/shunnkan/damcard_260116_new.xlsx" data/mlit/damcard.xlsx

echo "完了。次に: 01_entity → 00_link_kind → 02_population → 03_area → 05_adjacency → 06_reiki → 07_gikai → 08_uub → 09_card_manhole → 10_card_dam → 13_fiscal → 14_ruiji → src/build.py"
