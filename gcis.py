"""農藥廠商查核：串接經濟部商業發展署「商工行政資料開放平臺」公司登記資料。

用途：
  每一筆農藥登記資料都有「原始登記廠商」。本模組以廠商名稱查詢公司登記，
  取得統一編號、公司現況（核准設立／解散／廢止…）、資本額與設立日期，
  讓系統除了判斷「這款藥合不合法」，也能告訴農民「生產這款藥的公司是否仍合法營業」。

資料來源（政府資料開放授權條款－第1版，免費、免金鑰）：
  - 公司登記關鍵字查詢 API（以公司名稱查統一編號）
  - 公司登記基本資料-應用一 API（以統一編號查公司現況與基本資料）
  - 公司登記基本資料-應用三 API（以統一編號查營業項目，確認是否登記農藥製造／批發／零售）
  https://data.gcis.nat.gov.tw/od/detail?oid=8776818F-EB3C-445F-BE95-AE22577CBEBC

設計：
  - 查詢結果快取在 SQLite（company_registry），回答問題時只讀快取，不因外部 API 變慢。
  - 後端啟動後於背景補查尚未查過的廠商，之後每 7 天重新查核一次。
"""
import re
import time
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from fastapi import APIRouter

from db import get_db

TPE = timezone(timedelta(hours=8))
SEARCH_API = "https://data.gcis.nat.gov.tw/od/data/api/6BBA2268-1367-4B42-9CCA-BC17499EBE8C"
DETAIL_API = "https://data.gcis.nat.gov.tw/od/data/api/5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
ITEMS_API = "https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C"  # 公司登記基本資料-應用三（營業項目）
PESTICIDE_ITEMS = {"C802070": "農藥製造業", "F107040": "農藥批發業", "F207040": "農藥零售業"}
SOURCE_NAME = "經濟部商業發展署商工行政資料開放平臺"
REFRESH_DAYS = 7
NON_COMPANY_KEYWORDS = ("公務預算", "農會", "試驗所", "改良場", "政府", "大學", "研究院")

router = APIRouter(tags=["農藥廠商查核"])
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (pesticide-knowledge-system)"
_started = False


# ── 快取資料表 ────────────────────────────────────────────────
def init_table():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS company_registry (
        name TEXT PRIMARY KEY,
        kind TEXT,              -- company / non_company / branch / not_found
        ubn TEXT,
        status_desc TEXT,
        capital TEXT,
        setup_date TEXT,
        checked_at TEXT
    )''')
    cols = [r[1] for r in conn.execute("PRAGMA table_info(company_registry)").fetchall()]
    if "pesticide_items" not in cols:
        conn.execute("ALTER TABLE company_registry ADD COLUMN pesticide_items TEXT")
    conn.commit()
    conn.close()


def _roc_date(v) -> str:
    """民國日期 1040315 → 民國104年03月15日；其他格式原樣回傳。"""
    s = str(v or "").strip()
    if re.fullmatch(r"\d{7}", s):
        return f"民國{int(s[:3])}年{s[3:5]}月{s[5:]}日"
    return s


def _money(v) -> str:
    try:
        n = int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return ""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f} 億元".replace(".0 億", " 億")
    if n >= 10_000:
        return f"{n // 10_000:,} 萬元"
    return f"{n:,} 元"


def _get_json(url: str):
    r = _session.get(url, timeout=20)
    if r.status_code != 200:
        # 連線或服務異常時拋出錯誤：不寫入快取，避免把「查不到」誤判成「廠商不存在」
        raise RuntimeError(f"商工資料 API 回應 {r.status_code}")
    if not r.text.strip():
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def lookup_company(name: str) -> dict:
    """查一家廠商。回傳 dict（kind / ubn / status_desc / capital / setup_date）。"""
    name = (name or "").strip()
    if not name:
        return {"kind": "not_found"}
    if any(k in name for k in NON_COMPANY_KEYWORDS) or not name.endswith("公司"):
        return {"kind": "non_company"}
    if "分公司" in name:
        return {"kind": "branch"}

    # 1) 以公司名稱查詢「核准設立」中的公司，找出完全同名者的統一編號
    flt = quote(f"Company_Name like {name} and Company_Status eq 01")
    rows = _get_json(f"{SEARCH_API}?$format=json&$filter={flt}&$skip=0&$top=50")
    match = next((r for r in rows if (r.get("Company_Name") or "").strip() == name), None)
    if not match:
        return {"kind": "not_found"}
    ubn = (match.get("Business_Accounting_NO") or "").strip()

    # 2) 以統一編號查詢公司現況與基本資料
    detail = {}
    if ubn:
        flt2 = quote(f"Business_Accounting_NO eq {ubn}")
        d = _get_json(f"{DETAIL_API}?$format=json&$filter={flt2}&$skip=0")
        detail = d[0] if d else {}
    # 3) 以統一編號查營業項目，確認是否登記農藥製造／批發／零售（公司登記基本資料-應用三）
    items = []
    if ubn:
        try:
            flt3 = quote(f"Business_Accounting_NO eq {ubn}")
            d3 = _get_json(f"{ITEMS_API}?$format=json&$filter={flt3}&$skip=0&$top=1")
            biz = (d3[0].get("Cmp_Business") or []) if d3 else []
            codes = {(b.get("Business_Item") or "").strip() for b in biz}
            items = [name for code, name in PESTICIDE_ITEMS.items() if code in codes]
        except Exception:
            items = None
    src = {**match, **detail}
    capital = src.get("Paid_In_Capital_Amount") or src.get("Capital_Stock_Amount")
    return {
        "kind": "company",
        "ubn": ubn,
        "status_desc": (src.get("Company_Status_Desc") or "核准設立").strip(),
        "capital": _money(capital),
        "setup_date": _roc_date(src.get("Company_Setup_Date")),
        "pesticide_items": None if items is None else "、".join(items),
    }


def _save(name: str, info: dict):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO company_registry (name, kind, ubn, status_desc, capital, setup_date, checked_at, pesticide_items) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, info.get("kind"), info.get("ubn", ""), info.get("status_desc", ""),
         info.get("capital", ""), info.get("setup_date", ""), datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
         info.get("pesticide_items")),
    )
    conn.commit()
    conn.close()


def refresh(force: bool = False) -> dict:
    """查核資料庫中所有農藥廠商（只查未查過或超過 7 天的）。"""
    init_table()
    conn = get_db()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT DISTINCT 廠商名稱 FROM pesticides WHERE 廠商名稱 IS NOT NULL AND 廠商名稱 != ''").fetchall()]
    except Exception:
        names = []
    rows = conn.execute("SELECT name, checked_at, kind, pesticide_items FROM company_registry").fetchall()
    conn.close()
    cached = {r[0]: r[1] for r in rows}
    # 舊版快取沒有營業項目資料的公司，也要重新查一次
    no_items = {r[0] for r in rows if r[2] == "company" and r[3] is None}
    limit = (datetime.now(TPE) - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d %H:%M")
    todo = [n for n in names if force or n not in cached or n in no_items or (cached[n] or "") < limit]
    done = errors = 0
    for n in todo:
        try:
            _save(n, lookup_company(n))
            done += 1
        except Exception as e:
            errors += 1
            print(f"⚠️ 廠商查核失敗（{n}）：{e}")
        time.sleep(0.5)  # 對政府 API 保持禮貌
    print(f"🏢 廠商查核完成：{done} 家，失敗 {errors} 家，共 {len(names)} 家廠商")
    return {"checked": done, "errors": errors, "total": len(names)}


def start_background_refresh():
    """後端啟動後於背景查核一次，之後每 7 天一次。重複呼叫只會啟動一次。"""
    global _started
    if _started:
        return
    _started = True

    def _loop():
        time.sleep(20)  # 等待服務啟動完成
        while True:
            try:
                refresh()
            except Exception as e:
                print(f"⚠️ 廠商查核排程失敗：{e}")
            time.sleep(REFRESH_DAYS * 86400)

    threading.Thread(target=_loop, daemon=True).start()


# ── 給 rag.py 使用：只讀快取 ──────────────────────────────────
def get_cached(names) -> dict:
    names = [n for n in {(n or "").strip() for n in names} if n]
    if not names:
        return {}
    try:
        conn = get_db()
        rows = conn.execute(
            f"SELECT name, kind, ubn, status_desc, capital, setup_date, pesticide_items FROM company_registry "
            f"WHERE name IN ({','.join('?' * len(names))})", names).fetchall()
        conn.close()
    except Exception:
        return {}
    return {r[0]: {"kind": r[1], "ubn": r[2], "status": r[3], "capital": r[4], "setup_date": r[5],
                   "pesticide_items": r[6]} for r in rows}


def maker_badge(info: dict) -> dict:
    """把查核結果轉成前端顯示用的狀態：ok / warn / info。"""
    if not info:
        return {"level": "info", "label": "查核中"}
    kind = info.get("kind")
    if kind == "company":
        status = info.get("status") or "核准設立"
        ok = status in ("核准設立",)
        if not ok:
            return {"level": "warn", "label": status}
        items = info.get("pesticide_items")
        if items:
            main = next((n for n in ("農藥製造業", "農藥批發業", "農藥零售業") if n in items), items)
            return {"level": "ok", "label": f"營業中・登記{main}", "items": items}
        if items == "":
            return {"level": "ok", "label": "營業中", "items": "營業項目未見農藥相關登記"}
        return {"level": "ok", "label": "營業中"}
    if kind == "non_company":
        return {"level": "info", "label": "非公司組織（如政府單位、農會）"}
    if kind == "branch":
        return {"level": "info", "label": "外國公司在台分公司"}
    if kind == "not_found":
        return {"level": "warn", "label": "公司登記查無同名營業中公司"}
    return {"level": "info", "label": "查核中"}


def supply_summary(makers) -> str:
    """供應廠商分析，給 LLM 的知識庫補充（例如：由 18 家廠商供應，其中 2 家查無營業中登記）。"""
    makers = [m for m in {(m or "").strip() for m in makers} if m]
    if not makers:
        return ""
    cache = get_cached(makers)
    warn = [m for m in makers if maker_badge(cache.get(m)).get("level") == "warn"]
    with_items = [m for m in makers if (cache.get(m) or {}).get("pesticide_items")]
    s = f"【供應廠商分析（資料來源：{SOURCE_NAME}）】以上登記用藥由 {len(makers)} 家廠商登記供應"
    if with_items:
        s += f"，其中 {len(with_items)} 家在公司登記中登記有農藥製造、批發或零售營業項目"
    if warn:
        s += f"，其中 {len(warn)} 家在公司登記資料中查無營業中紀錄（{'、'.join(warn[:5])}），購買時建議確認來源"
    return s + "。"


# ── API ───────────────────────────────────────────────────────
@router.get("/api/companies/summary", summary="農藥廠商查核統計")
def companies_summary():
    init_table()
    conn = get_db()
    rows = conn.execute("SELECT kind, status_desc, COUNT(*) FROM company_registry GROUP BY kind, status_desc").fetchall()
    item_rows = conn.execute("SELECT pesticide_items FROM company_registry WHERE kind='company'").fetchall()
    last = conn.execute("SELECT MAX(checked_at) FROM company_registry").fetchone()[0]
    conn.close()
    return {"資料來源": SOURCE_NAME, "最近查核": last,
            "統計": [{"類型": r[0], "公司現況": r[1], "家數": r[2]} for r in rows],
            "營業項目查核": {name: sum(1 for (it,) in item_rows if it and name in it) for name in PESTICIDE_ITEMS.values()}
                        | {"未見農藥相關登記": sum(1 for (it,) in item_rows if it == "")}}


@router.get("/api/companies/lookup", summary="查詢單一廠商的公司登記")
def companies_lookup(name: str):
    init_table()
    cached = get_cached([name]).get(name.strip())
    if cached:
        return {"name": name, **cached, "badge": maker_badge(cached), "source": SOURCE_NAME}
    info = lookup_company(name)
    _save(name.strip(), info)
    c = {"kind": info.get("kind"), "ubn": info.get("ubn", ""), "status": info.get("status_desc", ""),
         "capital": info.get("capital", ""), "setup_date": info.get("setup_date", "")}
    return {"name": name, **c, "badge": maker_badge(c), "source": SOURCE_NAME}
