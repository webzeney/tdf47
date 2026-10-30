#!/usr/bin/env bash
# tdf47.com デプロイ: dist/ を VPS へ rsync（設計書: dist/ を rsync で VPS へ）。
#
# 使い方:
#   DEPLOY_SSH=user@your-vps DEPLOY_PATH=/var/www/tdf47/dist ./deploy/deploy.sh
# もしくは deploy/.env に書いておく（.gitignore 済み）。
#
# 事前に `python3 src/build.py` で dist/ を生成しておくこと。
# --delete で消えたページも同期（古いページが残らない）。生成物以外は触らない。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/deploy/.env" ] && . "$ROOT/deploy/.env"

: "${DEPLOY_SSH:?例: DEPLOY_SSH=user@vps-host を指定してください}"
: "${DEPLOY_PATH:=/var/www/tdf47/dist}"

DIST="$ROOT/dist"
[ -d "$DIST" ] || { echo "dist/ がありません。先に: python3 src/build.py" >&2; exit 1; }

echo "== tdf47.com deploy =="
echo "  from: $DIST/"
echo "  to:   $DEPLOY_SSH:$DEPLOY_PATH/"
echo

# 末尾スラッシュ重要（中身を同期）。rsync -a で権限/時刻保持、-z で圧縮転送。
rsync -az --delete --human-readable --info=stats1,progress2 \
  --exclude ".DS_Store" \
  "$DIST/" "$DEPLOY_SSH:$DEPLOY_PATH/"

echo
echo "完了。VPS側で Nginx が参照していれば即時反映されます。"
echo "初回は deploy/nginx/tdf47.conf の配置と certbot が必要です（deploy/README.md 参照）。"
