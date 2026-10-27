# 公開・デプロイ手順（GitHub Actions → FTP）

**PC非依存**で、GitHubに push すると自動でサイトを生成し、サーバー（共有ホスティング／Apache）へ FTP 配信する。

```
テンプレ/静的/DBを更新 → git push → GitHub Actions が build.py → dist/ を FTP配信
毎日 03:30(JST) → GitHub Actions が probe.py（リンク死活監視）→ DB更新 → 再配信
```

## 1. GitHub リポジトリに設定（Settings → Secrets and variables → Actions）

### Secrets（秘密。接続情報）
| 名前 | 値 |
|---|---|
| `FTP_HOST` | FTPサーバー名（ホスティングの指定値） |
| `FTP_USER` | FTPユーザー名 |
| `FTP_PASS` | FTPパスワード |

### Variables（公開してよい設定）
| 名前 | 値 | 備考 |
|---|---|---|
| `FTP_DIR` | 配信先ディレクトリ（例: `/` または `/tdf47.com/` 等、tdf47.com のドキュメントルート） | 必須 |
| `SITE_URL` | `https://tdf47.com` | canonical/OGP/sitemap に使用 |
| `FTP_PROTOCOL` | `ftps`（推奨）または `ftp` | 省略時 ftps |
| `FTP_PORT` | `21`（通常） | 省略可 |
| `GA4_ID` | `G-XXXXXXXXXX` | **後で設定でOK**（空ならタグ非出力） |
| `CONTACT_URL` | お問い合わせフォームURL | **後で設定でOK** |

## 2. ドメインをサーバーに向ける
- `tdf47.com` のDNS／独自ドメイン設定を、契約ホスティングの指示どおりに設定。
- サーバー側で tdf47.com のドキュメントルートを決め、それを `FTP_DIR` に設定。
- SSL（Let's Encrypt等）はホスティングの管理画面で有効化（`.htaccess` が http→https を強制）。

## 3. 初回公開（ソフトローンチ）
- main に push（または Actions で「ビルドしてFTP公開」を手動実行）→ `dist/` が配信される。
- この時点のリンクは「未確認」表示。

## 4. 初回のリンク全件検証（最終確認日を付ける）
- Actions →「リンク死活監視」→ **Run workflow** で `limit=0`（全件）を指定して手動実行。
  - 約12,744リンク×1.2秒＝数時間（GitHubの1ジョブ上限6h内）。DBが「確認済み・最終確認日つき」に更新され、自動で再配信される。
- 以後は毎日自動で古いものからローリング検証（既定 limit=2500／回）。

## 5. ふだんの更新
- テンプレートやデータを直して `git push` するだけ。Actionsが生成→配信。

## 補足
- **DB（db/tdf47.sqlite, 約4MB）はリポジトリにコミット**している。Actionsがデータ再取得なしで即ビルドできるため。probeの結果もDBに入り自動コミットされる。
- データを一から作り直す場合は `src/ingest/fetch_sources.sh` → 各ingest → `build.py`（N03が583MBで重いので普段は不要）。
- Apache向け設定は `build.py` が `dist/.htaccess` を自動生成（www→apex / http→https / 末尾スラッシュ / 404 / セキュリティヘッダ）。
- （参考）VPS+Nginxで運用する場合の設定は `deploy/nginx/tdf47.conf` と `deploy/cron/` に残してある。
