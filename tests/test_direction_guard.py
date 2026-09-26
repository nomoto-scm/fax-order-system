"""direction_guard（上り/下りガード）の単体テスト＋match_ddc統合テスト"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import direction_guard as dg


# ─── 純粋関数 ───

def test_extract_direction_variants():
    assert dg.extract_direction("北陸自動車道 栄店（上り）") == "上り"
    assert dg.extract_direction("北陸自動車道 栄店(下り)") == "下り"
    assert dg.extract_direction("北陸自動車道 栄店（上り）") == "上り"  # 全角括弧
    assert dg.extract_direction("栄店 上り") == "上り"  # 括弧なし（OCR原文対策）
    assert dg.extract_direction("三菱食品 ローソン埼京DDC") is None
    assert dg.extract_direction("") is None
    assert dg.extract_direction(None) is None


def test_opposite_direction():
    assert dg.opposite_direction("上り") == "下り"
    assert dg.opposite_direction("下り") == "上り"
    assert dg.opposite_direction("東口") is None


def test_strip_direction_pairs_share_base():
    up = "ネクスコ東日本リテイル 北陸自動車道 栄店（上り）"
    down = "ネクスコ東日本リテイル 北陸自動車道 栄店（下り）"
    assert dg.strip_direction(up) == dg.strip_direction(down)
    assert dg.strip_direction(up) != dg.strip_direction("ネクスコ東日本リテイル 北陸自動車道 名立谷浜店（上り）")


def test_find_direction_pairs():
    rows = [
        {"name": "ネクスコ東日本リテイル 北陸自動車道 栄店（上り）", "nohinsaki_code": "1090"},
        {"name": "ネクスコ東日本リテイル 北陸自動車道 栄店（下り）", "nohinsaki_code": "1091"},
        {"name": "ネクスコ東日本リテイル 関越自動車道 塩沢石打店（上り）", "nohinsaki_code": "1080"},
        {"name": "三菱食品 ローソン埼京DDC", "nohinsaki_code": "500"},
    ]
    pairs = dg.find_direction_pairs("ネクスコ東日本リテイル 北陸自動車道 栄店（上り）", rows)
    assert len(pairs) == 1
    assert pairs[0]["nohinsaki_code"] == "1091"
    # 方面なし名称にはペアなし
    assert dg.find_direction_pairs("三菱食品 ローソン埼京DDC", rows) == []


def test_build_direction_alert_mismatch():
    rows = [
        {"name": "北陸自動車道 栄店（上り）"},
        {"name": "北陸自動車道 栄店（下り）"},
    ]
    # OCR原文=下り なのにマッチ結果=上り → mismatch（実事故のパターン）
    alert = dg.build_direction_alert("北陸自動車道 栄店（下り）", "北陸自動車道 栄店（上り）", rows)
    assert alert["matched_direction"] == "上り"
    assert alert["ocr_direction"] == "下り"
    assert alert["mismatch"] is True
    assert len(alert["pairs"]) == 1

    # 方面一致 → mismatchなし（それでも要確認にはする＝呼び出し側の責務）
    alert2 = dg.build_direction_alert("栄店（上り）", "北陸自動車道 栄店（上り）", rows)
    assert alert2["mismatch"] is False

    # 方面なしマッチ → None
    assert dg.build_direction_alert("ローソン埼京DDC", "三菱食品 ローソン埼京DDC", rows) is None


# ─── match_ddc 統合テスト ───

def _make_master():
    import pandas as pd
    cols = {"取引先名": "ネクスコ東日本リテイル", "取引先コード": "200",
            "郵便番号": "959-0000", "FAX番号": "", "納品方法": ""}
    return pd.DataFrame([
        {"納品先名": "ネクスコ東日本リテイル 北陸自動車道 栄店（上り）", "納品先コード": "1090",
         "請求先コード": "1101", "住所": "新潟県三条市栄PA上り", "電話番号": "0256-00-0001", **cols},
        {"納品先名": "ネクスコ東日本リテイル 北陸自動車道 栄店（下り）", "納品先コード": "1091",
         "請求先コード": "1102", "住所": "新潟県三条市栄PA下り", "電話番号": "0256-00-0002", **cols},
        {"納品先名": "三菱食品 ローソン埼京DDC", "納品先コード": "500",
         "請求先コード": "600", "住所": "埼玉県戸田市", "電話番号": "048-000-0000",
         "取引先名": "三菱食品", "取引先コード": "100", "郵便番号": "335-0000", "FAX番号": "", "納品方法": ""},
    ])


def _patch_haruna():
    import supabase_client
    supabase_client._haruna_conditions_cache = {}


def test_match_ddc_direction_forces_review():
    """方面付き納品先は完全一致でも low_confidence=True＋direction_alert＋対候補付き"""
    _patch_haruna()
    from ocr_module import match_ddc
    master = _make_master()

    # OCRが「上り」と誤読 → 上り側に完全一致（実事故の再現）
    result = match_ddc("ネクスコ東日本リテイル 北陸自動車道 栄店（上り）", master)
    assert result["matched"] is True
    assert result["low_confidence"] is True, "方面付きは完全一致でも要確認になること"
    alert = result.get("direction_alert")
    assert alert is not None
    assert alert["matched_direction"] == "上り"
    assert len(alert["pairs"]) == 1
    assert alert["pairs"][0]["nohinsaki_code"] == "1091"
    # 対になる（下り）が候補に必ず並ぶこと
    cand_names = [c["name"] for c in result["candidates"]]
    assert any("（下り）" in n for n in cand_names)


def test_match_ddc_no_direction_unchanged():
    """方面なし納品先は従来挙動のまま（完全一致=OK・アラートなし）"""
    _patch_haruna()
    from ocr_module import match_ddc
    master = _make_master()
    result = match_ddc("三菱食品 ローソン埼京DDC", master)
    assert result["matched"] is True
    assert result["low_confidence"] is False
    assert "direction_alert" not in result
