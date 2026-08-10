"""對話紀錄 API：整筆對話以 JSON 存一欄，綁定 username，僅本人可存取。

換任何裝置、任何人的設備登入同一帳號，都讀得到自己的對話；且看不到別人的。
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import get_db
from auth import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["對話"])


class ConvUpsert(BaseModel):
    id: str
    title: str
    messages: list
    pinned: bool = False
    updated_at: int


class ConvRename(BaseModel):
    title: str


class ConvPin(BaseModel):
    pinned: bool


@router.get("", summary="列出自己的所有對話")
def list_conversations(username: str = Depends(get_current_user)):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title, messages, pinned, updated_at FROM conversations WHERE username=? ORDER BY pinned DESC, updated_at DESC",
        (username,),
    ).fetchall()
    conn.close()
    return [{
        "id": r["id"],
        "title": r["title"],
        "messages": json.loads(r["messages"]),
        "pinned": bool(r["pinned"]),
        "updatedAt": r["updated_at"],
    } for r in rows]


@router.put("/{conv_id}", summary="建立或更新一筆對話")
def upsert_conversation(conv_id: str, body: ConvUpsert, username: str = Depends(get_current_user)):
    conn = get_db()
    conn.execute(
        """INSERT INTO conversations (id, username, title, messages, pinned, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title,
             messages=excluded.messages,
             pinned=excluded.pinned,
             updated_at=excluded.updated_at
           WHERE conversations.username=excluded.username""",
        (conv_id, username, body.title, json.dumps(body.messages, ensure_ascii=False), int(body.pinned), body.updated_at),
    )
    conn.commit()
    conn.close()
    return {"message": "已儲存"}


@router.delete("/{conv_id}", summary="刪除一筆對話")
def delete_conversation_api(conv_id: str, username: str = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=? AND username=?", (conv_id, username))
    conn.commit()
    conn.close()
    return {"message": "已刪除"}


@router.patch("/{conv_id}/rename", summary="重新命名對話")
def rename_conversation_api(conv_id: str, body: ConvRename, username: str = Depends(get_current_user)):
    conn = get_db()
    conn.execute("UPDATE conversations SET title=? WHERE id=? AND username=?", (body.title.strip() or "未命名對話", conv_id, username))
    conn.commit()
    conn.close()
    return {"message": "已更新"}


@router.patch("/{conv_id}/pin", summary="釘選或取消釘選對話")
def pin_conversation_api(conv_id: str, body: ConvPin, username: str = Depends(get_current_user)):
    conn = get_db()
    conn.execute("UPDATE conversations SET pinned=? WHERE id=? AND username=?", (int(body.pinned), conv_id, username))
    conn.commit()
    conn.close()
    return {"message": "已更新"}
