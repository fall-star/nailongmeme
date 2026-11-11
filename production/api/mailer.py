"""Small transactional mail sender for the public API.

Credentials are read from the service environment only.  Never log or return
``MEME_SMTP_PASSWORD``.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

DEFAULT_HOST = "smtpdm.aliyun.com"
DEFAULT_PORT = 465


class MailError(RuntimeError):
    """Raised when the configured mail provider cannot accept a message."""


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    sender_name: str
    security: str


def _config() -> MailConfig:
    host = os.getenv("MEME_SMTP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = int(os.getenv("MEME_SMTP_PORT", str(DEFAULT_PORT)))
    username = os.getenv("MEME_SMTP_USER", "").strip()
    password = os.getenv("MEME_SMTP_PASSWORD", "")
    sender = os.getenv("MEME_SMTP_FROM", username).strip()
    sender_name = os.getenv("MEME_MAIL_FROM_NAME", "奶龙表情库").strip() or "奶龙表情库"
    security = os.getenv("MEME_SMTP_SECURITY", "ssl" if port == 465 else "starttls").strip().lower()
    if not username or not password or not sender:
        raise MailError("SMTP is not configured")
    if security not in {"ssl", "starttls", "none"}:
        raise MailError(f"unsupported SMTP security mode: {security}")
    return MailConfig(host, port, username, password, sender, sender_name, security)


def send_email(to: str, subject: str, text: str, *, retries: int = 2) -> None:
    cfg = _config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((cfg.sender_name, cfg.sender))
    message["To"] = to
    message.set_content(text)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            context = ssl.create_default_context()
            if cfg.security == "ssl":
                smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20, context=context)
            else:
                smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=20)
            try:
                if cfg.security == "starttls":
                    smtp.starttls(context=context)
                smtp.login(cfg.username, cfg.password)
                smtp.send_message(message)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    smtp.close()
            return
        except (OSError, smtplib.SMTPException) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise MailError(f"mail delivery failed: {last_error}")


def registration_email(nickname: str, email: str, site_url: str) -> None:
    send_email(
        email,
        "奶龙表情库 · 注册成功",
        f"你好，{nickname}：\n\n你的奶龙表情库账号注册成功。\n\n站点：{site_url}\n\n如果这不是你的操作，请忽略这封邮件。",
    )


def admin_code_email(email: str, code: str, site_url: str) -> None:
    send_email(
        email,
        "奶龙表情库 · 管理员登录验证码",
        f"管理员登录验证码：{code}\n\n验证码 10 分钟内有效。\n站点：{site_url}\n\n如果不是你本人操作，请立即检查账号安全。",
    )


def approval_email(email: str, nickname: str, title: str, body: str, site_url: str) -> None:
    send_email(
        email,
        f"奶龙表情库 · {title}",
        f"你好，{nickname}：\n\n{body}\n\n站点：{site_url}",
    )
