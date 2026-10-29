"""自建中文斷詞（取代 jieba），供 BM25 關鍵字檢索使用。

設計：
  1. 領域詞典：直接取自資料庫的官方登記資料（作物名稱、農藥普通名稱、病蟲害名稱），
     再加上 rag.py 註冊的俗名對照與法規、用藥關鍵詞。
  2. 正向最長比對：中文字串中優先切出詞典裡最長的詞；三到四字的詞（如小菜蛾、炭疽病）
     再補上二字片段，讓「菜蛾」也能命中「小菜蛾」。五字以上的長詞不拆，避免誤配。
  3. 詞典比對不到的部分，以中文二字切分（bigram）處理；含虛詞（我、的、要、怎麼、可以…）
     的片段不列入，避免口語問句被「要怎麼處理」這類無關內容干擾。
  4. 英數字（ppm、MRL、數字）整段保留並轉小寫。

不依賴任何第三方斷詞套件與外部模型，詞典來源皆為官方登記資料，可追溯、可稽核。
語料與查詢必須使用同一份詞典斷詞，因此詞典在程序啟動後第一次使用時建立，之後不再變動。
"""
import re
import threading

from db import get_db

MAX_WORD_LEN = 12
_CJK_OR_ALNUM = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+|[A-Za-z0-9][A-Za-z0-9.\-]*")
_SPLIT_COMPOUND = re.compile(r"[\s\u3000、/（）()]+")

# 虛詞：含這些字的二字片段不列入檢索（詞典裡的詞不受影響）
STOP_CHARS = set("我你妳他她它們的了著過要想可以請問該應怎麼什哪些幾少嗎呢吧啊呀喔哦"
                 "和與及或跟都也還就才再又在於把被讓給對從到向是很太最更這那個其之而且但如若則所")

DOMAIN_TERMS = [
    "安全採收期", "稀釋倍數", "施藥間隔", "施用次數", "使用時期", "注意事項", "每公頃",
    "殘留容許量", "許可證", "有效期間", "有效期限", "販賣業", "管理人員", "農藥管理法",
    "殺菌劑", "殺蟲劑", "除草劑", "殺蟎劑", "抗藥性", "作用機制", "中毒", "急救",
    "農藥行", "販賣執照", "防護衣", "口罩", "頭暈", "噁心", "嘔吐",
]

_lock = threading.Lock()
_words = None
_extra = set(DOMAIN_TERMS)


def add_terms(terms):
    """註冊額外詞彙（rag.py 在模組載入時呼叫，須在第一次斷詞前完成）。"""
    global _words
    clean = {t.strip() for t in terms if isinstance(t, str) and len(t.strip()) >= 2}
    _extra.update(clean)
    if _words is not None:
        _words.update(w for w in clean if len(w) <= MAX_WORD_LEN)


def _build():
    words = set(w for w in _extra if len(w) <= MAX_WORD_LEN)
    try:
        conn = get_db()
        for col in ("作物名稱", "農藥中文普通名稱", "病蟲害名稱"):
            try:
                for (v,) in conn.execute(f'SELECT DISTINCT "{col}" FROM pesticides'):
                    for part in _SPLIT_COMPOUND.split(str(v or "")):
                        if 2 <= len(part) <= MAX_WORD_LEN:
                            words.add(part)
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return words


def _dictionary():
    global _words
    if _words is None:
        with _lock:
            if _words is None:
                _words = _build()
                print(f"🔤 自建斷詞詞典載入完成：{len(_words)} 個詞")
    return _words


def _bigrams(s: str, filter_stop: bool = True) -> list:
    if len(s) == 1:
        return [] if (filter_stop and s in STOP_CHARS) else [s]
    grams = [s[i:i + 2] for i in range(len(s) - 1)]
    if filter_stop:
        grams = [g for g in grams if g[0] not in STOP_CHARS and g[1] not in STOP_CHARS]
    return grams


def _cut_cjk(run: str, words: set) -> list:
    tokens, pending, i, n = [], "", 0, len(run)
    while i < n:
        hit = None
        for L in range(min(MAX_WORD_LEN, n - i), 1, -1):
            if run[i:i + L] in words:
                hit = run[i:i + L]
                break
        if hit:
            if pending:
                tokens += _bigrams(pending)
                pending = ""
            tokens.append(hit)
            if 3 <= len(hit) <= 4:
                tokens += _bigrams(hit, filter_stop=False)
            i += len(hit)
        else:
            pending += run[i]
            i += 1
    if pending:
        tokens += _bigrams(pending)
    return tokens


def tokenize(text) -> list:
    words = _dictionary()
    tokens = []
    for m in _CJK_OR_ALNUM.finditer(str(text or "")):
        seg = m.group()
        if seg[0].isascii():
            tokens.append(seg.lower())
        else:
            tokens += _cut_cjk(seg, words)
    return tokens
