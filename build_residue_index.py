# -*- coding: utf-8 -*-
"""
build_residue_index.py — 把農藥殘留容許量標準向量化，存入 ChromaDB（獨立 collection: residue_limits）
執行：python build_residue_index.py
不會動到既有的 pesticides / regulations / qa_knowledge collection

⚠️ 這份資料量較大（7000+ 筆），向量化預估需要 5-10 分鐘，請耐心等待。
"""
import sqlite3, os, time
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
rows = conn.execute("SELECT id, 農藥名稱與作物, 限量_ppm, 備註, 用途分類 FROM residue_limits").fetchall()
conn.close()
print(f"✅ 讀取 {len(rows)} 筆殘留限量資料")

docs = []
for r in rows:
    d = dict(r)
    text = f"{d['農藥名稱與作物']}：殘留容許量 {d['限量_ppm']} ppm（{d['用途分類']}）"
    if d["備註"]:
        text += f"，{d['備註']}"
    docs.append(Document(
        page_content=text,
        metadata={
            "id": d["id"],
            "限量_ppm": d["限量_ppm"],
            "用途分類": d["用途分類"],
            "來源": "https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/463/relfile/77793/347247/7ca0f288-f7ad-4826-8a3f-20f0c6a2fa82.pdf",
        },
    ))

embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_KEY)

BATCH = 200
vectorstore = None
total = len(docs) // BATCH + 1
for i in range(0, len(docs), BATCH):
    batch = docs[i:i+BATCH]
    print(f"  批次 {i//BATCH+1}/{total}（{i+1}~{min(i+BATCH,len(docs))} 筆）...")
    if vectorstore is None:
        vectorstore = Chroma.from_documents(batch, embeddings, persist_directory=CHROMA_PATH, collection_name="residue_limits")
    else:
        vectorstore.add_documents(batch)
    if i + BATCH < len(docs):
        time.sleep(1)

print(f"\n✅ 完成！共向量化 {len(docs)} 筆，存入 collection: residue_limits")
