"""帳號系統：註冊、登入、登出、密碼雜湊與登入狀態驗證。

get_current_user 供其他模組（農藥查詢、模擬考、對話等需要登入的 API）
以 Depends(get_current_user) 方式重複使用。
"""
import re
import hashlib
import secrets
from datetime import date

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from db import get_db

router = APIRouter(prefix="/api/auth", tags=["帳號"])


class AuthRequest(BaseModel):
    username: str
    password: str


def hash_password(password: str, salt: str = None):
    """用 PBKDF2-HMAC-SHA256 加鹽雜湊密碼，避免資料庫存明碼。"""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return salt, digest.hex()


def validate_password_strength(password: str):
    """密碼強度規則：至少 6 碼，且須同時包含大寫英文、小寫英文、數字。"""
    if len(password) < 6:
        raise HTTPException(400, "密碼長度至少需要 6 個字元")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(400, "密碼需包含至少一個大寫英文字母")
    if not re.search(r"[a-z]", password):
        raise HTTPException(400, "密碼需包含至少一個小寫英文字母")
    if not re.search(r"[0-9]", password):
        raise HTTPException(400, "密碼需包含至少一個數字")


def get_current_user(authorization: str = Header(None)):
    """從 Authorization: Bearer <token> 標頭驗證登入狀態，驗證失敗回 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登入或憑證已失效，請重新登入")
    token = authorization.split(" ", 1)[1].strip()
    conn = get_db()
    row = conn.execute("SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "未登入或憑證已失效，請重新登入")
    return row["username"]


@router.post("/register", summary="註冊新帳號")
def register(req: AuthRequest):
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(400, "帳號與密碼皆不可為空")
    validate_password_strength(req.password)
    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(400, "此帳號已被註冊，請直接登入或換一個帳號")
    salt, pw_hash = hash_password(req.password)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (username, pw_hash, salt, str(date.today())),
    )
    conn.commit()
    conn.close()
    return {"message": "註冊成功，請登入"}


@router.post("/login", summary="登入")
def login(req: AuthRequest):
    username = req.username.strip()
    conn = get_db()
    row = conn.execute("SELECT password_hash, salt FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(401, "帳號或密碼錯誤")
    _, pw_hash = hash_password(req.password, row["salt"])
    if pw_hash != row["password_hash"]:
        conn.close()
        raise HTTPException(401, "帳號或密碼錯誤")
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, username, created_at) VALUES (?, ?, ?)",
        (token, username, str(date.today())),
    )
    conn.commit()
    conn.close()
    return {"token": token, "username": username}


@router.post("/logout", summary="登出")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
    return {"message": "已登出"}


@router.get("/me", summary="取得目前登入的使用者")
def me(username: str = Depends(get_current_user)):
    return {"username": username}
