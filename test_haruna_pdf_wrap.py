"""gen_haruna_pdf の備考折り返しの確認（枠内備考・欄外※備考）"""
from reportlab.lib.units import mm
from pypdf import PdfReader

from pdf_generator import gen_haruna_pdf, fit_lines, wrap_lines, tw

BIKOU_W, BIKOU_FS = 87 * mm, 10
LONG_NOTE = "木パレット禁止／ビールパレット可。予約システムあり（日本アクセスの専用システム）"
LONG_REMARK = ("賞味期限 2027.02.16以降。発注伝票No:01377283を納品書および請求書へ記載すること。"
               "賞味期限をワンバッチでお願いします。数量及び着日の変更時のみ必ずファックス返信願います。")
ITEMS = [{"master_name": "2Water Sparkling 500ml", "spec": "500ml×24本/cs",
          "pack": "24本×1cs", "quantity": 10, "case_quantity": 24}]


def build(notes, remarks):
    order = {"order_no": "T", "order_date": "2026-09-18", "delivery_date": "2026-09-25",
             "delivery_dest": "静岡物流Ｃ（伊藤忠商事日本アクセス案件）",
             "quantity": 10, "remarks": remarks}
    ddc = {"postal": "420-0922", "address": "静岡県静岡市葵区流通センター14番3号",
           "tel": "054-655-2520", "fax": "", "time": "午前中", "berse": "有り",
           "palette": "JPRパレット名義変更", "jpr": "15557", "method": "", "notes": notes}
    buf = gen_haruna_pdf(order, ddc, "伊藤", items=ITEMS)
    return "\n".join(p.extract_text() or "" for p in PdfReader(buf).pages).replace(" ", "")


ok = True


def chk(label, cond):
    global ok
    ok &= bool(cond)
    print(f"  {'OK' if cond else 'NG'} {label}")


print("1) 各行が幅に収まる（折り返しの基本）")
for fs, txt, w in [(10, LONG_NOTE, BIKOU_W), (14, "※ " + LONG_REMARK, 273 * mm)]:
    nfs, lines = fit_lines(txt, w, fs, max_lines=None, shrink=False)
    chk(f"fs={fs} 全{len(lines)}行が幅以内",
        all(tw(l, nfs) <= w for l in lines))
    chk(f"fs={fs} 連結すると元の文字列に戻る", "".join(lines) == txt)

print("\n2) 枠内の備考: 長文でも末尾まで出る")
t = build(LONG_NOTE, "")
chk("末尾『専用システム）』が残る", "専用システム）" in t)
chk("先頭『木パレット禁止』が残る", "木パレット禁止" in t)

print("\n3) 欄外の※備考: 長文でも末尾まで出る")
t = build("", LONG_REMARK)
chk("末尾『ファックス返信願います。』が残る", "ファックス返信願います。" in t)
chk("中間『ワンバッチ』が残る", "ワンバッチ" in t)

print("\n4) 短い備考は1行のまま（従来どおり）")
short = "木パレット禁止/ビールパレット可/予約システムあり"
nfs, lines = fit_lines(short, BIKOU_W, BIKOU_FS, max_lines=4)
chk("1行に収まる", len(lines) == 1 and nfs == BIKOU_FS)
t = build(short, "賞味期限2027.02.16以降")
chk("備考が出る", "予約システムあり" in t)
chk("※備考が出る", "賞味期限2027.02.16以降" in t)

print("\n5) 空でも落ちない")
t = build("", "")
chk("商品名は出る", "2WaterSparkling500ml" in t)
chk("小計は出る", "240本" in t)

print("\n" + ("すべて PASS" if ok else "FAIL あり"))
raise SystemExit(0 if ok else 1)
