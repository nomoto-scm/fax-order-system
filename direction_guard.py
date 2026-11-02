"""納品先の方面（上り/下り）ガード — OCR誤読による方面違い納品の防止

背景: ネクスコ系の納品先は同一店名で（上り）（下り）が対になっており、
それぞれ別の納品先コード・別の請求先を持つ。OCRが方面を誤読しても
名称類似度ではスコア差がほとんど出ず、誤った側が高スコアで自動確定して
誤納品・請求先違いにつながる（2026-06請求・2026-08受注で実発生）。

方針: 類似度の調整ではなく決定的ルールで止める。
- 名称に方面トークンを含むマッチは、マッチ経路（完全一致含む）によらず「要確認」
- 対になるもう一方（方面違い・同一基底名）を必ず候補として返す
- OCR原文の方面とマッチ結果の方面が食い違う場合は mismatch フラグを立てる

このモジュールは純粋関数のみ（pandas等の外部依存なし・単体テスト可能）。
"""
import re
import unicodedata

# 方面トークンの対。東西口・上下線など同種のペアが見つかればここに追加する。
DIRECTION_PAIRS = [("上り", "下り")]

_ALL_TOKENS = [t for pair in DIRECTION_PAIRS for t in pair]
# 「（上り）」「(上り)」を優先で拾い、括弧なしの「上り」も拾う（OCR原文対策）
_TOKEN_RE = re.compile(
    r"[（(]\s*(" + "|".join(_ALL_TOKENS) + r")\s*[）)]"
    r"|(" + "|".join(_ALL_TOKENS) + r")"
)


def _nfkc(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def extract_direction(name):
    """名称から方面トークン（'上り'/'下り'）を抽出する。無ければ None。"""
    m = _TOKEN_RE.search(_nfkc(name))
    if not m:
        return None
    return m.group(1) or m.group(2)


def opposite_direction(token):
    """対になる方面トークンを返す。対が定義されていなければ None。"""
    for a, b in DIRECTION_PAIRS:
        if token == a:
            return b
        if token == b:
            return a
    return None


def strip_direction(name):
    """方面トークン（括弧ごと）を除いた基底名を返す。比較用に空白も除去する。"""
    t = _TOKEN_RE.sub("", _nfkc(name))
    return re.sub(r"\s+", "", t)


def find_direction_pairs(matched_name, rows):
    """matched_name と基底名が同じで方面が異なる行を rows から返す。

    rows: [{"name": ...}, ...]。name キーのみ必須で、他のキーはそのまま返す。
    """
    my_dir = extract_direction(matched_name)
    if not my_dir:
        return []
    base = strip_direction(matched_name)
    out = []
    for r in rows:
        n = r.get("name", "")
        d = extract_direction(n)
        if d and d != my_dir and strip_direction(n) == base:
            out.append(r)
    return out


def build_direction_alert(ocr_dest_name, matched_name, rows):
    """方面アラート dict を返す。matched_name に方面トークンが無ければ None。

    返却値:
      matched_direction: マッチ結果の方面
      ocr_direction: OCR原文の方面（読み取れなければ None）
      mismatch: OCR原文とマッチ結果の方面が食い違うか
      pairs: 対になる納品先（rows のうち基底名一致・方面違いのもの）
    """
    matched_dir = extract_direction(matched_name)
    if not matched_dir:
        return None
    ocr_dir = extract_direction(ocr_dest_name)
    return {
        "matched_direction": matched_dir,
        "ocr_direction": ocr_dir,
        "mismatch": bool(ocr_dir and ocr_dir != matched_dir),
        "pairs": find_direction_pairs(matched_name, rows),
    }
