"""拍照問藥：上傳作物照片 → GPT-4o 視覺辨識作物與病蟲害 → 對應到資料庫的登記名稱。

設計原則：
  1. 影像模型只負責「看圖、提出候選」，不推薦任何農藥。
  2. 候選結果一律對應回 SQLite 登記資料中的正式作物／病蟲害名稱，
     使用者確認後才進入 RAG 查詢合法用藥（人機協作，避免辨識錯誤直接導致誤用藥）。
"""
import os
import re
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_db
from rag import CROP_ALIASES

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MAX_IMAGE_CHARS = 6_000_000  # base64 字串上限（約 4.5MB 圖檔）

router = APIRouter()


class IdentifyRequest(BaseModel):
    image: str  # data URL：data:image/jpeg;base64,....


VISION_PROMPT = """你是台灣的植物保護專家，專門協助農民判斷作物病蟲害。請觀察使用者上傳的照片並回傳 JSON。

規則：
1. 一律使用繁體中文與台灣農民常用的名稱。作物用常見名稱（例如：高麗菜、空心菜、番茄、草莓、水稻、芒果）。
   病蟲害用台灣農藥登記常見的名稱（例如：小菜蛾、斜紋夜蛾、蚜蟲類、薊馬類、葉蟎類、粉蝨類、炭疽病、白粉病、露菌病、灰黴病、晚疫病、細菌性軟腐病）。
2. 最多列出 3 個候選病蟲害，依可能性由高到低排序，confidence 為 0 到 1 的小數。
3. 若照片不是植物、看不清楚、或看不出任何病蟲害徵狀，is_plant 或 candidates 照實回報，不要硬猜。
4. 絕對不要推薦任何農藥或用藥方式。
5. 只回傳 JSON，格式如下：
{
  "is_plant": true,
  "crop": "作物名稱（看不出來就留空字串）",
  "observation": "用一到兩句白話描述你在照片中看到的徵狀，例如：葉片上有不規則的小孔洞，葉背可見綠色小蟲",
  "candidates": [
    {"name": "病蟲害名稱", "confidence": 0.7, "reason": "判斷依據，一句話"}
  ],
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
            model="gpt-4o",
            temperature=0,
            max_tokens=600,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "請辨識這張作物照片中的作物與可能的病蟲害。"},
                    {"type": "image_url", "image_url": {"url": req.image, "detail": "high"}},
                ]},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        raise HTTPException(502, f"影像辨識失敗：{e}")

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
        for c in (data.get("candidates") or [])[:3]:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            db_pest = match_pest(name, pests)
            count = 0
            if crop_db and db_pest:
                count = conn.execute(
                    "SELECT COUNT(DISTINCT 農藥中文普通名稱) FROM pesticides WHERE 作物名稱=? AND 病蟲害名稱=?",
                    (crop_db, db_pest),
                ).fetchone()[0]
            try:
                confidence = max(0.0, min(1.0, float(c.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            candidates.append({
                "name": name,
                "confidence": round(confidence, 2),
                "reason": (c.get("reason") or "").strip(),
                "db_pest": db_pest,
                "registered_count": count,
            })
    finally:
        conn.close()

    return {
        "is_plant": bool(data.get("is_plant", True)),
        "crop": crop_name,
        "crop_db": crop_db,
        "observation": (data.get("observation") or "").strip(),
        "candidates": candidates,
        "tip": (data.get("tip") or "").strip(),
    }
