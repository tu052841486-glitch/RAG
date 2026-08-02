# -*- coding: utf-8 -*-
"""
build_law_index.py — 把法規條文向量化，存入 ChromaDB（獨立 collection: regulations）
執行：python build_law_index.py
不會動到既有的 pesticides collection
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
rows = conn.execute("SELECT id, 法規名稱, 章節, 條號, 條文內容, 版本日期, 來源網址 FROM regulations").fetchall()
conn.close()
print(f"✅ 讀取 {len(rows)} 條法規條文")

docs = []
for r in rows:
    d = dict(r)
    text = f"{d['法規名稱']}{d['條號']}（{d['章節']}）：{d['條文內容']}"
    docs.append(Document(
        page_content=text,
        metadata={
            "id": d["id"],
            "法規名稱": d["法規名稱"],
            "條號": d["條號"],
            "章節": d["章節"],
            "版本日期": d["版本日期"],
            "來源": d["來源網址"],
            "類別": "法規",
        },
    ))

embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_KEY)

vectorstore = Chroma.from_documents(
    docs, embeddings,
    persist_directory=CHROMA_PATH,
    collection_name="regulations",  # 獨立 collection，不影響 pesticides
)

print(f"\n✅ 完成！共向量化 {len(docs)} 條法規條文，存入 collection: regulations")
