"""資料自動更新：定期重新爬取「農藥登記用藥」與「農藥法規」，比對差異後更新資料庫與向量索引。

設計重點：
  1. 差異更新：只新增／刪除有變動的登記資料，未變動的資料保留原本 id，向量索引只重算變動的部分。
  2. 安全防護：爬到的筆數若明顯少於現有資料（網站改版、連線中斷），本次更新自動放棄，不會把資料庫清空。
  3. 廢止下架：官方網站已不再登記的用藥組合，會從資料庫與向量索引中移除，系統不再推薦。
  4. 更新紀錄：每次執行結果寫入 data_updates 資料表，並同步更新回答中顯示的資料版本日期。
  5. 自動排程：後端啟動後於背景執行，每週日凌晨 3 點（台灣時間）自動更新；也可由管理端點手動觸發。
"""
import os
import re
import time
import threading
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Header, HTTPException, Query

from db import get_db, CHROMA_PATH

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "on").lower() != "off"
TPE = timezone(timedelta(hours=8))
MIN_RATIO = 0.8  # 新爬到的筆數低於現有 80% 視為異常，放棄更新

router = APIRouter(tags=["資料更新"])
_lock = threading.Lock()
_state = {"running": False, "current": None}


# ── 更新紀錄 ──────────────────────────────────────────────────
def init_update_table():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS data_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        added INTEGER DEFAULT 0,
        removed INTEGER DEFAULT 0,
        changed INTEGER DEFAULT 0,
        total INTEGER DEFAULT 0,
        message TEXT
    )''')
    conn.commit()
    conn.close()


def _now():
    return datetime.now(TPE).strftime("%Y-%m-%d %H:%M:%S")


def _log_start(source):
    conn = get_db()
    cur = conn.execute("INSERT INTO data_updates (source, started_at, status) VALUES (?, ?, 'running')", (source, _now()))
    conn.commit()
    log_id = cur.lastrowid
    conn.close()
    return log_id


def _log_finish(log_id, status, added=0, removed=0, changed=0, total=0, message=""):
    conn = get_db()
    conn.execute(
        "UPDATE data_updates SET finished_at=?, status=?, added=?, removed=?, changed=?, total=?, message=? WHERE id=?",
        (_now(), status, added, removed, changed, total, message[:2000], log_id),
    )
    conn.commit()
    conn.close()
    if status in ("success", "no_change"):
        refresh_source_versions()


def last_success(source):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT finished_at FROM data_updates WHERE source=? AND status IN ('success','no_change') "
            "ORDER BY id DESC LIMIT 1", (source,)
        ).fetchone()
    except Exception:
        row = None
    conn.close()
    return row[0] if row else None


def refresh_source_versions():
    """把最近一次成功更新的日期，同步到回答來源標籤（例如「2026/10/04 更新」）。"""
    import rag
    for source, keys in (("pesticides", ["pesticide"]), ("regulations", ["law"])):
        ts = last_success(source)
        if ts:
            label = ts[:10].replace("-", "/") + " 更新"
            for k in keys:
                rag.SOURCE_VERSIONS[k] = label


# ── 農藥登記用藥：爬取 → 比對 → 差異更新 ───────────────────────
def _chunk_text(d: dict) -> str:
    """與 build_index.py 相同的段落格式，確保新舊向量資料一致。"""
    fields = [("作物", "作物名稱"), ("防治病蟲害", "病蟲害名稱"), ("農藥名稱", "農藥中文普通名稱"),
              ("含量", "農藥含量"), ("劑型", "劑型"), ("每公頃每次用量", "每公頃每次用量"),
              ("稀釋倍數", "稀釋倍數"), ("使用時期", "使用時期"), ("施藥間隔", "施藥間隔"),
              ("施用次數", "施用次數"), ("安全採收期", "安全採收期_天"), ("施用方法", "施用方法"),
              ("注意事項", "注意事項")]
    parts = []
    for label, col in fields:
        v = d.get(col)
        if v:
            parts.append(f"{label}：{v}天" if col == "安全採收期_天" else f"{label}：{v}")
    return "，".join(parts) + "。"


def _scrape_pesticides() -> pd.DataFrame:
    import pesticide_final as pf
    leaves = pf.get_leaf_farms_any()
    if not leaves:
        raise RuntimeError("無法取得作物清單，官方網站可能暫時無法連線或已改版")
    # 沿用資料庫既有的作物名稱（例如「檬果 芒果」「甘藍 高麗菜」這種含俗名的複合名稱），
    # 讓俗名查詢與既有資料保持一致；官網新增的作物才使用新名稱。
    conn = get_db()
    try:
        known = dict(conn.execute("SELECT DISTINCT 作物代碼, 作物名稱 FROM pesticides").fetchall())
    except Exception:
        known = {}
    conn.close()
    for leaf in leaves:
        if leaf["代碼"] in known:
            leaf["名稱"] = known[leaf["代碼"]]
    rows = pf.crawl_all(leaves)
    if not rows:
        raise RuntimeError("沒有爬到任何登記資料")
    return pd.DataFrame(rows).drop_duplicates().fillna("").astype(str)


def update_pesticides(scraper=None, vectorstore_factory=None) -> dict:
    scraper = scraper or _scrape_pesticides
    log_id = _log_start("pesticides")
    try:
        new_df = scraper()
        conn = get_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pesticides)").fetchall() if r[1] != "id"]
        col_sql = ", ".join('"' + c + '"' for c in cols)
        old = conn.execute(f"SELECT id, {col_sql} FROM pesticides").fetchall()

        if len(new_df) < len(old) * MIN_RATIO:
            conn.close()
            msg = f"新資料僅 {len(new_df)} 筆，低於現有 {len(old)} 筆的 {int(MIN_RATIO * 100)}%，判定為爬取異常，本次不更新"
            _log_finish(log_id, "aborted", total=len(old), message=msg)
            return {"status": "aborted", "message": msg}

        for c in cols:
            if c not in new_df.columns:
                new_df[c] = ""
        new_keys = {}
        for rec in new_df[cols].itertuples(index=False):
            key = tuple((v or "").strip() for v in rec)
            new_keys[key] = dict(zip(cols, key))

        old_map = {}
        for r in old:
            key = tuple((str(v) if v is not None else "").strip() for v in tuple(r)[1:])
            old_map.setdefault(key, []).append(r[0])

        removed_ids = [i for k, ids in old_map.items() if k not in new_keys for i in ids]
        added_rows = [rec for k, rec in new_keys.items() if k not in old_map]

        # 「變更」：同一作物、病蟲害、藥劑、含量、劑型的組合仍存在，但用法數值改變（例如安全採收期調整）
        core = lambda d: (d.get("作物代碼"), d.get("病蟲害名稱"), d.get("農藥中文普通名稱"), d.get("農藥含量"), d.get("劑型"))
        removed_core = {core(dict(zip(cols, k))) for k in old_map if k not in new_keys}
        changed = sum(1 for rec in added_rows if core(rec) in removed_core)

        if not removed_ids and not added_rows:
            conn.close()
            _log_finish(log_id, "no_change", total=len(old), message="官方資料無異動")
            return {"status": "no_change", "total": len(old)}

        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM pesticides").fetchone()[0]
        new_with_ids = [{"id": max_id + offset, **rec} for offset, rec in enumerate(added_rows, 1)]

        # 1) 先寫入新增資料的向量（最花時間、最可能失敗的步驟）；失敗則整次放棄，SQLite 完全不動
        vs = (vectorstore_factory or _pesticide_vectorstore)()
        if vs is not None:
            # 清掉上次失敗殘留、id 大於目前最大值的孤兒向量，避免重複
            vs._collection.delete(where={"id": {"$gt": max_id}})
            if new_with_ids:
                from langchain_core.documents import Document
                docs = [Document(page_content=_chunk_text(r), metadata={
                    "id": r["id"], "作物名稱": r.get("作物名稱", ""), "普通名稱": r.get("農藥中文普通名稱", ""),
                    "病蟲名稱": r.get("病蟲害名稱", ""), "安全採收期": r.get("安全採收期_天", ""),
                    "來源": "農藥資訊服務網",
                }) for r in new_with_ids]
                for i in range(0, len(docs), 100):
                    vs.add_documents(docs[i:i + 100])

        # 2) 更新 SQLite（單一交易，未變動的資料保留原 id）
        with conn:
            for i in range(0, len(removed_ids), 500):
                chunk = removed_ids[i:i + 500]
                conn.execute(f"DELETE FROM pesticides WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            if new_with_ids:
                all_cols = ["id"] + cols
                ins_cols = ", ".join('"' + c + '"' for c in all_cols)
                marks = ", ".join("?" * len(all_cols))
                conn.executemany(
                    f"INSERT INTO pesticides ({ins_cols}) VALUES ({marks})",
                    [tuple(r[c] for c in all_cols) for r in new_with_ids],
                )
        total = conn.execute("SELECT COUNT(*) FROM pesticides").fetchone()[0]
        conn.close()

        # 3) 最後才把下架資料的向量刪除（此時資料庫已更新完成）
        if vs is not None:
            for i in range(0, len(removed_ids), 500):
                vs._collection.delete(where={"id": {"$in": removed_ids[i:i + 500]}})

        _invalidate_caches("pesticides")
        msg = f"新增 {len(added_rows)} 筆、下架 {len(removed_ids)} 筆（其中 {changed} 筆為用法變更）"
        _log_finish(log_id, "success", len(added_rows), len(removed_ids), changed, total, msg)
        return {"status": "success", "added": len(added_rows), "removed": len(removed_ids), "changed": changed, "total": total}
    except Exception as e:
        _log_finish(log_id, "failed", message=f"{e}\n{traceback.format_exc()[-1500:]}")
        return {"status": "failed", "message": str(e)}


def _pesticide_vectorstore():
    if not OPENAI_API_KEY or not os.path.exists(CHROMA_PATH):
        return None
    import rag
    return rag.get_vectorstore()


# ── 農藥法規：爬取 → 比對 → 有異動才整批更新 ───────────────────
def _scrape_laws() -> pd.DataFrame:
    import scrape_law as sl
    rows = []
    for name, url in sl.LAWS:
        rows.extend(sl.scrape_one_law(name, url))
    if not rows:
        raise RuntimeError("沒有擷取到任何法規條文")
    return pd.DataFrame(rows).fillna("").astype(str)


def update_regulations(scraper=None, rebuild_index=None) -> dict:
    scraper = scraper or _scrape_laws
    log_id = _log_start("regulations")
    try:
        new_df = scraper()
        conn = get_db()
        try:
            old = conn.execute("SELECT 法規名稱, 條號, 條文內容, 版本日期 FROM regulations").fetchall()
        except Exception:
            old = []
        if old and len(new_df) < len(old) * MIN_RATIO:
            conn.close()
            msg = f"新法規僅 {len(new_df)} 條，低於現有 {len(old)} 條的 {int(MIN_RATIO * 100)}%，判定為爬取異常，本次不更新"
            _log_finish(log_id, "aborted", total=len(old), message=msg)
            return {"status": "aborted", "message": msg}

        # 比對時忽略空白差異（網頁排版變動不算條文修正）
        norm = lambda x: re.sub(r"\s+", "", str(x or ""))
        old_set = {(r[0], norm(r[1]), norm(r[2])) for r in old}
        new_set = {(r["法規名稱"], norm(r["條號"]), norm(r["條文內容"])) for _, r in new_df.iterrows()}
        if old_set == new_set:
            conn.close()
            _log_finish(log_id, "no_change", total=len(old), message="法規條文無異動")
            return {"status": "no_change", "total": len(old)}

        old_keys = {(a, b): c for a, b, c in old_set}
        new_keys = {(a, b): c for a, b, c in new_set}
        added = [k for k in new_keys if k not in old_keys]
        removed = [k for k in old_keys if k not in new_keys]
        amended = [k for k in new_keys if k in old_keys and new_keys[k] != old_keys[k]]

        df = new_df[["法規名稱", "章節", "條號", "條文內容", "版本日期", "來源網址"]].copy()
        df.insert(0, "id", range(1, len(df) + 1))
        with conn:
            df.to_sql("regulations", conn, if_exists="replace", index=False)
            conn.execute('CREATE INDEX IF NOT EXISTS idx_law_article ON regulations("條號")')
        conn.close()

        (rebuild_index or _rebuild_law_index)(df)
        _invalidate_caches("regulations")

        detail = "、".join(f"{a}{b}" for a, b in (amended + added)[:20])
        msg = f"修正 {len(amended)} 條、新增 {len(added)} 條、刪除 {len(removed)} 條" + (f"：{detail}" if detail else "")
        _log_finish(log_id, "success", len(added), len(removed), len(amended), len(df), msg)
        return {"status": "success", "added": len(added), "removed": len(removed), "changed": len(amended), "total": len(df)}
    except Exception as e:
        _log_finish(log_id, "failed", message=f"{e}\n{traceback.format_exc()[-1500:]}")
        return {"status": "failed", "message": str(e)}


def _rebuild_law_index(df: pd.DataFrame):
    if not OPENAI_API_KEY or not os.path.exists(CHROMA_PATH):
        return
    import rag
    from langchain_core.documents import Document
    vs = rag.get_law_vectorstore()
    if vs is not None:
        try:
            existing = vs._collection.get(include=[])["ids"]
            for i in range(0, len(existing), 500):
                vs._collection.delete(ids=existing[i:i + 500])
        except Exception:
            pass
        docs = [Document(page_content=f"{r['法規名稱']}{r['條號']}（{r['章節']}）：{r['條文內容']}", metadata={
            "id": int(r["id"]), "法規名稱": r["法規名稱"], "條號": r["條號"], "章節": r["章節"],
            "版本日期": r["版本日期"], "來源": r["來源網址"], "類別": "法規",
        }) for _, r in df.iterrows()]
        for i in range(0, len(docs), 100):
            vs.add_documents(docs[i:i + 100])


def _invalidate_caches(table):
    import rag
    rag._bm25_cache.pop(table, None)


# ── 執行入口與排程 ────────────────────────────────────────────
def run_update(source: str = "all") -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "已有更新正在進行中"}
    _state.update(running=True, current=source)
    try:
        result = {}
        if source in ("all", "pesticides"):
            result["pesticides"] = update_pesticides()
        if source in ("all", "regulations"):
            result["regulations"] = update_regulations()
        print(f"🔄 資料更新完成：{result}")
        return result
    finally:
        _state.update(running=False, current=None)
        _lock.release()


def _seconds_until_next_run(now=None):
    """下一個週日凌晨 3:00（台灣時間）。"""
    now = now or datetime.now(TPE)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    days_ahead = (6 - now.weekday()) % 7  # weekday(): 週一=0 … 週日=6
    target += timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


def start_scheduler():
    init_update_table()
    refresh_source_versions()
    if not AUTO_UPDATE:
        print("⏸️ 資料自動更新已關閉（AUTO_UPDATE=off）")
        return

    def _loop():
        while True:
            wait = _seconds_until_next_run()
            print(f"⏰ 下次資料自動更新：{(datetime.now(TPE) + timedelta(seconds=wait)).strftime('%Y-%m-%d %H:%M')}（台灣時間）")
            time.sleep(wait)
            try:
                run_update("all")
            except Exception as e:
                print(f"⚠️ 自動更新失敗：{e}")
            time.sleep(60)

    threading.Thread(target=_loop, daemon=True).start()


# ── API ───────────────────────────────────────────────────────
@router.get("/api/data/status", summary="資料版本與自動更新紀錄")
def data_status():
    conn = get_db()
    try:
        logs = [dict(r) for r in conn.execute(
            "SELECT source, started_at, finished_at, status, added, removed, changed, total, message "
            "FROM data_updates ORDER BY id DESC LIMIT 10").fetchall()]
    except Exception:
        logs = []
    counts = {}
    for t in ("pesticides", "regulations", "residue_limits", "qa_knowledge"):
        try:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            counts[t] = None
    conn.close()
    return {
        "自動更新": "每週日 03:00（台灣時間）" if AUTO_UPDATE else "已關閉",
        "更新中": _state["running"],
        "農藥登記最近更新": last_success("pesticides"),
        "法規最近更新": last_success("regulations"),
        "資料筆數": counts,
        "更新紀錄": logs,
    }


@router.post("/api/admin/update", summary="手動觸發資料更新（需管理金鑰）")
def trigger_update(source: str = Query("all", pattern="^(all|pesticides|regulations)$"),
                   x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(403, "管理金鑰錯誤")
    if _state["running"]:
        return {"status": "busy", "message": "已有更新正在進行中"}
    threading.Thread(target=run_update, args=(source,), daemon=True).start()
    return {"status": "started", "message": f"已開始更新（{source}），約需 5～10 分鐘，可查詢 /api/data/status"}