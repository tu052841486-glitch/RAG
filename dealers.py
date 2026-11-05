"""合法農藥販賣業者查詢：串接經濟部商業發展署「商工行政資料開放平臺」。

資料來源（政府資料開放授權條款－第1版，免費）：
  - 營業項目代碼（F 零售、批發及餐飲業）查公司：找出營業項目登記「F207040 農藥零售業」「F107040 農藥批發業」的公司
  - 公司行號營業項目代碼表：取得營業項目代碼的正式名稱
  - 財政部財政資訊中心「全國營業(稅籍)登記資料集」：補上獨資、合夥的「行號」
    （鄉下的「○○農藥行」「○○農業資材行」多半是行號，不在公司登記裡），
    以稅籍登記的行業名稱含「農藥」篩選。

用途：回答農民「附近哪裡可以買農藥？」——依縣市、鄉鎮列出有登記農藥零售／批發的公司與行號。
注意：營業項目有登記農藥零售，不等於持有縣市政府核發的「農藥販賣業執照」，畫面與回答都會提醒購買前確認。

資料每 7 天背景更新一次，存於 SQLite（pesticide_dealers），查詢時只讀資料庫。
"""
import csv
import re
import time
import threading
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Query

from db import get_db

TPE = timezone(timedelta(hours=8))
F_COMPANY_API = "https://data.gcis.nat.gov.tw/od/data/api/C8782705-DA48-4897-8537-9F7B0FC463EF"
ITEM_CODE_API = "https://data.gcis.nat.gov.tw/od/data/api/FCB90AB1-E382-45CE-8D4F-394861851E28"
TAX_CSV_URL = "https://eip.fia.gov.tw/data/BGMOPEN1.csv"   # 全國營業(稅籍)登記資料集（營業中）
SOURCE_NAME = "經濟部商業發展署商工行政資料開放平臺、財政部財政資訊中心全國營業(稅籍)登記資料"
ITEMS = {"F207040": "農藥零售業", "F107040": "農藥批發業"}   # 官方代碼表抓不到時的預設名稱
REFRESH_DAYS = 7
PAGE = 1000

router = APIRouter(tags=["農藥販賣業者"])
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (pesticide-knowledge-system)"
_started = False

COUNTIES = ["臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市",
            "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
            "臺東縣", "澎湖縣", "金門縣", "連江縣"]


def _norm(s: str) -> str:
    return (s or "").replace("台", "臺").strip()


def split_address(addr: str):
    """地址 → (縣市, 鄉鎮市區)。"""
    a = _norm(addr)
    county = next((c for c in COUNTIES if a.startswith(c)), "")
    rest = a[len(county):] if county else a
    m = re.match(r"(.{1,4}?[鄉鎮市區])", rest)
    return county, (m.group(1) if m else "")


def init_table():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS pesticide_dealers (
        ubn TEXT PRIMARY KEY,
        name TEXT, address TEXT, county TEXT, town TEXT,
        capital INTEGER, items TEXT, checked_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_dealer_area ON pesticide_dealers(county, town)')
    conn.commit()
    conn.close()


def _item_names() -> dict:
    """由「公司行號營業項目代碼表」取得正式名稱；失敗時用預設值。"""
    names = dict(ITEMS)
    for code in ITEMS:
        try:
            r = _session.get(f"{ITEM_CODE_API}?$format=json&$filter=Business_Item eq {code}&$skip=0&$top=1", timeout=20)
            data = r.json() if r.status_code == 200 and r.text.strip() else []
            if data:
                d = data[0]
                desc = d.get("Business_Item_Desc") or d.get("Business_Item_Name") or ""
                if desc:
                    names[code] = desc.strip()
        except Exception:
            pass
    return names


def _fetch_code(code: str) -> list:
    out, skip = [], 0
    while True:
        url = f"{F_COMPANY_API}?$format=json&$filter=Business_Item eq {code}&$skip={skip}&$top={PAGE}"
        r = _session.get(url, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"商工資料 API 回應 {r.status_code}")
        if not r.text.strip():
            break
        try:
            rows = r.json()
        except ValueError:
            raise RuntimeError(f"商工資料 API 回傳非 JSON：{r.text[:80]}")
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < PAGE:
            break
        skip += PAGE
        time.sleep(0.5)
    return out


def _tax_lines(resp):
    """逐行解碼稅籍 CSV（自動判斷 UTF-8 或 Big5），不把整個檔案讀進記憶體。"""
    it = resp.iter_lines()
    first = next(it, b"")
    try:
        enc = "utf-8-sig"
        first.decode(enc)
    except UnicodeDecodeError:
        enc = "cp950"
    yield first.decode(enc, errors="replace")
    for raw in it:
        yield raw.decode(enc, errors="replace")


def _item_from_industry(name: str) -> str:
    if "零售" in name:
        return "農藥零售業"
    if "批發" in name:
        return "農藥批發業"
    return name.strip()


def _fetch_tax() -> dict:
    """從全國營業(稅籍)登記資料集找出行業名稱含「農藥」的營業人（含獨資、合夥行號）。"""
    r = _session.get(TAX_CSV_URL, stream=True, timeout=(30, 600))
    if r.status_code != 200:
        raise RuntimeError(f"稅籍資料下載失敗 {r.status_code}")
    reader = csv.reader(_tax_lines(r))
    header = None
    for row in reader:
        if any("統一編號" in (c or "") for c in row):
            header = [(c or "").strip() for c in row]
            break
    if not header:
        raise RuntimeError("稅籍資料找不到欄位名稱")

    def col(*keys, exclude=()):
        for i, h in enumerate(header):
            if all(k in h for k in keys) and not any(x in h for x in exclude):
                return i
        return None

    i_ubn = col("統一編號", exclude=("總機構",))
    i_name = col("營業人名稱")
    i_addr = col("營業地址")
    i_cap = col("資本額")
    i_org = col("組織別")
    ind = [i for i, h in enumerate(header) if h.startswith("名稱") or ("行業" in h and "名稱" in h)]
    if i_ubn is None or i_name is None or i_addr is None or not ind:
        raise RuntimeError(f"稅籍資料欄位不符：{header}")

    out = {}
    for row in reader:
        if len(row) <= max(i_ubn, i_name, i_addr, *ind):
            continue
        hits = [row[i] for i in ind if "農藥" in (row[i] or "")]
        if not hits:
            continue
        ubn = row[i_ubn].strip()
        if not ubn:
            continue
        try:
            cap = int(float(row[i_cap] or 0)) if i_cap is not None else 0
        except ValueError:
            cap = 0
        out[ubn] = {
            "name": row[i_name].strip(),
            "address": _norm(row[i_addr]),
            "capital": cap,
            "org": (row[i_org].strip() if i_org is not None and len(row) > i_org else ""),
            "items": {_item_from_industry(h) for h in hits},
        }
    print(f"🧾 稅籍資料：行業含「農藥」的營業人 {len(out)} 家")
    return out


def refresh() -> dict:
    init_table()
    merged = {}
    errors = []
    try:
        names = _item_names()
        company_rows = [(code, row) for code in ITEMS for row in _fetch_code(code)]
    except Exception as e:
        errors.append(f"商工資料：{e}")
        names, company_rows = dict(ITEMS), []
    for code, row in company_rows:
        if True:
            ubn = (row.get("Business_Accounting_NO") or "").strip()
            if not ubn:
                continue
            rec = merged.setdefault(ubn, {
                "name": (row.get("Company_Name") or "").strip(),
                "address": _norm(row.get("Company_Location")),
                "capital": row.get("Capital_Stock_Amount") or 0,
                "items": set(),
            })
            rec["items"].add(names.get(code, ITEMS[code]))
    n_company = len(merged)
    try:
        for ubn, t in _fetch_tax().items():
            if ubn in merged:
                merged[ubn]["items"] |= t["items"]
            else:
                merged[ubn] = t
    except Exception as e:
        errors.append(f"稅籍資料：{e}")
    for e in errors:
        print(f"⚠️ 農藥販賣業者部分來源失敗：{e}")
    if not merged:
        raise RuntimeError("沒有取得任何農藥販賣業者資料；" + "；".join(errors))
    now = datetime.now(TPE).strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM pesticide_dealers")
        conn.executemany(
            "INSERT INTO pesticide_dealers (ubn, name, address, county, town, capital, items, checked_at) VALUES (?,?,?,?,?,?,?,?)",
            [(u, r["name"], r["address"], *split_address(r["address"]), int(float(r["capital"] or 0)),
              "、".join(sorted(r["items"])), now) for u, r in merged.items()],
        )
    conn.close()
    print(f"🏪 農藥販賣業者更新完成：共 {len(merged)} 家（商工公司 {n_company} 家，其餘為稅籍行號）")
    return {"total": len(merged), "company": n_company, "errors": errors}


def start_background_refresh():
    global _started
    if _started:
        return
    _started = True

    def _loop():
        time.sleep(90)  # 等服務與索引啟動完成
        while True:
            try:
                refresh()
            except Exception as e:
                print(f"⚠️ 農藥販賣業者更新失敗：{e}")
            time.sleep(REFRESH_DAYS * 86400)

    threading.Thread(target=_loop, daemon=True).start()


# ── 查詢 ──────────────────────────────────────────────────────
def find_location(text: str):
    """從問句找出縣市與鄉鎮（支援「嘉義民雄」「台中」「民雄鄉」等說法）。
    「嘉義」「新竹」同時對應市與縣時，以鄉鎮名稱判斷；判斷不出來就兩者都查。"""
    t = _norm(text)
    full = [c for c in COUNTIES if c in t]
    cands = full or [c for c in COUNTIES if c[:2] in t]
    try:
        conn = get_db()
        sql = "SELECT DISTINCT county, town FROM pesticide_dealers WHERE town != ''"
        params = []
        if cands:
            sql += f" AND county IN ({','.join('?' * len(cands))})"
            params = cands
        towns = conn.execute(sql, params).fetchall()
        conn.close()
    except Exception:
        towns = []
    hits = [(c, tw) for c, tw in towns if tw and (tw in t or (len(tw) >= 3 and tw[:-1] in t))]
    if hits:
        c, tw = max(hits, key=lambda x: len(x[1]))
        return [c], tw
    return cands, ""


def query_dealers(county="", town: str = "", limit: int = 20) -> dict:
    """county 可以是單一縣市字串或縣市清單。"""
    init_table()
    counties = [_norm(c) for c in (county if isinstance(county, (list, tuple)) else [county]) if c]
    conn = get_db()
    where, params = [], []
    if counties:
        where.append(f"county IN ({','.join('?' * len(counties))})"); params.extend(counties)
    if town:
        where.append("town = ?"); params.append(town)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM pesticide_dealers {w}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT ubn, name, address, county, town, capital, items FROM pesticide_dealers {w} "
        "ORDER BY (items LIKE '%零售%') DESC, capital DESC LIMIT ?", params + [limit]).fetchall()
    retail = conn.execute(f"SELECT COUNT(*) FROM pesticide_dealers {w}{' AND' if w else ' WHERE'} items LIKE '%零售%'",
                          params).fetchone()[0]
    checked = conn.execute("SELECT MAX(checked_at) FROM pesticide_dealers").fetchone()[0]
    conn.close()
    return {
        "county": "、".join(counties), "town": town, "total": total, "retail": retail, "checked_at": checked,
        "source": SOURCE_NAME,
        "dealers": [{"ubn": r[0], "name": r[1], "address": r[2], "county": r[3], "town": r[4],
                     "capital": r[5], "items": r[6]} for r in rows],
    }


def dealer_context(res: dict) -> str:
    area = (res.get("county") or "") + (res.get("town") or "")
    if not res.get("total"):
        return (f"【合法農藥販賣業者查詢（{SOURCE_NAME}）】{area or '該地區'}查無登記農藥零售或批發的公司或行號。"
                "請建議農友改查鄰近鄉鎮，或洽詢當地農會。")
    lines = [f"【合法農藥販賣業者查詢（{SOURCE_NAME}）】{area}共有 {res['total']} 家公司或行號登記農藥零售或批發"
             f"（其中零售 {res['retail']} 家），畫面會列出清單與地址。"]
    for d in res["dealers"][:5]:
        lines.append(f"・{d['name']}（{d['items']}）：{d['address']}")
    lines.append("提醒：營業項目登記不等於持有農藥販賣業執照，購買前請確認店家掛有縣市政府核發的農藥販賣業執照，並確認產品有農藥許可證字號。")
    return "\n".join(lines)


# ── API ───────────────────────────────────────────────────────
@router.get("/api/dealers", summary="依縣市鄉鎮查詢登記農藥零售／批發的公司與行號")
def dealers_api(county: str = Query(""), town: str = Query(""), limit: int = Query(30, le=200)):
    return query_dealers(county, town, limit)


@router.get("/api/dealers/summary", summary="各縣市農藥販賣業者家數")
def dealers_summary():
    init_table()
    conn = get_db()
    rows = conn.execute("SELECT county, COUNT(*), SUM(items LIKE '%零售%') FROM pesticide_dealers "
                        "GROUP BY county ORDER BY COUNT(*) DESC").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM pesticide_dealers").fetchone()[0]
    checked = conn.execute("SELECT MAX(checked_at) FROM pesticide_dealers").fetchone()[0]
    conn.close()
    return {"資料來源": SOURCE_NAME, "最近更新": checked, "總家數": total,
            "各縣市": [{"縣市": r[0] or "（未辨識）", "家數": r[1], "零售": r[2]} for r in rows]}
