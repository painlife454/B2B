"""
Security utilities shared across blueprints:
- JWT session tokens (httpOnly cookie)
- CSRF double-submit protection for state-changing requests
- Input validators
- Simple in-memory rate limiter for auth endpoints
- Role-based access decorators
"""
import re
import time
import secrets
from functools import wraps
from collections import defaultdict, deque

import jwt
from flask import request, jsonify, current_app, g

from database import query

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---------------------------------------------------------------------------
# Rate limiting (in-memory; fine for a single-process demo deployment)
# ---------------------------------------------------------------------------
_attempts = defaultdict(deque)
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 60


def rate_limited(key):
    now = time.time()
    dq = _attempts[key]
    while dq and now - dq[0] > WINDOW_SECONDS:
        dq.popleft()
    if len(dq) >= MAX_ATTEMPTS:
        return True
    dq.append(now)
    return False


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def valid_email(email):
    return bool(email) and len(email) <= 255 and bool(EMAIL_RE.match(email))


def valid_password(pw):
    # Minimum reasonable complexity for a demo: length + at least one digit/letter mix
    return bool(pw) and 8 <= len(pw) <= 128 and any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw)


def clean_str(s, max_len=255):
    if s is None:
        return ""
    s = str(s).strip()
    return s[:max_len]


def valid_role(role):
    return role in ("supplier", "buyer")  # admin accounts are never self-registered


# ---------------------------------------------------------------------------
# JWT session tokens + CSRF (double-submit cookie pattern)
# ---------------------------------------------------------------------------
TOKEN_TTL_SECONDS = 2 * 60 * 60  # 2 hours


def issue_token(user_row):
    csrf_token = secrets.token_hex(24)
    payload = {
        "uid": user_row["id"],
        "role": user_row["role"],
        "csrf": csrf_token,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    token = jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")
    return token, csrf_token


def decode_token(token):
    try:
        return jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def set_auth_cookies(response, token, csrf_token):
    response.set_cookie(
        "session_token", token,
        httponly=True, secure=current_app.config["COOKIE_SECURE"],
        samesite="Lax", max_age=TOKEN_TTL_SECONDS,
    )
    # Non-httpOnly so frontend JS can read it and echo it back in a header
    # (double-submit CSRF pattern). It carries no auth power on its own.
    response.set_cookie(
        "csrf_token", csrf_token,
        httponly=False, secure=current_app.config["COOKIE_SECURE"],
        samesite="Lax", max_age=TOKEN_TTL_SECONDS,
    )


def clear_auth_cookies(response):
    response.delete_cookie("session_token")
    response.delete_cookie("csrf_token")


def get_current_user():
    if "user" in g:
        return g.user
    token = request.cookies.get("session_token")
    if not token:
        g.user = None
        return None
    payload = decode_token(token)
    if not payload:
        g.user = None
        return None
    row = query("SELECT * FROM users WHERE id = ? AND is_active = 1", (payload["uid"],), one=True)
    if row is None:
        g.user = None
        return None
    g.user = row
    g.token_payload = payload
    return row


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required."}), 401
        # CSRF check on state-changing methods
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            header_token = request.headers.get("X-CSRF-Token", "")
            if not header_token or header_token != g.token_payload.get("csrf"):
                return jsonify({"error": "Invalid or missing CSRF token."}), 403
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if g.user["role"] not in roles:
                return jsonify({"error": "You do not have permission to do that."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator
