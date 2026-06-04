-- =============================================================
-- FAX受注システム v2 セットアップSQL
-- 目的: 既存 infomart_orders_scm / infomart_order_items_scm を触らず、
--       FAX/メール/Slack 経由の受注を格納する新テーブルとVIEWを追加する。
-- 実行場所: Supabase SQL Editor（gyoomkdnvafgauohklwn プロジェクト）
-- =============================================================


-- ============================================================
-- ① fax_orders_scm（ヘッダー）
-- ------------------------------------------------------------
-- カラム構成は infomart_orders_scm と互換にし、VIEW で UNION しやすくする。
-- source_channel で経路を区別: 'efax' / 'paltac' / 'email_pdf' / 'slack' 等
-- status で処理段階を管理: 'draft' / 'confirmed' / 'processed' / 'error'
-- ============================================================
CREATE TABLE IF NOT EXISTS fax_orders_scm (
    -- 共通フィールド（infomart_orders_scm と同じ型・名前）
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id_system           TEXT,                       -- 外部システム取引ID（インフォマートのtrade_idに相当、FAXでは空でOK）
    slip_number               TEXT,                       -- 伝票番号（OCRで読み取ったオーダーNo等）
    source_channel            TEXT NOT NULL,              -- 'efax' / 'paltac' / 'email_pdf' / 'slack'
    order_type                TEXT DEFAULT 'wholesale',
    status                    TEXT NOT NULL DEFAULT 'draft',   -- 'draft' / 'confirmed' / 'processed' / 'error'
    order_date                DATE,
    delivery_date             DATE,
    ship_date                 DATE,
    slip_date                 DATE,
    partner_code              TEXT,
    partner_name              TEXT,
    delivery_location_code    TEXT,
    delivery_location_name    TEXT,
    delivery_location_address TEXT,
    grand_total               NUMERIC(12,2),
    notes                     TEXT,

    -- FAX/OCR固有フィールド
    source_file_path          TEXT,                       -- 元PDF/CSVの保存パス（Google Drive）
    source_file_name          TEXT,                       -- 元ファイル名
    source_email_id           TEXT,                       -- 元メールのGmail Message ID（重複防止）
    source_email_from         TEXT,                       -- 元メールの差出人
    source_email_received_at  TIMESTAMPTZ,                -- 元メールの受信日時
    ocr_page_no               INTEGER,                    -- 複数ページPDFの場合のページ番号
    ocr_raw                   JSONB,                      -- OCR生データ（再処理・デバッグ用）
    ocr_confidence            NUMERIC(4,3),               -- OCR信頼度（0.000〜1.000）
    ddc_matched               BOOLEAN DEFAULT false,      -- 納品先マスタにマッチしたか
    ddc_match_candidates      JSONB,                      -- DDC候補（確認アプリで表示）
    warehouse                 TEXT,                       -- 判定結果: 'シルビア' / 'ハルナ' / 'ベルーナ' 等（旧'SBS'は2026-05-01廃止）
    shipping_type             TEXT,                       -- '直送' / '自社倉庫'
    status_flags              TEXT[],                     -- ['ロット未満', 'リードタイム', 'DDC未マッチ'] 等
    confirmed_by              TEXT,                       -- 確定者
    confirmed_at              TIMESTAMPTZ,                -- 確定日時
    processed_at              TIMESTAMPTZ,                -- NE/Coola 送信完了日時

    created_at                TIMESTAMPTZ DEFAULT now(),
    updated_at                TIMESTAMPTZ DEFAULT now(),

    -- 重複防止: 同じメール×同じページは1レコードのみ
    CONSTRAINT uq_email_page UNIQUE (source_email_id, ocr_page_no)
);

-- インデックス（確認アプリのソート/絞り込み用）
CREATE INDEX IF NOT EXISTS idx_fax_orders_status          ON fax_orders_scm(status);
CREATE INDEX IF NOT EXISTS idx_fax_orders_source_channel  ON fax_orders_scm(source_channel);
CREATE INDEX IF NOT EXISTS idx_fax_orders_delivery_date   ON fax_orders_scm(delivery_date);
CREATE INDEX IF NOT EXISTS idx_fax_orders_created_at      ON fax_orders_scm(created_at DESC);


-- ============================================================
-- ② fax_order_items_scm（明細）
-- ------------------------------------------------------------
-- カラム構成は infomart_order_items_scm と互換。
-- OCRマッチング結果（JANコード, マスタ一致フラグ等）を追加。
-- ============================================================
CREATE TABLE IF NOT EXISTS fax_order_items_scm (
    -- 共通フィールド
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                 UUID NOT NULL REFERENCES fax_orders_scm(id) ON DELETE CASCADE,
    slip_detail_id_system    TEXT,
    line_no                  INTEGER,
    product_code             TEXT,
    product_code_raw         TEXT,
    product_name             TEXT,
    spec                     TEXT,
    quantity                 NUMERIC(10,2),
    unit                     TEXT,
    unit_price               NUMERIC(12,2),
    amount                   NUMERIC(12,2),
    tax                      NUMERIC(12,2) DEFAULT 0,
    subtotal                 NUMERIC(12,2),
    tax_category             TEXT DEFAULT '',
    tax_type                 TEXT DEFAULT '外税',

    -- FAX/OCR固有フィールド
    jan_code                 TEXT,                        -- マッチングで得たJANコード
    product_master_matched   BOOLEAN DEFAULT false,       -- 商品マスタにマッチしたか
    ocr_raw_name             TEXT,                        -- OCRが読み取った生商品名（マッチ前）
    ocr_confidence           NUMERIC(4,3),

    created_at               TIMESTAMPTZ DEFAULT now(),
    updated_at               TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fax_order_items_order_id ON fax_order_items_scm(order_id);


-- ============================================================
-- ③ all_orders_view_scm（ヘッダー統合VIEW）
-- ------------------------------------------------------------
-- infomart_orders_scm と fax_orders_scm を UNION。
-- 確認アプリはこのVIEWを SELECT するだけで全経路のデータが取れる。
-- 共通カラムのみを返す（FAX固有フィールドは個別に fax_orders_scm を参照）
-- ============================================================
CREATE OR REPLACE VIEW all_orders_view_scm AS
SELECT
    id,
    trade_id_system,
    slip_number,
    source_channel,
    order_type,
    status,
    order_date,
    delivery_date,
    ship_date,
    slip_date,
    partner_code,
    partner_name,
    delivery_location_code,
    delivery_location_name,
    delivery_location_address,
    grand_total,
    notes,
    created_at,
    updated_at
FROM infomart_orders_scm
UNION ALL
SELECT
    id,
    trade_id_system,
    slip_number,
    source_channel,
    order_type,
    status,
    order_date,
    delivery_date,
    ship_date,
    slip_date,
    partner_code,
    partner_name,
    delivery_location_code,
    delivery_location_name,
    delivery_location_address,
    grand_total,
    notes,
    created_at,
    updated_at
FROM fax_orders_scm;


-- ============================================================
-- ④ all_order_items_view_scm（明細統合VIEW）
-- ============================================================
CREATE OR REPLACE VIEW all_order_items_view_scm AS
SELECT
    id,
    order_id,
    slip_detail_id_system,
    line_no,
    product_code,
    product_code_raw,
    product_name,
    spec,
    quantity,
    unit,
    unit_price,
    amount,
    tax,
    subtotal,
    tax_category,
    tax_type,
    created_at,
    updated_at
FROM infomart_order_items_scm
UNION ALL
SELECT
    id,
    order_id,
    slip_detail_id_system,
    line_no,
    product_code,
    product_code_raw,
    product_name,
    spec,
    quantity,
    unit,
    unit_price,
    amount,
    tax,
    subtotal,
    tax_category,
    tax_type,
    created_at,
    updated_at
FROM fax_order_items_scm;


-- ============================================================
-- ⑤ updated_at 自動更新トリガー（fax_orders_scm / fax_order_items_scm）
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_fax_orders_updated_at      ON fax_orders_scm;
CREATE TRIGGER trg_fax_orders_updated_at
    BEFORE UPDATE ON fax_orders_scm
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_fax_order_items_updated_at ON fax_order_items_scm;
CREATE TRIGGER trg_fax_order_items_updated_at
    BEFORE UPDATE ON fax_order_items_scm
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- ⑥ Phase 4 用カラム追加（2026-04-29 適用済）
-- ============================================================
-- 確定情報の追跡：confirmation_count（確定回数）/ confirmation_history（履歴）
-- 送料情報：shipping_fee（税抜）/ shipping_fee_two_burden（TWO負担True時はCSV未入力）
--          / shipping_fee_breakdown（内訳JSON）
ALTER TABLE fax_orders_scm
    ADD COLUMN IF NOT EXISTS confirmation_count        int DEFAULT 0,
    ADD COLUMN IF NOT EXISTS confirmation_history      jsonb DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS shipping_fee              int,
    ADD COLUMN IF NOT EXISTS shipping_fee_two_burden   boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS shipping_fee_breakdown    jsonb;

-- confirmation_history の構造例:
-- [{"at": "2026-04-30T14:30:00+09:00", "by": "伊藤",
--   "snapshot": {全フィールドのスナップショット}}]
-- 再確定するたびに append する。配列の最後が最新確定。
--
-- shipping_fee_breakdown の構造例（calculate_shipping_fee の戻り値を保存）:
-- {"total_fee": 1500, "prefecture": "東京都", "zone": "関東",
--  "groups": [{"brand": "2Snack", "fee": 1500, "method": "snack_lot_break", ...}],
--  "needs_manual_input_overall": false}


-- ============================================================
-- 動作確認用クエリ（実行後の検証用）
-- ============================================================
-- SELECT source_channel, status, COUNT(*) FROM all_orders_view_scm GROUP BY 1, 2;
-- SELECT * FROM all_orders_view_scm WHERE status = 'draft' ORDER BY created_at DESC LIMIT 10;
-- SELECT id, slip_number, status, confirmation_count, shipping_fee, shipping_fee_two_burden
--     FROM fax_orders_scm ORDER BY created_at DESC LIMIT 10;
