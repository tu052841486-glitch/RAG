"""拍照判讀紀錄與病害通報熱區（AI 治理）。

治理設計：
  - 稽核軌跡：每次判讀記錄時間、模型版本、健康等級、候選與使用者確認結果。
  - 人在迴路中：只有使用者「確認」的判讀才會進入病害熱區統計；「都不是」記為否決，用來追蹤 AI 判錯率。
  - 資料最小化：不保存照片、不綁定帳號，位置只記錄到鄉鎮，而且是使用者自願填寫。
  - 去識別化：區域通報數未滿 K_MIN 筆不顯示數字，避免反推特定農戶。
  - 示範資料：is_demo=1 標記，儀表板可切換顯示並一鍵清除。

API：
  POST /api/diagnosis/{id}/confirm   使用者確認或否決判讀（公開）
  GET  /api/areas                    縣市與鄉鎮清單（公開，取自販賣業者資料）
  GET  /api/admin/diagnosis/heatmap  病害熱區（需 X-Admin-Token）
  GET  /api/admin/diagnosis/governance  AI 治理指標與稽核紀錄（需 X-Admin-Token）
  POST/DELETE /api/admin/diagnosis/demo 產生／清除示範資料（需 X-Admin-Token）
  GET  /governance                   治理儀表板頁面
"""
import hmac
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from db import get_db
from dealers import COUNTIES, _norm

TPE = timezone(timedelta(hours=8))
K_MIN = 3
router = APIRouter(tags=["判讀紀錄與病害熱區"])
_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "governance.html")
_ready = False
_areas_cache = {"at": 0.0, "data": None}


def _now():
    return datetime.now(TPE)


def _ts(dt=None) -> str:
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")


def init_table():
    global _ready
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS diagnosis_log (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        model TEXT,
        health TEXT,
        quality TEXT,
        crop TEXT,
        crop_db TEXT,
        candidates TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        confirmed_pest TEXT,
        county TEXT NOT NULL DEFAULT '',
        town TEXT NOT NULL DEFAULT '',
        confirmed_at TEXT,
        is_demo INTEGER NOT NULL DEFAULT 0
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_diag_status ON diagnosis_log(status, confirmed_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_diag_area ON diagnosis_log(county, town)')
    conn.commit()
    conn.close()
    _ready = True


def _ensure():
    if not _ready:
        init_table()


# ── 寫入 ──────────────────────────────────────────────────────
def log_identification(result: dict, model: str) -> str:
    """vision.py 每次判讀後呼叫；回傳紀錄 id 給前端，供使用者確認時回填。"""
    _ensure()
    diag_id = uuid.uuid4().hex
    cands = [{"name": c.get("name"), "db_pest": c.get("db_pest"), "confidence": c.get("confidence")}
             for c in result.get("candidates") or []]
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO diagnosis_log (id, created_at, model, health, quality, crop, crop_db, candidates) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (diag_id, _ts(), model, result.get("health"), result.get("quality"),
             result.get("crop") or "", result.get("crop_db") or "", json.dumps(cands, ensure_ascii=False)),
        )
    conn.close()
    return diag_id


def _areas() -> dict:
    """縣市 → 鄉鎮清單，取自販賣業者資料（每小時重新整理一次）。"""
    if _areas_cache["data"] is not None and time.time() - _areas_cache["at"] < 3600:
        return _areas_cache["data"]
    towns = {c: [] for c in COUNTIES}
    try:
        conn = get_db()
        rows = conn.execute("SELECT DISTINCT county, town FROM pesticide_dealers "
                            "WHERE county != '' AND town != '' ORDER BY county, town").fetchall()
        conn.close()
        for c, t in rows:
            if c in towns:
                towns[c].append(t)
    except Exception:
        pass
    _areas_cache.update(at=time.time(), data=towns)
    return towns


class ConfirmRequest(BaseModel):
    pest: Optional[str] = None  # None 代表使用者選「以上都不是」
    county: str = ""
    town: str = ""


@router.post("/api/diagnosis/{diag_id}/confirm", summary="使用者確認或否決拍照判讀結果")
def confirm(diag_id: str, req: ConfirmRequest):
    _ensure()
    county, town = _norm(req.county), _norm(req.town)
    if county not in COUNTIES:
        county, town = "", ""
    if town and town not in _areas().get(county, []):
        town = ""

    conn = get_db()
    row = conn.execute("SELECT candidates FROM diagnosis_log WHERE id=?", (diag_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "找不到這筆判讀紀錄")
    pest = (req.pest or "").strip()
    if pest:
        allowed = set()
        for c in json.loads(row[0] or "[]"):
            allowed.update(v for v in (c.get("name"), c.get("db_pest")) if v)
        if pest not in allowed:
            conn.close()
            raise HTTPException(400, "確認的病蟲害必須是判讀候選之一")
    status = "confirmed" if pest else "rejected"
    with conn:
        conn.execute("UPDATE diagnosis_log SET status=?, confirmed_pest=?, county=?, town=?, confirmed_at=? WHERE id=?",
                     (status, pest or None, county, town, _ts(), diag_id))
    conn.close()
    return {"ok": True, "status": status}


@router.get("/api/areas", summary="縣市與鄉鎮清單")
def areas():
    return {"counties": COUNTIES, "towns": _areas()}


# ── 管理端 ────────────────────────────────────────────────────
def _require_admin(token: str):
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(503, "ADMIN_TOKEN 未設定")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "管理密碼錯誤")


def _base_where(days: int, include_demo: bool, field: str = "created_at"):
    where = [f"{field} >= ?"]
    params = [_ts(_now() - timedelta(days=days))]
    if not include_demo:
        where.append("is_demo = 0")
    return where, params


@router.get("/api/admin/diagnosis/heatmap", summary="病害通報熱區（僅計入使用者確認的判讀）")
def heatmap(days: int = Query(30, ge=1, le=365), crop: str = Query(""), pest: str = Query(""),
            include_demo: bool = Query(True), x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_admin(x_admin_token)
    _ensure()
    where, params = _base_where(days, include_demo, "confirmed_at")
    where += ["status = 'confirmed'", "county != ''"]
    if crop:
        where.append("crop = ?"); params.append(crop)
    if pest:
        where.append("confirmed_pest = ?"); params.append(pest)
    w = " AND ".join(where)

    conn = get_db()
    county_rows = conn.execute(f"SELECT county, COUNT(*) FROM diagnosis_log WHERE {w} GROUP BY county", params).fetchall()
    town_rows = conn.execute(
        f"SELECT county, town, COUNT(*) FROM diagnosis_log WHERE {w} AND town != '' "
        f"GROUP BY county, town HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT 15", params + [K_MIN]).fetchall()
    pest_rows = conn.execute(
        f"SELECT crop, confirmed_pest, COUNT(*) FROM diagnosis_log WHERE {w} "
        f"GROUP BY crop, confirmed_pest HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT 10", params + [K_MIN]).fetchall()
    # 篩選選單：不受作物／病害條件影響
    w0, p0 = _base_where(days, include_demo, "confirmed_at")
    w0 = " AND ".join(w0 + ["status = 'confirmed'"])
    crop_opts = [r[0] for r in conn.execute(f"SELECT DISTINCT crop FROM diagnosis_log WHERE {w0} AND crop != '' ORDER BY crop", p0)]
    pest_opts = [r[0] for r in conn.execute(f"SELECT DISTINCT confirmed_pest FROM diagnosis_log WHERE {w0} ORDER BY confirmed_pest", p0)]
    demo_n = conn.execute("SELECT COUNT(*) FROM diagnosis_log WHERE is_demo = 1").fetchone()[0]
    conn.close()

    counties = [{"county": c, "count": n if n >= K_MIN else None, "suppressed": n < K_MIN} for c, n in county_rows]
    return {
        "days": days, "k_min": K_MIN, "demo_rows": demo_n,
        "total": sum(n for _, n in county_rows),
        "counties": counties,
        "towns": [{"county": c, "town": t, "count": n} for c, t, n in town_rows],
        "pests": [{"crop": c, "pest": p, "count": n} for c, p, n in pest_rows],
        "options": {"crops": crop_opts, "pests": pest_opts},
    }


@router.get("/api/admin/diagnosis/governance", summary="AI 治理指標與稽核紀錄")
def governance(days: int = Query(30, ge=1, le=365), include_demo: bool = Query(True),
               x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_admin(x_admin_token)
    _ensure()
    where, params = _base_where(days, include_demo)
    w = " AND ".join(where)
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM diagnosis_log WHERE {w}", params).fetchone()[0]
    status = dict(conn.execute(f"SELECT status, COUNT(*) FROM diagnosis_log WHERE {w} GROUP BY status", params).fetchall())
    health = dict(conn.execute(f"SELECT health, COUNT(*) FROM diagnosis_log WHERE {w} GROUP BY health", params).fetchall())
    models = dict(conn.execute(f"SELECT model, COUNT(*) FROM diagnosis_log WHERE {w} GROUP BY model", params).fetchall())
    recent = conn.execute(
        f"SELECT created_at, model, health, crop, candidates, status, confirmed_pest, county, is_demo "
        f"FROM diagnosis_log WHERE {w} ORDER BY created_at DESC LIMIT 12", params).fetchall()
    conn.close()

    confirmed, rejected = status.get("confirmed", 0), status.get("rejected", 0)
    decided = confirmed + rejected
    audit = []
    for r in recent:
        cands = json.loads(r[4] or "[]")
        audit.append({
            "time": r[0], "model": r[1], "health": r[2], "crop": r[3] or "—",
            "ai_top": cands[0]["name"] if cands else "—", "status": r[5],
            "confirmed_pest": r[6] or "", "county": r[7] or "", "demo": bool(r[8]),
        })
    return {
        "days": days, "total": total,
        "confirmed": confirmed, "rejected": rejected, "pending": status.get("pending", 0),
        "accept_rate": round(confirmed / decided, 3) if decided else None,
        "reject_rate": round(rejected / decided, 3) if decided else None,
        "health": {k or "unknown": v for k, v in health.items()},
        "models": models,
        "audit": audit,
    }


# ── 示範資料 ──────────────────────────────────────────────────
DEMO_PATTERNS = [
    ("芒果", "炭疽病", [("臺南市", ["玉井區", "楠西區", "南化區"], 9), ("屏東縣", ["枋山鄉", "枋寮鄉"], 5), ("高雄市", ["六龜區"], 2)]),
    ("高麗菜", "小菜蛾", [("雲林縣", ["西螺鎮", "二崙鄉", "崙背鄉"], 8), ("彰化縣", ["埔鹽鄉", "溪湖鎮"], 5), ("南投縣", ["仁愛鄉"], 2)]),
    ("水稻", "稻熱病", [("嘉義縣", ["民雄鄉", "新港鄉", "太保市"], 7), ("臺南市", ["後壁區"], 3), ("雲林縣", ["斗南鎮"], 2)]),
    ("番茄", "晚疫病", [("嘉義縣", ["朴子市"], 2), ("高雄市", ["路竹區"], 3), ("臺南市", ["善化區"], 2)]),
    ("草莓", "灰黴病", [("苗栗縣", ["大湖鄉", "獅潭鄉"], 5)]),
    ("茶", "茶角盲椿象", [("南投縣", ["名間鄉", "鹿谷鄉"], 3), ("新北市", ["坪林區"], 1)]),
]
DEMO_SPARSE = [("宜蘭縣", "三星鄉", "蔥", "銹病", 2)]  # 未滿 K_MIN，用來展示去識別化


@router.post("/api/admin/diagnosis/demo", summary="產生示範資料（標記 is_demo）")
def seed_demo(n: int = Query(260, ge=50, le=1000), x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_admin(x_admin_token)
    _ensure()
    rng = random.Random()
    pool = []
    for crop, pest, areas_ in DEMO_PATTERNS:
        for county, towns, weight in areas_:
            pool += [(crop, pest, county, towns)] * weight
    rows = []

    def row(crop, pest, county, town, status, health):
        created = _now() - timedelta(days=rng.uniform(0, 29), minutes=rng.randint(0, 600))
        conf = round(rng.uniform(0.55, 0.92), 2)
        cands = json.dumps([{"name": pest, "db_pest": pest, "confidence": conf}], ensure_ascii=False)
        confirmed_at = _ts(created + timedelta(minutes=rng.randint(1, 30))) if status != "pending" else None
        return (uuid.uuid4().hex, _ts(created), "demo", health, "ok", crop, crop, cands, status,
                pest if status == "confirmed" else None, county if status != "pending" else "",
                town if status != "pending" else "", confirmed_at, 1)

    for _ in range(n):
        crop, pest, county, towns = rng.choice(pool)
        r = rng.random()
        status = "confirmed" if r < 0.78 else ("rejected" if r < 0.88 else "pending")
        rows.append(row(crop, pest, county, rng.choice(towns), status, "diseased" if rng.random() < 0.8 else "suspect"))
    for county, town, crop, pest, k in DEMO_SPARSE:
        rows += [row(crop, pest, county, town, "confirmed", "suspect") for _ in range(k)]
    for _ in range(max(10, n // 8)):  # 健康照片
        rows.append((uuid.uuid4().hex, _ts(_now() - timedelta(days=rng.uniform(0, 29))), "demo", "healthy", "ok",
                     rng.choice(["芒果", "高麗菜", "水稻"]), "", "[]", "pending", None, "", "", None, 1))

    conn = get_db()
    with conn:
        conn.executemany(
            "INSERT INTO diagnosis_log (id, created_at, model, health, quality, crop, crop_db, candidates, status, "
            "confirmed_pest, county, town, confirmed_at, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.close()
    return {"ok": True, "inserted": len(rows)}


@router.delete("/api/admin/diagnosis/demo", summary="清除示範資料")
def clear_demo(x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_admin(x_admin_token)
    _ensure()
    conn = get_db()
    with conn:
        cur = conn.execute("DELETE FROM diagnosis_log WHERE is_demo = 1")
    conn.close()
    return {"ok": True, "deleted": cur.rowcount}


@router.get("/governance", include_in_schema=False)
def governance_page():
    if not os.path.exists(_HTML_PATH):
        return HTMLResponse("<h1>governance.html 不存在</h1>", status_code=404)
    with open(_HTML_PATH, encoding="utf-8") as f:
        return HTMLResponse(f.read())