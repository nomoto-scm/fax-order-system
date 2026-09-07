"""gen_haruna_pdf が商品マスタの名称を出すことの確認（Sparkling / 2L / 混在 / 旧呼び出し）"""
import io
from pdf_generator import gen_haruna_pdf
from pypdf import PdfReader

order = {"order_no": "TEST-001", "order_date": "2026-09-07", "delivery_date": "2026-09-10",
         "delivery_dest": "テスト物流センター", "quantity": 100, "remarks": ""}
ddc = {"postal": "100-0001", "address": "東京都千代田区1-1", "tel": "03-0000-0000",
       "fax": "03-0000-0001", "time": "午前中", "berse": "無",
       "palette": "JPRパレット名義変更", "jpr": "18571", "method": "", "notes": ""}

SPARKLING = {"master_name": "2Water Sparkling 500ml", "spec": "500ml×24本/cs",
             "pack": "24本×1cs", "quantity": 100, "case_quantity": 24}
CERAMIDE = {"master_name": "2Water Ceramide 500ml", "spec": "500ml×24本/cs",
            "pack": "24本×1cs", "quantity": 40, "case_quantity": 24}
TWO_L = {"master_name": "2Water Ceramide 2L", "spec": "2L×6本/cs",
         "pack": "6本×1cs", "quantity": 10, "case_quantity": 6}


def text_of(buf):
    return "\n".join(p.extract_text() or "" for p in PdfReader(buf).pages)


def check(label, items, must_have, must_not, expect_bara):
    buf = gen_haruna_pdf(order, ddc, "伊藤", items=items)
    t = text_of(buf).replace(" ", "")
    ok = True
    for m in must_have:
        hit = m.replace(" ", "") in t
        print(f"    {'OK' if hit else 'NG'} 含む: {m!r}")
        ok &= hit
    for m in must_not:
        hit = m.replace(" ", "") in t
        print(f"    {'OK' if not hit else 'NG'} 含まない: {m!r}")
        ok &= not hit
    b = "{:,}本".format(expect_bara)
    hit = b.replace(" ", "") in t
    print(f"    {'OK' if hit else 'NG'} 小計: {b}")
    ok &= hit
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


allok = True
print("1) Sparkling 単品 100cs")
allok &= check("Sparkling", [SPARKLING], ["2Water Sparkling 500ml"], ["2Water Ceramide"], 2400)

print("2) 2L 10cs（入数6 — 旧コードは×24で誤算）")
allok &= check("2L", [TWO_L], ["2Water Ceramide 2L", "2L×6本/cs"], [], 60)

print("3) Sparkling + Ceramide 混在")
allok &= check("混在", [SPARKLING, CERAMIDE],
               ["2Water Sparkling 500ml", "2Water Ceramide 500ml"], [], 2400 + 960)

print("4) items 無し（後方互換・従来の1行）")
allok &= check("フォールバック", None, ["2Water Ceramide"], [], 2400)

print("\n" + ("すべて PASS" if allok else "FAIL あり"))
raise SystemExit(0 if allok else 1)
