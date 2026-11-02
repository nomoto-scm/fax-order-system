"""明細行ごとに納品先が異なる発注書（伊藤忠食品SEJ形式）の分割・統合テスト"""
import pytest

from ocr_module import expand_multi_dest_ocr, merge_multi_dest_results, normalize


def _row(name, dest, qty, jan="4589570801331"):
    return {
        "product_name": name,
        "jan_code": jan,
        "quantity_cs": qty,
        "delivery_dest": dest,
    }


SEJ_DESTS = [
    "常温一括大津物流センター", "大正ＳＥＪ倉＜常温＞", "西淀川ＳＥＪ倉＜常温＞",
    "岸和田ＳＥＪ倉", "大阪北ＳＥＪ倉", "加古川ＳＥＪ倉",
]


def _sej_page(order_no, product, jan, quantities):
    return {
        "order_no": order_no,
        "delivery_date": "2026-09-18",
        "sender": "伊藤忠食品株式会社",
        "delivery_dest": SEJ_DESTS[0],
        "items": [_row(product, d, q, jan) for d, q in zip(SEJ_DESTS, quantities)],
    }


class TestExpand:
    def test_splits_one_result_per_destination(self):
        ocr = _sej_page("540631", "2Gummy リポソームＶＣ", "4589570801331",
                        [22, 22, 22, 15, 25, 18])
        out = expand_multi_dest_ocr(ocr)
        assert len(out) == len(SEJ_DESTS)
        # delivery_dest は既存のNFKC正規化（全角→半角）が掛かった状態で返る
        assert [o["delivery_dest"] for _, o in out] == [normalize(d) for d in SEJ_DESTS]
        assert [o["items"][0]["quantity_cs"] for _, o in out] == [22, 22, 22, 15, 25, 18]
        # 各分割結果は1納品先ぶんの明細だけを持つ
        assert all(len(o["items"]) == 1 for _, o in out)

    def test_order_no_is_branch_numbered_per_destination(self):
        ocr = _sej_page("540631", "2Gummy リポソームＶＣ", "4589570801331",
                        [22, 22, 22, 15, 25, 18])
        out = expand_multi_dest_ocr(ocr)
        assert [o["order_no"] for _, o in out] == [
            "540631-01", "540631-02", "540631-03",
            "540631-04", "540631-05", "540631-06",
        ]
        # FAX原本の発注Noは追跡用に残る
        assert all(o["source_order_no"] == "540631" for _, o in out)

    def test_single_destination_page_is_untouched(self):
        """納品先列が無い従来フォーマットは分割しない（既存動作の保護）"""
        ocr = {
            "order_no": "A123",
            "delivery_dest": "伊藤忠食品 三郷物流センター",
            "items": [
                {"product_name": "2Water Ceramide 500ml", "quantity_cs": 10},
                {"product_name": "2Energy 250ml", "quantity_cs": 5},
            ],
        }
        out = expand_multi_dest_ocr(ocr)
        assert len(out) == 1
        assert out[0][0] == 0
        assert out[0][1] is ocr
        assert out[0][1]["order_no"] == "A123"

    def test_all_rows_same_destination_is_not_split(self):
        ocr = {
            "order_no": "B1",
            "delivery_dest": "大津物流センター",
            "items": [_row("2Water", "大津物流センター", 3),
                      _row("2Energy", "大津物流センター", 4)],
        }
        assert len(expand_multi_dest_ocr(ocr)) == 1

    def test_blank_destination_row_continues_previous(self):
        """セル結合で納品先が空の行は直前の納品先に属する"""
        ocr = {
            "order_no": "C1",
            "delivery_dest": "大津",
            "items": [_row("VC", "大津", 5), _row("VD", "", 6), _row("VC", "大正", 7)],
        }
        out = expand_multi_dest_ocr(ocr)
        assert len(out) == 2
        assert [i["quantity_cs"] for i in out[0][1]["items"]] == [5, 6]
        assert [i["quantity_cs"] for i in out[1][1]["items"]] == [7]

    def test_empty_items_is_untouched(self):
        ocr = {"order_no": "D1", "delivery_dest": "大津", "items": []}
        assert expand_multi_dest_ocr(ocr) == [(0, ocr)]


def _result(page, dest_code, dest_name, order_no, src_no, items, multi=True):
    return {
        "page": page,
        "image_page": page,
        "ocr_raw": {
            "order_no": order_no,
            "source_order_no": src_no,
            "delivery_date": "2026-09-18",
            "delivery_dest": dest_name,
        },
        "matched_items": items,
        "ddc_match": {"matched": True, "nohinsaki_code": dest_code, "name": dest_name},
        "multi_dest": multi,
    }


def _item(name, qty, dest="ハルナ"):
    return {"matched": True, "master_name": name, "quantity": qty, "output_dest": dest}


class TestMerge:
    def test_same_destination_across_pages_becomes_one_order(self):
        """1枚目=VC / 2枚目=VD が同じ納品先なら1受注2明細にまとまる"""
        results = [
            _result(1, "D001", "常温一括大津物流センター", "540631-01", "540631", [_item("VC", 22)]),
            _result(1, "D002", "大正ＳＥＪ倉", "540631-02", "540631", [_item("VC", 22)]),
            _result(2, "D001", "常温一括大津物流センター", "640000-01", "640000", [_item("VD", 21)]),
            _result(2, "D002", "大正ＳＥＪ倉", "640000-02", "640000", [_item("VD", 21)]),
        ]
        merged = merge_multi_dest_results(results)
        assert len(merged) == 2
        assert [r["page"] for r in merged] == [1, 2]
        otsu = merged[0]
        assert [i["master_name"] for i in otsu["matched_items"]] == ["VC", "VD"]
        assert [i["quantity"] for i in otsu["matched_items"]] == [22, 21]
        # 発注番号は1枚目基準、2枚目の発注Noは追跡用に残る
        assert otsu["ocr_raw"]["order_no"] == "540631-01"
        assert otsu["source_order_nos"] == ["540631", "640000"]
        assert otsu["merged_image_pages"] == [1, 2]

    def test_output_dest_lists_are_rebuilt_after_merge(self):
        results = [
            _result(1, "D001", "大津", "1-01", "1", [_item("VC", 22, "ハルナ")]),
            _result(2, "D001", "大津", "2-01", "2", [_item("2Snack", 5, "シルビア")]),
        ]
        merged = merge_multi_dest_results(results)
        assert len(merged) == 1
        assert [i["master_name"] for i in merged[0]["haruna_items"]] == ["VC"]
        assert [i["master_name"] for i in merged[0]["sylvia_items"]] == ["2Snack"]

    def test_different_delivery_dates_are_not_merged(self):
        a = _result(1, "D001", "大津", "1-01", "1", [_item("VC", 22)])
        b = _result(2, "D001", "大津", "2-01", "2", [_item("VD", 21)])
        b["ocr_raw"]["delivery_date"] = "2026-09-19"
        merged = merge_multi_dest_results([a, b])
        assert len(merged) == 2

    def test_unmatched_destination_is_left_separate(self):
        """納品先マスタ未確定の行は誤マージを避けてそのまま残す"""
        a = _result(1, "", "謎センター", "1-01", "1", [_item("VC", 22)])
        a["ddc_match"] = {"matched": False, "name": "謎センター"}
        b = _result(2, "", "謎センター", "2-01", "2", [_item("VD", 21)])
        b["ddc_match"] = {"matched": False, "name": "謎センター"}
        merged = merge_multi_dest_results([a, b])
        assert len(merged) == 2
        assert [r["page"] for r in merged] == [1, 2]

    def test_non_multi_dest_results_are_not_merged(self):
        """従来フォーマット（1ページ1納品先）は同一納品先でも統合しない"""
        a = _result(1, "D001", "大津", "A", "", [_item("VC", 22)], multi=False)
        b = _result(2, "D001", "大津", "B", "", [_item("VD", 21)], multi=False)
        merged = merge_multi_dest_results([a, b])
        assert len(merged) == 2

    def test_error_pages_survive_and_keep_image_page(self):
        err = {"page": 1, "image_page": 1, "error": "OCR error"}
        ok = _result(2, "D001", "大津", "2-01", "2", [_item("VD", 21)])
        merged = merge_multi_dest_results([err, ok])
        assert len(merged) == 2
        assert merged[0]["error"] == "OCR error"
        assert merged[0]["image_page"] == 1
        # ページ番号は通し番号に振り直され、原本画像はimage_pageで引ける
        assert merged[1]["page"] == 2
        assert merged[1]["image_page"] == 2


from ocr_module import _center_place_key, _center_place_score, _chain_hint, match_ddc


class TestCenterPlaceMatching:
    """発注書とマスタでセンター名の呼び方が違うケースの突合"""

    @pytest.mark.parametrize("raw,expected", [
        ("常温一括大津物流センター", "大津"),
        ("大正ＳＥＪ倉＜常温＞", "大正"),
        ("岡山ＳＥＪ倉〈常温〉", "岡山"),
        ("北九州ＳＥＪ倉＜常温＞", "北九州"),
        ("常温大阪大正センター(伊藤忠商事（株）)", "大阪大正"),
        ("常温一括加古川センター(伊藤忠商事（コンフェックス）", "加古川"),
    ])
    def test_place_key_extraction(self, raw, expected):
        assert _center_place_key(raw) == expected

    def test_same_place_scores_high(self):
        assert _center_place_score("加古川SEJ倉", "常温一括加古川センター") == 0.90

    def test_place_contained_in_longer_place_scores_slightly_lower(self):
        # FAX「大正」 vs マスタ「大阪大正」
        assert _center_place_score("大正SEJ倉", "常温大阪大正センター") == 0.88

    def test_different_places_score_zero(self):
        assert _center_place_score("大阪北SEJ倉", "常温大阪大正センター") == 0.0

    def test_non_facility_strings_are_ignored(self):
        """施設名らしさが無い文字列には効かせない（社名同士の誤ヒット防止）"""
        assert _center_place_score("伊藤忠食品", "三菱食品") == 0.0

    @pytest.mark.parametrize("name,expected", [
        ("広島ＳＥＪ倉＜常温＞", "セブンイレブン"),
        ("福岡SEJ倉", "セブンイレブン"),
        ("常温一括大津物流センター", ""),
        ("三郷物流センター", ""),
    ])
    def test_chain_hint(self, name, expected):
        assert _chain_hint(name) == expected


class TestChainGating:
    """地名突合は取引先（チェーン）が一致する行にしか効かせない"""

    @staticmethod
    def _master():
        import pandas as pd
        return pd.DataFrame([
            {"納品先名": "常温広島センター(伊藤忠商事（株）)", "納品先コード": "9194",
             "取引先名": "株式会社セブンイレブン・ジャパン（関東）", "住所": "広島県広島市X",
             "郵便番号": "", "電話番号": "", "FAX番号": "", "取引先コード": "",
             "請求先コード": "", "納品方法": ""},
            {"納品先名": "広島物流センター", "納品先コード": "832",
             "取引先名": "イオンリテール株式会社（カフェランテ）", "住所": "広島県広島市Y",
             "郵便番号": "", "電話番号": "", "FAX番号": "", "取引先コード": "",
             "請求先コード": "", "納品方法": ""},
        ])

    def test_sej_destination_does_not_match_other_chain(self):
        """「広島ＳＥＪ倉」がイオンの広島物流センターに落ちないこと"""
        r = match_ddc("広島ＳＥＪ倉＜常温＞", self._master())
        assert r["matched"] is True
        assert r["nohinsaki_code"] == "9194"
        assert "セブンイレブン" in r["torihikisaki"]

    def test_page_level_chain_hint_applies_to_rows_without_abbreviation(self):
        """行に「SEJ」が無くても、ページ全体のチェーンヒントで取引先を絞れる"""
        r = match_ddc("常温一括広島物流センター", self._master(), chain="セブンイレブン")
        assert r["nohinsaki_code"] == "9194"

    def test_place_match_is_flagged_for_review(self):
        """地名突合は新ヒューリスティックなので必ず要確認（低信頼度）にする"""
        r = match_ddc("広島ＳＥＪ倉＜常温＞", self._master())
        assert r["low_confidence"] is True
        assert r["match_score"] <= 0.90

    def test_without_chain_hint_place_match_does_not_fire(self):
        """チェーンが特定できない場合は地名だけで寄せない（他チェーン誤ヒット防止）"""
        r = match_ddc("福岡センター", self._master())
        assert not r.get("matched")


from process_fax import _split_address
from shipping_fee_calculator import extract_prefecture, get_zone


class TestPrefectureFallback:
    """納品先マスタの住所に都道府県が無い場合の補完

    実例: セブンイレブン 常温大阪大正センター[9188]「大阪市大正区船町1丁目2-21」、
          常温山口センター[9195]「山口市鋳銭司字鋳銭司団地北447-44」
    補完しないとロジレスCSVの配送先都道府県が空になり、送料のゾーン判定も不能になる。
    """

    @pytest.mark.parametrize("address,expected", [
        ("大阪市大正区船町1丁目2-21", "大阪府"),
        ("山口市鋳銭司字鋳銭司団地北447-44", "山口県"),
        ("北九州市小倉南区曽根北町5-14", "福岡県"),
        ("札幌市中央区X", "北海道"),
    ])
    def test_infers_prefecture_when_omitted(self, address, expected):
        assert extract_prefecture(address) == expected

    def test_explicit_prefecture_still_wins(self):
        assert extract_prefecture("大阪府大阪市西淀川区西島1-1-62") == "大阪府"

    def test_unknown_address_returns_none(self):
        assert extract_prefecture("どこかの村1-2-3") is None

    def test_zone_is_resolvable_after_inference(self):
        """ゾーン判定（送料計算）まで通ること"""
        assert get_zone(extract_prefecture("大阪市大正区船町1丁目2-21")) == "関西"
        assert get_zone(extract_prefecture("山口市鋳銭司字鋳銭司団地北447-44")) == "中国/四国"

    def test_split_address_fills_prefecture_without_duplicating_it(self):
        """補完しても住所本体に都道府県を二重に付けない"""
        assert _split_address("大阪市大正区船町1丁目2-21") == (
            "大阪府", "大阪市大正区船町1丁目2-21", "", "")

    def test_split_address_matches_the_complete_record(self):
        """都道府県あり/なしの同一センターが同じ分割結果になること
        （[9188] 伊藤忠商事（株） と [1168] コンフェックス は同じ住所）"""
        assert (_split_address("大阪市大正区船町1丁目2-21")
                == _split_address("大阪府大阪市大正区船町1丁目2-21"))

    def test_existing_prefecture_prefix_behaviour_unchanged(self):
        assert _split_address("東京都港区赤坂1-2-3") == ("東京都", "港区赤坂1-2-3", "", "")
