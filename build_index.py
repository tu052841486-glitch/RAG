# -*- coding: utf-8 -*-
"""
build_index.py — 向量化農藥資料存入 ChromaDB
執行一次即可：python build_index.py
"""
import sqlite3, os, time, shutil
from dotenv import load_dotenv
load_dotenv()

DB_PATH     = os.path.join(os.path.dirname(__file__), "pesticides.db")
CHROMA_PATH = os.path.join(os.path.dirname(__file__), "chroma_db")
OPENAI_KEY  = os.environ.get("OPENAI_API_KEY", "")

if not OPENAI_KEY:
    print("❌ 請先在 .env 設定 OPENAI_API_KEY"); exit(1)

from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT id, 作物名稱, 病蟲害名稱, 農藥中文普通名稱, 農藥含量, 劑型,
           每公頃每次用量, 稀釋倍數, 使用時期, 施藥間隔, 施用次數,
           安全採收期_天, 施用方法, 注意事項, 核准日期, 廠商名稱, 登記分類名稱
    FROM pesticides
""").fetchall()
conn.close()
print(f"✅ 讀取 {len(rows)} 筆資料")


def build_chunk_text(d: dict) -> str:
    """把一筆農藥資料組合成適合語意檢索的自然語言段落"""
    parts = [f"作物：{d.get('作物名稱') or ''}"]
    if d.get("病蟲害名稱"):
        parts.append(f"防治病蟲害：{d['病蟲害名稱']}")
    if d.get("農藥中文普通名稱"):
        parts.append(f"農藥名稱：{d['農藥中文普通名稱']}")
    if d.get("農藥含量"):
        parts.append(f"含量：{d['農藥含量']}")
    if d.get("劑型"):
        parts.append(f"劑型：{d['劑型']}")
    if d.get("每公頃每次用量"):
        parts.append(f"每公頃每次用量：{d['每公頃每次用量']}")
    if d.get("稀釋倍數"):
        parts.append(f"稀釋倍數：{d['稀釋倍數']}")
    if d.get("使用時期"):
        parts.append(f"使用時期：{d['使用時期']}")
    if d.get("施藥間隔"):
        parts.append(f"施藥間隔：{d['施藥間隔']}")
    if d.get("施用次數"):
        parts.append(f"施用次數：{d['施用次數']}")
    if d.get("安全採收期_天"):
        parts.append(f"安全採收期：{d['安全採收期_天']}天")
    if d.get("施用方法"):
        parts.append(f"施用方法：{d['施用方法']}")
    if d.get("注意事項"):
        parts.append(f"注意事項：{d['注意事項']}")
    return "，".join(p for p in parts if p) + "。"


docs = []
for r in rows:
    d = dict(r)
    docs.append(Document(
        page_content=build_chunk_text(d),
        metadata={
            "id": d["id"],
            "作物名稱": d.get("作物名稱") or "",
            "普通名稱": d.get("農藥中文普通名稱") or "",
            "病蟲名稱": d.get("病蟲害名稱") or "",
            "安全採收期": d.get("安全採收期_天") or "",
            "來源": "農藥資訊服務網 2026 版",
        },
    ))

embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_KEY)

if os.path.exists(CHROMA_PATH):
    shutil.rmtree(CHROMA_PATH)
    print("🗑️  清除舊向量資料庫")

BATCH = 100
vectorstore = None
total = len(docs) // BATCH + 1
for i in range(0, len(docs), BATCH):
    batch = docs[i:i+BATCH]
    print(f"  批次 {i//BATCH+1}/{total}（{i+1}~{min(i+BATCH,len(docs))} 筆）...")
    if vectorstore is None:
        vectorstore = Chroma.from_documents(batch, embeddings, persist_directory=CHROMA_PATH, collection_name="pesticides")
    else:
        vectorstore.add_documents(batch)
    if i + BATCH < len(docs):
        time.sleep(1)

print(f"\n✅ 完成！共向量化 {len(docs)} 筆")
print("🚀 現在執行：uvicorn main:app --reload --port 8000")