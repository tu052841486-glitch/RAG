"""農藥博士 /health 健康檢查端點。

自動偵測：
  - SQLite 路徑：讀 db.py 中結尾為 .db/.sqlite 的變數，找不到再掃專案資料夾
  - ChromaDB：直接沿用 rag.py 已建立的 client，找不到再搜尋含 chroma.sqlite3 的資料夾
也可用環境變數 SQLITE_PATH / CHROMA_PATH 強制指定。

一律回 200（避免 Fly.io 誤判機器掛掉），實際狀態看 body 的 status。
  GET /health            快速檢查（不呼叫 OpenAI）
  GET /health?deep=true  另外實際驗證 OpenAI key（不耗 token）
"""
import inspect
import os
import sqlite3
import time
from datetime import date

from fastapi import APIRouter, Query

router = APIRouter(tags=["系統"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPECTED_COLLECTIONS = 4
START_TIME = time.time()
_SKIP_DIRS = {"node_modules", ".git", "frontend", "__pycache__", "venv", ".venv"}
_SQLITE_EXT = (".db", ".sqlite", ".sqlite3")

_chroma_client = None


def _existing_path(p):
    if p and os.path.exists(p):
        return p
    if p and not os.path.isabs(p):
        alt = os.path.join(BASE_DIR, p)
        if os.path.exists(alt):
            return alt
    return None


def _find_sqlite_path():
    env = _existing_path(os.getenv("SQLITE_PATH"))
    if env:
        return env
    try:
        import db
        for val in vars(db).values():
            if isinstance(val, str) and val.lower().endswith(_SQLITE_EXT) and "chroma" not in val.lower():
                found = _existing_path(val)
                if found:
                    return found
    except Exception:
        pass
    for f in sorted(os.listdir(BASE_DIR)):
        if f.lower().endswith(_SQLITE_EXT):
            return os.path.join(BASE_DIR, f)
    return None


def _find_chroma_client():
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    # 1. 沿用 rag.py 已建立的 client
    try:
        import rag
        for val in vars(rag).values():
            if inspect.ismodule(val) or inspect.isclass(val) or inspect.isfunction(val):
                continue
            try:
                if callable(getattr(val, "list_collections", None)):
                    _chroma_client = val
                    return val
            except Exception:
                continue
    except Exception:
        pass

    # 2. 自己開
    import chromadb
    path = _existing_path(os.getenv("CHROMA_PATH"))
    if not path:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            if "chroma.sqlite3" in files:
                path = root
                break
    if not path:
        raise RuntimeError("找不到 ChromaDB 資料夾，請設定環境變數 CHROMA_PATH")
    _chroma_client = chromadb.PersistentClient(path=path)
    return _chroma_client


def check_sqlite():
    t = time.perf_counter()
    path = _find_sqlite_path()
    if not path:
        return {"ok": False, "error": "找不到 SQLite 檔，請設定環境變數 SQLITE_PATH"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        conn.execute("SELECT 1")
        conn.close()
        return {"ok": True, "file": os.path.basename(path), "ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def check_chroma():
    t = time.perf_counter()
    try:
        n = len(_find_chroma_client().list_collections())
        return {
            "ok": n >= EXPECTED_COLLECTIONS,
            "collections": n,
            "ms": round((time.perf_counter() - t) * 1000),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def check_openai(deep: bool):
    if not os.getenv("OPENAI_API_KEY"):
        return {"ok": False, "error": "OPENAI_API_KEY 未設定"}
    if not deep:
        return {"ok": True, "mode": "key_present"}
    t = time.perf_counter()
    try:
        from openai import OpenAI
        OpenAI().models.retrieve("gpt-4o-mini")
        return {"ok": True, "mode": "verified", "ms": round((time.perf_counter() - t) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.get("/health", summary="系統健康檢查")
def health(deep: bool = Query(False, description="true 時實際驗證 OpenAI key")):
    checks = {
        "sqlite": check_sqlite(),
        "chroma": check_chroma(),
        "openai": check_openai(deep),
    }
    all_ok = all(c["ok"] for c in checks.values())
    return {
        "狀態": "正常" if all_ok else "異常",
        "status": "ok" if all_ok else "degraded",
        "日期": str(date.today()),
        "uptime_sec": round(time.time() - START_TIME),
        "checks": checks,
    }