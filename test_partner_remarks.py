"""代理店4社の発注書の既定備考が新しい文言になっているかの確認"""
from pypdf import PdfReader

from pdf_generator import (_PARTNER_DEFAULT_REMARKS, gen_nomura_pdf, gen_emsink_pdf,
                           gen_powbar_pdf, gen_daylily_pdf)

NEW = "貼り紙と納品書の添付は不要です"
OLD = "別紙添付の貼り紙を外装箱に貼り付けて出荷をお願いします。"

ORDER = {"order_no": "T-1", "order_date": "2026-09-18", "delivery_date": "2026-09-25",
         "delivery_dest": "テスト宛先", "quantity": 10, "remarks": ""}
ITEMS = [{"master_name": "テスト商品", "jan": "4500000000000", "spec": "1袋",
          "pack": "1袋", "quantity": 12, "case_quantity": 1,
          "unit_price": 0, "cs_price": 0, "amount": 0, "code": "TEST001"}]

ok = True


def chk(label, cond):
    global ok
    ok &= bool(cond)
    print(f"  {'OK' if cond else 'NG'} {label}")


print("1) 定数")
chk(f"新しい文言になっている: {NEW}", _PARTNER_DEFAULT_REMARKS == NEW)

print("\n2) 4社の発注書PDFに反映される")
for name, fn in [("野村不動産", gen_nomura_pdf), ("エムズインク", gen_emsink_pdf),
                 ("POW BAR", gen_powbar_pdf), ("DAYLILY", gen_daylily_pdf)]:
    t = "\n".join(p.extract_text() or "" for p in PdfReader(fn(ORDER, ITEMS, "伊藤")).pages)
    flat = t.replace(" ", "").replace("　", "")
    chk(f"{name}: 新文言が出る", NEW.replace(" ", "") in flat)
    chk(f"{name}: 旧文言が残っていない", OLD.replace(" ", "") not in flat)

print("\n3) 個別のremarksが指定されていればそちらが優先される（非回帰）")
o = dict(ORDER, remarks="この案件だけの特記事項")
t = "\n".join(p.extract_text() or "" for p in PdfReader(gen_powbar_pdf(o, ITEMS, "伊藤")).pages)
chk("指定した備考が出る", "この案件だけの特記事項" in t.replace(" ", ""))

print("\n" + ("すべて PASS" if ok else "FAIL あり"))
raise SystemExit(0 if ok else 1)
