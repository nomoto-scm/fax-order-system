"""発注書を請求管理の突合フォルダ（請求管理/input/発注書/）へ自動複製する。

背景:
  他社品（伊藤忠経由の他社商品＝商品コードが `7` 始まり）で
  「先方発注書はバラ（個）記載なのに NE登録はケース記載」という単位の食い違いにより、
  請求額が入数分だけ過少になる事故が2026年6月分で発生した。
  請求処理（billing skill / 請求管理/scripts/check_po_reconcile.py）が
  `請求管理/input/発注書/` を読んで突合ワークシートを作るため、
  FAX受注システム側から自動投入して「担当が入れ忘れる」経路を潰す。

投入するのは2種類:
  発注書_受領_*  先方から受領した発注書の原本。
                 **単位の食い違いを実際に検知できるのはこちら。**
  発注書_生成_*  弊社がパートナー（野村不動産／エムズインク／POW BAR／DAYLILY）へ出した発注書。
                 弊社が「何袋出荷指示したか」の証跡。ただし数量は NE CSV と同じ入力値
                 （process_fax._get_irisuu）から作られるため、これ単独では
                 入力時点の単位間違いを検知できない（先方発注書との突合が必要）。

対象の絞り込み:
  他社品（商品コード 7 始まり／パートナー直送品）を含む受注のみ複製する。
  全受注を入れると突合チェックリストが数百件になり使い物にならないため。
  シルビア／ハルナの発注書（＝自社品の出荷指示書）は複製しない。

環境差:
  Render 等 Gドライブが無い環境では黙ってスキップする（受注の確定処理は絶対に止めない）。
  投入先は環境変数 `PO_INBOX_DIR` で上書きできる。
"""
import hashlib
import os
import re
from datetime import date

# 請求管理の恒久ドロップ先（請求管理/scripts/billing_paths.py の PO_INBOX と同じ場所）
DEFAULT_PO_INBOX = r"G:\共有ドライブ\TWO\SCM\99_AI活用\請求管理\input\発注書"

# 経路A（eFax/PALTAC 自動取込）の受領原本の置き場所。web_app.DRIVE_BASE と同じ
DRIVE_BASE = r"G:\共有ドライブ\TWO\SCM\31_受発注確認\受注受信\_自動取込"

# 他社品の商品コード接頭辞（請求管理/scripts/check_po_reconcile.py と同一ルール）
OTHER_COMPANY_PREFIX = "7"

# 他社品パートナー（process_fax.PARTNER_PDF_GENERATORS の出力先と対応）
PARTNER_DESTS = ("野村不動産", "エムズインク", "POW BAR", "DAYLILY")

_UNSORTED = "未整理"
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def inbox_dir():
    """投入先フォルダを返す。存在しない環境では None（＝スキップ）。"""
    path = os.environ.get("PO_INBOX_DIR") or DEFAULT_PO_INBOX
    return path if os.path.isdir(path) else None


def has_other_company_item(results):
    """受注結果に他社品（商品コード7始まり／パートナー直送品）が含まれるか。"""
    for r in results or []:
        if "error" in r:
            continue
        for item in r.get("matched_items", []) or []:
            if not item.get("matched"):
                continue
            if str(item.get("code", "") or "").strip().startswith(OTHER_COMPANY_PREFIX):
                return True
            if (item.get("output_dest") or "").strip() in PARTNER_DESTS:
                return True
    return False


def _safe(s, limit=40):
    """ファイル名に使える文字列へ整える。"""
    s = _INVALID_CHARS.sub("", str(s or "")).strip().strip(".")
    return s.replace(" ", "").replace("　", "")[:limit]


def _ymd(delivery_date):
    """納品日を 'YYYYMMDD' に正規化する。判別できなければ None。

    check_po_reconcile.py は請求の transaction_date（＝納品日）基準で突合するため、
    ファイル名に埋める年月は受注日ではなく納品日を使う。
    billing_paths.find_po_files はファイル名に 'YYYYMM' が含まれれば当月分として拾う。
    """
    s = str(delivery_date or "").strip()
    if not s:
        return None
    parts = [p for p in re.split(r"[-/.年月日\s]+", s) if p]
    if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
        y, m, d = parts[0], parts[1], parts[2]
    elif len(parts) == 2 and all(p.isdigit() for p in parts[:2]):
        # 年なし（M/D）→ 当年とみなす
        y, m, d = str(date.today().year), parts[0], parts[1]
    elif len(parts) == 1 and len(parts[0]) == 8 and parts[0].isdigit():
        y, m, d = parts[0][:4], parts[0][4:6], parts[0][6:8]
    else:
        return None
    if len(y) != 4:
        return None
    m, d = m.zfill(2), d.zfill(2)
    if not (1 <= int(m) <= 12 and 1 <= int(d) <= 31):
        return None
    return f"{y}{m}{d}"


def _short_hash(*parts):
    """伝票番号が無い受注を安定して区別するための短縮ハッシュ。

    タイムスタンプではなく内容由来にすることで、同じ受注を再確定しても
    同じファイル名へ上書きされる（＝コピーが増殖しない）。
    """
    src = "|".join(str(p or "") for p in parts)
    return hashlib.md5(src.encode("utf-8")).hexdigest()[:6]


def _write(dest_path, data):
    """内容が変わっていなければ書かない（再確定のたびに更新日時が濁るのを防ぐ）。"""
    if os.path.exists(dest_path):
        try:
            with open(dest_path, "rb") as f:
                if f.read() == data:
                    return None
        except OSError:
            pass
    with open(dest_path, "wb") as f:
        f.write(data)
    return dest_path


def _place(filename, ymd, data):
    """投入先を決めて書き込む。納品日が不明なものは 未整理/ へ。"""
    base = inbox_dir()
    if not base:
        return None
    target_dir = base if ymd else os.path.join(base, _UNSORTED)
    os.makedirs(target_dir, exist_ok=True)
    return _write(os.path.join(target_dir, filename), data)


def archive_generated_po(pdf_path, partner_name, delivery_date="", slip_number="",
                         page=None, dest_name="", source_name=""):
    """弊社がパートナーへ出した発注書PDFを突合フォルダへ複製する。

    Args:
        pdf_path: 生成済みPDFのパス（受注OCR/output/ 配下）
        partner_name: 出力先（野村不動産／エムズインク／POW BAR／DAYLILY）
        delivery_date: 納品日（ファイル名の年月に使う）
        slip_number: 発注番号（受注1件を識別するキー）
        page: 元FAXのページ番号
        dest_name: 納品先名（伝票番号が無いときの識別に使う）
        source_name: 元ファイル名（同上）

    Returns:
        複製先パス。スキップ・変更なしの場合は None。
    """
    if not inbox_dir() or not os.path.exists(pdf_path):
        return None
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    ymd = _ymd(delivery_date)
    parts = ["発注書", "生成", _safe(partner_name, 20)]
    if ymd:
        parts.append(ymd)
    slip = _safe(slip_number, 24)
    parts.append(slip if slip else _short_hash(dest_name, source_name, page))
    if page:
        parts.append("p%s" % page)
    return _place("_".join(parts) + ".pdf", ymd, data)


def archive_received_po(results, source_name, src_bytes=None,
                        partner_name="", delivery_date="", slip_number=""):
    """先方から受領した発注書の原本を突合フォルダへ複製する。

    他社品を含む受注のみ対象。1つの原本に複数受注が入っている場合も
    ファイル名は元ファイル名基準なので1件しか置かれない（冪等）。

    Args:
        results: 受注結果（他社品判定に使う）
        source_name: 元ファイル名。経路AではDrive上の実ファイル名
        src_bytes: 経路B（手動アップロード）で保持している原本バイト列
        partner_name: 取引先名
        delivery_date: 納品日
        slip_number: 発注番号（元ファイル名が取れないときの識別用）

    Returns:
        複製先パス。スキップ・変更なしの場合は None。
    """
    if not inbox_dir() or not has_other_company_item(results):
        return None

    data = src_bytes or _read_from_drive(source_name)
    if not data:
        return None

    stem, ext = os.path.splitext(os.path.basename(source_name or "受領発注書.pdf"))
    ext = ext.lower() or ".pdf"
    ymd = _ymd(delivery_date)
    parts = ["発注書", "受領"]
    if ymd:
        parts.append(ymd)
    if partner_name:
        parts.append(_safe(partner_name, 20))
    parts.append(_safe(stem, 60) or _safe(slip_number, 24) or _short_hash(source_name))
    return _place("_".join(parts) + ext, ymd, data)


def _read_from_drive(source_name):
    """経路A（自動取込）の受領原本をDriveの取込フォルダから読む。

    確定時点では原本は「処理済み」へ移動済みだが、タイミングにより
    「新着」に残っていることもあるため両方見る。
    """
    if not source_name:
        return None
    name = os.path.basename(source_name)
    for folder in ("処理済み", "新着", "過去　処理不要"):
        path = os.path.join(DRIVE_BASE, folder, name)
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                continue
    return None
