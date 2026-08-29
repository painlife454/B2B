from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import role_required, clean_str, get_current_user

bp = Blueprint("subscription", __name__, url_prefix="/api/subscription")

ALLOWED_METHODS = {"bkash", "nagad", "card", "sslcommerz"}


def _setting(key, default):
    row = query("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return row["value"] if row else default


def _plan():
    price = float(_setting("subscription_price", "500"))
    days = int(_setting("subscription_days", "30"))
    return price, days


def _is_currently_featured(user_row):
    if not user_row["is_featured"]:
        return False
    if not user_row["featured_until"]:
        return False
    try:
        return datetime.utcnow() < datetime.fromisoformat(user_row["featured_until"])
    except ValueError:
        return False


@bp.get("/plan")
def plan():
    price, days = _plan()
    return jsonify({"price": price, "days": days})


@bp.get("/status")
@role_required("supplier")
def status():
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    return jsonify({
        "is_featured": _is_currently_featured(user),
        "featured_until": user["featured_until"],
    })


@bp.post("/subscribe")
@role_required("supplier")
def subscribe():
    """
    Simulated subscription payment — same honesty caveat as the order
    payment flow: no real gateway is called, this just records the
    transaction and grants featured placement for the plan's duration.
    """
    data = request.get_json(silent=True) or {}
    method = clean_str(data.get("method"), 20).lower()
    if method not in ALLOWED_METHODS:
        return jsonify({"error": "Unsupported payment method."}), 400

    price, days = _plan()
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)

    # Extend from the current expiry if still active, otherwise from now.
    base = datetime.utcnow()
    if user["featured_until"]:
        try:
            existing = datetime.fromisoformat(user["featured_until"])
            if existing > base:
                base = existing
        except ValueError:
            pass
    expires_at = (base + timedelta(days=days)).isoformat()

    execute(
        "INSERT INTO subscriptions (supplier_id, amount, method, days, expires_at) VALUES (?, ?, ?, ?, ?)",
        (g.user["id"], price, method, days, expires_at),
    )
    execute(
        "UPDATE users SET is_featured = 1, featured_until = ? WHERE id = ?",
        (expires_at, g.user["id"]),
    )
    return jsonify({
        "message": "Subscription active — your products are now featured.",
        "featured_until": expires_at,
    }), 201
