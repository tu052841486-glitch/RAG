# -*- coding: utf-8 -*-
"""
build_qa_index.py — 把農藥合理使用問答集向量化，存入 ChromaDB（獨立 collection: qa_knowledge）
執行：python build_qa_index.py
不會動到既有的 pesticides / regulations collection
"""
import sqlite3, os
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
rows = conn.execute("SELECT id, 分類, 問題, 答案, 來源類別, 來源網址 FROM qa_knowledge").fetchall()
conn.close()
print(f"✅ 讀取 {len(rows)} 組問答")

docs = []
for r in rows:
    d = dict(r)
    text = f"問：{d['問題']}\n答：{d['答案']}"
    docs.append(Document(
        page_content=text,
        metadata={
            "id": d["id"],
            "分類": d["分類"],
            "來源類別": d["來源類別"],
            "來源": d["來源網址"],
        },
    ))

embeddings = OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=OPENAI_KEY)

vectorstore = Chroma.from_documents(
    docs, embeddings,
    persist_directory=CHROMA_PATH,
    collection_name="qa_knowledge",
)

print(f"\n✅ 完成！共向量化 {len(docs)} 組問答，存入 collection: qa_knowledge")
