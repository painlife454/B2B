import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
KEY_FILE = BASE_DIR / "instance" / "secret.key"


def _load_or_create_secret_key():
    """
    Generate a random 256-bit secret key on first run and persist it, rather
    than hardcoding a key in source. Never commit instance/secret.key.
    """
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    KEY_FILE.write_text(key)
    return key


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or _load_or_create_secret_key()
    # Set to True when served over HTTPS in production.
    COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024  # 6 MB request body cap (allows direct image uploads)