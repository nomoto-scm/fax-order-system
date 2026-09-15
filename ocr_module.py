"""OCR module - Claude Vision API for FAX PDF reading"""
import os
import sys
import json
import base64
import unicodedata

# Windows terminal UTF-8 output
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import time

import anthropic
import pandas as pd
from difflib import SequenceMatcher


def normalize(s):
    """NFKC normalization: convert full-width chars to half-width (e.g. ＤＤＣ→DDC)"""
    t = unicodedata.normalize('NFKC', str(s)).strip()
    # ノーブレークスペース(\xa0)等の特殊スペースを通常スペースに変換
    t = t.replace('\xa0', ' ').replace('\u3000', ' ')
    return t


def normalize_company(s):
    """会社名の表記ゆれを統一してからNFKC正規化する。
    「株式会社」「(株)」「㈱」→ 空文字に統一し、スペースも正規化。
    DDCマッチ時のスコア比較専用（表示には使わない）。
    """
    import re
    t = normalize(s)
    # 株式会社の表記ゆれをスペースに変換（中間にある場合のセパレータ役割を保持）
    # 空文字にすると「伊藤忠食品(株)三郷物流センター」→「伊藤忠食品三郷物流センター」になり分割不能
    t = re.sub(r'株式会社|（株）|\(株\)|㈱', ' ', t)
    # 有限会社
    t = re.sub(r'有限会社|（有）|\(有\)|㈲', ' ', t)
    # 角括弧内のメモを除去 例: ＜TWO＞ → '' / <TWO> → ''
    t = re.sub(r'[<＜][^>＞]*[>＞]', '', t)
    # 末尾の「宛」「様」「御中」「行」を除去
    t = re.sub(r'[宛様行]$', '', t)
    t = re.sub(r'御中$', '', t)
    # 連続スペースを1つに、前後の空白除去
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def get_api_key():
    """Get Anthropic API key from environment variable"""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_key")
        if os.path.exists(key_file):
            with open(key_file, "r") as f:
                key = f.read().strip()
    return key


def _auto_rotate_image(img):
    """FAX画像の向きを自動検出して正立方向に回転する。

    FAXのPDFには横向き（ランドスケープ）で格納されているページがある。
    縦書き方向の分散比率 (h_cv/v_cv) で横向きを検出し、
    上部の暗ピクセル密度でCW/CCWどちらに回転すべきかを判定する。

    検出は低解像度サムネイルで行い、実際の回転はオリジナル解像度で適用する。

    Args:
        img: PIL.Image オブジェクト

    Returns:
        回転補正後の PIL.Image オブジェクト
    """
    import numpy as np

    # 検出用に縮小（長辺600px相当）— 精度は十分、速度を優先
    thumb_size = 600
    ratio = thumb_size / max(img.width, img.height)
    thumb = img.resize(
        (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
        resample=0  # NEAREST: 最速
    )

    arr = np.array(thumb.convert('L'))
    dark = (arr < 128).astype(np.float32)
    hproj = dark.sum(axis=1)
    vproj = dark.sum(axis=0)
    h_cv = float(np.std(hproj)) / (float(np.mean(hproj)) + 1e-6)
    v_cv = float(np.std(vproj)) / (float(np.mean(vproj)) + 1e-6)
    h_score = h_cv / (v_cv + 1e-6)

    # h_score < 0.8 → テキスト行が縦方向 → 横向きページ
    if h_score >= 0.8:
        return img  # 正立方向、回転不要

    # CCW (+90°) と CW (-90°) どちらが正しいか：
    # FAX文書は上部にヘッダがあるため、正立方向では上部の暗ピクセルが多い
    def top_bottom_ratio(rotated_thumb):
        a = np.array(rotated_thumb.convert('L'))
        h = a.shape[0]
        top = float((a[:h // 4] < 128).sum())
        bot = float((a[3 * h // 4:] < 128).sum())
        return top / (bot + 1e-6)

    thumb_ccw = thumb.rotate(90, expand=True)
    thumb_cw = thumb.rotate(-90, expand=True)
    if top_bottom_ratio(thumb_ccw) >= top_bottom_ratio(thumb_cw):
        return img.rotate(90, expand=True)
    return img.rotate(-90, expand=True)


def pdf_to_images(pdf_bytes, dpi=150):
    """Convert PDF to list of per-page base64 JPEG images (with auto-rotation).

    メモリ最適化:
    - DPI=150（OCR精度を維持しつつメモリ削減、DPI=200比で56%）
    - JPEG圧縮（PNG比で60-70%サイズ削減）
    - 各ページ処理後にメモリ解放
    """
    import fitz  # PyMuPDF
    from PIL import Image
    import io
    import gc
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        # pixmapを即座に解放
        del pix
        img = _auto_rotate_image(img)
        buf = io.BytesIO()
        # JPEG圧縮でサイズ削減（品質85: OCR精度に影響なし）
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        images.append({"page": page_num + 1, "base64": b64})
        # メモリ解放
        del img, buf
        gc.collect()
    doc.close()
    return images


OCR_PROMPT = """このFAX発注書の画像を正確に読み取り、以下のJSON形式で情報を抽出してください。

{
  "order_no": "オーダーNO/発注NO",
  "delivery_date": "納品日/入荷日 (YYYY-MM-DD)",
  "sender": "発注者/発注元の会社名",
  "sender_contact": "担当者名",
  "sender_fax": "発注元FAX番号",
  "delivery_dest": "納品先/入荷場所の名称",
  "delivery_address": "納品先住所",
  "delivery_tel": "納品先電話番号",
  "delivery_fax": "納品先FAX番号",
  "items": [
    {
      "product_name": "商品名",
      "jan_code": "JANコード（13桁）",
      "product_code": "商品コード",
      "quantity_cs": "ケース数（CS単位の数値のみ）",
      "unit_price": "単価（数値、あれば）",
      "amount": "金額（数値、あれば）",
      "delivery_dest": "この明細行の納品先（表に納品先列がある場合のみ。無ければ空文字）"
    }
  ],
  "notes": "備考・特記事項"
}

【重要】この発注書で扱う主な商品は以下のとおりです：
1. ２Snack香り華やぐ和紅茶サブレ（JAN: 4589570801393）入数12袋/cs
2. ２Snack香るトリュフ（JAN: 4589570801416）入数12袋/cs
3. ２Snack焦がしガーリック（JAN: 4589570801430）入数12袋/cs
4. ２Snack濃厚ガトーショコラ風サブレ（JAN: 4589570801454）入数12袋/cs
5. 2Water Ceramide 500ml（JAN: 4589570801485）入数24本/cs
6. 2Water Ceramide 2L（JAN: 4589570801683）入数6本/cs
7. 2Water Sparkling 500ml（JAN: 4589570801607）入数24本/cs
8. 2Gummy LIPOSOME VC（JAN: 4589570801331）入数48袋/cs
9. 2Gummy リポ Fe(鉄)（JAN: 4589570801744）入数48袋/cs
10. 2Gummy リポソーム VD（JAN: 4589570801768）入数48袋/cs
11. 2Energy 250ml（JAN: 4589570801348）入数30本/cs
12. 2Energy 250ml 26RN（JAN: 4589570801621）入数30本/cs

読み取りルール：
- 商品名が読みにくい場合、JANコード（4589570801で始まる13桁）から上記リストで特定してください。
- quantity_csは必ず「ケース数(CS)」です。入数（12袋、24本等）や数量バラ（個数）ではありません。
  例：「12袋×3cs」→ quantity_cs は 3。「24本/cs × 10」→ quantity_cs は 10。
  数量(CS)列、発注数量(CS)列、函数列の値を読み取ってください。
- 「函数」「函」もケース数(CS)と同じ意味です。例：「8函」→ quantity_cs は 8。
- 手書き修正がある場合（取り消し線で数字が消されている場合）は、最終的な記載数字を使用してください。
- itemsの並び順は、FAX原本の表に記載されている上から下の順番を維持してください。
- delivery_dateは納品日（入荷日）です。製造日・賞味期限の日付と混同しないこと。
  年が不明な場合は2026年としてください。
- 数量が0または空欄の商品行はitemsに含めないでください。

【読み取り注意】
- 「函数」「函」はケース数(CS)と同じ意味です。「入数」（12入など）は1CS内の袋/本数なので混同しないこと。
- 商品名に「TWO」ブランドが先頭に付く場合（例：TWO 2Snack 焦がしガーリック）、上記商品リストで照合してください。
- FAX画質の劣化により「昭」「鷺」「鷹」は字形が似ているため慎重に判断してください。
- 「埼京」と「埼玉」は1文字違いです（右側の字が「京」か「玉」か正確に判断）。
- 「DDC」「SDC」「RDC」等のアルファベットコードは正確に読み取ってください。

【発注書フォーマットへの対応】ページ上部に「発注書」というタイトルがある場合：
- 「納入先」欄は右側に配置されているので、その会社名＋施設名を delivery_dest に読み取ること。
- 左上の「○○商事（株）御中」はFAXの宛先（受信者）なので delivery_dest には使わないこと。
- 「函数」列の数字が quantity_cs です（「入数」列の12入等と混同しないこと）。

【明細表に「納品先」列があるフォーマット（1行＝1納品先）】
- 表のヘッダに「納品先」「納品先（SEJ）」「届け先」「センター」等の列があり、
  行ごとに異なる納品先と数量が並んでいるフォーマットがあります。
  （例：伊藤忠食品のSEJ向け発注書。1枚に1商品、行ごとに納品先＋ケース数が16行並ぶ）
- この場合は **表の全行を1行も省略せず** items に出力し、各itemの delivery_dest に
  その行の納品先名をそのまま入れてください。行数が20行を超えても必ず全行出力すること。
- 同じ商品名・同じ商品コードが全行に繰り返されていても、納品先が違えば別の行として出力すること
  （重複とみなして省略しないこと）。
- このフォーマットではトップレベルの delivery_dest には1行目の納品先を入れてください。

【入荷場所が複数記載されている場合（上記の「納品先」列が無いフォーマット）】
- 明細表に納品先列が無く、伝票のヘッダ等に入荷場所・納品先が複数箇所に記載されている
  フォーマットがあります。
- その場合は **一番上（最初）に記載されている入荷場所** を delivery_dest として読み取ってください。
- 下段の入荷場所コードやライフ番号等は無視してください。

- JSONのみを返してください。説明文やマークダウンは不要です。"""


def expand_multi_dest_ocr(ocr_result):
    """明細行ごとに納品先が指定された発注書を、納品先単位のOCR結果に分割する。

    伊藤忠食品のSEJ向け発注書のように「1枚＝1商品／行ごとに納品先＋ケース数」という
    フォーマットでは、1ページから複数の納品先ぶんの受注が起きる。
    従来はページ単位で delivery_dest が1つしか持てず、先頭行の納品先しか取り込めなかった。

    Args:
        ocr_result: ocr_fax_page() が返したdict

    Returns:
        [(dest_seq, ocr_dict), ...]
        dest_seq は表の上からの1始まり連番（発注番号の枝番に使う）。
        分割が不要なフォーマットでは [(0, ocr_result)] を返す（従来動作）。
    """
    items = ocr_result.get("items") or []
    if not items:
        return [(0, ocr_result)]

    header_dest = normalize(ocr_result.get("delivery_dest") or "")

    groups = []       # [[key, dest_name, [item, ...]], ...] 表の出現順を保持
    index = {}        # key -> groups内のインデックス
    last_dest = ""
    distinct_row_dests = set()

    for it in items:
        row_dest = normalize(it.get("delivery_dest") or "")
        if row_dest:
            distinct_row_dests.add(normalize_company(row_dest))
            last_dest = row_dest
        # 納品先が空の行は直前行の続き（セル結合）とみなし、先頭行ならヘッダの納品先に寄せる
        dest = row_dest or last_dest or header_dest
        key = normalize_company(dest)
        if key not in index:
            index[key] = len(groups)
            groups.append([key, dest, []])
        groups[index[key]][2].append(it)

    # 行ごとの納品先が1種類以下なら従来どおりページ単位で扱う
    if len(distinct_row_dests) <= 1 or len(groups) <= 1:
        return [(0, ocr_result)]

    # ページ全体のチェーン（取引先）を多数決で決める。
    # 「福岡ＳＥＪ倉」等でSEJが読めた行があれば、略号が落ちた行にも同じ取引先を適用する。
    page_chain = ""
    for _key, dest, _items in groups:
        hint = _chain_hint(dest)
        if hint:
            page_chain = hint
            break

    base_no = str(ocr_result.get("order_no") or "").strip()
    expanded = []
    for seq, (_key, dest, group_items) in enumerate(groups, start=1):
        sub = dict(ocr_result)
        sub["delivery_dest"] = dest
        sub["chain_hint"] = page_chain
        sub["items"] = group_items
        # 発注番号は納品先ごとに弊社で付番する: FAX原本の発注No + 行順の枝番
        sub["order_no"] = f"{base_no}-{seq:02d}" if base_no else ""
        sub["source_order_no"] = base_no
        sub["dest_seq"] = seq
        expanded.append((seq, sub))
    return expanded


def _dest_merge_key(page_result):
    """納品先統合用のキー。納品先マスタに当たっていればコード、未マッチならOCR名で寄せる。"""
    ddc = page_result.get("ddc_match") or {}
    if ddc.get("matched") and ddc.get("nohinsaki_code"):
        dest_key = ("code", str(ddc["nohinsaki_code"]))
    elif ddc.get("matched") and ddc.get("name"):
        dest_key = ("name", normalize_company(ddc["name"]))
    else:
        ocr = page_result.get("ocr_raw") or {}
        dest_key = ("ocr", normalize_company(ocr.get("delivery_dest") or ""))
    delivery_date = str((page_result.get("ocr_raw") or {}).get("delivery_date") or "")
    return (dest_key, delivery_date)


def merge_multi_dest_results(results):
    """「1商品1枚」形式で複数ページに分かれた発注書を納品先単位に統合する。

    例: 1枚目=リポソームVC×16納品先 / 2枚目=リポソームVD×16納品先 の発注書は、
    統合後は「納品先16件、各2明細」になる。実配送が納品先あたり1回なので、
    送料計算・出荷指示が二重計上されるのを防ぐ。

    統合対象は multi_dest フラグが立った結果のみ（従来フォーマットの挙動は変えない）。
    納品先マスタに当たらなかった行は統合せずそのまま残し、画面で修正できるようにする。
    """
    merged = []
    index = {}    # merge key -> merged内のインデックス

    for r in results:
        if "error" in r or not r.get("multi_dest"):
            merged.append(r)
            continue
        ddc = r.get("ddc_match") or {}
        if not ddc.get("matched"):
            # 納品先未確定のまま統合すると誤マージになるため単独で残す
            merged.append(r)
            continue

        key = _dest_merge_key(r)
        if key not in index:
            index[key] = len(merged)
            r["source_order_nos"] = [n for n in [
                (r.get("ocr_raw") or {}).get("source_order_no"),
            ] if n]
            merged.append(r)
            continue

        target = merged[index[key]]
        target["matched_items"] = list(target.get("matched_items", [])) + list(r.get("matched_items", []))
        src_no = (r.get("ocr_raw") or {}).get("source_order_no")
        if src_no and src_no not in target.get("source_order_nos", []):
            target.setdefault("source_order_nos", []).append(src_no)
        # 統合元ページを追跡できるようにする（画像プレビュー・監査用）
        target.setdefault("merged_image_pages", [target.get("image_page")])
        if r.get("image_page") not in target["merged_image_pages"]:
            target["merged_image_pages"].append(r.get("image_page"))

    # 出力先別リストを統合後の明細で組み直し、ページ番号を通し番号に振り直す
    for i, r in enumerate(merged, start=1):
        r["page"] = i
        if "error" in r:
            continue
        items = r.get("matched_items", [])
        r["sylvia_items"] = [x for x in items if x.get("matched") and x.get("output_dest") == "シルビア"]
        r["haruna_items"] = [x for x in items if x.get("matched") and x.get("output_dest") == "ハルナ"]
        r["warehouse_items"] = [x for x in items if x.get("matched") and x.get("output_dest") == "自社倉庫"]
    return merged


def ocr_fax_page(image_b64, model="claude-sonnet-4-6"):
    """Read one FAX page image with Claude Vision API and return parsed JSON"""
    # Windows環境でのエンコーディング問題を回避
    import io as _io
    if sys.stdout and hasattr(sys.stdout, 'encoding') and (sys.stdout.encoding or '').lower() in ('ascii', 'cp932', 'shift_jis', 'mbcs'):
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    if sys.stderr and hasattr(sys.stderr, 'encoding') and (sys.stderr.encoding or '').lower() in ('ascii', 'cp932', 'shift_jis', 'mbcs'):
        sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

    api_key = get_api_key()
    if not api_key:
        return {"error": "API key not set. Set ANTHROPIC_API_KEY environment variable."}

    client = anthropic.Anthropic(api_key=api_key)

    # 529 (Overloaded) / 503 / RateLimit を指数バックオフでリトライ
    # 1s → 2s → 4s → 8s → 16s (最大5回試行)
    max_attempts = 5
    backoff_seconds = [1, 2, 4, 8, 16]
    last_error = None

    for attempt in range(max_attempts):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=16000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64
                            }
                        },
                        {"type": "text", "text": OCR_PROMPT}
                    ]
                }]
            )

            text = response.content[0].text.strip()
            # Extract JSON from response
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"error": "JSON parse failed", "raw": text}

        except UnicodeEncodeError as e:
            return {"error": "Encoding error: " + repr(e)}
        except Exception as e:
            last_error = e
            status_code = getattr(e, "status_code", None)
            is_retryable = (
                status_code in (429, 503, 529)
                or isinstance(e, getattr(anthropic, "APIConnectionError", tuple()))
            )
            if is_retryable and attempt < max_attempts - 1:
                wait = backoff_seconds[attempt]
                print(f"  [OCR retry] {type(e).__name__} status={status_code}, "
                      f"{wait}秒待機して再試行 ({attempt + 1}/{max_attempts - 1})")
                time.sleep(wait)
                continue
            break

    try:
        msg = repr(last_error)
    except Exception:
        msg = type(last_error).__name__ if last_error else "unknown"
    return {"error": "OCR error: " + msg}


def load_product_master():
    """Load product master (Supabase優先、CSVフォールバック)"""
    from supabase_client import load_product_master as _load_from_supabase
    return _load_from_supabase()


def load_ddc_master():
    """Load DDC master (Supabase torihikisaki_master優先、CSVフォールバック)"""
    from supabase_client import load_torihikisaki_master_from_supabase
    df = load_torihikisaki_master_from_supabase()
    if df is not None and len(df) > 0:
        return df
    # フォールバック: ローカルCSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ddc_master.csv")
    print(f"  [CSV] 納品先マスタ読み込み: {csv_path}")
    return pd.read_csv(csv_path)


def load_staff():
    """Load staff JSON"""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "staff.json")
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_product(ocr_item, product_master):
    """Match a single OCR item to product master.
    Returns dict with matched product data or None."""
    jan = str(ocr_item.get("jan_code", "")).strip()
    product_code = str(ocr_item.get("product_code", "")).strip()
    ocr_name = normalize(ocr_item.get("product_name", ""))

    # 0. Product code exact match (自社管理商品コード)
    if product_code:
        m = product_master[product_master["商品コード"] == product_code]
        if len(m) > 0:
            return _product_row_to_dict(m.iloc[0], ocr_item)

    # 1. JAN code exact match
    if jan and len(jan) == 13:
        m = product_master[product_master["JANコード"] == jan]
        if len(m) > 0:
            result = _product_row_to_dict(m.iloc[0], ocr_item)
            # 2Gummy（JAN:4589570801331）→ アテンションシールありを優先
            if jan == "4589570801331":
                alt = product_master[product_master["商品コード"] == "1005038A0440"]
                if len(alt) > 0:
                    result = _product_row_to_dict(alt.iloc[0], ocr_item)
                    result["jan"] = jan  # JANは元のまま保持
            # 2Energy 新品（JAN:4589570801621）→ 1005038A0314 (2Energy_2607) を明示優先
            # 2026-07-01 以降、Energy と表記されている発注書でこの新JAN が載っていれば
            # 旧品ではなく新品にヒットさせる。JAN一意なので通常はここに落ちるはずだが
            # 過去に旧品として登録された行が残っている環境でも確実に切り替わるよう明示。
            if jan == "4589570801621":
                alt = product_master[product_master["商品コード"] == "1005038A0314"]
                if len(alt) > 0:
                    result = _product_row_to_dict(alt.iloc[0], ocr_item)
                    result["jan"] = jan
            return result

    # 2. Product name exact match (normalized)
    if ocr_name:
        norm_pm = product_master["商品名"].apply(normalize)
        exact_idx = norm_pm[norm_pm == ocr_name].index
        if len(exact_idx) > 0:
            return _product_row_to_dict(product_master.loc[exact_idx[0]], ocr_item)

    # 3. Fuzzy match on product name (both sides normalized)
    if ocr_name:
        best_ratio = 0
        best_row = None
        for _, row in product_master.iterrows():
            master_name = normalize(row["商品名"])
            score = 0.0
            if ocr_name in master_name or master_name in ocr_name:
                score = 0.85
            seq_ratio = SequenceMatcher(None, ocr_name, master_name).ratio()
            final_score = max(score, seq_ratio)
            if final_score > best_ratio and final_score >= 0.5:
                best_ratio = final_score
                best_row = row
        if best_row is not None:
            return _product_row_to_dict(best_row, ocr_item)

    return None


def _product_row_to_dict(row, ocr_item):
    """Convert a product master row to a standardized dict"""
    try:
        qty = int(float(str(ocr_item.get("quantity_cs", 0)).strip() or "0"))
    except (ValueError, TypeError):
        qty = 0
    cs_price = float(row.get("CS単価", 0) or 0)
    case_qty = int(row.get("入数", 0) or 0)
    return {
        "matched": True,
        "ocr_name": str(ocr_item.get("product_name", "")),
        "master_name": str(row["商品名"]),
        "jan": str(row["JANコード"]) if pd.notna(row.get("JANコード")) else "",
        "code": str(row.get("商品コード", "")),
        "spec": str(row.get("規格", "")),
        "pack": str(row.get("配送荷姿", "")),
        "unit_price": float(row.get("1袋単価", 0) or 0),
        "cs_price": cs_price,
        "case_quantity": case_qty,
        "output_dest": str(row.get("出力先", "")),
        "quantity": qty,
        "amount": qty * cs_price,
    }


def _normalize_dc(s):
    """DDC/SDC/RDC等を統一（スコアリング専用）: [A-Z]DC → _DC
    OCRが DDC を SDC と誤読するケースを吸収する。"""
    import re
    return re.sub(r'[A-Z]DC', '_DC', s)


def _parts_score(ocr, master):
    """OCR と master の部分文字列マッチスコアを算出（パーツ比較ヘルパー）"""
    best = 0.0
    # OCR側がスペース区切り
    if ' ' in ocr:
        for part in [p for p in ocr.split(' ') if len(p) >= 2]:
            r = SequenceMatcher(None, part, master).ratio()
            s = r * 0.85
            if len(part) >= 4 and (part in master or master in part):
                s = max(s, 0.70)
            best = max(best, s)
    # マスタ側がスペース区切り
    if ' ' in master:
        for mpart in [p for p in master.split(' ') if len(p) >= 2]:
            r = SequenceMatcher(None, ocr, mpart).ratio()
            s = r * 0.85
            if len(mpart) >= 4 and (ocr in mpart or mpart in ocr):
                s = max(s, 0.70)
            best = max(best, s)
    return best


# 物流センター名から「地名」だけを抜き出すためのノイズ語
# 例: 「大正SEJ倉＜常温＞」→「大正」 / 「常温大阪大正センター(伊藤忠商事（株）)」→「大阪大正」
_CENTER_NOISE_RE = None


def _center_place_key(s):
    """物流センター名から地名部分を抽出する（センター名表記ゆれの吸収用）。

    発注書側とマスタ側で同じセンターの呼び方が大きく違うケースがある。
    例) 伊藤忠食品のSEJ向け発注書:
        FAX  「大正ＳＥＪ倉＜常温＞」「常温一括大津物流センター」
        マスタ「常温大阪大正センター(伊藤忠商事（株）)」「常温大津センター(伊藤忠商事（株）)」
    温度帯・施設種別・運営会社の括弧書きを落とすと地名だけが残り、突合できる。

    地名が取れない場合は空文字を返す（＝この判定は使わない）。
    """
    global _CENTER_NOISE_RE
    import re
    if _CENTER_NOISE_RE is None:
        _CENTER_NOISE_RE = re.compile(
            r'[<＜〈《\[【][^>＞〉》\]】]*[>＞〉》\]】]'   # ＜常温＞ 〈常温〉 等の括弧書き
            r'|[（(][^）)]*[）)]'                        # (伊藤忠商事（株）) 等の運営会社
            r'|常温一括|一括|常温|冷蔵|冷凍|チルド|定温'      # 温度帯・一括区分
            r'|ＳＥＪ|SEJ'                                # チェーン略号
            r'|物流センター|センター|デポ|倉庫|倉|ＤＣ|DC|Ｃ'  # 施設種別
        )
    t = normalize_company(s)
    t = _CENTER_NOISE_RE.sub('', t)
    return re.sub(r'[\s　・]+', '', t).strip()


# 発注書の納品先名に現れるチェーン略号 → 納品先マスタ「取引先名」に含まれる語
_CHAIN_HINTS = [
    ("SEJ", "セブンイレブン"),
    ("セブンイレブン", "セブンイレブン"),
    ("セブン－イレブン", "セブンイレブン"),
]


def _chain_hint(dest_name):
    """納品先名からチェーン（取引先）を推定する。不明なら空文字。

    「広島ＳＥＪ倉」のように地名＋チェーン略号で書かれる発注書があり、
    地名だけで突合すると他チェーンの同名センター（イオン広島物流センター等）に
    誤ヒットする。略号が読み取れた場合は取引先を絞り込むために使う。
    """
    t = normalize(dest_name).upper()
    for token, chain in _CHAIN_HINTS:
        if token.upper() in t:
            return chain
    return ""


def _center_place_score(dest_company, master_company):
    """地名ベースでセンター名を突合したスコア。判定不能なら 0.0。

    新しいヒューリスティックなので上限を 0.90 に抑え、必ず「要確認」扱いにする
    （match_ddc は 0.92 未満を low_confidence とする）。誤確定より目視1回を優先。
    """
    # 施設名らしさが無い文字列には適用しない（社名同士の比較に効かせない）
    import re
    if not re.search(r'センター|倉|デポ|ＤＣ|DC', dest_company + master_company):
        return 0.0
    a = _center_place_key(dest_company)
    b = _center_place_key(master_company)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    if a == b:
        return 0.90
    # 「大正」⊂「大阪大正」のように、片方が府県名等を余分に持つケース
    if a in b or b in a:
        return 0.88
    return 0.0


def _score_ddc(dest_name_clean, dest_company, master_name, master_company):
    """DDCマッチスコアを算出する（内部関数）"""
    # 通常の正規化でスコア算出
    score = 0.0
    if dest_name_clean in master_name or master_name in dest_name_clean:
        score = 0.85
    seq_ratio = SequenceMatcher(None, dest_name_clean, master_name).ratio()

    # 会社名正規化版でもスコア算出（株式会社等の表記ゆれを吸収）
    score_company = 0.0
    if dest_company in master_company or master_company in dest_company:
        score_company = 0.85
    seq_ratio_company = SequenceMatcher(None, dest_company, master_company).ratio()

    # マスタ名が長い場合がOCRの長い文字列に含まれているかチェック
    if len(master_company) >= 5 and master_company in dest_company:
        score_company = max(score_company, 0.90)

    best = max(score, seq_ratio, score_company, seq_ratio_company)

    # パーツマッチング（スペース区切りの各部分を比較）
    best = max(best, _parts_score(dest_company, master_company))

    # DDC/SDC/RDC 1文字誤読吸収: [A-Z]DC を _DC に統一してパーツ比較
    # 例: OCR「埼京SDC」→「埼京_DC」 vs マスタ「ローソン埼京DDC」→「ローソン埼京_DC」
    dest_dc = _normalize_dc(dest_company)
    master_dc = _normalize_dc(master_company)
    if dest_dc != dest_company or master_dc != master_company:
        best = max(best, _parts_score(dest_dc, master_dc))

    return best


def match_ddc(dest_name, ddc_master, max_candidates=3, sender="", chain=""):
    """Match delivery destination name to DDC master.

    Args:
        dest_name: OCRで読み取った納品先名
        ddc_master: 納品先マスタ DataFrame
        max_candidates: 返す候補数
        sender: 発注元会社名（OCRから取得）。マスタ名の先頭と一致すればスコアをブースト。
        chain: チェーン（取引先）ヒント。発注書全体で判明している場合に渡す。
            例: 納品先列が「納品先（SEJ）」の発注書 → "セブンイレブン"。
            個々の行から略号が読み取れなくても取引先を絞り込める。

    Returns dict with:
        - matched: True/False
        - name, address, tel, etc. (1位の候補データ)
        - match_score: 1位のスコア
        - low_confidence: スコア0.92未満ならTrue（0.90は要確認ゾーン）
        - candidates: 上位N件のリスト [{name, score, address}, ...]
    """
    dest_name_clean = normalize(dest_name)
    if not dest_name_clean:
        return {"matched": False, "name": dest_name, "candidates": []}

    dest_company = normalize_company(dest_name)
    # 納品先名から読み取れるチェーン（取引先）ヒント。例: 「広島ＳＥＪ倉」→ セブンイレブン
    chain_hint = _chain_hint(dest_name) or normalize(chain or "")
    # 発注元の正規化（"三菱食品株式会社 加食G" → "三菱食品 加食G" → 先頭4文字以上で判定）
    sender_norm = normalize_company(sender) if sender else ""
    # 卸先コードプレフィックス（"三菱食品 加食G" の "三菱食品" 部分だけ取る）
    sender_key = sender_norm.split(' ')[0] if ' ' in sender_norm else sender_norm

    # 1. Exact match (normalized)
    norm_master = ddc_master["納品先名"].apply(normalize)
    exact_idx = norm_master[norm_master == dest_name_clean].index
    if len(exact_idx) > 0:
        result = _ddc_row_to_dict(ddc_master.loc[exact_idx[0]])
        result["match_score"] = 1.0
        result["low_confidence"] = False
        result["candidates"] = [{"name": result["name"], "score": 1.0}]
        return result

    # 1b. Exact match (会社名正規化版)
    comp_master = ddc_master["納品先名"].apply(normalize_company)
    exact_comp_idx = comp_master[comp_master == dest_company].index
    if len(exact_comp_idx) > 0:
        result = _ddc_row_to_dict(ddc_master.loc[exact_comp_idx[0]])
        result["match_score"] = 1.0
        result["low_confidence"] = False
        result["candidates"] = [{"name": result["name"], "score": 1.0}]
        return result

    # 2. Fuzzy scoring → 上位N件を候補として返す
    DDC_FUZZY_THRESHOLD = 0.35  # 候補収集は広めに（採用判定は0.7以上）
    scored = []
    scored_facility = {}  # row index → facility_ok (True/False/None)
    sender_matched = []  # 発注元プレフィックスが一致するマスタ（base scoreに関わらず収集）

    # マスタ名から「[コード] / 住所...」サフィックスを剥がす（スコアリング用）
    import re as _re_strip
    def _strip_master_suffix(s):
        t = _re_strip.sub(r'\s*\[[^\]]*\]\s*/\s*.*$', '', s)
        return _re_strip.sub(r'\s*\[[^\]]*\]\s*$', '', t).strip()

    for idx, row in ddc_master.iterrows():
        raw_master = row["納品先名"]
        master_name = normalize(_strip_master_suffix(str(raw_master)))
        master_company = normalize_company(_strip_master_suffix(str(raw_master)))
        base_score = _score_ddc(dest_name_clean, dest_company, master_name, master_company)

        # センター名の呼び方が発注書とマスタで大きく違う場合、地名で突合する。
        # ただしチェーン略号（SEJ等）が読み取れたときだけに限定する。
        # 地名だけで寄せると「広島ＳＥＪ倉」がイオンの広島物流センターに当たる等の
        # 他チェーン誤ヒットが起きるため、取引先が一致する行にしか効かせない。
        if chain_hint and chain_hint in normalize(str(row.get("取引先名", "")) or ""):
            base_score = max(base_score, _center_place_score(dest_company, master_company))

        is_sender_match = (
            sender_key and len(sender_key) >= 3
            and master_company.startswith(sender_key)
        )

        # 施設名一致チェック: 「会社名 施設名」または「会社名(施設名)」形式の場合
        # 会社名が一致していても施設名が違うマスタには高スコアを付けない
        facility_ok = None  # None=判定不可, True=施設名一致, False=施設名不一致
        import re as _re

        def _split_facility(s):
            """「会社名 施設名」または「会社名(施設名)」を (head, tail) に分割。不可なら (s, None)。"""
            if ' ' in s:
                h = s.split(' ')[0]
                t = s[len(h) + 1:]
                if len(t) >= 2:
                    return h, t
            m = _re.match(r'^(.+?)\((.+)\)$', s)
            if m and len(m.group(1)) >= 2 and len(m.group(2)) >= 2:
                return m.group(1).strip(), m.group(2).strip()
            return s, None

        ocr_head, ocr_tail = _split_facility(dest_company)
        mst_head, mst_tail = _split_facility(master_company)
        if ocr_tail is not None and mst_tail is not None:
            # 両方「会社名 施設名」形式: 会社名一致なら施設名を比較
            head_r = SequenceMatcher(None, ocr_head, mst_head).ratio()
            tail_r = SequenceMatcher(None, ocr_tail, mst_tail).ratio()
            if head_r >= 0.8:
                facility_ok = tail_r >= 0.85
        elif ocr_tail is None and mst_tail is not None and is_sender_match:
            # OCRが施設名のみ（「静岡物流センター」）、マスタが「会社名 施設名」の場合
            # sender boost が効くケースでのみ、OCR全体とマスタ施設部分を比較
            tail_r = SequenceMatcher(None, dest_company, mst_tail).ratio()
            if tail_r < 0.85:
                facility_ok = False

        # 発注元ブースト: マスタ名の先頭が発注元社名と一致する場合にスコアを加算
        # 例: sender="三菱食品", master="三菱食品 ローソン埼京DDC" → +0.25
        final_score = base_score
        if is_sender_match and base_score >= 0.15:
            final_score = min(base_score + 0.25, 0.95)

        # 施設名が不一致の場合: ブーストしても要確認止まりに制限
        # 例: OCR=「船橋物流センター」, マスタ=「昭島センター」→ 0.88以下に制限
        if facility_ok is False:
            final_score = min(final_score, 0.88)

        if final_score >= DDC_FUZZY_THRESHOLD:
            scored.append((final_score, row))
            scored_facility[id(row)] = facility_ok

        # OCR誤読対策: 発注元マッチのマスタはbaseスコアが低くても別収集しておく
        if is_sender_match:
            sender_matched.append((min(base_score + 0.25, 0.95), row))

    # スコア降順でソート
    scored.sort(key=lambda x: x[0], reverse=True)

    # 発注元マッチ強制候補: OCRが完全に誤読した場合でも候補に出す
    # 例: 昭島→鷹殿 のような誤読でも「伊藤忠食品（株）昭島センター」が候補に残る
    if sender_matched:
        sender_matched.sort(key=lambda x: x[0], reverse=True)
        best_sender_score, best_sender_row = sender_matched[0]
        if best_sender_score >= 0.30:
            existing_names = {str(r.get("納品先名", "")) for _, r in scored}
            if str(best_sender_row.get("納品先名", "")) not in existing_names:
                scored.append((best_sender_score, best_sender_row))
                scored.sort(key=lambda x: x[0], reverse=True)

    # 候補リスト作成
    candidates = []
    for s, row in scored[:max_candidates]:
        candidates.append({
            "name": str(row.get("納品先名", "")),
            "score": round(s, 3),
            "address": str(row.get("住所", "")),
        })

    # 1位が0.7以上なら採用
    if scored and scored[0][0] >= 0.7:
        best_score, best_row = scored[0]
        best_facility = scored_facility.get(id(best_row))
        # 施設名が明確に不一致なベスト候補は自動マッチさせない（誤確定を防止）
        # 例: OCR=「伊藤忠食品 北陸物流センター」、マスタに「伊藤忠食品 三郷/昭島...」しか無いケース
        # → 候補リストには残し、ユーザーが手動選択する運用へ
        if best_facility is False:
            return {"matched": False, "name": dest_name, "candidates": candidates}
        result = _ddc_row_to_dict(best_row)
        result["match_score"] = best_score
        # 0.90未満は低信頼度（候補確認推奨）
        result["low_confidence"] = best_score < 0.92
        result["candidates"] = candidates
        return result

    # 未マッチ（候補は残す）
    return {"matched": False, "name": dest_name, "candidates": candidates}


def _ddc_row_to_dict(row):
    """Convert a DDC master row to dict, haruna_conditionsから入荷時間等を補完"""
    def safe(val):
        s = str(val).strip() if val is not None else ""
        return "" if s.lower() in ("nan", "none", "null") else s

    raw_name = safe(row.get("納品先名", ""))
    # 表示名から「[コード] / 住所...」を除去して純粋な名前に戻す
    import re
    name = re.sub(r'\s*\[[^\]]*\]\s*/\s*.*$', '', raw_name)
    name = re.sub(r'\s*\[[^\]]*\]\s*$', '', name).strip()

    # haruna_conditionsから入荷時間・バース予約・パレット条件を取得
    # 完全一致 → 部分一致 → DC名ゆれ吸収（SDC/DDC/RDC等）
    import re
    from supabase_client import load_haruna_conditions
    haruna = load_haruna_conditions()
    hc = haruna.get(name, None)
    if hc is None and name:
        # 部分一致
        for hname, hdata in haruna.items():
            if hname in name or name in hname:
                hc = hdata
                break
    if hc is None and name:
        # DC名ゆれ + 会社名正規化で比較
        def _norm_haruna(s):
            t = re.sub(r'[A-Z]DC', '_DC', s)  # SDC/DDC/RDC統一
            t = re.sub(r'株式会社|（株）|\(株\)|㈱', '', t)  # 会社名表記ゆれ
            t = re.sub(r'\s+', ' ', t).strip()
            return t
        name_norm = _norm_haruna(name)
        for hname, hdata in haruna.items():
            hname_norm = _norm_haruna(hname)
            if hname_norm in name_norm or name_norm in hname_norm:
                hc = hdata
                break
    if hc is None:
        hc = {}

    # haruna_conditions.notes の取り扱い:
    # - "pdf:表示名" 形式 → PDF上の納品先名を上書き（既存動作）
    # - それ以外 → 「備考」として PDF に表示（Amazon FBA等の固定備考用途）
    pdf_name_override = ""
    hc_notes = hc.get("notes", "") or ""
    notes_for_display = ""
    if hc_notes.startswith("pdf:"):
        pdf_name_override = hc_notes[4:].strip()
    else:
        notes_for_display = hc_notes

    return {
        "matched": True,
        "name": pdf_name_override or name,
        "nohinsaki_code": safe(row.get("納品先コード", "")),
        "torihikisaki": safe(row.get("取引先名", "")),
        "torihikisaki_code": safe(row.get("取引先コード", "")),
        # 請求先コード: 二重梱包不可判定などで使用 (例: PALTAC=1021)
        "seikyu_code": safe(row.get("請求先コード", "")),
        "postal": safe(row.get("郵便番号", "")),
        "address": safe(row.get("住所", "")),
        "tel": safe(row.get("電話番号", "")),
        "fax": safe(row.get("FAX番号", "")),
        "time": hc.get("arrival_time", ""),
        "berse": hc.get("basse_reservation", ""),
        "palette": hc.get("pallet_condition", ""),
        "jpr": hc.get("jpr_code", ""),
        "method": safe(row.get("納品方法", "")),
        "notes": notes_for_display,
    }


def process_fax_pdf(pdf_path, model="claude-sonnet-4-6"):
    """Process a FAX PDF file end-to-end.

    Args:
        pdf_path: Path to the FAX PDF file
        model: Claude model to use for OCR

    Returns:
        list of dicts, one per page, each containing:
            - ocr_raw: raw OCR result
            - matched_items: list of matched product items
            - ddc_match: matched delivery destination
            - sylvia_items: items for Sylvia
            - haruna_items: items for Haruna
    """
    pm = load_product_master()
    ddc = load_ddc_master()

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    pages = pdf_to_images(pdf_bytes, dpi=300)
    results = []

    for page_info in pages:
        print(f"  Page {page_info['page']}/{len(pages)} - OCR processing...")
        ocr_result = ocr_fax_page(page_info["base64"], model=model)

        if "error" in ocr_result:
            results.append({
                "page": page_info["page"],
                "image_page": page_info["page"],
                "error": ocr_result["error"],
                "ocr_raw": ocr_result,
            })
            continue

        # 年の異常値補正: OCRが製造年や賞味期限年を発注日と誤読する場合がある
        delivery_date = ocr_result.get("delivery_date", "")
        if delivery_date and len(delivery_date) >= 4 and delivery_date[:4].isdigit():
            year = int(delivery_date[:4])
            if year < 2020 or year > 2030:
                ocr_result["delivery_date"] = "2026" + delivery_date[4:]

        # 明細行ごとに納品先が異なるフォーマット（伊藤忠食品SEJ等）は納品先単位に分割する
        for dest_seq, sub_ocr in expand_multi_dest_ocr(ocr_result):
            # Match products
            matched_items = []
            for ocr_item in sub_ocr.get("items", []):
                match = match_product(ocr_item, pm)
                if match:
                    matched_items.append(match)
                else:
                    matched_items.append({
                        "matched": False,
                        "ocr_name": ocr_item.get("product_name", ""),
                        "jan": str(ocr_item.get("jan_code", "")),
                        "quantity": int(float(str(ocr_item.get("quantity_cs") or 0).strip() or "0")),
                    })

            # Match DDC（発注元情報もヒントとして渡す）
            ddc_match = match_ddc(
                sub_ocr.get("delivery_dest", ""),
                ddc,
                sender=sub_ocr.get("sender", ""),
                chain=sub_ocr.get("chain_hint", ""),
            )

            # Split by output destination
            sylvia_items = [i for i in matched_items if i.get("matched") and i.get("output_dest") == "シルビア"]
            haruna_items = [i for i in matched_items if i.get("matched") and i.get("output_dest") == "ハルナ"]
            warehouse_items = [i for i in matched_items if i.get("matched") and i.get("output_dest") == "自社倉庫"]

            results.append({
                "page": page_info["page"],
                "image_page": page_info["page"],
                "ocr_raw": sub_ocr,
                "matched_items": matched_items,
                "ddc_match": ddc_match,
                "sylvia_items": sylvia_items,
                "haruna_items": haruna_items,
                "warehouse_items": warehouse_items,
                "multi_dest": dest_seq > 0,
            })

    # 「1商品1枚」形式（商品ごとにページが分かれ納品先が対になる）を納品先単位に統合
    return merge_multi_dest_results(results)
