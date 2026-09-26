"""expiry_guard（賞味期限逆転・残存日数ガード）の単体テスト"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import expiry_guard as eg


def _order(oid, partner, loc_code, loc_name, items, warehouse="ハルナ", at="2026-08-01T00:00:00"):
    """fax_orders_scm 行のミニチュア（confirmation_history の最後のエントリだけ見る）"""
    return {
        "id": oid,
        "partner_name": partner,
        "delivery_location_code": loc_code,
        "delivery_location_name": loc_name,
        "warehouse": warehouse,
        "confirmed_at": at,
        "confirmation_history": [
            {"at": "2026-07-01T00:00:00", "snapshot": {"pages": [{"items": [
                {"jan": "4900000000001", "expiry_date": "2026-01-01"}]}]}},  # 古いエントリ=無視される
            {"at": at, "snapshot": {"pages": [{
                "partner_name": partner,
                "delivery_location_code": loc_code,
                "delivery_location_name": loc_name,
                "delivery_date": "2026-08-05",
                "items": items,
            }]}},
        ],
    }


ORDERS = [
    _order("o1", "三菱食品株式会社 加食G", "", "ナチュラルローソン 埼京DDC",
           [{"jan": "4900000000001", "master_name": "2Water", "expiry_date": "2026-12-31"},
            {"jan": "4900000000002", "master_name": "2Snack", "expiry_date": "2026-11-30"}]),
    _order("o2", "三菱食品 加食G", "", "ナチュラルローソン 関西DDC",
           [{"jan": "4900000000001", "master_name": "2Water", "expiry_date": "2026-12-15"}],
           warehouse="ベルーナ"),
    _order("o3", "伊藤忠食品", "500", "伊藤忠 昭島センター",
           [{"jan": "4900000000001", "master_name": "2Water", "expiry_date": "2027-01-31"}]),
    _order("o4", "無効", "", "期限なし",
           [{"jan": "4900000000001", "expiry_date": ""}, {"jan": "4900000000001", "expiry_date": "不明"}]),
]


def test_normalize_partner_strips_corporate_and_department():
    assert eg.normalize_partner("三菱食品株式会社 加食G") == "三菱食品"
    assert eg.normalize_partner("三菱食品 加食G") == "三菱食品"
    assert eg.normalize_partner("（株）伊藤忠食品") == "伊藤忠食品"
    assert eg.normalize_partner("") == ""


def test_parse_date():
    assert eg.parse_date("2026-08-21") == date(2026, 8, 21)
    assert eg.parse_date("2026/08/21") is None
    assert eg.parse_date("") is None
    assert eg.parse_date(None) is None
    assert eg.parse_date(date(2026, 1, 1)) == date(2026, 1, 1)


def test_history_rows_uses_last_entry_and_skips_invalid():
    rows = eg.history_rows_from_orders(ORDERS)
    # o1=2行, o2=1行, o3=1行, o4=0行（期限なし/不正は除外）。古いエントリ(2026-01-01)は採用されない
    assert len(rows) == 4
    assert all(r["expiry_date"] != date(2026, 1, 1) for r in rows)
    o3 = [r for r in rows if r["order_id"] == "o3"][0]
    assert o3["delivery_location_code"] == "500"
    assert o3["product_name"] == "2Water"


def test_reference_scope_fallback_order():
    rows = eg.history_rows_from_orders(ORDERS)
    jan = "4900000000001"
    # 納品先名一致 → そのスコープ（埼京DDC=12/31）
    ref = eg.find_reference_expiry(rows, jan, delivery_location_name="ナチュラルローソン　埼京DDC",
                                   partner_name="三菱食品株式会社 加食G")
    assert ref["scope"] == "納品先名" and ref["max_expiry"] == date(2026, 12, 31)
    # 納品先は未知だが取引先（チェーン）が一致 → 取引先スコープの最大（埼京12/31 vs 関西12/15 → 12/31）
    ref = eg.find_reference_expiry(rows, jan, delivery_location_name="ナチュラルローソン 新店",
                                   partner_name="三菱食品 加食G")
    assert ref["scope"] == "取引先" and ref["max_expiry"] == date(2026, 12, 31)
    # 納品先コードが一致すれば最優先
    ref = eg.find_reference_expiry(rows, jan, delivery_location_code="500", partner_name="三菱食品")
    assert ref["scope"] == "納品先コード" and ref["max_expiry"] == date(2027, 1, 31)
    # どこにも一致しない → 全社（最大=2027-01-31）
    ref = eg.find_reference_expiry(rows, jan, delivery_location_name="新規取引先DC", partner_name="新規")
    assert ref["scope"] == "全社" and ref["max_expiry"] == date(2027, 1, 31)
    # 自分自身（再確定時）は除外
    ref = eg.find_reference_expiry(rows, jan, delivery_location_code="500", partner_name="伊藤忠食品",
                                   exclude_order_id="o3")
    assert ref["scope"] != "納品先コード"
    # 実績のないJAN
    assert eg.find_reference_expiry(rows, "0000000000000") is None


def test_evaluate_reversal_and_min_remaining():
    rows = eg.history_rows_from_orders(ORDERS)
    ref = eg.find_reference_expiry(rows, "4900000000001", delivery_location_name="ナチュラルローソン 埼京DDC")
    # 実事故の型: 前回12/31 → 今回12/10（出荷元がベルーナに変わっても照合はDB基準なので検知）
    alerts = eg.evaluate_item("2026-12-10", ref)
    assert len(alerts) == 1 and alerts[0]["type"] == "reversal"
    assert alerts[0]["days_behind"] == 21 and alerts[0]["scope"] == "納品先名"
    # 同日・新しい → 問題なし
    assert eg.evaluate_item("2026-12-31", ref) == []
    assert eg.evaluate_item("2027-03-01", ref) == []
    # 残存日数不足（納品日8/5・最低60日 → 9/1は27日しかない）
    alerts = eg.evaluate_item("2026-09-01", None, delivery_date="2026-08-05", min_remaining_days=60)
    assert len(alerts) == 1 and alerts[0]["type"] == "min_remaining" and alerts[0]["remaining_days"] == 27
    # 両方同時
    alerts = eg.evaluate_item("2026-09-01", ref, delivery_date="2026-08-05", min_remaining_days=60)
    assert {a["type"] for a in alerts} == {"reversal", "min_remaining"}
    # 期限未入力は評価しない
    assert eg.evaluate_item("", ref, delivery_date="2026-08-05", min_remaining_days=60) == []
