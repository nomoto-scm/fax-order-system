"""OCR全ページ失敗時にエラーフォルダへ退避されるかの判定テスト。

2026-09-23 のAPIクレジット枯渇時、全ページ失敗したFAXが「処理済み」へ
流れて障害に気づけなかったリグレッションを防ぐ。
"""
from drive_processor import is_ocr_total_failure


def test_全ページ失敗はエラー扱い():
    results = [{"error": "OCR error: credit balance too low"},
               {"error": "OCR error: credit balance too low"}]
    assert is_ocr_total_failure(results) is True


def test_一部成功はエラー扱いにしない():
    results = [{"error": "OCR error"}, {"delivery_dest": "成城ドライ物流センター"}]
    assert is_ocr_total_failure(results) is False


def test_全ページ成功はエラー扱いにしない():
    results = [{"delivery_dest": "A"}, {"delivery_dest": "B"}]
    assert is_ocr_total_failure(results) is False


def test_結果が空なら判定しない():
    # CSV等で results が空のケースは既存の別分岐が扱う
    assert is_ocr_total_failure([]) is False


def test_単ページ失敗もエラー扱い():
    assert is_ocr_total_failure([{"error": "JSON parse failed"}]) is True
