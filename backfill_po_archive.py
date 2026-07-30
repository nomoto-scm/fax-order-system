"""過去に生成したパートナー発注書PDFを、請求突合フォルダへ遡って投入する。

`po_archive` は確定処理のたびに自動複製するが、機能追加前（2026-07以前）に
生成された発注書は複製されていない。このスクリプトで遡って埋める。

やり方:
  output/ と output/履歴/ にあるパートナー発注書PDFから、印字されている
  「オーダーNO」と「納品日」を読み取る（＝ファイル名や更新日時に頼らない）。
  同じオーダーNOが複数ある場合（再確定で履歴に積まれたもの）は最新版だけを採用する。

使い方:
  python backfill_po_archive.py --since 2026-06-01
  python backfill_po_archive.py --since 2026-06-01 --dry-run
"""
import argparse
import glob
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp932", "shift_jis", "mbcs"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import po_archive

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
HISTORY_DIR = os.path.join(OUTPUT_DIR, "履歴")

# ファイル名プレフィックス → 出力先（process_fax.PARTNER_PDF_GENERATORS と対応）
PREFIX_TO_PARTNER = {
    "野村不動産": "野村不動産",
    "エムズインク": "エムズインク",
    "POWBAR": "POW BAR",
    "DAYLILY": "DAYLILY",
}

# 伝票番号は「0077 5656」のように途中に空白が入ることがあるので行末まで拾う
# （po_archive._safe が空白を除去してファイル名にする）
_ORDER_NO_RE = re.compile(r"オーダーNO：[ \t]*\n?[ \t]*(.+)")
_DELIVERY_RE = re.compile(r"納品日：[ \t]*(\S+)")


def _pdf_text(path):
    """PDFの1ページ目のテキストを返す（PyMuPDF）。"""
    try:
        import fitz
    except ImportError:
        print("PyMuPDF (fitz) が必要です: pip install pymupdf")
        raise
    try:
        with fitz.open(path) as doc:
            return doc[0].get_text() if doc.page_count else ""
    except Exception as e:
        print(f"  読み取り失敗 {os.path.basename(path)}: {e}")
        return ""


def _partner_of(filename):
    for prefix, partner in PREFIX_TO_PARTNER.items():
        if filename.startswith(prefix + "_"):
            return partner
    return None


def collect(since):
    """パートナー発注書PDFを走査し、オーダーNO単位で最新版を返す。"""
    candidates = sorted(glob.glob(os.path.join(OUTPUT_DIR, "*.pdf"))) + \
        sorted(glob.glob(os.path.join(HISTORY_DIR, "*.pdf")))

    by_key = {}
    for path in candidates:
        name = os.path.basename(path)
        partner = _partner_of(name)
        if not partner:
            continue
        text = _pdf_text(path)
        if not text:
            continue
        m_order = _ORDER_NO_RE.search(text)
        m_deliv = _DELIVERY_RE.search(text)
        order_no = (m_order.group(1).strip() if m_order else "")
        delivery = (m_deliv.group(1).strip() if m_deliv else "")
        ymd = po_archive._ymd(delivery)
        if since and (not ymd or ymd < since):
            continue
        page = 1
        m_page = re.search(r"_p(\d+)(?:_\d+)?\.pdf$", name)
        if m_page:
            page = int(m_page.group(1))
        key = (partner, order_no or name, page)
        # 同一オーダーは更新日時の新しい方（＝最後に確定した版）を採用
        prev = by_key.get(key)
        if prev is None or os.path.getmtime(path) > os.path.getmtime(prev["path"]):
            by_key[key] = {
                "path": path, "partner": partner, "order_no": order_no,
                "delivery": delivery, "page": page, "ymd": ymd,
            }
    return sorted(by_key.values(), key=lambda r: (r["ymd"] or "", r["partner"]))


def main(since, dry_run=False):
    inbox = po_archive.inbox_dir()
    print(f"投入先: {inbox or '(見つからない → 中止)'}")
    if not inbox:
        return 1

    rows = collect(since)
    print(f"対象（納品日 {since} 以降）: {len(rows)}件\n")

    copied = skipped = 0
    for r in rows:
        label = f"{r['ymd']} {r['partner']:<8} 伝票{r['order_no']:<12} p{r['page']}  {os.path.basename(r['path'])}"
        if dry_run:
            print(f"  [dry-run] {label}")
            continue
        dest = po_archive.archive_generated_po(
            r["path"], r["partner"],
            delivery_date=r["delivery"],
            slip_number=r["order_no"],
            page=r["page"],
            source_name=os.path.basename(r["path"]),
        )
        if dest:
            copied += 1
            print(f"  ✅ {label}\n       → {os.path.basename(dest)}")
        else:
            skipped += 1
            print(f"  ・ 変更なし {label}")

    if not dry_run:
        print(f"\n複製 {copied}件 / 変更なし {skipped}件")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="過去の生成発注書を請求突合フォルダへ遡り投入")
    ap.add_argument("--since", default="20260601", help="この納品日以降を対象 (YYYYMMDD or YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="コピーせず対象一覧だけ表示")
    args = ap.parse_args()
    since = re.sub(r"\D", "", args.since) or None
    sys.exit(main(since, args.dry_run))
