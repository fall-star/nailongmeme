"""Account, session, avatar and email-code authentication.

The public site uses a small SQLite database under ``data/auth``.  Passwords
are stored with scrypt; session and email-code secrets are stored as hashes.
The admin account must complete an email code challenge on every login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response as PlainResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps

from .mailer import MailError, admin_code_email, registration_email

LOGGER = logging.getLogger("meme_library.auth")
ROOT = Path(os.getenv("MEME_DATA_DIR", "/home/nailong/api"))
AUTH_DIR = ROOT / "data" / "auth"
AVATAR_DIR = ROOT / "data" / "avatars"
DB_PATH = AUTH_DIR / "auth.db"
COOKIE_NAME = "nailong_session"
COOKIE_SECURE = os.getenv("MEME_COOKIE_SECURE", "1") != "0"
SESSION_DAYS = 7
ADMIN_SESSION_DAYS = 1
CODE_MINUTES = 10
NICKNAME_RE = re.compile(r"^[^\W_][\w\u3400-\u9fff-]{1,23}$", re.UNICODE)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SITE_URL = os.getenv("MEME_PUBLIC_BASE_URL", "https://nailong.fall-star.top").rstrip("/")
SESSION_COOKIE = "nailong_session"

router = APIRouter(prefix="/api/auth", tags=["auth"])
account_router = APIRouter(prefix="/api/account", tags=["account"])
avatar_router = APIRouter(prefix="/api/avatars", tags=["avatars"])


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _db() -> sqlite3.Connection:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_auth() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL,
                nickname_key TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                email_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                avatar_path TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                ip TEXT,
                user_agent TEXT
            );
            CREATE TABLE IF NOT EXISTS email_codes (
                challenge_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_codes_user ON email_codes(user_id, purpose);
            """
        )
    _seed_admin()


def _seed_admin() -> None:
    nickname = os.getenv("MEME_ADMIN_NICKNAME", "").strip()
    email = os.getenv("MEME_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("MEME_ADMIN_PASSWORD", "")
    if not nickname or not email or len(password) < 6:
        LOGGER.info("admin seed skipped: MEME_ADMIN_* is not configured")
        return
    with _db() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE nickname_key=? OR email_key=?",
            (nickname.casefold(), email),
        ).fetchone()
        if exists:
            return
        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users(id,nickname,nickname_key,email,email_key,password_hash,is_admin,created_at) VALUES(?,?,?,?,?,?,1,?)",
            (user_id, nickname, nickname.casefold(), email, email, _hash_password(password), _iso()),
        )
        LOGGER.info("seeded admin account %s", nickname)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_text, digest_text = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _normalise_nickname(value: str) -> str:
    nickname = str(value or "").strip()
    if not NICKNAME_RE.fullmatch(nickname):
        raise HTTPException(400, "昵称需要 2–24 个字符，只能包含中文、字母、数字、下划线或短横线")
    return nickname


def _normalise_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "邮箱格式不正确")
    return email


def _normalise_password(value: str) -> str:
    password = str(value or "")
    if len(password) < 6:
        raise HTTPException(400, "密码至少需要 6 位")
    if len(password) > 128:
        raise HTTPException(400, "密码不能超过 128 位")
    return password


def _user_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    user_id = item["id"]
    avatar = f"/api/avatars/{user_id}" if (AVATAR_DIR / f"{user_id}.webp").is_file() else "/api/avatars/default"
    return {
        "id": user_id,
        "nickname": item["nickname"],
        "email": item["email"],
        "avatar_url": avatar,
        "is_admin": bool(item.get("is_admin", 0)),
    }


def _set_session(response: Response, user: dict[str, Any], token: str) -> None:
    days = ADMIN_SESSION_DAYS if user.get("is_admin") else SESSION_DAYS
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=days * 86400,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _create_session(conn: sqlite3.Connection, user_id: str, request: Request, *, admin: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    days = ADMIN_SESSION_DAYS if admin else SESSION_DAYS
    now = _now()
    conn.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen_at,ip,user_agent) VALUES(?,?,?,?,?,?,?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            user_id,
            _iso(now),
            _iso(now + timedelta(days=days)),
            _iso(now),
            (request.client.host if request.client else "")[:64],
            (request.headers.get("user-agent", ""))[:300],
        ),
    )
    return token


def get_current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise HTTPException(401, "请先登录")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
            (token_hash, _iso()),
        ).fetchone()
        if row is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (_iso(), token_hash))
    return _user_payload(row)


def require_user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return user


def _code_hash(challenge_id: str, code: str) -> str:
    return hashlib.sha256(f"{challenge_id}:{code}".encode()).hexdigest()


def _send_code(user: dict[str, Any]) -> str:
    challenge_id = uuid.uuid4().hex
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    with _db() as conn:
        conn.execute(
            "INSERT INTO email_codes(challenge_id,user_id,purpose,code_hash,expires_at,created_at) VALUES(?,?,?,?,?,?)",
            (challenge_id, user["id"], "admin_login", _code_hash(challenge_id, code), _iso(now + timedelta(minutes=CODE_MINUTES)), _iso(now)),
        )
    try:
        admin_code_email(user["email"], code, SITE_URL)
    except MailError as exc:
        with _db() as conn:
            conn.execute("DELETE FROM email_codes WHERE challenge_id=?", (challenge_id,))
        LOGGER.error("admin code mail failed: %s", exc)
        raise HTTPException(503, "验证码邮件发送失败，请检查邮件服务") from exc
    return challenge_id


class RegisterRequest(BaseModel):
    nickname: str = Field(min_length=2, max_length=24)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class VerifyCodeRequest(BaseModel):
    challenge_id: str = Field(min_length=16, max_length=64)
    code: str = Field(min_length=6, max_length=6)


class ResendCodeRequest(BaseModel):
    challenge_id: str = Field(min_length=16, max_length=64)


class AvatarUpload(BaseModel):
    pass


@router.post("/register", status_code=201)
def register(body: RegisterRequest, request: Request, response: Response) -> dict[str, Any]:
    nickname = _normalise_nickname(body.nickname)
    email = _normalise_email(body.email)
    password = _normalise_password(body.password)
    user_id = uuid.uuid4().hex
    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO users(id,nickname,nickname_key,email,email_key,password_hash,is_admin,created_at) VALUES(?,?,?,?,?,?,0,?)",
                (user_id, nickname, nickname.casefold(), email, email, _hash_password(password), _iso()),
            )
        except sqlite3.IntegrityError as exc:
            text = str(exc)
            if "nickname_key" in text:
                raise HTTPException(409, "昵称已被使用") from exc
            if "email_key" in text:
                raise HTTPException(409, "邮箱已被注册") from exc
            raise HTTPException(409, "昵称或邮箱已被使用") from exc
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        token = _create_session(conn, user_id, request)
    _set_session(response, _user_payload(row), token)
    mail_sent = True
    try:
        registration_email(nickname, email, SITE_URL)
    except MailError as exc:
        mail_sent = False
        LOGGER.error("registration mail failed for %s: %s", user_id, exc)
    return {"user": _user_payload(row), "mail_sent": mail_sent}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> Any:
    identifier = body.identifier.strip()
    password = body.password
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE nickname_key=? OR email_key=?",
            (identifier.casefold(), identifier.lower()),
        ).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            raise HTTPException(401, "账号或密码错误")
        if row["is_admin"]:
            user = _user_payload(row)
    if row["is_admin"]:
        challenge_id = _send_code(user)
        masked = user["email"][:2] + "****" + user["email"].split("@", 1)[-1]
        return JSONResponse(
            {"requires_email_code": True, "challenge_id": challenge_id, "masked_email": masked, "user": user},
            headers={"Cache-Control": "no-store"},
        )
    with _db() as conn:
        token = _create_session(conn, row["id"], request)
    payload = _user_payload(row)
    _set_session(response, payload, token)
    response.headers["Cache-Control"] = "no-store"
    return {"requires_email_code": False, "user": payload}


@router.post("/verify-code")
def verify_code(body: VerifyCodeRequest, request: Request, response: Response) -> dict[str, Any]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM email_codes WHERE challenge_id=?", (body.challenge_id,)).fetchone()
        if row is None or row["used_at"] is not None:
            raise HTTPException(400, "验证码无效或已使用")
        if _parse(row["expires_at"]) < _now():
            raise HTTPException(400, "验证码已过期")
        if row["attempts"] >= 5:
            raise HTTPException(429, "验证码错误次数过多，请重新登录")
        if not hmac.compare_digest(row["code_hash"], _code_hash(body.challenge_id, body.code)):
            conn.execute("UPDATE email_codes SET attempts=attempts+1 WHERE challenge_id=?", (body.challenge_id,))
            raise HTTPException(401, "验证码错误")
        conn.execute("UPDATE email_codes SET used_at=? WHERE challenge_id=?", (_iso(), body.challenge_id))
        user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if user is None:
            raise HTTPException(400, "用户不存在")
        token = _create_session(conn, user["id"], request, admin=bool(user["is_admin"]))
    payload = _user_payload(user)
    _set_session(response, payload, token)
    response.headers["Cache-Control"] = "no-store"
    return {"user": payload}


@router.post("/resend-code")
def resend_code(body: ResendCodeRequest) -> dict[str, Any]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM email_codes WHERE challenge_id=?", (body.challenge_id,)).fetchone()
        if row is None or row["used_at"] is not None or _parse(row["expires_at"]) < _now():
            raise HTTPException(400, "验证码请求已失效，请重新登录")
        if (_now() - _parse(row["created_at"])).total_seconds() < 60:
            raise HTTPException(429, "请 60 秒后再发送验证码")
        user_row = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if user_row is None:
            raise HTTPException(400, "用户不存在")
        user = _user_payload(user_row)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = _now()
        conn.execute(
            "UPDATE email_codes SET code_hash=?,expires_at=?,created_at=?,attempts=0 WHERE challenge_id=?",
            (_code_hash(body.challenge_id, code), _iso(now + timedelta(minutes=CODE_MINUTES)), _iso(now), body.challenge_id),
        )
    try:
        admin_code_email(user["email"], code, SITE_URL)
    except MailError as exc:
        raise HTTPException(503, "验证码邮件发送失败") from exc
    return {"ok": True, "challenge_id": body.challenge_id, "masked_email": user["email"][:2] + "****" + user["email"].split("@", 1)[-1]}


@router.get("/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        with _db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


def _default_avatar_svg(nickname: str = "") -> str:
    initial = (nickname or "奶").strip()[:1]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect width="128" height="128" rx="64" fill="#ffd84d"/><circle cx="46" cy="54" r="7" fill="#22313b"/><circle cx="82" cy="54" r="7" fill="#22313b"/><path d="M44 78c10 13 30 13 40 0" fill="none" stroke="#22313b" stroke-width="6" stroke-linecap="round"/><text x="64" y="112" text-anchor="middle" font-family="sans-serif" font-size="18" font-weight="700" fill="#22313b">{initial}</text></svg>'''


@avatar_router.get("/default")
def default_avatar() -> Response:
    return Response(_default_avatar_svg(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@avatar_router.get("/{user_id}")
def avatar(user_id: str) -> Response:
    if not USER_ID_RE.fullmatch(user_id):
        raise HTTPException(404, "avatar not found")
    path = AVATAR_DIR / f"{user_id}.webp"
    if path.is_file():
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})
    return Response(_default_avatar_svg(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@account_router.post("/avatar")
async def upload_avatar(request: Request, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    body = await request.body()
    if not body:
        raise HTTPException(400, "头像文件为空")
    if len(body) > 5 * 1024 * 1024:
        raise HTTPException(413, "头像不能超过 5 MB")
    try:
        with Image.open(__import__("io").BytesIO(body)) as opened:
            opened.verify()
        with Image.open(__import__("io").BytesIO(body)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            image = ImageOps.fit(image, (256, 256), method=Image.Resampling.LANCZOS)
            target = AVATAR_DIR / f"{user['id']}.webp"
            fd, temp_name = tempfile.mkstemp(prefix=".avatar-", suffix=".webp", dir=str(AVATAR_DIR))
            os.close(fd)
            try:
                image.save(temp_name, "WEBP", quality=88, method=6)
                os.replace(temp_name, target)
            finally:
                Path(temp_name).unlink(missing_ok=True)
    except (OSError, ValueError, Image.UnidentifiedImageError) as exc:
        raise HTTPException(400, "头像必须是有效图片") from exc
    return {"avatar_url": f"/api/avatars/{user['id']}"}


def user_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user["id"],
        "user_nickname": user["nickname"],
        "user_email": user["email"],
        "user_avatar_url": user["avatar_url"],
    }
