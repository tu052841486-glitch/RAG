"""農藥 RAG 系統後端 - 應用組裝進入點。

實際邏輯拆分於各模組：
  db.py            資料庫連線與建表
  auth.py          帳號系統（註冊/登入/登出/驗證）
  conversations.py 對話紀錄 API
  rag.py           檢索問答、農藥/法規查詢、模擬考、新聞
本檔只負責建立 app、設定 CORS、掛載各模組路由，以及啟動時初始化。
"""
import os
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from db import init_auth_tables, init_conversation_table
import auth
import conversations
import rag

app = FastAPI(title="農藥知識問答系統 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 掛載各模組路由
app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(rag.router)


@app.get("/health", tags=["系統"], summary="系統健康檢查")
def health():
    return {"狀態": "正常", "日期": str(date.today())}


# dashboard 靜態頁（若檔案存在）
_dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
DASHBOARD = open(_dashboard_path, encoding="utf-8").read() if os.path.exists(_dashboard_path) else "<h1>Dashboard</h1>"


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return HTMLResponse(content=DASHBOARD)


@app.on_event("startup")
async def _startup_init_tables():
    init_auth_tables()
    init_conversation_table()
    print("✅ 帳號與對話紀錄資料表已就緒")


@app.on_event("startup")
async def _startup_prewarm_bm25():
    """啟動時在背景執行緒預建所有 BM25 索引，避免第一位使用者發問時卡在建索引。"""
    import threading

    def _build_all():
        for table_name, cfg in rag._TABLE_CONFIG.items():
            try:
                rag.get_bm25_index(table_name, cfg["fetch_columns"], cfg["score_columns"])
                print(f"✅ BM25 索引預建完成：{table_name}")
            except Exception as e:
                print(f"⚠️ BM25 索引預建失敗（{table_name}）：{e}")

    threading.Thread(target=_build_all, daemon=True).start()