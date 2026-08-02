"""農藥 RAG 系統後端 - FastAPI + LangChain + ChromaDB + SQLite"""
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import date
from typing import Optional
import sqlite3, os, json, re, httpx, hashlib, secrets
import jieba
from rank_bm25 import BM25Okapi
from dotenv import load_dotenv
load_dotenv()

OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DB_PATH           = os.path.join(os.path.dirname(__file__), "pesticides.db")
CHROMA_PATH       = os.path.join(os.path.dirname(__file__), "chroma_db")

app = FastAPI(title="農藥知識問答系統 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_news_cache: dict = {"date": None, "data": None}
_retriever = None

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

_vectorstore = None
_law_vectorstore = None

def get_vectorstore():
    global _vectorstore
    if _vectorstore: return _vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    _vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="pesticides")
    return _vectorstore

def get_law_vectorstore():
    global _law_vectorstore
    if _law_vectorstore: return _law_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _law_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="regulations")
    except Exception:
        _law_vectorstore = None
    return _law_vectorstore

_qa_vectorstore = None

def get_qa_vectorstore():
    global _qa_vectorstore
    if _qa_vectorstore: return _qa_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _qa_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="qa_knowledge")
    except Exception:
        _qa_vectorstore = None
    return _qa_vectorstore

_residue_vectorstore = None

def get_residue_vectorstore():
    global _residue_vectorstore
    if _residue_vectorstore: return _residue_vectorstore
    from langchain_openai import OpenAIEmbeddings
    from langchain_chroma import Chroma
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_API_KEY)
    try:
        _residue_vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings, collection_name="residue_limits")
    except Exception:
        _residue_vectorstore = None
    return _residue_vectorstore


# ═══════════════════════════════════════════════════
# BM25 混合搜尋：關鍵字統計比對，補強語意向量搜尋
# 對「農藥商品名」「精確詞彙」比對特別有效，不需要呼叫 OpenAI API
# ═══════════════════════════════════════════════════
_bm25_cache = {}

def get_bm25_index(table_name: str, fetch_columns: list, score_columns: list):
    """建立（或取得快取的）指定資料表的 BM25 索引。
    fetch_columns：SQL 要撈取的完整欄位（供之後重建內容/metadata 用）
    score_columns：實際拿去斷詞、計算 BM25 分數的欄位（通常是 fetch_columns 的子集，
                   刻意排除像「法規名稱」這種同一部法規每條都重複、會灌水分數的欄位）
    首次呼叫時會讀取全表、用 jieba 斷詞、建立索引並快取在記憶體，
    之後同一個 table_name 直接使用快取，不會重複建置。"""
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
    """用 BM25 對指定資料表做關鍵字排序搜尋，回傳前 k 筆原始資料列（dict 格式，含完整 fetch_columns）"""
    index = get_bm25_index(table_name, fetch_columns, score_columns)
    if not index:
        return []
    bm25, docs_meta = index
    query_tokens = list(jieba.cut(query))
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [docs_meta[i] for i in ranked[:k] if scores[i] > 0]


def reciprocal_rank_fusion(rank_lists: list, k: int = 60, top_n: int = 5):
    """排序融合演算法（RRF）：把多組不同方法（BM25、語意向量）各自的排序結果，
    依名次（不是原始分數，避免不同方法的分數尺度不一致）加權合併成單一排序。
    rank_lists: [[id1, id2, ...], [id3, id1, ...], ...]（每個子清單是該方法的排序結果，由高到低）
    回傳融合後的 id 排序（取前 top_n 個）"""
    scores = {}
    for rank_list in rank_lists:
        for rank, item_id in enumerate(rank_list):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_n]


class SimpleDoc:
    """輕量文件物件，統一 BM25 重建結果與向量搜尋 Document 的介面（page_content / metadata）"""
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


# 各資料表的 BM25 欄位設定：
# fetch_columns：SQL 要撈的完整欄位（供內容/metadata 重建用）
# score_columns：實際拿去斷詞計分的欄位（刻意排除「法規名稱」等同一法規每條重複、會灌水分數的欄位）
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
    """混合搜尋：BM25 關鍵字排序 + 語意向量排序，用 RRF 融合後回傳前 k 筆 SimpleDoc。
    若 vectorstore 為 None（collection 不存在），退化為純 BM25。

    guaranteed_vector_slots：保留給「純向量搜尋」最頂尖結果的名額，不經過 RRF 篩選。
    這是因為 BM25 只認字面（例如看不懂「期限」跟「期間」是同義詞），
    若某條法規恰好在字面上跟其他不相關法規重疊較多，BM25 可能把語意上真正
    正確的答案擠出前幾名；保留幾個「向量搜尋保底名額」可以避免這種語意理解
    被字面雜訊蓋過的狀況，其餘名額才用 RRF 融合，兼顧多樣性與精確關鍵字比對。"""
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

    # 向量搜尋回傳的 metadata 不一定有 "id" 欄位（實測發現 pesticides collection 就沒有），
    # 若直接用 metadata.get("id") 篩選，這些真正吻合作物篩選(vector_filter)的結果會被整批當成
    # 「沒有 id」而濾掉，導致 vector_ids 變空、guaranteed_vector_slots 保底機制完全失效，
    # 最後退化成沒有作物篩選的純 BM25 搜尋（就是這裡才會抓到不相關的籠統分類，例如「蔬菜」）。
    # 修法：沒有 id 時就用內容本身當作穩定的替代 key。
    def _doc_key(d):
        doc_id = d.metadata.get("id")
        return doc_id if doc_id is not None else f"vec::{d.page_content}"

    # 建立 id -> (content, metadata) 對照表，向量搜尋結果優先（內容已經是預先組好的完整版本）
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

    # 先保留向量搜尋最頂尖的幾筆（不經過 RRF 篩選，確保語意理解不會被字面雜訊蓋過）
    final_ids = list(vector_ids[:guaranteed_vector_slots])
    # 剩餘名額用 RRF 融合補上，並排除已經保底納入的 id
    remaining_slots = max(k - len(final_ids), 0)
    if remaining_slots:
        fused_ids = reciprocal_rank_fusion([vector_ids, bm25_ids], top_n=k + guaranteed_vector_slots)
        for fid in fused_ids:
            if fid not in final_ids:
                final_ids.append(fid)
            if len(final_ids) >= k:
                break

    return [SimpleDoc(pool[i][0], pool[i][1]) for i in final_ids[:k] if i in pool]

class AskRequest(BaseModel):
    question: str

class NewsItem(BaseModel):
    title: str; url: str; source: str; date: str; tag: str

class AuthRequest(BaseModel):
    username: str
    password: str


# ═══════════════════════════════════════════════════
# 帳號密碼登入：使用者資料表 + 密碼雜湊 + 登入憑證（session token）
# ═══════════════════════════════════════════════════
def init_auth_tables():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


def hash_password(password: str, salt: str = None):
    """用 PBKDF2-HMAC-SHA256 加鹽雜湊密碼，避免資料庫存明碼。"""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return salt, digest.hex()


def get_current_user(authorization: str = Header(None)):
    """從 Authorization: Bearer <token> 標頭驗證登入狀態，驗證失敗回 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登入或憑證已失效，請重新登入")
    token = authorization.split(" ", 1)[1].strip()
    conn = get_db()
    row = conn.execute("SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "未登入或憑證已失效，請重新登入")
    return row["username"]


@app.post("/api/auth/register", tags=["帳號"], summary="註冊新帳號")
def register(req: AuthRequest):
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(400, "帳號與密碼皆不可為空")
    if len(req.password) < 4:
        raise HTTPException(400, "密碼長度至少需要 4 個字元")
    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(400, "此帳號已被註冊，請直接登入或換一個帳號")
    salt, pw_hash = hash_password(req.password)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, str(date.today())),
    )
    conn.commit()
    conn.close()
    return {"message": "註冊成功，請登入"}


@app.post("/api/auth/login", tags=["帳號"], summary="登入")
def login(req: AuthRequest):
    username = req.username.strip()
    conn = get_db()
    row = conn.execute("SELECT password_hash, salt FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(401, "帳號或密碼錯誤")
    _, pw_hash = hash_password(req.password, row["salt"])
    if pw_hash != row["password_hash"]:
        conn.close()
        raise HTTPException(401, "帳號或密碼錯誤")
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, username, created_at) VALUES (?, ?, ?)",
        (token, username, str(date.today())),
    )
    conn.commit()
    conn.close()
    return {"token": token, "username": username}


@app.post("/api/auth/logout", tags=["帳號"], summary="登出")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
    return {"message": "已登出"}


@app.get("/api/auth/me", tags=["帳號"], summary="取得目前登入的使用者")
def me(username: str = Depends(get_current_user)):
    return {"username": username}


DASHBOARD = open(os.path.join(os.path.dirname(__file__), "dashboard.html"), encoding="utf-8").read() if os.path.exists(os.path.join(os.path.dirname(__file__), "dashboard.html")) else "<h1>Dashboard</h1>"

@app.get("/dashboard", include_in_schema=False)
def dashboard(): return HTMLResponse(content=DASHBOARD)

@app.on_event("startup")
async def prewarm_bm25_indexes():
    """伺服器啟動時就在背景執行緒預先建好所有 BM25 索引，
    避免第一個使用者發問時卡在建索引（尤其農藥用量表 52,000+ 筆較耗時）"""
    import threading

    def _build_all():
        for table_name, cfg in _TABLE_CONFIG.items():
            try:
                get_bm25_index(table_name, cfg["fetch_columns"], cfg["score_columns"])
                print(f"✅ BM25 索引預建完成：{table_name}")
            except Exception as e:
                print(f"⚠️ BM25 索引預建失敗（{table_name}）：{e}")

    threading.Thread(target=_build_all, daemon=True).start()


@app.on_event("startup")
async def _init_auth_on_startup():
    init_auth_tables()
    print("✅ 帳號登入資料表已就緒")


@app.get("/health", tags=["系統"], summary="系統健康檢查")
def health(): return {"狀態": "正常", "日期": str(date.today())}

@app.get("/api/pesticides", tags=["農藥資料"], summary="查詢農藥列表")
def get_pesticides(crop: Optional[str]=Query(None), type: Optional[str]=Query(None), name: Optional[str]=Query(None), limit: int=Query(50, le=200), username: str = Depends(get_current_user)):
    conn = get_db()
    conds, params = [], []
    if crop: conds.append("作物名稱 = ?"); params.append(crop)
    if name: conds.append("農藥中文普通名稱 LIKE ?"); params.append(f"%{name}%")
    if type: conds.append("病蟲害名稱 LIKE ?"); params.append(f"%{type}%")
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = conn.execute(f"SELECT id,作物名稱,病蟲害名稱,農藥中文普通名稱,農藥含量,稀釋倍數,使用時期,安全採收期_天,施藥間隔,施用次數,注意事項,施用方法 FROM pesticides {where} LIMIT ?", params+[limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/pesticides/crops", tags=["農藥資料"], summary="取得所有作物名稱")
def get_crops(username: str = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT 作物名稱 FROM pesticides ORDER BY 作物名稱").fetchall()
    conn.close()
    return {"作物清單": [r[0] for r in rows], "總數": len(rows)}

@app.get("/api/pesticides/search", tags=["農藥資料"], summary="農藥關鍵字搜尋")
def search_pesticides(q: str=Query(...), username: str = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute("SELECT id,作物名稱,病蟲害名稱,農藥中文普通名稱,農藥含量,安全採收期_天,使用時期,注意事項 FROM pesticides WHERE 農藥中文普通名稱 LIKE ? OR 作物名稱 LIKE ? OR 病蟲害名稱 LIKE ? LIMIT 30", (f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
    conn.close()
    return {"關鍵字": q, "結果": [dict(r) for r in rows], "筆數": len(rows)}

@app.get("/api/law/search", tags=["法規資料"], summary="農藥管理法條文搜尋")
def search_law(q: str=Query(...), username: str = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id,法規名稱,章節,條號,條文內容,版本日期 FROM regulations WHERE 條文內容 LIKE ? OR 條號 = ? LIMIT 20", (f"%{q}%", q)).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        raise HTTPException(503, "法規資料表尚未建立，請先執行 scrape_law.py → build_law_db.py")
    conn.close()
    return {"關鍵字": q, "結果": [dict(r) for r in rows], "筆數": len(rows)}

def split_questions(q: str) -> list:
    """把合併問句（用問號分隔）拆成多個子問題。若只有一句，回傳單一元素清單，
    行為跟拆解前完全一致，不影響原本單一問題的處理流程。"""
    parts = re.split(r'(?<=[？?])', q)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if parts else [q]


# 常見俗名 → 官方登記名稱對照（農民常用口語詞，但資料庫是用正式登記名稱）
# 這是暫時性的小型對照表；長期應建立完整的作物別名資料庫
CROP_ALIASES = {
    "空心菜": "蕹菜", "地瓜葉": "甘藷", "地瓜": "甘藷", "番薯": "甘藷",
    "高麗菜": "甘藍", "大陸妹": "結球萵苣", "娃娃菜": "結球白菜", "白菜": "小白菜",
    "紅蘿蔔": "胡蘿蔔", "馬鈴薯": "馬鈴薯", "洋芋": "馬鈴薯", "青蔥": "蔥",
    "蒜頭": "蒜", "小黃瓜": "胡瓜", "花生": "落花生", "毛豆": "大豆",
    "四季豆": "菜豆", "皇帝豆": "萊豆", "甜豆": "豌豆", "荷蘭豆": "豌豆",
    "芭樂": "番石榴", "奇異果": "獼猴桃", "火龍果": "紅龍果", "釋迦": "番荔枝",
    "鱷梨": "酪梨", "水蜜桃": "桃",
}

# 農藥常見商品名／俗稱 → 官方登記通用名對照
# 農藥常有多個名稱（商品名、通用名、成分名），農民慣用名稱未必等於資料庫登記名稱，
# 此對照表比照作物俗名做法，補強農藥名稱查詢之召回率。
# 註：僅保留已實測驗證、確實對應到資料庫登記名稱之項目，避免錯誤對應反而造成查詢失敗
PESTICIDE_ALIASES = {
    "好年冬": "加保扶",      # 已驗證：加保扶（Carbofuran）
    "呋喃丹": "加保扶",      # 加保扶之另一常見俗稱
}

# 各資料來源之資料擷取時間，用於在回答來源標籤上標示資訊時效性
# （呼應計畫書「可溯源、標示版本日期」之規格；標示為實際擷取年份，
#  而非官方公告版本，以確保標示之準確性與誠實性）
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


def retrieve_for_question(q: str) -> dict:
    """對單一問題執行完整檢索流程（作物俗名轉換、SQL精確查詢、BM25+向量混合搜尋），
    回傳 {context, sources, q_normalized, is_calc_question}。
    這個函式被抽出來，讓「合併問句拆解後逐一檢索」可以重複呼叫同一套邏輯，
    不用另外寫一份，也確保單一問題與合併問句子題目的檢索品質完全一致。"""
    conn = get_db()
    crops = [r[0] for r in conn.execute("SELECT DISTINCT 作物名稱 FROM pesticides").fetchall()]

    q_normalized = q
    crop_alias_used = None  # 記錄「使用者說的俗名」→「資料庫正式名稱」的對照，讓 AI 知道兩者是同一個作物
    for alias, official in CROP_ALIASES.items():
        if alias in q and official not in q:
            q_normalized = q_normalized.replace(alias, official)
            crop_alias_used = (alias, official)
    for alias, official in PESTICIDE_ALIASES.items():
        if alias in q_normalized and official != alias and official not in q_normalized:
            q_normalized = q_normalized.replace(alias, official)

    crop = next((c for c in crops if c in q_normalized), None)
    if not crop:
        # 一般比對找不到時，改用「使用者說的別名/正式名稱」是否包含在作物欄位裡來比對，
        # 因應資料庫作物名稱可能帶額外寫法（例如「蕹菜(空心菜)」「蕹菜 台灣蕹菜」），
        # 避免明明有資料卻因為字串比對太嚴格而找不到、被誤判成「知識庫沒有這個作物」
        for alias, official in CROP_ALIASES.items():
            if alias in q:
                crop = next((c for c in crops if official in c or alias in c), None)
                if crop:
                    break
    ptype = next((t for t in ['殺菌','殺蟲','除草','殺螨'] if t in q), None)
    sql_ctx = ""
    sources = []
    if crop and ptype:
        rows = conn.execute("SELECT 農藥中文普通名稱,稀釋倍數,使用時期,安全採收期_天,施藥間隔 FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱 LIKE ? GROUP BY 農藥中文普通名稱 LIMIT 15", (crop, f"%{ptype}%")).fetchall()
        if rows:
            sql_ctx = f"【{crop} {ptype}劑 精確查詢】\n" + "\n".join(f"• {r['農藥中文普通名稱']}：稀釋{r['稀釋倍數']}倍，{r['使用時期']}，採收期{r['安全採收期_天']}天" for r in rows)
            sources.append({"title": f"{crop}{ptype}劑登記資料", "url": "https://pesticide.aphia.gov.tw", "date": SOURCE_VERSIONS["pesticide"]})

    residue_sql_ctx = ""
    pesticide_names = [r[0] for r in conn.execute("SELECT DISTINCT 農藥中文普通名稱 FROM pesticides WHERE 農藥中文普通名稱 != ''").fetchall()]
    matched_pesticide = next((p for p in pesticide_names if p and len(p) >= 2 and p in q), None)
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
        if not docs:
            docs = hybrid_retrieve("pesticides", vs, q_normalized, k=5)
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

    if crop_alias_used:
        alias, official = crop_alias_used
        context = f"（提醒：使用者問的「{alias}」，在本資料庫的正式登記名稱是「{official}」，兩者是同一種作物，下面的「{official}」資料就是「{alias}」的專屬資料，不是替代品或相近作物的資料。）\n\n" + context

    return {"context": context, "sources": sources, "q_normalized": q_normalized, "is_calc_question": is_calc_question}


SYSTEM_PROMPT = r"""你是農藥安全顧問，請根據知識庫內容回答問題，用繁體中文。

【最優先規則，絕對不可違反】回答只能是純文字，絕對不要使用任何 markdown 語法：不要用 **粗體**、不要用 ### 標題、不要用 - 或 * 開頭的項目符號、不要用 1. 2. 3. 這種編號清單搭配粗體標籤。條列內容一律用「・」開頭的純文字呈現，不加任何星號或井字號。

用自然、親切、有溫度的口語化語氣回答，像是在跟來請教問題的農民朋友聊天解釋，而不是在唸公文或罐頭稿。每次回答的開頭、用詞、句子長短、段落安排都可以依問題內容彈性調整，不要每次都套用一模一樣的固定模板、開頭語或結尾語，避免死板生硬、千篇一律的感覺。回答時請沿用使用者提問時用的作物或農藥說法（例如使用者說「空心菜」就回答「空心菜」），不要擅自換成正式登記名稱（例如「蕹菜」），除非使用者自己就是用正式名稱發問。

若問題是「某作物可用哪些農藥」這類查詢，用「・」條列每一種農藥，並用一般敘述句把稀釋倍數、使用時期、安全採收期等重點自然帶進去即可，例如：
・速殺氟：防治蚜蟲類與粉蝨類，稀釋倍數約 14000 倍，害蟲發生時開始施藥，安全採收期 6 天。
不要用粗體標籤、不要每個屬性都另起一行加「-」符號。

若問題是農藥合理使用的一般性觀念（如抗藥性、混用、施藥時機、法規等），用幾句自然的話說清楚即可，不需要套用清單格式。

若題目包含多個子問題（標示為「1.」「2.」等），請針對每一個子問題分別、完整回答，不要只回答其中一題就結束；子問題之間可以用簡短的過渡語或適當分段呈現，不必每次都套用一模一樣的「【子問題1】」制式標題。

只有在真的有實用的補充提醒、或有明確可註記的資料來源時，才視情況自然帶一句注意事項或來源說明，不需要每次都機械式地在結尾附上固定的兩行罐頭文字。

規則：只用知識庫資料，不推測，繁體中文。若引用法規，需標明條號（如「依農藥管理法第29條」）。無資料則回「知識庫無此資訊，建議撥打 0800-022228」。若是多個子問題，且其中某些子問題有資料、某些沒有，請針對有資料的子問題正常回答，針對沒有資料的子問題單獨註明「該部分知識庫無此資訊」，不要因為其中一題沒資料就整體拒答。

【重要：對照提醒優先於「找不到完全對應」的判斷】如果下方知識庫內容開頭出現「（提醒：使用者問的『XX』，在本資料庫的正式登記名稱是『YY』...）」這樣的對照說明，代表資料庫檢索系統已經確認這兩個是同一種作物、資料是完全對應的，請直接把後面列出的資料當成使用者所問作物的專屬資料正常回答，不要再說「沒有專屬資料」或「以下提供相近資料參考」這類保留語氣，就像資料庫裡本來就叫這個名字一樣直接回答即可。

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


@app.post("/api/ask", tags=["RAG 問答"], summary="農藥知識問答")
async def ask(req: AskRequest):
    if not OPENAI_API_KEY: raise HTTPException(500, "OPENAI_API_KEY 未設定")
    if not os.path.exists(CHROMA_PATH): raise HTTPException(503, "向量資料庫尚未建立，請先執行 build_index.py")
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        q = req.question
        sub_questions = split_questions(q)

        # 合併問句處理：把每個子問題各自跑一次完整檢索流程，
        # 再把所有子問題的知識庫內容跟來源合併，一次請 AI 針對每個子問題分別作答，
        # 避免像純單一問題那樣只回答其中一個主題就結束
        results = [retrieve_for_question(sq) for sq in sub_questions]

        # 注意：question_for_llm 用使用者原本輸入的說法（sub_questions），不用 q_normalized
        # （q_normalized 只用於資料庫檢索時比對正式登記名稱，例如「空心菜」→「蕹菜」），
        # 這樣 AI 回答時才會沿用使用者自己的口語說法，而不是把「空心菜」自動換成「蕹菜」
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

        model_name = "gpt-4o" if is_calc_question else "gpt-4o-mini"
        llm = ChatOpenAI(model=model_name, temperature=0, openai_api_key=OPENAI_API_KEY)
        resp = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"知識庫：\n{context}\n\n問題：{question_for_llm}"),
        ])
        answer = resp.content
        # 判斷是否為「真正的拒答」：只有當回答主要就是標準拒答語（建議撥打專線），
        # 才算整題拒答；若只是某些欄位缺值而出現「該部分知識庫無此資訊」之註記，
        # 但整體仍正常回答了內容，則不算拒答，來源標籤應正常顯示
        REFUSAL_PHRASE = "知識庫無此資訊，建議撥打"
        is_refusal = REFUSAL_PHRASE in answer and len(answer) < 60

        if is_refusal:
            sources = []
        else:
            seen = set()
            deduped_sources = []
            for s in sources:
                if s["title"] not in seen:
                    seen.add(s["title"])
                    deduped_sources.append(s)
            sources = deduped_sources

        return {"answer": answer, "sources": sources, "isRefusal": is_refusal}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/quiz/generate", tags=["模擬考"], summary="自動產生模擬考題目")
def generate_quiz(category: str = Query("all"), count: int = Query(10), username: str = Depends(get_current_user)):
    """從現有知識庫（農藥登記資料、法規、問答集）自動抽題組成選擇題。
    category: all(綜合) / law(法規) / pest(植物保護) / safety(安全採收期) / term(名詞與定義)
    回傳格式：{"題目": [{question, options:[...], answer_index, explanation, category, source}, ...]}
    此功能運用既有知識庫資料自動生成試題，屬於知識庫資料之延伸應用。
    注意：內部欄位名稱使用英文（question/options/answer_index/explanation/category/source），
    以對齊前端 Quiz.jsx 的預期格式；answer_index 為正確答案在 options 陣列中的索引（從 0 開始）。"""
    import random
    conn = get_db()
    questions = []

    def distractors(correct, pool, n=3):
        """從 pool 中挑出 n 個與正確答案不同的干擾選項"""
        opts = [p for p in set(pool) if p and p != correct]
        random.shuffle(opts)
        return opts[:n]

    try:
        # 題型一：安全採收期（safety）— 問某作物用某農藥的安全採收期
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

        # 題型二：植物保護（pest）— 問某作物某病蟲害可用哪種農藥
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

        # 題型三：法規（law）— 問某條文出自哪部法規
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

        # 題型四：名詞與定義（term）— 取自問答集，問某問題的正確說明
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


@app.get("/api/news", tags=["新聞"], summary="今日農藥新聞", response_model=list[NewsItem])
async def get_news(username: str = Depends(get_current_user)):
    today = str(date.today())
    if _news_cache["date"] == today and _news_cache["data"]: return _news_cache["data"]
    if not ANTHROPIC_API_KEY: raise HTTPException(500, "ANTHROPIC_API_KEY 未設定")
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
    if not match: raise HTTPException(502, "無法解析新聞 JSON")
    news = json.loads(match.group())[:3]
    _news_cache.update({"date": today, "data": news})
    return news