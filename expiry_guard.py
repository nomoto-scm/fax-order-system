"""賞味期限ガード — 過去出荷との「期限逆転」と「残存日数不足」の検知

背景: 同一納品先に前回より古い賞味期限の商品を納めると入荷拒否になる
（2026-08 ナチュラルローソン向け2Water: ベルーナ出荷で前回ロットとの比較が
人の記憶頼りになり逆転→入荷拒否→物流費倍増）。ブラウザの記憶
（localStorage）は端末ローカルで比較にも使えないため、DBに保存済みの
確定スナップショット（fax_orders_scm.confirmation_history）を正として照合する。

照合スコープは 納品先コード → 納品先名 → 取引先（チェーン） → 全社 の順に
フォールバックし、最も狭いスコープで実績が見つかった時点で採用する。
出荷元倉庫や入力経路（手入力/メモリ自動適用）には依存しない。

このモジュールは純粋関数のみ（I/Oなし・単体テスト可能）。
"""
import re
import unicodedata
from datetime import date, datetime

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _nfkc(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def normalize_location(name):
    """納品先名の比較用正規化（NFKC・空白除去）。"""
    return re.sub(r"\s+", "", _nfkc(name))


def normalize_partner(name):
    """取引先（チェーン）の比較用正規化。

    「三菱食品株式会社 加食G」→「三菱食品」のように、法人格と部署サフィックスを
    落として会社単位に揃える（同一チェーンの別部署発注を同一スコープにするため）。
    """
    t = _nfkc(name)
    t = re.sub(r"株式会社|\(株\)|（株）|有限会社|\(有\)|（有）|合同会社", "", t)
    t = t.strip()
    return t.split(" ")[0] if " " in t else t


def parse_date(s):
    """'YYYY-MM-DD' → date。それ以外は None。"""
    if isinstance(s, date):
        return s
    s = _nfkc(s)
    if not _DATE_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def history_rows_from_orders(orders):
    """fax_orders_scm の行（confirmation_history 付き）を明細単位の実績行に平坦化する。

    最後の確定エントリ（最新スナップショット）のみを採用する。
    返却行: {order_id, confirmed_at, partner_name, delivery_location_code,
             delivery_location_name, warehouse, jan, product_name, expiry_date(date)}
    """
    rows = []
    for o in orders or []:
        hist = o.get("confirmation_history") or []
        if not isinstance(hist, list) or not hist:
            continue
        last = hist[-1] or {}
        snap = last.get("snapshot") or {}
        for p in snap.get("pages") or []:
            loc_code = p.get("delivery_location_code") or o.get("delivery_location_code") or ""
            loc_name = p.get("delivery_location_name") or o.get("delivery_location_name") or ""
            partner = p.get("partner_name") or o.get("partner_name") or ""
            for i in p.get("items") or []:
                exp = parse_date(i.get("expiry_date"))
                if exp is None:
                    continue
                rows.append({
                    "order_id": o.get("id"),
                    "confirmed_at": last.get("at") or o.get("confirmed_at"),
                    "partner_name": partner,
                    "delivery_location_code": str(loc_code or ""),
                    "delivery_location_name": loc_name,
                    "warehouse": o.get("warehouse") or "",
                    "jan": str(i.get("jan") or ""),
                    "product_name": i.get("master_name") or i.get("ocr_name") or "",
                    "expiry_date": exp,
                })
    return rows


def find_reference_expiry(rows, jan, delivery_location_code="", delivery_location_name="",
                          partner_name="", exclude_order_id=None):
    """同一JANの過去実績から、最も狭いスコープで見つかった最大賞味期限を返す。

    返却: {scope, max_expiry(date), order_id, confirmed_at, warehouse,
           delivery_location_name, count} または None（実績なし）
    """
    jan = str(jan or "")
    if not jan:
        return None
    same_jan = [r for r in rows if r["jan"] == jan
                and (exclude_order_id is None or r["order_id"] != exclude_order_id)]
    if not same_jan:
        return None

    loc_code = str(delivery_location_code or "")
    loc_name = normalize_location(delivery_location_name)
    partner = normalize_partner(partner_name)

    scopes = []
    if loc_code:
        scopes.append(("納品先コード", lambda r: r["delivery_location_code"] == loc_code))
    if loc_name:
        scopes.append(("納品先名", lambda r: normalize_location(r["delivery_location_name"]) == loc_name))
    if partner:
        scopes.append(("取引先", lambda r: normalize_partner(r["partner_name"]) == partner))
    scopes.append(("全社", lambda r: True))

    for scope_name, pred in scopes:
        hits = [r for r in same_jan if pred(r)]
        if not hits:
            continue
        best = max(hits, key=lambda r: r["expiry_date"])
        return {
            "scope": scope_name,
            "max_expiry": best["expiry_date"],
            "order_id": best["order_id"],
            "confirmed_at": best["confirmed_at"],
            "warehouse": best["warehouse"],
            "delivery_location_name": best["delivery_location_name"],
            "count": len(hits),
        }
    return None


def evaluate_item(expiry_date, reference=None, delivery_date=None, min_remaining_days=None):
    """1明細の賞味期限を評価し、アラートのリストを返す（空なら問題なし）。

    - reversal: 過去実績の最大賞味期限より古い（期限逆転）
    - min_remaining: 納品日時点の残存日数が shipping_expiry_rules の最低日数未満
    """
    alerts = []
    exp = parse_date(expiry_date)
    if exp is None:
        return alerts

    if reference and reference.get("max_expiry") and exp < reference["max_expiry"]:
        alerts.append({
            "type": "reversal",
            "scope": reference["scope"],
            "expiry_date": exp.isoformat(),
            "reference_expiry": reference["max_expiry"].isoformat(),
            "reference_order_id": reference.get("order_id"),
            "reference_confirmed_at": reference.get("confirmed_at"),
            "reference_warehouse": reference.get("warehouse"),
            "reference_location": reference.get("delivery_location_name"),
            "days_behind": (reference["max_expiry"] - exp).days,
        })

    dlv = parse_date(delivery_date)
    if dlv is not None and min_remaining_days is not None:
        try:
            min_days = int(min_remaining_days)
        except (TypeError, ValueError):
            min_days = None
        if min_days is not None:
            remaining = (exp - dlv).days
            if remaining < min_days:
                alerts.append({
                    "type": "min_remaining",
                    "expiry_date": exp.isoformat(),
                    "delivery_date": dlv.isoformat(),
                    "remaining_days": remaining,
                    "min_remaining_days": min_days,
                })
    return alerts
