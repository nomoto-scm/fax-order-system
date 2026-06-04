"""COOLA CSV コメント列テスト - BC列に21845が入るか確認"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# process_fax から COOLA関連のみインポート
from process_fax import results_to_coola_csv, COOLA_HEADERS, OUTPUT_DIR

# BC列（55番目）の確認
print(f"COOLA_HEADERS[54] = {repr(COOLA_HEADERS[54])}")
print(f"BC列は「コメント」か: {COOLA_HEADERS[54] == 'コメント'}")

# 自社倉庫アイテムのダミーデータ
dummy_results = [{
    "page": 1,
    "ocr_raw": {
        "order_no": "TEST001",
        "delivery_date": "2026-06-03",
        "delivery_dest": "テスト納品先",
        "sender": "テスト発注元",
        "notes": "",
    },
    "ddc_match": {
        "matched": True,
        "name": "テスト納品先",
        "postal": "1000001",
        "address": "東京都千代田区1-1",
        "tel": "03-0000-0000",
    },
    "matched_items": [{
        "matched": True,
        "master_name": "2Gummy テスト商品",
        "jan": "4560000000001",
        "code": "GUMMY-TEST-001",
        "output_dest": "自社倉庫",
        "quantity": 2,
        "cs_price": 10000,
        "case_quantity": 12,
        "shipping_route": "warehouse",
    }],
    "two_order_no": "TEST001",
    "shipping_fee": 0,
}]

path = results_to_coola_csv(dummy_results, "TEST_COOLA_CHECK.pdf")
print(f"\n生成ファイル: {path}")

if path and os.path.exists(path):
    with open(path, encoding="cp932", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if rows:
        bc_val = rows[0].get("コメント", "KEY NOT FOUND")
        print(f"BC列（コメント）の値: {repr(bc_val)}")
        if bc_val == "21845":
            print("✓ OK: 21845が正しく入っています")
        else:
            print("✗ NG: 21845が入っていません")
    else:
        print("行が生成されませんでした")
else:
    print("CSVが生成されませんでした（Noneが返された）")
