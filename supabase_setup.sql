-- =============================================================
-- FAXシステム用テーブル作成SQL (SCM専用・productsテーブル変更なし)
-- Supabase SQL Editor に貼り付けて実行してください
-- =============================================================


-- ① fax_product_master（FAX転記用・商品マスタ）
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fax_product_master (
    jan             TEXT PRIMARY KEY,           -- JANコード (products.jan と紐づく)
    output_dest     TEXT NOT NULL,              -- シルビア / ハルナ / 自社倉庫
    unit_price      NUMERIC(10,2),              -- バラ単価（仕入単価/袋・本）
    cs_price        NUMERIC(10,2) NOT NULL,     -- CS単価 = unit_price × 入数
    price_display   TEXT NOT NULL DEFAULT 'cs', -- 出荷指示書の単価表示: 'cs' or 'bara'
    spec            TEXT,                       -- 規格 例: 28g×12袋/cs
    pack            TEXT,                       -- 配送荷姿 例: 12袋×1cs
    notes           TEXT,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 初期データ投入
INSERT INTO fax_product_master (jan, output_dest, unit_price, cs_price, price_display, spec, pack) VALUES
-- シルビア商品（price_display='cs'：発注書にCS単価で記載）
('4589570801393', 'シルビア',  101, 1212, 'cs',   '40g/袋×12袋/cs', '12袋×1cs'),  -- 和紅茶サブレ  101×12=1212
('4589570801416', 'シルビア',   89, 1068, 'cs',   '28g/袋×12袋/cs', '12袋×1cs'),  -- トリュフ       89×12=1068
('4589570801430', 'シルビア',   89, 1068, 'cs',   '28g/袋×12袋/cs', '12袋×1cs'),  -- 焦がしガーリック 89×12=1068
('4589570801454', 'シルビア',  111, 1332, 'cs',   '30g/袋×12袋/cs', '12袋×1cs'),  -- ガトーショコラ 111×12=1332
-- ハルナ商品
('4589570801485', 'ハルナ',     44, 1056, 'cs',   '500ml×24本/cs',  '24本×1cs'),   -- 2Water       44×24=1056
-- 自社倉庫商品（price_display='bara'：出荷指示書にバラ単価で記載）
('4589570801331', '自社倉庫',  108, 5184, 'bara', '50g×48袋/cs',    '48袋×1cs'),  -- 2Gummy       108×48=5184 ※最新ロットのみ110円
('4589570801348', '自社倉庫',   86, 2580, 'bara', '250ml×30本/cs',  '30本×1cs'),  -- 2Energy       86×30=2580
('4589570801621', '自社倉庫',   86, 2580, 'bara', '250ml×30本/cs',  '30本×1cs'),  -- 2Energy(26RN) 86×30=2580
-- ショコラおかき/ラスク（後回し・TODO）
('4589570801713', 'シルビア',    0,    0, 'cs',   '35g×48袋/cs',    '48袋×1cs'),  -- TODO 後回し
('4589570801706', 'シルビア',    0,    0, 'cs',   '35g×48袋/cs',    '48袋×1cs')   -- TODO 後回し
ON CONFLICT (jan) DO NOTHING;


-- ② shipping_rules（倉庫振分けルール）
-- ---------------------------------------------------------------
-- priority: 数値が小さいほど優先。より具体的なルールを小さい数値にする
-- jan / ddc_code が NULL の場合は「全商品」「全納品先」に適用
CREATE TABLE IF NOT EXISTS shipping_rules (
    id          SERIAL PRIMARY KEY,
    jan         TEXT,           -- NULL = 全商品
    ddc_code    TEXT,           -- NULL = 全納品先
    min_cs      INTEGER NOT NULL DEFAULT 0,
    max_cs      INTEGER,        -- NULL = 上限なし
    warehouse   TEXT NOT NULL,  -- シルビア / ハルナ / ベルーナ / ニューイースト
    priority    INTEGER NOT NULL DEFAULT 10,
    notes       TEXT
);

-- 初期データ（確定値）
-- ※自社倉庫表記は 2026-05-01 より「SBS」を廃止し「ベルーナ」に統一
INSERT INTO shipping_rules (jan, ddc_code, min_cs, max_cs, warehouse, priority, notes) VALUES
-- 2Snack 4種: 10CS以上→シルビア直送、未満→ベルーナ自社倉庫
(NULL, NULL, 10, NULL, 'シルビア',  1, '2Snack 10CS以上 シルビア直送'),
(NULL, NULL,  0,   10, 'ベルーナ',  2, '2Snack 10CS未満 自社倉庫(ベルーナ)'),
-- 2Water: 10CS以上→ハルナ直送、未満→ベルーナ
('4589570801485', NULL, 10, NULL, 'ハルナ',     1, '2Water 10CS以上 ハルナ直送'),
('4589570801485', NULL,  0,   10, 'ベルーナ',   2, '2Water 10CS未満 自社倉庫(ベルーナ)'),
-- 2Gummy: 全数 自社倉庫 ベルーナ
('4589570801331', NULL,  0, NULL, 'ベルーナ',    1, '2Gummy 自社倉庫(ベルーナ)'),
-- 2Energy: 全数 自社倉庫 ベルーナ
('4589570801348', NULL,  0, NULL, 'ベルーナ',    1, '2Energy 自社倉庫(ベルーナ)'),
('4589570801621', NULL,  0, NULL, 'ベルーナ',    1, '2Energy(26RN) 自社倉庫(ベルーナ)');


-- ③ shipping_expiry_rules（出荷賞味期限ルール・商品ごと）
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shipping_expiry_rules (
    jan                 TEXT PRIMARY KEY,
    min_remaining_days  INTEGER NOT NULL,   -- 出荷時点で必要な最低残存日数
    notes               TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- 初期データ（確定値）
INSERT INTO shipping_expiry_rules (jan, min_remaining_days, notes) VALUES
('4589570801393', 180, '和紅茶サブレ   賞味365日 6ヶ月残し'),
('4589570801416', 180, 'トリュフ       賞味365日 6ヶ月残し'),
('4589570801430', 180, '焦がしガーリック 賞味365日 6ヶ月残し'),
('4589570801454', 180, 'ガトーショコラ  賞味365日 6ヶ月残し'),
('4589570801713', 120, 'ショコラおかき  賞味240日 TODO:後回し'),
('4589570801706', 120, 'ショコララスク  賞味240日 TODO:後回し'),
('4589570801331', 360, '2Gummy         賞味540日 2/3残し=12ヶ月'),
('4589570801348', 480, '2Energy        賞味720日 2/3残し=16ヶ月'),
('4589570801621', 480, '2Energy(26RN)  賞味720日 2/3残し=16ヶ月'),
('4589570801485', 480, '2Water         賞味720日 2/3残し=16ヶ月')
ON CONFLICT (jan) DO NOTHING;


-- ④ haruna_conditions（ハルナ出荷指示書用・納品先別条件）
-- ---------------------------------------------------------------
-- ハルナ経由で出荷する際、出荷指示書に記載が必要な納品先別条件
-- 将来的に torihikisaki_master にカラム追加して統合可能
-- 結合キー: nohinsaki（納品先名）※ファジーマッチで照合
CREATE TABLE IF NOT EXISTS haruna_conditions (
    nohinsaki           TEXT PRIMARY KEY,   -- 納品先名（torihikisaki_master.nohinsaki と紐づく）
    arrival_time        TEXT,               -- 入荷時間
    basse_reservation   TEXT,               -- バース予約
    pallet_condition    TEXT,               -- パレット条件
    jpr_code            TEXT,               -- JPRコード
    notes               TEXT,
    updated_at          TIMESTAMPTZ DEFAULT now()
);

INSERT INTO haruna_conditions (nohinsaki, arrival_time, basse_reservation, pallet_condition, jpr_code) VALUES
('㈱マッシュビューティーラボ  68倉庫宛',   '午前中',           NULL, 'JPRパレット登録無',   NULL),
('成城石井関西物流センター',               '午前中',           '無', 'JPRパレット名義変更', '0069065-0000'),
('成城石井ドライ物流センター',             '午前中',           '無', 'JPRパレット名義変更', '0037730-0000'),
('三菱食品 ローソン埼京SDC',               '8:00～11:00',      '無', 'JPRパレット名義変更', '15909'),
('Amazon.co.jp TPB5',                      NULL,               '無', 'JPRパレット名義変更', '0070990-0000'),
('プラザスタイル東扇島センター',           '午前中',           '無', 'JPRパレット登録無',   NULL),
('館林物流センター',                       '8:00～11:30',      '無', 'JPRパレット名義変更', '0018764'),
('ていれぎの湯',                           '午前中～午後も可', '無', 'パレット納品不可',    NULL),
('北八ヶ岳松原湖温泉 八峰の湯',           '午前中～午後も可', '無', 'パレット納品不可',    NULL),
('美人の湯かかみがはら',                   '午前中～午後も可', '無', 'パレット納品不可',    NULL),
('加藤産業㈱ 高谷　特売第2倉庫',          '午前',             '無', 'パレット納品不可',    NULL),
('スカイプラザミサワ㈱',                   '午前',             '無', 'パレット納品不可',    NULL),
('㈱ABC商会',                              '午前',             '無', 'パレット納品不可',    NULL)
ON CONFLICT (nohinsaki) DO NOTHING;
