"""用藥決策引擎：輪替用藥計畫。

把「可以用哪些藥」升級成「接下來該怎麼噴」：在同時滿足以下條件下，排出可執行的噴藥時程——
  1. 安全採收期：每一次噴藥日 + 該藥安全採收期 ≤ 預計採收日
  2. 施藥間隔：兩次噴藥至少間隔上一款藥登記的施藥間隔天數
  3. 施用次數：同一款藥不超過登記的施用次數
  4. 抗藥性管理：連續兩次噴藥使用不同作用機制（IRAC／FRAC／HRAC 分類），整份計畫盡量涵蓋多種作用機制
  5. 廠商查核：優先選用登記廠商仍營業中的產品

所有天數與數值都由資料庫與演算法計算，生成式 AI 只負責用白話說明，不參與排程。
"""
import re
from datetime import date, timedelta

from db import get_db

DEFAULT_INTERVAL = 7   # 未登記施藥間隔時的保守預設天數
MAX_SPRAYS = 4
MOA_RE = re.compile(r"(IRAC|FRAC|HRAC)\s*[:：]?\s*([0-9A-Za-z]+(?:\s*[,，、/+＋&]\s*[0-9A-Za-z]+)*)", re.I)


# ── 欄位解析 ──────────────────────────────────────────────────
def _first_int(text):
    m = re.search(r"\d+", str(text or ""))
    return int(m.group()) if m else None


def parse_phi(v):
    """安全採收期；'-' 或空白回傳 None（未登記）。"""
    return _first_int(v)


def parse_interval(v):
    """施藥間隔：'7-10' 取下限 7（最短可再噴的天數）；未登記用預設值。"""
    n = _first_int(v)
    return n if n else DEFAULT_INTERVAL


def parse_times(v):
    """施用次數：'共2次'、'最多2次'、'連續3次'、'3-4'（取下限）；未登記回傳 None（不限）。"""
    return _first_int(v)


def parse_moa(text):
    """作用機制 → 分類集合，例如 'IRAC: 3A, 28' → {'IRAC 3A', 'IRAC 28'}。"""
    groups = set()
    for system, codes in MOA_RE.findall(str(text or "")):
        for c in re.split(r"[\s,，、/+＋&]+", codes):
            if c:
                groups.add(f"{system.upper()} {c.upper()}")
    return groups


# ── 讀取候選藥劑 ──────────────────────────────────────────────
def init_moa_table():
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS pesticide_moa (name TEXT PRIMARY KEY, moa TEXT)")
    conn.commit()
    conn.close()


def load_candidates(crop: str, pest: str) -> list:
    init_moa_table()
    conn = get_db()
    rows = conn.execute(
        "SELECT p.農藥中文普通名稱 AS name, p.稀釋倍數 AS dilution, p.每公頃每次用量 AS dosage, "
        "p.使用時期 AS timing, p.施藥間隔 AS interval, p.施用次數 AS times, p.安全採收期_天 AS phi, "
        "p.農藥含量 AS content, p.劑型 AS formulation, p.廠商名稱 AS maker, m.moa AS moa "
        "FROM pesticides p LEFT JOIN pesticide_moa m ON m.name = p.農藥中文普通名稱 "
        "WHERE p.作物名稱 = ? AND p.病蟲害名稱 = ? GROUP BY p.農藥中文普通名稱",
        (crop, pest),
    ).fetchall()
    conn.close()

    try:
        import gcis
        makers = gcis.get_cached(r["maker"] for r in rows)
    except Exception:
        gcis, makers = None, {}

    cands = []
    for r in rows:
        d = dict(r)
        badge = gcis.maker_badge(makers.get(d.get("maker") or "")) if (gcis and d.get("maker")) else None
        cands.append({
            "name": d["name"],
            "dilution": (d.get("dilution") or "").strip(" -"),
            "dosage": (d.get("dosage") or "").strip(" -"),
            "timing": (d.get("timing") or "").strip(" -"),
            "content": d.get("content") or "",
            "formulation": d.get("formulation") or "",
            "maker": d.get("maker") or "",
            "maker_badge": badge,
            "phi": parse_phi(d.get("phi")),
            "interval": parse_interval(d.get("interval")),
            "times": parse_times(d.get("times")),
            "moa_text": d.get("moa") or "",
            "moa": parse_moa(d.get("moa")),
        })
    return cands


# ── 排程演算法 ────────────────────────────────────────────────
def _base_score(c: dict) -> float:
    s = 0.0
    if c["moa"]:
        s += 3                                  # 有作用機制資料，才能確保輪替
    if c["maker_badge"] and c["maker_badge"].get("level") == "ok":
        s += 2                                  # 廠商營業中
    if c["maker_badge"] and c["maker_badge"].get("level") == "warn":
        s -= 2
    if c["dilution"] or c["dosage"]:
        s += 1                                  # 用量資料完整
    if c["phi"] is not None:
        s += max(0.0, 2 - c["phi"] / 10)        # 安全採收期越短越有彈性
    return s


def _fits(c, day, harvest_day):
    if harvest_day is None:
        return True
    if c["phi"] is None:
        return False                            # 未登記安全採收期，不排進有採收期限的計畫
    return day + c["phi"] <= harvest_day


def make_plan(crop: str, pest: str, days_to_harvest=None, sprays=None, start: date = None) -> dict:
    cands = load_candidates(crop, pest)
    crop_display = crop.split()[-1] if " " in crop else crop
    result = {
        "crop": crop, "crop_display": crop_display, "pest": pest,
        "days_to_harvest": days_to_harvest, "candidates": len(cands),
        "steps": [], "warnings": [], "harvest_ready_day": None,
    }
    if not cands:
        result["warnings"].append(f"資料庫查無「{crop_display}」防治「{pest}」的登記用藥，請洽詢當地農會或植物醫師。")
        return result

    n = min(int(sprays or 3), MAX_SPRAYS)
    if days_to_harvest is not None:
        # 噴藥次數不超過採收前可容納的次數
        n = max(1, min(n, days_to_harvest // DEFAULT_INTERVAL + 1))

    pool = sorted(cands, key=_base_score, reverse=True)[:15]
    best = {"score": float("-inf"), "seq": []}

    def dfs(seq, day, used_count, score):
        if len(seq) > len(best["seq"]) or (len(seq) == len(best["seq"]) and score > best["score"]):
            best.update(score=score, seq=list(seq))
        if len(seq) == n:
            return
        prev = seq[-1][1] if seq else None
        for c in pool:
            if c["times"] is not None and used_count.get(c["name"], 0) >= c["times"]:
                continue
            if not _fits(c, day, days_to_harvest):
                continue
            s = _base_score(c)
            if prev is not None:
                if c["name"] == prev["name"]:
                    s -= 4                      # 同一款藥連續使用
                if c["moa"] and prev["moa"]:
                    if c["moa"] & prev["moa"]:
                        continue                # 連續兩次作用機制相同 → 不允許
                    s += 2
                else:
                    s -= 1                      # 無法確認輪替
            seen = set().union(*(x[1]["moa"] for x in seq)) if seq else set()
            if c["moa"] and not (c["moa"] & seen):
                s += 1.5                        # 整份計畫涵蓋更多作用機制
            used_count[c["name"]] = used_count.get(c["name"], 0) + 1
            seq.append((day, c))
            dfs(seq, day + c["interval"], used_count, score + s)
            seq.pop()
            used_count[c["name"]] -= 1

    dfs([], 0, {}, 0.0)

    if not best["seq"]:
        result["warnings"].append(
            f"距離採收只剩 {days_to_harvest} 天，沒有任何登記藥劑的安全採收期夠短。"
            "建議改用物理或耕作防治（如摘除受害葉片、黏蟲紙），或延後採收，並洽詢當地植物醫師。")
        return result

    seq = best["seq"]
    for i, (day, c) in enumerate(seq):
        nxt = seq[i + 1][0] if i + 1 < len(seq) else None
        reasons = []
        if c["phi"] is not None:
            reasons.append(f"安全採收期 {c['phi']} 天")
        if c["moa"]:
            if i == 0:
                reasons.append(f"作用機制 {'、'.join(sorted(c['moa']))}")
            elif seq[i - 1][1]["moa"]:
                reasons.append(f"換成作用機制 {'、'.join(sorted(c['moa']))}，與上一次不同，降低抗藥性風險")
            else:
                reasons.append(f"作用機制 {'、'.join(sorted(c['moa']))}")
        if c["maker_badge"] and c["maker_badge"].get("level") == "ok":
            reasons.append("登記廠商營業中")
        result["steps"].append({
            "order": i + 1,
            "day": day + 1,                     # 以「第 1 天」為開始噴藥日
            "date": (start + timedelta(days=day)).isoformat() if start else None,
            "name": c["name"],
            "dilution": c["dilution"],
            "dosage": c["dosage"],
            "timing": c["timing"],
            "phi": c["phi"],
            "moa": sorted(c["moa"]),
            "moa_text": c["moa_text"],
            "maker": c["maker"],
            "maker_badge": c["maker_badge"],
            "next_after_days": (nxt - day) if nxt is not None else None,
            "reason": "；".join(reasons),
        })

    ready = max(day + (c["phi"] or 0) for day, c in seq)
    result["harvest_ready_day"] = ready + 1
    if len(seq) < n:
        result["warnings"].append(
            f"在採收期限內只能安排 {len(seq)} 次噴藥（原本希望 {n} 次），若蟲害嚴重請搭配物理防治。")
    known = [c for _, c in seq if c["moa"]]
    if len(known) < len(seq):
        result["warnings"].append("部分藥劑缺少作用機制資料，無法完全確認輪替效果，建議參考農藥標示上的作用機制代碼。")
    if days_to_harvest is None:
        result["warnings"].append("未提供預計採收日，計畫未考慮採收期限；請告訴我距離採收還有幾天，可重新排出更安全的計畫。")
    return result


def plan_context(plan: dict) -> str:
    """給 LLM 的計畫摘要（數字來自演算法，LLM 只能照此說明）。"""
    if not plan.get("steps"):
        return "【輪替用藥計畫】" + "；".join(plan.get("warnings") or ["無法排出計畫"])
    lines = [f"【輪替用藥計畫（演算法排程，數字不得更改）】{plan['crop_display']}・{plan['pest']}"
             + (f"・距離採收 {plan['days_to_harvest']} 天" if plan.get("days_to_harvest") is not None else "")]
    for s in plan["steps"]:
        lines.append(f"第 {s['day']} 天：{s['name']}（稀釋 {s['dilution'] or '依標示'} 倍；{s['reason']}）")
    lines.append(f"以今天為第 1 天起算，第 {plan['harvest_ready_day']} 天起即可安全採收"
                 + (f"（預計採收日為第 {plan['days_to_harvest'] + 1} 天，符合安全採收期）。" if plan.get("days_to_harvest") is not None else "。"))
    for w in plan.get("warnings") or []:
        lines.append(f"提醒：{w}")
    return "\n".join(lines)


def save_moa_from_scrape(df) -> int:
    """自動更新時，把官網的「作用機制」欄存成 農藥名稱 → 作用機制 對照表。"""
    if df is None or "作用機制" not in getattr(df, "columns", []):
        return 0
    init_moa_table()
    sub = df[["農藥中文普通名稱", "作用機制"]].copy()
    sub = sub[(sub["農藥中文普通名稱"].str.strip() != "") & (sub["作用機制"].str.strip() != "")]
    if sub.empty:
        return 0
    mapping = sub.groupby("農藥中文普通名稱")["作用機制"].agg(lambda s: s.value_counts().index[0]).to_dict()
    conn = get_db()
    with conn:
        conn.executemany("INSERT OR REPLACE INTO pesticide_moa (name, moa) VALUES (?, ?)", list(mapping.items()))
    conn.close()
    return len(mapping)
