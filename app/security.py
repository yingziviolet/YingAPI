"""密钥相关:渠道 API key 的 Fernet 加密存储、虚拟 key 生成与哈希。"""
import hashlib
import logging
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import Settings

logger = logging.getLogger("gateway.security")

VIRTUAL_KEY_PREFIX = "sk-gw-"


def load_fernet(settings: Settings) -> Fernet:
    """加载 Fernet 密钥;未配置时生成并持久化到文件(开发便利,生产应显式配置 GW_SECRET_KEY)。"""
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    path = Path(settings.secret_key_file)
    if path.exists():
        return Fernet(path.read_bytes().strip())
    key = Fernet.generate_key()
    # 0o600 + O_EXCL:密钥文件不给同机其他用户读;并发生成时输家读赢家的文件
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return Fernet(path.read_bytes().strip())
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.warning(
        "GW_SECRET_KEY not set; generated a new key at %s (dev only — set it explicitly in production)",
        path,
    )
    return Fernet(key)


def encrypt_api_key(fernet: Fernet, raw: str) -> str:
    return fernet.encrypt(raw.encode()).decode()


def decrypt_api_key(fernet: Fernet, encrypted: str) -> str:
    return fernet.decrypt(encrypted.encode()).decode()


def generate_virtual_key() -> str:
    return VIRTUAL_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_virtual_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def mask_key(raw: str) -> str:
    """展示用掩码:sk-gw-abcd****wxyz"""
    if len(raw) <= 14:
        return raw[:6] + "****"
    return raw[:10] + "****" + raw[-4:]
