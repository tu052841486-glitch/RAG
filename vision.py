"""拍照問藥：上傳作物照片 → 視覺模型辨識作物與病蟲害 → 對應到資料庫的登記名稱。

設計原則（AI 治理）：
  1. 影像模型只負責「看圖、提出候選」，不推薦任何農藥；用藥資訊一律來自登記資料庫。
  2. 三級判定（🟢 健康／🟡 疑似異常／🔴 明顯病徵），並由程式規則把關：
     信心不足的「明顯病徵」自動降為「疑似異常」，避免過度診斷。
  3. 模型輸出經過防護：照顧建議中若出現藥劑字眼一律濾除。
  4. 候選結果對應回正式作物／病蟲害名稱，使用者確認後才查詢合法用藥（人在迴路中）。
  5. 每次判讀都寫入 diagnosis_log（時間、模型、等級），形成稽核軌跡；不保存照片與帳號。
"""
import os
import re
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db
from rag import CROP_ALIASES
import diagnosis

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-4o")  # 模型層可抽換：改環境變數即可切換
MAX_IMAGE_CHARS = 6_000_000  # base64 字串上限（約 4.5MB 圖檔）
DISEASED_MIN_CONF = 0.5      # 「明顯病徵」最低信心門檻，不足則降為「疑似異常」
HEALTHY_MAX_CONF = 0.6       # 判「健康」卻有高信心候選 → 視為矛盾，改為「疑似異常」

HEALTH_LEVELS = {"healthy", "suspect", "diseased"}
QUALITY_TIPS = {
    "dark": "照片太暗，請在白天或光線充足的地方再拍一次。",
    "blurry": "照片有點模糊，請拿穩手機、點一下螢幕對焦後再拍。",
    "too_far": "距離太遠，請靠近一點，讓受害的葉片、果實或蟲體占畫面一半以上。",
    "not_plant": "這張照片看起來不是作物，請拍攝作物的葉片、果實或莖部。",
}
BANNED_IN_CARE = re.compile(r"藥|劑|噴灑|稀釋|倍數")

router = APIRouter()


class IdentifyRequest(BaseModel):
    image: str  # data URL：data:image/jpeg;base64,....


VISION_PROMPT = """你是台灣的植物保護專家，專門協助農民判斷作物病蟲害。請觀察使用者上傳的照片並回傳 JSON。

規則：
1. 一律使用繁體中文與台灣農民常用的名稱。作物用常見名稱（例如：高麗菜、空心菜、番茄、草莓、水稻、芒果）。
   病蟲害用台灣農藥登記常見的名稱（例如：小菜蛾、斜紋夜蛾、蚜蟲類、薊馬類、葉蟎類、粉蝨類、炭疽病、白粉病、露菌病、灰黴病、晚疫病、細菌性軟腐病）。
2. 先判斷照片品質 quality：
   ok（清楚可判讀）、dark（太暗）、blurry（模糊）、too_far（太遠，受害部位太小）、not_plant（不是作物）。
3. 再判斷健康等級 health：
   healthy（看得清楚，沒有可見的病斑、蟲體或食痕）、
   suspect（有輕微或不典型的異常，無法確定）、
   diseased（有明確、典型的病徵或可見害蟲）。
4. health 為 suspect 或 diseased 時，最多列出 3 個候選病蟲害，依可能性由高到低排序，confidence 為 0 到 1 的小數。
   每個候選要附上 reason（你的判斷依據）與 check（農民可以自己在田間確認的特徵，用來和其他候選區分）。
   health 為 healthy 時 candidates 給空陣列。
5. care_tips：最多 2 點「不用藥」的田間管理建議（例如摘除病葉、改善通風、避免傍晚灑水）。
   絕對不可以提到任何農藥、藥劑、資材名稱或用量。
6. 看不清楚或沒有把握時照實回報，不要硬猜。絕對不要推薦任何農藥或用藥方式。
7. 只回傳 JSON，格式如下：
{
  "is_plant": true,
  "quality": "ok",
  "health": "suspect",
  "crop": "作物名稱（看不出來就留空字串）",
  "observation": "用一到兩句白話描述照片中看到的狀況",
  "candidates": [
    {"name": "病蟲害名稱", "confidence": 0.7, "reason": "判斷依據，一句話", "check": "可自行確認的特徵，一句話"}
  ],
  "care_tips": ["不用藥的管理建議"],
  "tip": "若需要更準確判斷，可以補拍的部位或角度（一句話，可留空）"
}"""


def _load_crops(conn) -> list:
    return [r[0] for r in conn.execute("SELECT DISTINCT 作物名稱 FROM pesticides").fetchall() if r[0]]


def match_crop(name: str, crops: list):
    """把模型回傳的作物俗名，對應到資料庫中的正式作物名稱（資料庫常為「甘藍 高麗菜」這種複合格式）。"""
    name = (name or "").strip()
    if not name:
        return None
    candidates = [name]
    if name in CROP_ALIASES:
        candidates.append(CROP_ALIASES[name])
    for n in candidates:
        for c in crops:
            if c == n:
                return c
        for c in crops:
            parts = [p for p in re.split(r"[\s\u3000、/]+", c) if p]
            if n in parts:
                return c
    for n in candidates:
        for c in crops:
            if len(n) >= 2 and n in c:
                return c
    # 反向比對：模型回傳「小番茄」→ 資料庫「番茄」，取最長的命中避免誤配
    hits = []
    for c in crops:
        for part in re.split(r"[\s\u3000、/]+", c):
            if len(part) >= 2 and part in name:
                hits.append((len(part), c))
    return max(hits)[1] if hits else None


def match_pest(name: str, pests: list):
    """把模型回傳的病蟲害名稱，對應到該作物在資料庫中的登記病蟲害名稱。"""
    name = (name or "").strip()
    if not name or not pests:
        return None
    if name in pests:
        return name
    core = re.sub(r"(類|病)$", "", name)
    contains = [p for p in pests if name in p or (len(core) >= 2 and core in p)]
    if contains:
        return min(contains, key=len)
    inside = [p for p in pests if len(p) >= 2 and p in name]
    if inside:
        return max(inside, key=len)
    return None


def _confidence(c) -> float:
    try:
        return max(0.0, min(1.0, float(c.get("confidence", 0))))
    except (TypeError, ValueError):
        return 0.0


def _normalize(data: dict) -> dict:
    """程式規則把關：品質、健康等級、照顧建議的防護與一致性檢查。"""
    is_plant = bool(data.get("is_plant", True))
    quality = (data.get("quality") or "ok").strip()
    if quality not in QUALITY_TIPS and quality != "ok":
        quality = "ok"
    if not is_plant:
        quality = "not_plant"

    raw = [c for c in (data.get("candidates") or []) if (c.get("name") or "").strip()][:3]
    raw.sort(key=_confidence, reverse=True)
    top = _confidence(raw[0]) if raw else 0.0

    health = (data.get("health") or "").strip()
    if health not in HEALTH_LEVELS:
        health = ("diseased" if top >= DISEASED_MIN_CONF else "suspect") if raw else "healthy"
    if health == "diseased" and (not raw or top < DISEASED_MIN_CONF):
        health = "suspect"
    if health == "healthy" and raw and top >= HEALTHY_MAX_CONF:
        health = "suspect"
    if health == "suspect" and not raw:
        health = "healthy" if quality == "ok" else "unknown"
    if quality != "ok" and not raw:
        health = "unknown"
    if health == "healthy":
        raw = []

    care = [t.strip() for t in (data.get("care_tips") or []) if isinstance(t, str) and t.strip()]
    care = [t for t in care if not BANNED_IN_CARE.search(t)][:2]

    return {"is_plant": is_plant, "quality": quality, "health": health, "raw": raw, "care_tips": care}


@router.post("/api/identify", tags=["拍照問藥"], summary="上傳作物照片辨識病蟲害")
async def identify(req: IdentifyRequest):
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY 未設定")
    if not re.match(r"^data:image/(jpeg|jpg|png|webp);base64,", req.image or ""):
        raise HTTPException(400, "請上傳 JPG、PNG 或 WEBP 圖片")
    if len(req.image) > MAX_IMAGE_CHARS:
        raise HTTPException(413, "圖片太大，請縮小後再上傳")

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=VISION_MODEL,
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "請判斷這張作物照片的品質、健康等級，以及可能的病蟲害。"},
                    {"type": "image_url", "image_url": {"url": req.image, "detail": "high"}},
                ]},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        raise HTTPException(502, f"影像辨識失敗：{e}")

    norm = _normalize(data)

    conn = get_db()
    try:
        crops = _load_crops(conn)
        crop_name = (data.get("crop") or "").strip()
        crop_db = match_crop(crop_name, crops)
        pests = []
        if crop_db:
            pests = [r[0] for r in conn.execute(
                "SELECT DISTINCT 病蟲害名稱 FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱 != ''", (crop_db,)
            ).fetchall()]

        candidates = []
        for c in norm["raw"]:
            name = (c.get("name") or "").strip()
            db_pest = match_pest(name, pests)
            count = 0
            if crop_db and db_pest:
                count = conn.execute(
                    "SELECT COUNT(DISTINCT 農藥中文普通名稱) FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱=?",
                    (crop_db, db_pest),
                ).fetchone()[0]
            candidates.append({
                "name": name,
                "confidence": round(_confidence(c), 2),
                "reason": (c.get("reason") or "").strip(),
                "check": (c.get("check") or "").strip(),
                "db_pest": db_pest,
                "registered_count": count,
            })
    finally:
        conn.close()

    result = {
        "is_plant": norm["is_plant"],
        "quality": norm["quality"],
        "quality_tip": QUALITY_TIPS.get(norm["quality"], ""),
        "health": norm["health"],
        "crop": crop_name,
        "crop_db": crop_db,
        "observation": (data.get("observation") or "").strip(),
        "candidates": candidates,
        "care_tips": norm["care_tips"],
        "tip": (data.get("tip") or "").strip(),
        "model": VISION_MODEL,
    }
    try:
        result["diagnosis_id"] = diagnosis.log_identification(result, VISION_MODEL)
    except Exception as e:  # 紀錄失敗不影響使用者取得判讀結果
        print(f"⚠️ 判讀紀錄寫入失敗：{e}")
        result["diagnosis_id"] = None
    return result