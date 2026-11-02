"""ハルナ発注書PDFのファイル名が 納品先名_納品日_ハルナ発注書_オーダーNO_pページ.pdf になることの確認。

本番と同じ process_fax.generate_pdfs() を呼ぶ。出力先だけ一時フォルダに差し替えるので、
Supabase / Google Drive / output/ には一切書き込まない。

実行: python test_haruna_filename.py
"""
import os
import shutil
import tempfile

import process_fax
from process_fax import generate_pdfs

HARUNA_ITEM = {
    "matched": True, "output_dest": "ハルナ",
    "master_name": "2Water Ceramide 500ml", "jan": "4589570801485",
    "spec": "500ml×24本/cs", "pack": "24本×1cs",
    "quantity": 48, "case_quantity": 24, "cs_price": 1056, "unit_price": 44,
    "amount": 50688,
}


def page(page_num, dest_name, delivery_date, order_no):
    """generate_pdfs が受け取る1ページ分の結果を組み立てる"""
    item = dict(HARUNA_ITEM)
    return {
        "page": page_num,
        "ocr_raw": {"order_no": order_no, "delivery_date": delivery_date,
                    "delivery_dest": dest_name, "sender": "テスト発注元"},
        "ddc_match": {"name": dest_name, "matched": True,
                      "postal": "253-0111", "address": "神奈川県高座郡寒川町倉見1339-2",
                      "tel": "0467722141", "fax": "", "time": "6:00～11:00",
                      "berse": "無", "palette": "", "jpr": "", "method": "", "notes": ""},
        "sylvia_items": [], "haruna_items": [item], "matched_items": [item],
        "remarks": "",
    }


def run(label, results, expected):
    generated = generate_pdfs(results, "efax_TEST_20260910.pdf", "伊藤")
    actual = [os.path.basename(p) for lbl, p in generated if lbl == "ハルナ"]
    ok = actual == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    for name in actual:
        exists = os.path.exists(os.path.join(process_fax.OUTPUT_DIR, name))
        print(f"      {'OK' if exists else 'NG(未生成)'} {name}")
    if not ok:
        print(f"      期待: {expected}")
        print(f"      実際: {actual}")
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="haruna_filename_test_")
    # 出力先を一時フォルダへ退避（output/ を汚さない）
    orig_out, orig_hist = process_fax.OUTPUT_DIR, process_fax.HISTORY_DIR
    process_fax.OUTPUT_DIR = tmp
    process_fax.HISTORY_DIR = os.path.join(tmp, "履歴")
    allok = True
    try:
        print("1) 通常1ページ（括弧つきの納品先名）")
        allok &= run("成城ドライ物流センター",
                     [page(1, "成城ドライ物流センター(三井物産流通グループ)", "2026-08-26", "260810004")],
                     ["成城ドライ物流センター(三井物産流通グループ)_2026-08-26_ハルナ発注書_260810004_p1.pdf"])

        print("2) 全角スペース・㈱・末尾の \" を含む納品先名（サニタイズ）")
        allok &= run("加藤産業㈱ 高谷　特売第2倉庫",
                     [page(1, '加藤産業㈱ 高谷　特売第2倉庫"', "2026-09-01", "260901007")],
                     ["加藤産業㈱ 高谷 特売第2倉庫_2026-09-01_ハルナ発注書_260901007_p1.pdf"])

        print("3) 同一納品先・同一納品日が2ページ（pNで衝突しないこと）")
        allok &= run("2ページ",
                     [page(1, "京浜ＤＤＣ", "2026-08-20", "260810001"),
                      page(2, "京浜ＤＤＣ", "2026-08-20", "260810001")],
                     ["京浜ＤＤＣ_2026-08-20_ハルナ発注書_260810001_p1.pdf",
                      "京浜ＤＤＣ_2026-08-20_ハルナ発注書_260810001_p2.pdf"])

        print("4) 納品日・オーダーNOが空（落ちずにフォールバック）")
        allok &= run("欠損値",
                     [page(1, "京浜ＤＤＣ", "", "")],
                     ["京浜ＤＤＣ_納品日不明_ハルナ発注書_オーダーNO不明_p1.pdf"])

        print("5) 納品先名も空（全項目フォールバック）")
        p = page(1, "", "2026-09-10", "260910001")
        p["ddc_match"]["name"] = ""
        allok &= run("納品先名なし", [p],
                     ["納品先不明_2026-09-10_ハルナ発注書_260910001_p1.pdf"])
    finally:
        process_fax.OUTPUT_DIR, process_fax.HISTORY_DIR = orig_out, orig_hist
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("すべて PASS" if allok else "FAIL あり"))
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
