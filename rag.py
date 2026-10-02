"""RAG 檢索問答、農藥/法規查詢、模擬考出題、今日新聞。

這是系統的核心知識模組：BM25 + 向量語意的混合檢索、作物/農藥俗名對照、
多子問題拆解、可溯源回覆、知識邊界保護、用藥合法性檢查、結構化處方卡，
以及以既有知識庫自動出題的模擬考。
"""
import os
import re
import json
import sqlite3
from datetime import date
from typing import Optional

import httpx
import jieba
from rank_bm25 import BM25Okapi
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from db import get_db, CHROMA_PATH
from auth import get_current_user

OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

router = APIRouter()

_news_cache: dict = {"date": None, "data": None}

# ── 向量庫（各 collection 惰性載入並快取）─────────────────────
_vectorstore = None
_law_vectorstore = None
_qa_vectorstore = None
_residue_vectorstore = None


def get_vectorstore():
    global _vectorstore
    if _vectorstore:
        return _vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    _vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="pesticides")
    return _vectorstore


def get_law_vectorstore():
    global _law_vectorstore
    if _law_vectorstore:
        return _law_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _law_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="regulations")
    except Exception:
        _law_vectorstore = None
    return _law_vectorstore


def get_qa_vectorstore():
    global _qa_vectorstore
    if _qa_vectorstore:
        return _qa_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _qa_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="qa_knowledge")
    except Exception:
        _qa_vectorstore = None
    return _qa_vectorstore


def get_residue_vectorstore():
    global _residue_vectorstore
    if _residue_vectorstore:
        return _residue_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _residue_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="residue_limits")
    except Exception:
        _residue_vectorstore = None
    return _residue_vectorstore


# ── BM25 混合搜尋 ─────────────────────────────────────────────
_bm25_cache = {}


def get_bm25_index(table_name: str, fetch_columns: list, score_columns: list):
    if table_name in _bm25_cache:
        return _bm25_cache[table_name]

    conn = get_db()
    all_cols = list(dict.fromkeys(fetch_columns + ["id"]))
    cols_sql = ", ".join(f'"{c}"' for c in all_cols)
    try:
        rows = conn.execute(f'SELECT {cols_sql} FROM {table_name}').fetchall()
    except sqlite3.OperationalError:
        conn.close()
        _bm25_cache[table_name] = None
        return None
    conn.close()

    corpus_tokens = []
    docs_meta = []
    for row in rows:
        d = dict(row)
        text = " ".join(str(d.get(c) or "") for c in score_columns)
        corpus_tokens.append(list(jieba.cut(text)))
        docs_meta.append(d)

    if not corpus_tokens:
        _bm25_cache[table_name] = None
        return None

    bm25 = BM25Okapi(corpus_tokens)
    _bm25_cache[table_name] = (bm25, docs_meta)
    return _bm25_cache[table_name]


def bm25_search(table_name: str, fetch_columns: list, score_columns: list, query: str, k: int = 10):
    index = get_bm25_index(table_name, fetch_columns, score_columns)
    if not index:
        return []
    bm25, docs_meta = index
    query_tokens = list(jieba.cut(query))
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [docs_meta[i] for i in ranked[:k] if scores[i] > 0]


def reciprocal_rank_fusion(rank_lists: list, k: int = 60, top_n: int = 5):
    scores = {}
    for rank_list in rank_lists:
        for rank, item_id in enumerate(rank_list):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_n]


class SimpleDoc:
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


_TABLE_CONFIG = {
    "pesticides": {
        "fetch_columns": ["作物名稱", "農藥中文普通名稱", "病蟲害名稱", "使用時期", "安全採收期_天"],
        "score_columns": ["作物名稱", "農藥中文普通名稱", "病蟲害名稱"],
        "content_fn": lambda d: f"作物：{d.get('作物名稱','')}，農藥：{d.get('農藥中文普通名稱','')}，病蟲害：{d.get('病蟲害名稱','')}，使用時期：{d.get('使用時期','')}，安全採收期：{d.get('安全採收期_天','')}天",
        "metadata_fn": lambda d: {"作物名稱": d.get("作物名稱", ""), "普通名稱": d.get("農藥中文普通名稱", "")},
    },
    "regulations": {
        "fetch_columns": ["法規名稱", "章節", "條號", "條文內容", "來源網址"],
        "score_columns": ["條文內容"],
        "content_fn": lambda d: f"{d.get('法規名稱','')}{d.get('條號','')}（{d.get('章節','')}）：{d.get('條文內容','')}",
        "metadata_fn": lambda d: {"法規名稱": d.get("法規名稱", ""), "條號": d.get("條號", ""), "來源": d.get("來源網址", "")},
    },
    "qa_knowledge": {
        "fetch_columns": ["問題", "答案", "來源類別", "分類", "來源網址"],
        "score_columns": ["問題", "答案"],
        "content_fn": lambda d: f"問：{d.get('問題','')}\n答：{d.get('答案','')}",
        "metadata_fn": lambda d: {"來源類別": d.get("來源類別", ""), "分類": d.get("分類", ""), "來源": d.get("來源網址", "")},
    },
    "residue_limits": {
        "fetch_columns": ["農藥名稱與作物", "限量_ppm", "用途分類"],
        "score_columns": ["農藥名稱與作物"],
        "content_fn": lambda d: f"{d.get('農藥名稱與作物','')}：殘留容許量 {d.get('限量_ppm','')} ppm（{d.get('用途分類','')}）",
        "metadata_fn": lambda d: {"用途分類": d.get("用途分類", ""), "來源": "https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/463/relfile/77793/347247/7ca0f288-f7ad-4826-8a3f-20f0c6a2fa82.pdf"},
    },
}


def hybrid_retrieve(table_name: str, vectorstore, query: str, k: int = 5, vector_filter: dict = None, guaranteed_vector_slots: int = 2):
    cfg = _TABLE_CONFIG[table_name]

    vector_docs = []
    if vectorstore:
        try:
            if vector_filter:
                vector_docs = vectorstore.similarity_search(query, k=10, filter=vector_filter)
            else:
                vector_docs = vectorstore.similarity_search(query, k=10)
        except Exception:
            vector_docs = []

    bm25_rows = bm25_search(table_name, cfg["fetch_columns"], cfg["score_columns"], query, k=10)

    def _doc_key(d):
        doc_id = d.metadata.get("id")
        return doc_id if doc_id is not None else f"vec::{d.page_content}"

    pool = {}
    for d in vector_docs:
        pool[_doc_key(d)] = (d.page_content, d.metadata)
    for row in bm25_rows:
        row_id = row.get("id")
        if row_id is not None and row_id not in pool:
            pool[row_id] = (cfg["content_fn"](row), cfg["metadata_fn"](row))

    vector_ids = [_doc_key(d) for d in vector_docs]
    bm25_ids = [r.get("id") for r in bm25_rows if r.get("id") is not None]

    if not vector_ids and not bm25_ids:
        return []

    final_ids = list(vector_ids[:guaranteed_vector_slots])
    remaining_slots = max(k - len(final_ids), 0)
    if remaining_slots:
        fused_ids = reciprocal_rank_fusion([vector_ids, bm25_ids], top_n=k + guaranteed_vector_slots)
        for fid in fused_ids:
            if fid not in final_ids:
                final_ids.append(fid)
            if len(final_ids) >= k:
                break

    return [SimpleDoc(pool[i][0], pool[i][1]) for i in final_ids[:k] if i in pool]


# ── 請求模型 ──────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str
    prev_crop: Optional[str] = None  # 上一輪對話鎖定的作物，用於「還有哪些」這類接續問句
    prev_pest: Optional[str] = None  # 上一輪對話鎖定的病蟲害，用於接續時延續同一病蟲害、數字一致
    is_followup: Optional[bool] = False  # 是否為「還有哪些」這類接續追問（前端偵測後標記）
    history: Optional[list] = None  # 最近幾輪對話 [{role: "user"|"bot", text: "..."}]，用於連續問答


# ── 連續問答：先判斷主題、把追問改寫成可獨立檢索的完整問題 ─────────────
OFF_TOPIC_ANSWER = ("知識庫無此資訊。我是農藥博士，專門回答作物病蟲害用藥、安全採收期、"
                    "農藥法規與中毒急救的問題，換個農業相關的問題試試看吧！")

ROUTER_PROMPT = """你是農藥問答系統的前置判斷器。請根據「先前對話」理解使用者「最新的話」，回傳 JSON。

type 只能是以下三種之一：
- "on_topic"：與農業、作物、病蟲害、雜草、農藥、施藥、稀釋計算、農藥殘留、食品安全、農藥法規、農藥中毒急救有關。
  換了一種作物或病蟲害（例如先問木瓜、再問「那芒果呢？」）仍然是 on_topic。
- "chitchat"：打招呼、道謝、稱讚、問你是誰這類閒聊。
- "off_topic"：與上述農業／農藥主題無關的問題（例如寫程式、數學作業、股票、旅遊、娛樂、一般常識）。

question：若 type 是 on_topic，把最新的話改寫成「不需要看先前對話也能看懂」的完整問題，補上從先前對話推得出的作物、病蟲害、農藥名稱。
  例如先前在談「木瓜炭疽病」，最新的話是「那芒果呢？」→「芒果炭疽病要用什麼藥？」；
  先前在談「高麗菜小菜蛾用亞克瑞」，最新的話是「要噴幾次？」→「高麗菜小菜蛾用亞克瑞要噴幾次？」；
  先前在談「高麗菜小菜蛾」，最新的話是「還有哪些？」→「高麗菜小菜蛾還有哪些登記用藥？」。
  沿用使用者的作物俗名（說空心菜就寫空心菜），不要回答問題，只改寫。最新的話本身已經完整就原樣保留。
reply：若 type 是 chitchat，用一兩句親切的繁體中文回應，並帶一句可以問什麼；其他情況留空字串。

只回傳 JSON：{"type": "...", "question": "...", "reply": "..."}"""


def _history_lines(history: list, limit: int = 8) -> list:
    """整理前端傳來的對話紀錄：只取最近幾則、去掉空白、每則截斷，避免 token 暴增。"""
    lines = []
    for h in (history or [])[-limit:]:
        if not isinstance(h, dict):
            continue
        text = str(h.get("text") or "").strip()
        if not text:
            continue
        role = "user" if h.get("role") == "user" else "bot"
        lines.append((role, text[:600]))
    return lines


def route_question(question: str, history: list) -> dict:
    """主題判斷＋追問改寫。失敗時保守地當作 on_topic、原問題照用，不影響原本功能。"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    lines = _history_lines(history)
    convo = "\n".join(f"{'使用者' if r == 'user' else '農藥博士'}：{t}" for r, t in lines) or "（無）"
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, openai_api_key=OPENAI_API_KEY,
                         model_kwargs={"response_format": {"type": "json_object"}})
        resp = llm.invoke([
            SystemMessage(content=ROUTER_PROMPT),
            HumanMessage(content=f"先前對話：\n{convo}\n\n最新的話：{question}"),
        ])
        data = json.loads(resp.content)
        rtype = data.get("type") if data.get("type") in ("on_topic", "chitchat", "off_topic") else "on_topic"
        rewritten = (data.get("question") or "").strip() or question
        return {"type": rtype, "question": rewritten, "reply": (data.get("reply") or "").strip()}
    except Exception:
        return {"type": "on_topic", "question": question, "reply": ""}


class NewsItem(BaseModel):
    title: str; url: str; source: str; date: str; tag: str


def split_questions(q: str) -> list:
    parts = re.split(r'(?<=[？?])', q)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if parts else [q]


# ── 用藥處方卡：關鍵數值一律直接取自 SQLite 登記資料，不經 LLM 生成 ──────────
CARD_COLUMNS = ("農藥中文普通名稱, 農藥含量, 劑型, 每公頃每次用量, 稀釋倍數, 使用時期, "
                "施藥間隔, 施用次數, 安全採收期_天, 施用方法, 注意事項, 病蟲害名稱, 核准日期")


def _clean(v):
    v = (str(v) if v is not None else "").strip()
    return "" if v in ("-", "－", "nan", "None") else v


def _format_approved(v):
    """核准日期為民國年 7 碼（如 0840426），轉成「民國84年04月26日」方便閱讀。"""
    v = _clean(v)
    if len(v) == 7 and v.isdigit():
        return f"民國{int(v[:3])}年{v[3:5]}月{v[5:]}日"
    return v


def row_to_card(r, crop: str) -> dict:
    d = dict(r)
    return {
        "name": _clean(d.get("農藥中文普通名稱")),
        "crop": crop,
        "pest": _clean(d.get("病蟲害名稱")),
        "content": _clean(d.get("農藥含量")),
        "formulation": _clean(d.get("劑型")),
        "dosage": _clean(d.get("每公頃每次用量")),
        "dilution": _clean(d.get("稀釋倍數")),
        "timing": _clean(d.get("使用時期")),
        "interval": _clean(d.get("施藥間隔")),
        "times": _clean(d.get("施用次數")),
        "phi": _clean(d.get("安全採收期_天")),
        "method": _clean(d.get("施用方法")),
        "notes": _clean(d.get("注意事項")),
        "approved": _format_approved(d.get("核准日期")),
    }


def _match_pesticide(text: str, pesticide_names: list):
    """問句中出現的農藥名稱，取最長的匹配（避免「加保扶」被「丁基加保扶」以外的短名先吃掉）。"""
    hits = [p for p in pesticide_names if p and len(p) >= 2 and p in text]
    return max(hits, key=len) if hits else None


def check_compliance(conn, crop: str, pesticide: str, alias_used=None) -> dict:
    """用藥合法性檢查：該農藥是否登記用於該作物。"""
    rows = conn.execute(
        "SELECT DISTINCT 病蟲害名稱 FROM pesticides WHERE 作物名稱=? AND 農藥中文普通名稱=? AND 病蟲害名稱 != ''",
        (crop, pesticide),
    ).fetchall()
    crop_display = alias_used[0] if alias_used else crop.split()[0]
    if rows:
        return {
            "status": "registered", "crop": crop, "crop_display": crop_display,
            "pesticide": pesticide, "pests": [r[0] for r in rows][:12],
        }
    others = conn.execute(
        "SELECT DISTINCT 作物名稱 FROM pesticides WHERE 農藥中文普通名稱=? LIMIT 8", (pesticide,)
    ).fetchall()
    return {
        "status": "not_registered", "crop": crop, "crop_display": crop_display,
        "pesticide": pesticide, "other_crops": [r[0].split()[0] for r in others],
    }


def compliance_context(c: dict) -> str:
    if c["status"] == "registered":
        return (f"【用藥合法性檢查：已登記】「{c['pesticide']}」已登記用於「{c['crop_display']}」，"
                f"登記防治對象：{'、'.join(c['pests'])}。請依登記用法使用，並遵守安全採收期。")
    return (f"【用藥合法性檢查：未登記】資料庫查無「{c['pesticide']}」登記用於「{c['crop_display']}」的紀錄。"
            f"農藥必須依登記的作物與病蟲害使用，請在回答第一句就明確告訴使用者「不可以」將此藥用於{c['crop_display']}，"
            f"並建議改用{c['crop_display']}已登記的藥劑，或洽詢當地農會、植物醫師。")


CROP_ALIASES = {
    "空心菜": "蕹菜", "地瓜葉": "甘藷", "地瓜": "甘藷", "番薯": "甘藷",
    "高麗菜": "甘藍", "大陸妹": "結球萵苣", "娃娃菜": "結球白菜", "白菜": "小白菜",
    "紅蘿蔔": "胡蘿蔔", "馬鈴薯": "馬鈴薯", "洋芋": "馬鈴薯", "青蔥": "蔥",
    "蒜頭": "蒜", "小黃瓜": "胡瓜", "花生": "落花生", "毛豆": "大豆",
    "四季豆": "菜豆", "皇帝豆": "萊豆", "甜豆": "豌豆", "荷蘭豆": "豌豆",
    "芭樂": "番石榴", "奇異果": "獼猴桃", "火龍果": "紅龍果", "釋迦": "番荔枝",
    "鱷梨": "酪梨", "水蜜桃": "桃",
}

PESTICIDE_ALIASES = {
    "好年冬": "加保扶",
    "呋喃丹": "加保扶",
}

SOURCE_VERSIONS = {
    "pesticide": "2026年擷取",
    "law": "2026年擷取",
    "qa": "2026年擷取",
    "residue": "2026年擷取",
    "poison": "2026年擷取",
}

LAW_KEYWORDS = ['法規','罰則','罰鍰','違法','違規','許可證','禁用','偽農藥','劣農藥','販賣業','登記',
                '殘留','超標','抽驗','檢驗','訓練','證書','展延','管理人員','規費','收費']
QA_KEYWORDS = ['中午','噴藥時機','施藥時機','混用','混合','桶混','抗藥性','雨季','下雨',
               '飄散','飄移','過期','保存','劑型','餌劑','系統性','接觸性','天敵',
               '生物防治','用藥量','稀釋倍數','重入間隔','施藥後','噴藥後','施用次數',
               '合理使用','安全使用','施藥方法','噴藥方法',
               '中毒','急救','解毒劑','有機磷','中毒症狀','誤食','誤觸','送醫','就醫',
               '毒物中心','防護','中毒諮詢']
RESIDUE_KEYWORDS = ['殘留容許量', '殘留標準', '最大殘留限量', 'ppm', '容許量', '殘留量', '殘留限量', 'MRL']
CALC_KEYWORDS = ['計算', '換算', 'ppm', '倍數', '公升', '毫升', '公頃', '甲', '分地',
                 '稀釋', '調配', '需要多少', '幾桶', '桶數']


def retrieve_for_question(q: str, prev_crop: str = None, prev_pest: str = None, is_followup: bool = False) -> dict:
    conn = get_db()
    crops = [r[0] for r in conn.execute("SELECT DISTINCT 作物名稱 FROM pesticides").fetchall()]

    q_normalized = q
    crop_alias_used = None
    for alias, official in CROP_ALIASES.items():
        if alias in q and official not in q:
            q_normalized = q_normalized.replace(alias, official)
            crop_alias_used = (alias, official)
    for alias, official in PESTICIDE_ALIASES.items():
        if alias in q_normalized and official != alias and official not in q_normalized:
            q_normalized = q_normalized.replace(alias, official)

    def _crop_matches(crop_name, text):
        """判斷作物是否命中：完整名稱出現、或複合名稱（用空格分隔，如「檬果 芒果」）
        中任一組成詞出現在問句裡，都算命中。解決資料庫作物名為複合格式時對不上的問題。"""
        if crop_name in text:
            return True
        # 複合名稱拆解（空格、全形空格、頓號）
        for part in re.split(r'[\s\u3000、/]+', crop_name):
            if len(part) >= 2 and part in text:
                return True
        return False

    crop = next((c for c in crops if _crop_matches(c, q_normalized)), None)
    if not crop:
        for alias, official in CROP_ALIASES.items():
            if alias in q:
                crop = next((c for c in crops if official in c or alias in c), None)
                if crop:
                    break

    # 接續對話：若這一輪問句「沒有」偵測到作物，但出現「還有／其他／它／那個」等接續詞，
    # 且上一輪有鎖定作物（prev_crop），就沿用上一輪的作物，讓「還有哪些」能接續前一題。
    # 反之，只要這一輪明確講了新的作物，就用新的（不硬帶舊的），避免換話題時帶錯。
    FOLLOWUP_WORDS = ['還有', '其他', '別的', '它', '牠', '那個', '這個', '再', '更多', '呢']
    q_is_followup = is_followup or any(w in q for w in FOLLOWUP_WORDS)
    if not crop and prev_crop and prev_crop in crops:
        if q_is_followup:
            crop = prev_crop
    # 接續追問時，若這一輪問句沒帶新的病蟲害，就沿用上一輪的病蟲害，讓「還有哪些」延續同一病蟲害
    # （例如「芒果炭疽病用藥」→「還有哪些」仍是查芒果炭疽病，數字一致、不會跳成整個作物）
    followup_pest = None
    if q_is_followup and prev_pest:
        followup_pest = prev_pest
    # 藥劑類型：資料庫沒有「藥劑類型」欄位，改由登記的防治對象推斷
    #   殺菌劑 → 病害（名稱含「病」）；殺蟎劑 → 蟎類；除草劑 → 雜草；殺蟲劑 → 其餘蟲害
    ptype = next((t for t in ['殺菌', '殺蟲', '除草', '殺蟎', '殺螨'] if t in q), None)
    if ptype == '殺螨':
        ptype = '殺蟎'
    PTYPE_SQL = {
        '殺菌': "病蟲害名稱 LIKE '%病%'",
        '殺蟎': "(病蟲害名稱 LIKE '%蟎%' OR 病蟲害名稱 LIKE '%螨%')",
        '除草': "(病蟲害名稱 LIKE '%草%' OR 病蟲害名稱 LIKE '%雜草%')",
        '殺蟲': ("病蟲害名稱 NOT LIKE '%病%' AND 病蟲害名稱 NOT LIKE '%蟎%' AND 病蟲害名稱 NOT LIKE '%螨%' "
                 "AND 病蟲害名稱 NOT LIKE '%草%' AND 病蟲害名稱 NOT LIKE '%調節%' AND 病蟲害名稱 NOT LIKE '%促進%' "
                 "AND 病蟲害名稱 != ''"),
    }
    sql_ctx = ""
    sources = []
    cards = []
    if crop and ptype:
        rows = conn.execute(f"SELECT {CARD_COLUMNS} FROM pesticides WHERE 作物名稱=? AND {PTYPE_SQL[ptype]} GROUP BY 農藥中文普通名稱 LIMIT 15", (crop,)).fetchall()
        if rows:
            cards = [row_to_card(r, crop) for r in rows]
            sql_ctx = f"【{crop} {ptype}劑 精確查詢】\n" + "\n".join(f"• {r['農藥中文普通名稱']}：稀釋{r['稀釋倍數']}倍，{r['使用時期']}，採收期{r['安全採收期_天']}天" for r in rows)
            sources.append({"title": f"{crop}{ptype}劑登記資料", "url": "https://pesticide.aphia.gov.tw", "date": SOURCE_VERSIONS["pesticide"]})

    # 若上面的「作物+類型」精確查詢沒撈到（例如問「芒果炭疽病用藥」這種帶病蟲害名、
    # 但不含殺菌/殺蟲/除草/殺蟎字眼的查詢），改用「作物+病蟲害名稱」直接把該作物該病蟲害
    # 的所有登記藥一次撈齊（避免只靠向量檢索被壓縮到剩兩三種）。
    matched_pest = None
    if crop and not sql_ctx:
        pest_rows = conn.execute(
            "SELECT DISTINCT 病蟲害名稱 FROM pesticides WHERE 作物名稱=?", (crop,)
        ).fetchall()
        # 找出問句提到的病蟲害名稱（取最長的匹配，避免「炭疽」比「炭疽病」先中而抓到較短的）
        matched_pests = [p[0] for p in pest_rows if p[0] and p[0] in q_normalized]
        matched_pest = max(matched_pests, key=len) if matched_pests else None
        # 接續追問（「還有哪些」）時，若這輪沒帶新病蟲害，沿用上一輪的病蟲害，讓數字與範圍一致
        if not matched_pest and followup_pest:
            matched_pest = followup_pest
        if matched_pest:
            all_rows = conn.execute(
                f"SELECT {CARD_COLUMNS} "
                "FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱=? "
                "GROUP BY 農藥中文普通名稱", (crop, matched_pest)
            ).fetchall()
            total = len(all_rows)
            # 一般首次詢問只列前 12 種；接續追問（還有哪些）時把全部餵給 AI，並提示接著講前面沒提到的，
            # 這樣數字全程一致（都是 total 種）、AI 也盡量不重複。
            if q_is_followup and followup_pest:
                shown = all_rows
                header = f"【{crop} {matched_pest} 登記用藥，資料庫共 {total} 種（使用者想看更多，請接著介紹前面對話還沒提到的品項，不要重複已經講過的）】"
            else:
                shown = all_rows[:12]
                header = f"【{crop} {matched_pest} 登記用藥，資料庫共 {total} 種，以下列出主要 {len(shown)} 種】"
            cards = [row_to_card(r, crop) for r in shown]
            if shown:
                sql_ctx = header + "\n" + \
                    "\n".join(f"• {r['農藥中文普通名稱']}：稀釋{r['稀釋倍數']}倍，{r['使用時期']}，採收期{r['安全採收期_天']}天" for r in shown)
                if total > len(shown):
                    sql_ctx += f"\n（另有 {total - len(shown)} 種未列出，可再詢問完整清單）"
                sources.append({"title": f"{crop}{matched_pest}登記用藥（共{total}種）", "url": "https://pesticide.aphia.gov.tw", "date": SOURCE_VERSIONS["pesticide"]})

    # 若問句只有作物、沒有對應到任何具體病蟲害（例如只問「木瓜」「木瓜類」），
    # 就撈出該作物有登記的病蟲害清單，每種病蟲害列一個代表藥，形成「用藥目錄」。
    # context 明確標示這些就是該作物的登記資料，讓 AI 不會誤判成「沒有專屬資料」。
    if crop and not sql_ctx:
        catalog_rows = conn.execute(
            "SELECT 病蟲害名稱, 農藥中文普通名稱, 稀釋倍數, 使用時期, 安全採收期_天 "
            "FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱 != '' AND 病蟲害名稱 IS NOT NULL "
            "GROUP BY 病蟲害名稱 ORDER BY 病蟲害名稱", (crop,)
        ).fetchall()
        if catalog_rows:
            pest_total = len(catalog_rows)
            shown_cat = catalog_rows[:15]
            lines = []
            for r in shown_cat:
                lines.append(f"• {r['病蟲害名稱']}：可用{r['農藥中文普通名稱']}（稀釋{r['稀釋倍數']}倍，{r['使用時期']}，採收期{r['安全採收期_天']}天）等")
            header = f"【{crop} 登記用藥總覽：本資料庫確實有「{crop}」的完整登記資料，共可防治 {pest_total} 種病蟲害，以下列出主要 {len(shown_cat)} 種防治對象與代表藥劑】"
            sql_ctx = header + "\n" + "\n".join(lines)
            if pest_total > len(shown_cat):
                sql_ctx += f"\n（{crop}另有其他病蟲害的登記用藥未列出）"
            example_pest = shown_cat[0]["病蟲害名稱"]
            sql_ctx += f"\n（提示：想看某個病蟲害的完整用藥清單，可再問「{crop}某病蟲害用藥」，例如「{crop}{example_pest}用藥」）"
            sources.append({"title": f"{crop}登記用藥總覽（可防治{pest_total}種病蟲害）", "url": "https://pesticide.aphia.gov.tw", "date": SOURCE_VERSIONS["pesticide"]})

    residue_sql_ctx = ""
    pesticide_names = [r[0] for r in conn.execute("SELECT DISTINCT 農藥中文普通名稱 FROM pesticides WHERE 農藥中文普通名稱 != ''").fetchall()]
    matched_pesticide = _match_pesticide(q_normalized, pesticide_names)
    compliance = None
    if crop and matched_pesticide:
        compliance = check_compliance(conn, crop, matched_pesticide, crop_alias_used)
    if matched_pesticide:
        try:
            residue_rows = conn.execute(
                'SELECT 農藥名稱與作物, 限量_ppm, 用途分類 FROM residue_limits WHERE 農藥名稱與作物 LIKE ? LIMIT 30',
                (f"%{matched_pesticide}%",)
            ).fetchall()
            if residue_rows:
                residue_sql_ctx = f"【{matched_pesticide} 殘留容許量精確查詢】\n" + "\n".join(
                    f"• {r['農藥名稱與作物']}：{r['限量_ppm']} ppm（{r['用途分類']}）" for r in residue_rows
                )
                sources.append({"title": f"{matched_pesticide} 殘留容許量標準", "url": "https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/463/relfile/77793/347247/7ca0f288-f7ad-4826-8a3f-20f0c6a2fa82.pdf", "date": SOURCE_VERSIONS["residue"]})
        except sqlite3.OperationalError:
            pass

    law_sql_ctx = ""
    if any(k in q for k in ["許可證", "展延", "有效期"]):
        try:
            law_rows = []
            for term_b in ["有效期間", "有效期限", "展延"]:
                rows2 = conn.execute(
                    'SELECT 法規名稱, 條號, 條文內容, 來源網址 FROM regulations WHERE 條文內容 LIKE ? AND 條文內容 LIKE ? LIMIT 5',
                    ("%許可證%", f"%{term_b}%")
                ).fetchall()
                law_rows.extend(rows2)
            seen_articles = set()
            unique_rows = []
            for r in law_rows:
                key = (r["法規名稱"], r["條號"])
                if key not in seen_articles:
                    seen_articles.add(key)
                    unique_rows.append(r)
            if unique_rows:
                law_sql_ctx = "【法規精確查詢（同義詞比對）】\n" + "\n".join(
                    f"{r['法規名稱']}{r['條號']}：{r['條文內容']}" for r in unique_rows[:5]
                )
                for r in unique_rows[:5]:
                    sources.append({"title": f"{r['法規名稱']}{r['條號']}", "url": r["來源網址"] or "https://pesticide.aphia.gov.tw/information/Data/Law/1", "date": SOURCE_VERSIONS["law"]})
        except sqlite3.OperationalError:
            pass

    conn.close()

    vs = get_vectorstore()
    if crop:
        docs = hybrid_retrieve("pesticides", vs, q_normalized, k=5, vector_filter={"作物名稱": crop})
        # BM25 目前是全庫搜尋，混合排序後仍要再次套用作物白名單，
        # 避免不同作物的資料被合併回來，或在查不到時退回全庫結果。
        docs = [d for d in docs if d.metadata.get("作物名稱") == crop]
    else:
        docs = hybrid_retrieve("pesticides", vs, q_normalized, k=5)

    vec_ctx = "\n\n".join(d.page_content for d in docs)

    law_docs = []
    if any(k in q for k in LAW_KEYWORDS):
        law_vs = get_law_vectorstore()
        law_docs = hybrid_retrieve("regulations", law_vs, q_normalized, k=5)

    qa_docs = []
    if any(k in q for k in QA_KEYWORDS):
        qa_vs = get_qa_vectorstore()
        qa_docs = hybrid_retrieve("qa_knowledge", qa_vs, q_normalized, k=5)

    residue_docs = []
    if any(k in q for k in RESIDUE_KEYWORDS):
        residue_vs = get_residue_vectorstore()
        residue_docs = hybrid_retrieve("residue_limits", residue_vs, q_normalized, k=5)

    context = sql_ctx
    if residue_sql_ctx:
        context += ("\n\n" + residue_sql_ctx) if context else residue_sql_ctx
    if law_sql_ctx:
        context += ("\n\n" + law_sql_ctx) if context else law_sql_ctx
    if crop or (not law_docs and not qa_docs and not residue_docs and not residue_sql_ctx and not law_sql_ctx):
        context += ("\n\n【語意搜尋補充】\n" + vec_ctx) if context else vec_ctx
    if law_docs:
        law_ctx = "\n\n".join(d.page_content for d in law_docs)
        context += "\n\n【法規依據】\n" + law_ctx
    if qa_docs:
        qa_ctx = "\n\n".join(d.page_content for d in qa_docs)
        context += "\n\n【農藥合理使用問答集】\n" + qa_ctx
    if residue_docs:
        residue_ctx = "\n\n".join(d.page_content for d in residue_docs)
        context += "\n\n【農藥殘留容許量標準（語意補充）】\n" + residue_ctx

    is_calc_question = sum(k in q for k in CALC_KEYWORDS) >= 3

    if crop or (not is_calc_question and not law_docs and not qa_docs and not residue_docs and not residue_sql_ctx and not law_sql_ctx):
        for d in docs:
            t = f"{d.metadata.get('作物名稱','')} - {d.metadata.get('普通名稱','')}"
            sources.append({"title": t, "url": "https://pesticide.aphia.gov.tw", "date": SOURCE_VERSIONS["pesticide"]})
    for d in law_docs:
        t = f"{d.metadata.get('法規名稱','')}{d.metadata.get('條號','')}"
        law_url = d.metadata.get("來源") or "https://pesticide.aphia.gov.tw/information/Data/Law/1"
        sources.append({"title": t, "url": law_url, "date": SOURCE_VERSIONS["law"]})
    for d in qa_docs:
        t = f"{d.metadata.get('來源類別','')} - {d.metadata.get('分類','')}"
        qa_url = d.metadata.get("來源") or "https://www.acri.gov.tw"
        sources.append({"title": t, "url": qa_url, "date": SOURCE_VERSIONS["qa"]})
    for d in residue_docs:
        t = f"農藥殘留容許量標準（{d.metadata.get('用途分類','')}）"
        residue_url = d.metadata.get("來源") or "https://consumer.fda.gov.tw"
        sources.append({"title": t, "url": residue_url, "date": SOURCE_VERSIONS["residue"]})

    if compliance:
        context = compliance_context(compliance) + "\n\n" + context

    if crop_alias_used:
        alias, official = crop_alias_used
        context = f"（提醒：使用者問的「{alias}」，在本資料庫的正式登記名稱是「{official}」，兩者是同一種作物，下面的「{official}」資料就是「{alias}」的專屬資料，不是替代品或相近作物的資料。）\n\n" + context

    return {"context": context, "sources": sources, "q_normalized": q_normalized, "is_calc_question": is_calc_question,
            "crop": crop, "pest": matched_pest, "cards": cards, "compliance": compliance}


SYSTEM_PROMPT = r"""你是「農藥博士」，角色就像農會裡經驗豐富的植物保護指導員（植物醫師），正在田邊面對面回答農友的問題。農友要的不是一份清單，而是「我現在該怎麼做」。請根據知識庫內容，用繁體中文回答。

【最優先規則，絕對不可違反】回答只能是純文字，絕對不要使用任何 markdown 語法：不要用 **粗體**、不要用 ### 標題、不要用 - 或 * 開頭的項目符號、不要用 1. 2. 3. 這種編號清單搭配粗體標籤。需要條列時，一律用「・」開頭的純文字。

【回答的順序：像指導員一樣先講結論，再講做法】
1. 第一句直接回答農友最想知道的事：能不能用、該用什麼、大概要怎麼處理。不要先鋪陳背景，也不要用「根據知識庫」「以下是」這類開頭。
2. 接著給「現在可以怎麼做」：從知識庫裡挑 2～3 種最值得推薦的藥劑，用一句話說明為什麼推薦，例如安全採收期比較短、適合快要採收的時候用，或是作用方式不同、可以輪流使用避免抗藥性。每種藥只要帶出最關鍵的一兩個數字（通常是稀釋倍數和安全採收期）。
3. 系統會在回答下方用「處方卡」完整列出每一種藥的稀釋倍數、用量、施藥間隔、注意事項，所以不要把所有藥劑和所有欄位逐一列完。若知識庫標明「資料庫共 N 種」，用一句話告訴農友總共有 N 種登記用藥，完整清單可以看下方處方卡，不要謊報總數。
4. 補一句田間實務提醒，挑和這個問題最相關的就好，例如：不同成分輪流用比較不會產生抗藥性、避開中午高溫和下雨前噴藥、噴藥要穿戴防護、採收前一定要算好安全採收期。這類一般安全用藥觀念可以說，但絕對不可以自己編出知識庫沒有的稀釋倍數、用量、天數等數字。
5. 如果農友的問題缺少關鍵資訊，在最後用一句話反問，幫他下一步問得更準。例如：沒說是什麼病蟲害就問「葉子上是看到蟲，還是斑點、白粉？」；推薦藥劑時若不知道離採收還有多久，就問「離採收大概還有幾天？我可以幫你挑安全採收期夠短的藥」。資訊已經足夠時就不用反問。

【連續對話】前面的對話只用來理解農友在問什麼、接續話題，不要重複上一輪已經講過的內容。藥劑和數字一律以這一輪提供的知識庫為準，前面對話裡的數字不能拿來當作新答案的依據。

【語氣】
・像在跟農友聊天：句子短、口語、台灣農村常用的說法，例如「這款藥」「噴」「採收前幾天要停藥」，不要公文腔，也不要客服腔。
・可以自然地稱呼對方「農友」或「你」，但不要每次都用一樣的開頭和結尾，也不要每次都說「隨時可以問我」。
・一般回答控制在 4～8 句，簡單問題可以更短，不要長篇大論。
・沿用農友提問時用的作物或農藥說法（農友說「空心菜」就回答「空心菜」），不要擅自換成正式登記名稱（例如「蕹菜」），除非農友自己就是用正式名稱發問。

【名詞白話解釋】回答中若出現下列農業專有名詞，請在該名詞後面用括號補上簡短的白話解釋（例如「分蘗期（稻子長出分枝新莖的時期）」），讓不熟悉農業的使用者也看得懂。同一個名詞在同一則回答裡只需解釋一次，不用每次都補。若回答中沒出現這些詞就不用勉強加。對照表如下：
・分蘗期：稻子等禾本科作物長出分枝、新莖的時期
・萌芽期：種子或芽剛開始冒出、發芽的時期
・抽穗期：稻麥等作物抽出花穗的時期
・孕穗期：稻麥花穗還包在葉鞘裡、即將抽出的時期
・幼苗期：作物剛長出、還很小的階段
・幼果期：開花後果實剛形成、還很小的時期
・著果期：果實開始穩定長成的時期
・開花初期：作物剛開始開花的階段
・生育期：作物從發芽到成熟的整個生長過程
・落花後：花謝掉之後的階段
・安全採收期：最後一次施藥後，需間隔幾天才能採收食用，以確保農藥殘留降到安全值
・施藥間隔：兩次施藥之間至少要間隔的天數
・稀釋倍數：農藥原液加水稀釋的比例，數字越大代表加的水越多、藥液越淡
・有效成分：農藥中真正發揮防治作用的化學物質
・系統性藥劑：會被植物吸收、隨汁液傳輸到全株的藥劑
・接觸性藥劑：需直接接觸到病蟲害才有效、不會被植物吸收的藥劑
・輪替用藥：交替使用不同作用機制的農藥，避免病蟲害產生抗藥性
・套袋：用紙袋或塑膠袋把果實包起來，保護果實不受病蟲害或藥劑影響

若問題是農藥合理使用的一般性觀念（如抗藥性、混用、施藥時機、法規等），先用一句話講結論，再用兩三句自然的話說清楚怎麼做，不需要套用清單格式。

若題目包含多個子問題（標示為「1.」「2.」等），請針對每一個子問題分別回答，不要只回答其中一題就結束；子問題之間用簡短的過渡語或分段即可，不必使用「【子問題1】」這類制式標題。

【用藥合法性檢查】若知識庫開頭有「【用藥合法性檢查：未登記】」，第一句必須明確說「不可以」，並用一句話說明原因（這款藥沒有登記用在這個作物，用了可能違規，採收的作物也可能被驗出不合格），不得提供該藥用於該作物的任何用法；接著像指導員一樣，建議改用這個作物有登記的藥劑或洽詢當地農會、植物醫師，若知道農友想防治的病蟲害，可以直接點出替代藥劑。若是「【用藥合法性檢查：已登記】」，第一句先說可以用、有登記，再提醒稀釋倍數與安全採收期要照標示。

規則：藥劑名稱、稀釋倍數、用量、天數、法規條文只用知識庫資料，不推測；一般安全用藥觀念的提醒可以補充。繁體中文。若引用法規，需標明條號（如「依農藥管理法第29條」）。無資料則回「知識庫無此資訊，建議撥打 0800-022228」。若是多個子問題，且其中某些子問題有資料、某些沒有，請針對有資料的子問題正常回答，針對沒有資料的子問題單獨註明「該部分知識庫無此資訊」，不要因為其中一題沒資料就整體拒答。

【重要：對照提醒優先於「找不到完全對應」的判斷】如果下方知識庫內容開頭出現「（提醒：使用者問的『XX』，在本資料庫的正式登記名稱是『YY』...）」這樣的對照說明，代表資料庫檢索系統已經確認這兩個是同一種作物、資料是完全對應的，請直接把後面列出的資料當成使用者所問作物的專屬資料正常回答，不要再說「沒有專屬資料」或「以下提供相近資料參考」這類保留語氣，就像資料庫裡本來就叫這個名字一樣直接回答即可。

【最高原則：有撈到資料就直接回答，不要因為用字差異而說「沒有專屬資料」】判斷「有沒有這個作物的資料」時，唯一的標準是「下方知識庫內容裡有沒有實際的登記藥劑資料」，而不是「使用者的用字跟資料庫名稱像不像」。只要下方 context 裡出現了實際的農藥、稀釋倍數、使用時期等登記資料，就代表系統已經成功對應到使用者要問的作物，請直接、有自信地把這些資料當成該作物的正式登記資料回答，沿用使用者的說法。使用者常會多打或少打字、用俗名、語順不同（例如「木瓜類／木瓜的病／木瓜」其實都是問「木瓜」；「芒果／檬果」是同一種），這些都不算「找不到」，絕對不要因此加上「目前資料庫沒有XX的專屬登記資料，以下僅供參考」這類保留語氣。只有在下方 context 裡「完全沒有任何相關登記藥劑」時，才適用下面的拒答或參考語氣規則。

【重要：真的找不到完全對應的作物名稱時，不要直接拒答】若使用者問的作物在知識庫裡沒有完全對應名稱的記錄、也沒有出現上述的對照提醒，但檢索到語意相近的其他資料（例如同樣是葉菜類、防治對象相同的病蟲害、其他作物的同類農藥使用方式），請不要因為名稱對不上就直接回「知識庫無此資訊」。應該：
1. 先誠實告知：「目前資料庫沒有『使用者問的作物』的專屬登記資料，以下提供其他相近作物的農藥使用資訊供參考，實際用藥仍建議洽詢當地農會或撥打 0800-022228 確認」。
2. 接著正常列出檢索到的相近資料（農藥名稱、稀釋倍數、使用時期、安全採收期等）。
只有在完全沒有檢索到任何相關資料、或問題本身與農藥/農業完全無關時，才單純回覆「知識庫無此資訊，建議撥打 0800-022228」，不要附加其他內容。

【重要：主題判斷優先於檢索內容】在使用下方提供的知識庫內容之前，請先判斷「使用者的問題本身」是否確實與農藥、病蟲害防治、農業實務相關。
- 若問題與農藥/農業「完全無關」（例如：純數學/代數題目、與農業無關的一般常識、考試/學術題目、與農藥無涉的計算），即使下方仍提供了一些檢索到的知識庫片段，也一律回答「知識庫無此資訊，建議撥打 0800-022228」，不要因為檢索系統剛好找到幾筆看似相關但實際無關的內容，就用你自己的通用知識去回答這類問題。
- 但是，若問題「本身」明確屬於農藥領域（例如：藥液調配、稀釋倍數計算、施藥面積換算、農藥用量計算、病蟲害防治相關計算等），即使知識庫檢索沒有找到直接支援的資料，仍應正常運用你的數學/邏輯推理能力完整作答，不可拒答——這類農藥領域的計算題本來就不是靠知識庫檢索回答，而是靠你自身的推理能力，請依前面【格式規則】與【計算範例】的指示逐步計算並給出完整答案。
- 判斷標準很簡單：問題的「主詞/情境」是不是在講農藥、施藥、病蟲害、作物防治？是的話就算沒有檢索資料也要回答；完全不是（如單純數學課本題目、與農業無關的情境）才拒答。

若問題涉及數學計算（如稀釋倍數、ppm換算、藥液調配量、單位換算等）：
- 務必逐步列出計算式，每一步驟都明確標示單位（公升、毫升、公克、ppm等），不可跳步驟。
- ppm（百萬分之一）之標準定義為「每公升幾毫克」（1 ppm = 1 mg/L），計算時務必依此定義，不可自行創造其他換算方式。
- 若題目明確指示「改用某種計算方法」（如「改用倍數法」），需完全依該方法計算，不可與其他方法（如標籤標示用量）混用或疊加計算。
- 計算完成後，請自行檢查結果是否合理（例如藥液原液量是否符合實務上的常見範圍），若結果明顯不合理（過大或過小），請重新檢查每一步驟的單位換算是否正確。

【計算範例，示範正確的推理方式】
題目：「1甲≈0.97公頃≈10分地」，某田地0.5甲，每分地用水100L，求總用水量。
正確作法：題目給的「1甲≈10分地」是可以直接使用的換算關係，不需要繞道先換算成公頃再換算回分地（那樣反而會多套用0.97的誤差，算錯）。
　　0.5甲 × 10分地/甲 = 5分地
　　5分地 × 100L/分地 = 500L
※ 重點：題目中若同時給出「A≈B≈C」這種多個單位的平行換算關係，永遠選擇「最直接」的那一條換算路徑計算，不要繞道其他單位再換算回來。
※ 重點：若題目明確指示「改用某方法」，就只用該方法算到底，絕不可以與其他方法（如標籤建議用量）混合或疊加使用。

【格式規則】回答一律使用純文字，不要使用 markdown 符號（如 **粗體**、### 標題、- 項目符號）或 LaTeX 數學符號（如 \[ \] 、\text{}）。條列項目請用「・」或直接換行呈現，計算步驟直接用文字說明（例如「總用水量 = 12分地 × 150L/分地 = 1800L」），不要用反斜線或特殊排版符號。"""


@router.post("/api/ask", tags=["RAG 問答"], summary="農藥知識問答")
async def ask(req: AskRequest):
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY 未設定")
    if not os.path.exists(CHROMA_PATH):
        raise HTTPException(503, "向量資料庫尚未建立，請先執行 build_index.py")
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        original_q = req.question.strip()
        history = _history_lines(req.history)

        # 1) 主題判斷＋追問改寫：無關問題直接回「查無資訊」，閒聊簡短回應，不進檢索
        route = route_question(original_q, req.history)
        if route["type"] == "off_topic":
            return {"answer": OFF_TOPIC_ANSWER, "sources": [], "isRefusal": True,
                    "crop": None, "pest": None, "cards": [], "compliance": []}
        if route["type"] == "chitchat":
            reply = route["reply"] or "不客氣！有任何作物病蟲害、用藥或安全採收期的問題，隨時問我。"
            return {"answer": reply, "sources": [], "isRefusal": False,
                    "crop": req.prev_crop, "pest": req.prev_pest, "cards": [], "compliance": []}

        # 2) 用改寫後的完整問題去檢索（追問已補上作物／病蟲害，換作物也能正確切換）
        q = route["question"] if history else original_q
        sub_questions = split_questions(q)
        # 只有「單一問句」時才套用接續對話的作物沿用；多個子問題時各自獨立處理。
        if len(sub_questions) == 1:
            results = [retrieve_for_question(sub_questions[0], prev_crop=req.prev_crop, prev_pest=req.prev_pest,
                                             is_followup=req.is_followup and not history)]
        else:
            results = [retrieve_for_question(sq) for sq in sub_questions]

        cards = [c for r in results for c in (r.get("cards") or [])][:24]
        compliance = [r["compliance"] for r in results if r.get("compliance")]

        if len(sub_questions) == 1:
            context = results[0]["context"]
            question_for_llm = sub_questions[0]
            sources = results[0]["sources"]
            is_calc_question = results[0]["is_calc_question"]
        else:
            context = "\n\n".join(
                f"【子問題{i}：{r['q_normalized']}】\n{r['context'] or '（此子問題無相關檢索資料）'}"
                for i, r in enumerate(results, 1)
            )
            question_for_llm = "\n".join(f"{i}. {sq}" for i, sq in enumerate(sub_questions, 1))
            sources = [s for r in results for s in r["sources"]]
            is_calc_question = any(r["is_calc_question"] for r in results)

        # 回答模型：預設 gpt-4o（口語自然度較佳）；想省成本可在 Fly secrets 設 ANSWER_MODEL=gpt-4o-mini
        model_name = "gpt-4o" if is_calc_question else os.environ.get("ANSWER_MODEL", "gpt-4o")
        # 計算題維持 temperature 0 確保穩定；一般問答稍微放寬，讓語氣不那麼制式（數字仍以處方卡為準）
        llm = ChatOpenAI(model=model_name, temperature=0 if is_calc_question else 0.4, openai_api_key=OPENAI_API_KEY)
        # 3) 把最近幾輪對話一起交給模型，回答才會接得上前文（數字仍只能來自這一輪的知識庫）
        from langchain_core.messages import AIMessage
        convo_msgs = [HumanMessage(content=t) if r == "user" else AIMessage(content=t) for r, t in history[-6:]]
        asked = original_q if question_for_llm == original_q else f"{original_q}\n（完整意思：{question_for_llm}）"
        resp = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            *convo_msgs,
            HumanMessage(content=f"知識庫：\n{context}\n\n問題：{asked}"),
        ])
        answer = resp.content
        REFUSAL_PHRASE = "知識庫無此資訊，建議撥打"
        is_refusal = REFUSAL_PHRASE in answer and len(answer) < 60

        if is_refusal:
            sources = []
            cards = []
            compliance = []
        else:
            seen = set()
            deduped_sources = []
            for s in sources:
                if s["title"] not in seen:
                    seen.add(s["title"])
                    deduped_sources.append(s)
            sources = deduped_sources

        # 把這一輪實際鎖定的作物回傳給前端，前端記住後，下一題若是「還有哪些」就能接續。
        current_crop = results[0].get("crop") if len(sub_questions) == 1 else None
        current_pest = results[0].get("pest") if len(sub_questions) == 1 else None
        return {"answer": answer, "sources": sources, "isRefusal": is_refusal, "crop": current_crop, "pest": current_pest,
                "cards": cards, "compliance": compliance}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── 農藥資料查詢 ──────────────────────────────────────────────
@router.get("/api/pesticides", tags=["農藥資料"], summary="查詢農藥列表")
def get_pesticides(crop: Optional[str] = Query(None), type: Optional[str] = Query(None), name: Optional[str] = Query(None), limit: int = Query(50, le=200), username: str = Depends(get_current_user)):
    conn = get_db()
    conds, params = [], []
    if crop: conds.append("作物名稱 = ?"); params.append(crop)
    if name: conds.append("農藥中文普通名稱 LIKE ?"); params.append(f"%{name}%")
    if type: conds.append("病蟲害名稱 LIKE ?"); params.append(f"%{type}%")
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = conn.execute(f"SELECT id,作物名稱,病蟲害名稱,農藥中文普通名稱,農藥含量,稀釋倍數,使用時期,安全採收期_天,施藥間隔,施用次數,注意事項,施用方法 FROM pesticides {where} LIMIT ?", params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/pesticides/crops", tags=["農藥資料"], summary="取得所有作物名稱")
def get_crops(username: str = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT 作物名稱 FROM pesticides ORDER BY 作物名稱").fetchall()
    conn.close()
    return {"作物清單": [r[0] for r in rows], "總數": len(rows)}


@router.get("/api/pesticides/search", tags=["農藥資料"], summary="農藥關鍵字搜尋")
def search_pesticides(q: str = Query(...), username: str = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT id,作物名稱,病蟲害名稱,農藥中文普通名稱,農藥含量,安全採收期_天,使用時期,注意事項 FROM pesticides WHERE 農藥中文普通名稱 LIKE ? OR 作物名稱 LIKE ? OR 病蟲害名稱 LIKE ? LIMIT 30", (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return {"關鍵字": q, "結果": [dict(r) for r in rows], "筆數": len(rows)}


@router.get("/api/law/search", tags=["法規資料"], summary="農藥管理法條文搜尋")
def search_law(q: str = Query(...), username: str = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id,法規名稱,章節,條號,條文內容,版本日期 FROM regulations WHERE 條文內容 LIKE ? OR 條號 = ? LIMIT 20", (f"%{q}%", q)).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        raise HTTPException(503, "法規資料表尚未建立，請先執行 scrape_law.py → build_law_db.py")
    conn.close()
    return {"關鍵字": q, "結果": [dict(r) for r in rows], "筆數": len(rows)}


# ── 模擬考 ────────────────────────────────────────────────────
@router.get("/api/quiz/generate", tags=["模擬考"], summary="自動產生模擬考題目")
def generate_quiz(category: str = Query("all"), count: int = Query(10), username: str = Depends(get_current_user)):
    import random
    conn = get_db()
    questions = []

    def distractors(correct, pool, n=3):
        opts = [p for p in set(pool) if p and p != correct]
        random.shuffle(opts)
        return opts[:n]

    try:
        if category in ("all", "safety"):
            rows = conn.execute(
                "SELECT 作物名稱,農藥中文普通名稱,安全採收期_天 FROM pesticides "
                "WHERE 安全採收期_天 != '' AND 安全採收期_天 IS NOT NULL AND 安全採收期_天 != '-' "
                "ORDER BY RANDOM() LIMIT 60"
            ).fetchall()
            all_days = [str(r["安全採收期_天"]) for r in rows]
            for r in rows:
                correct = f"{r['安全採收期_天']} 天"
                wrong = [f"{d} 天" for d in distractors(str(r["安全採收期_天"]), all_days)]
                if len(wrong) < 3:
                    continue
                opts = wrong + [correct]
                random.shuffle(opts)
                questions.append({
                    "question": f"「{r['作物名稱']}」施用「{r['農藥中文普通名稱']}」時，安全採收期為幾天？",
                    "options": opts,
                    "answer_index": opts.index(correct),
                    "explanation": f"依農藥登記資料，{r['作物名稱']}施用{r['農藥中文普通名稱']}之安全採收期為 {r['安全採收期_天']} 天。",
                    "category": "安全採收期",
                    "source": "https://pesticide.aphia.gov.tw",
                })

        if category in ("all", "pest"):
            rows = conn.execute(
                "SELECT 作物名稱,病蟲害名稱,農藥中文普通名稱 FROM pesticides "
                "WHERE 病蟲害名稱 != '' AND 農藥中文普通名稱 != '' ORDER BY RANDOM() LIMIT 60"
            ).fetchall()
            all_pest = [r["農藥中文普通名稱"] for r in rows]
            for r in rows:
                correct = r["農藥中文普通名稱"]
                wrong = distractors(correct, all_pest)
                if len(wrong) < 3:
                    continue
                opts = wrong + [correct]
                random.shuffle(opts)
                questions.append({
                    "question": f"防治「{r['作物名稱']}」的「{r['病蟲害名稱']}」，下列何者為登記可用之農藥？",
                    "options": opts,
                    "answer_index": opts.index(correct),
                    "explanation": f"依農藥登記資料，{r['作物名稱']}之{r['病蟲害名稱']}可使用{correct}防治。",
                    "category": "植物保護",
                    "source": "https://pesticide.aphia.gov.tw",
                })

        if category in ("all", "law"):
            try:
                rows = conn.execute(
                    "SELECT 法規名稱,條號,條文內容 FROM regulations "
                    "WHERE length(條文內容) > 15 ORDER BY RANDOM() LIMIT 30"
                ).fetchall()
                all_laws = [r["法規名稱"] for r in rows]
                for r in rows:
                    correct = r["法規名稱"]
                    wrong = distractors(correct, all_laws + ["農藥管理法", "農藥許可證申請及核發辦法", "農藥管理人員訓練及管理辦法"])
                    if len(wrong) < 3:
                        continue
                    opts = wrong + [correct]
                    random.shuffle(opts)
                    excerpt = r["條文內容"][:45].strip()
                    questions.append({
                        "question": f"下列條文「{excerpt}…」出自哪一部法規？",
                        "options": opts,
                        "answer_index": opts.index(correct),
                        "explanation": f"此條文出自《{correct}》{r['條號']}。",
                        "category": "農藥法規",
                        "source": "https://pesticide.aphia.gov.tw/information/Data/Law/1",
                    })
            except sqlite3.OperationalError:
                pass

        if category in ("all", "term"):
            try:
                rows = conn.execute(
                    "SELECT 問題,答案,分類 FROM qa_knowledge "
                    "WHERE length(答案) > 10 AND length(答案) < 80 ORDER BY RANDOM() LIMIT 30"
                ).fetchall()
                all_ans = [r["答案"] for r in rows]
                for r in rows:
                    correct = r["答案"]
                    wrong = distractors(correct, all_ans)
                    if len(wrong) < 3:
                        continue
                    opts = wrong + [correct]
                    random.shuffle(opts)
                    questions.append({
                        "question": r["問題"],
                        "options": opts,
                        "answer_index": opts.index(correct),
                        "explanation": "參考資料來源：農藥合理使用問答集。",
                        "category": "名詞與定義",
                        "source": "https://www.acri.gov.tw",
                    })
            except sqlite3.OperationalError:
                pass

    finally:
        conn.close()

    random.shuffle(questions)
    return {"題目": questions[:count]}


# ── 今日新聞 ──────────────────────────────────────────────────
@router.get("/api/news", tags=["新聞"], summary="今日農藥新聞", response_model=list[NewsItem])
async def get_news(username: str = Depends(get_current_user)):
    today = str(date.today())
    if _news_cache["date"] == today and _news_cache["data"]:
        return _news_cache["data"]
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY 未設定")
    try:
        ANTHROPIC_API_KEY.encode("ascii")
    except UnicodeEncodeError:
        raise HTTPException(500, "ANTHROPIC_API_KEY 格式不正確（可能還是 .env 裡的預留文字，例如「你的key」，請換成真正的 API 金鑰）")
    payload = {"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "tools": [{"type": "web_search_20250305", "name": "web_search"}], "system": "搜尋台灣農藥新聞並回傳 JSON。", "messages": [{"role": "user", "content": f"搜尋 {today} 台灣農藥相關新聞3則，只回傳JSON：[{{\"title\":\"\",\"url\":\"\",\"source\":\"\",\"date\":\"\",\"tag\":\"法規公告或食安議題或研究新知\"}}]"}]}
    headers = {"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = " ".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    match = re.search(r"\[[\s\S]*?\]", text)
    if not match:
        raise HTTPException(502, "無法解析新聞 JSON")
    news = json.loads(match.group())[:3]
    _news_cache.update({"date": today, "data": news})
    return news