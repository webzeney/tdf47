-- tdf47.com DBスキーマ（設計書 5章「DBスキーマ」の正本を転記）
-- 縦持ちの link テーブルが肝。v2.0 で項目が増えても行の追加だけで済む（スキーマ変更不要）。
-- SQLite / Python 3.12 前提。実行時には DB も Python も動かさない（静的生成の素材）。

PRAGMA foreign_keys = ON;

-- 団体マスタ。生成対象の絞り込みは phase 列で行う。
CREATE TABLE IF NOT EXISTS entity (
  code        TEXT PRIMARY KEY,   -- 全国地方公共団体コード6桁（チェックデジット込み）
  kind        TEXT NOT NULL,      -- pref | city | ward | union
  pref_code   TEXT NOT NULL,      -- 所属都道府県の団体コード6桁
  parent_code TEXT,               -- 行政区→政令市のcode。それ以外はNULL
  name        TEXT NOT NULL,      -- 蕨市
  kana        TEXT NOT NULL,      -- わらびし（ひらがな正規化済み）
  slug        TEXT NOT NULL,      -- warabi-shi
  path        TEXT NOT NULL UNIQUE, -- /saitama/warabi-shi/
  gun         TEXT,               -- 郡名（衝突回避に使用。漢字）
  type_label  TEXT,               -- 市 | 町 | 村 | 区 | 政令指定都市 | 中核市 | 広域連合
  population  INTEGER,
  households  INTEGER,
  area_km2    REAL,
  office_zip  TEXT,
  rep_zip     TEXT,               -- 代表郵便番号7桁（日本郵便「以下に掲載がない場合」より）
  office_addr TEXT,
  office_tel  TEXT,
  office_lat  REAL,
  office_lng  REAL,
  pop_as_of   TEXT,
  area_as_of  TEXT,
  phase       TEXT NOT NULL,      -- v1.0 | v1.5 ← 生成対象の絞り込み
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_pref ON entity(pref_code);
CREATE INDEX IF NOT EXISTS idx_entity_kind ON entity(kind, phase);

-- リンク種別マスタ。phase='v2.0' のものは画面で「準備中」と表示する。
CREATE TABLE IF NOT EXISTS link_kind (
  code     TEXT PRIMARY KEY,  -- official | reiki | gikai | kouhou | sns_x | sns_line | ...
  label    TEXT NOT NULL,
  category TEXT NOT NULL,     -- admin | sns | card
  phase    TEXT NOT NULL,     -- v1.0 | v2.0 ← v2.0なら「準備中」と表示
  sort     INTEGER NOT NULL,
  icon     TEXT
);

-- 縦持ちリンク。虫食いゼロが価値の核。status の4値で状態を明示する。
CREATE TABLE IF NOT EXISTS link (
  id           INTEGER PRIMARY KEY,
  entity_code  TEXT NOT NULL REFERENCES entity(code),
  kind_code    TEXT NOT NULL REFERENCES link_kind(code),
  url          TEXT,
  title        TEXT,  -- 「広報わらび 令和8年9月号」など
  note         TEXT,
  status       TEXT NOT NULL,  -- ok | dead | none | unchecked
  source       TEXT NOT NULL,  -- rilg | uub | app-mints | gkp | mlit | manual
  first_seen   TEXT,
  last_checked TEXT,  -- ★ 最終確認日。中核機能なので必ず埋める
  UNIQUE(entity_code, kind_code, url)
);
CREATE INDEX IF NOT EXISTS idx_link_entity ON link(entity_code);
CREATE INDEX IF NOT EXISTS idx_link_status ON link(status, last_checked);

-- 死活監視・差分検知の履歴。
CREATE TABLE IF NOT EXISTS probe (
  id          INTEGER PRIMARY KEY,
  link_id     INTEGER NOT NULL REFERENCES link(id),
  checked_at  TEXT NOT NULL,
  http_status INTEGER,
  final_url   TEXT,   -- 最終リダイレクト先
  page_title  TEXT,   -- <title>
  changed     INTEGER NOT NULL DEFAULT 0,  -- 月次レビュー対象
  error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe_link ON probe(link_id, checked_at);

-- 隣接自治体（無向。両方向を格納する運用）。
CREATE TABLE IF NOT EXISTS adjacency (
  a_code TEXT NOT NULL REFERENCES entity(code),
  b_code TEXT NOT NULL REFERENCES entity(code),
  PRIMARY KEY (a_code, b_code)
);

-- ご当地カード（マンホール・ダム）。画像は権利確認が済むまで NULL。
CREATE TABLE IF NOT EXISTS card (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,  -- manhole | dam
  card_no      TEXT,           -- 蕨市A001
  entity_code  TEXT NOT NULL REFERENCES entity(code),
  series       TEXT,           -- 第14弾
  issued_on    TEXT,
  place_name   TEXT,
  place_addr   TEXT,
  place_hours  TEXT,
  stock        TEXT,           -- ok | low | ended
  ended_on     TEXT,
  ended_note   TEXT,
  image_url    TEXT,           -- 権利確認が済むまでNULL
  source_url   TEXT,
  last_checked TEXT
);

-- 広域連合・一部事務組合の構成団体。
CREATE TABLE IF NOT EXISTS union_member (
  union_code  TEXT NOT NULL REFERENCES entity(code),
  member_code TEXT NOT NULL REFERENCES entity(code),
  PRIMARY KEY (union_code, member_code)
);

-- 財政指標・類似団体区分（縦持ち。年度×団体×指標。指標が増えても行追加のみ）。
-- 総務省「主要財政指標一覧」等から取得。数値には必ず基準年度(fiscal_year)と出典(source_url)を併記する。
CREATE TABLE IF NOT EXISTS fiscal (
  entity_code TEXT NOT NULL REFERENCES entity(code),
  fiscal_year TEXT NOT NULL,       -- 令和6年度 など（基準年度。職員が資料に引用できるよう必ず保持）
  metric      TEXT NOT NULL,       -- zaisei_ryoku | keijo_shushi | kosaihi | shorai_futan | laspeyres | ruiji_type ...
  value       REAL,                -- 数値指標。類型など文字区分は value_text を使う
  value_text  TEXT,                -- 類似団体区分コード等、数値でない区分
  source_url  TEXT NOT NULL,
  updated_at  TEXT,
  PRIMARY KEY (entity_code, fiscal_year, metric)
);
CREATE INDEX IF NOT EXISTS idx_fiscal_entity ON fiscal(entity_code);

-- 自治体DXの取組状況（デジタル庁「自治体DXの取組に関するダッシュボード」のデータテーブル）
CREATE TABLE IF NOT EXISTS dx_status (
  entity_code TEXT NOT NULL REFERENCES entity(code),
  metric      TEXT NOT NULL,       -- cio | cio_hosa | houshin | taisei | gaibu | ikusei | kenshu | ai | rpa | telework | mynumber | online26 | online32
  value_text  TEXT NOT NULL,       -- 実施 | 未実施 | 調査無し | 72%
  source_url  TEXT NOT NULL,
  updated_at  TEXT,
  PRIMARY KEY (entity_code, metric)
);
