from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, role_required, clean_str

bp = Blueprint("ads", __name__, url_prefix="/api/ads")

ALLOWED_METHODS = {"bkash", "nagad", "card", "sslcommerz"}
MAX_ACTIVE_ADS = 5

AD_SELECT = """
    SELECT a.*, u.company_name,
           p.name AS product_name, p.image_url AS product_image_url,
           p.category AS product_category, p.price AS product_price
    FROM ads a
    JOIN users u ON u.id = a.user_id
    JOIN products p ON p.id = a.product_id
"""


def _setting(key, default):
    row = query("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return row["value"] if row else default


def _plan():
    price = float(_setting("ad_price", "300"))
    days = int(_setting("ad_days", "7"))
    return price, days


def _active_ad_count():
    row = query(
        "SELECT COUNT(*) AS c FROM ads WHERE status = 'active' AND expires_at > datetime('now')",
        one=True,
    )
    return row["c"]


def serialize(a):
    keys = a.keys()
    return {
        "id": a["id"], "user_id": a["user_id"], "product_id": a["product_id"],
        "title": a["title"], "amount": a["amount"], "method": a["method"],
        "status": a["status"], "created_at": a["created_at"], "expires_at": a["expires_at"],
        "company_name": a["company_name"] if "company_name" in keys else None,
        "product_name": a["product_name"] if "product_name" in keys else None,
        "product_image_url": a["product_image_url"] if "product_image_url" in keys else None,
        "product_category": a["product_category"] if "product_category" in keys else None,
        "product_price": a["product_price"] if "product_price" in keys else None,
    }


@bp.get("/plan")
def plan():
    price, days = _plan()
    return jsonify({"price": price, "days": days, "max_slots": MAX_ACTIVE_ADS, "slots_used": _active_ad_count()})


@bp.get("/active")
def active_ads():
    """
    Public feed of currently running ads - shown as a fixed grid of up to
    MAX_ACTIVE_ADS slots on the homepage. Each ad links to its product page,
    which already shows full details, ordering, and other products in the
    same category.
    """
    rows = query(
        AD_SELECT + " WHERE a.status = 'active' AND a.expires_at > datetime('now') "
        "ORDER BY a.created_at DESC LIMIT ?",
        (MAX_ACTIVE_ADS,),
    )
    return jsonify({"ads": [serialize(r) for r in rows], "max_slots": MAX_ACTIVE_ADS})


@bp.post("/<int:ad_id>/toggle_active")
@login_required
def toggle_my_ad(ad_id):
    ad = query("SELECT * FROM ads WHERE id = ? AND user_id = ?", (ad_id, g.user["id"]), one=True)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404
    if ad["status"] == "inactive" and _active_ad_count() >= MAX_ACTIVE_ADS:
        return jsonify({"error": f"All {MAX_ACTIVE_ADS} ad slots are currently full."}), 409
    new_status = "inactive" if ad["status"] == "active" else "active"
    execute("UPDATE ads SET status = ? WHERE id = ?", (new_status, ad_id))
    return jsonify({"message": "Updated.", "status": new_status})


@bp.post("")
@role_required("admin")
def buy_ad():
    """
    Only admins create ads - a seller who wants a banner asks the admin to
    run it for them (see for_user_id below). Ads link to one of that
    seller's real products; the homepage banner uses the product's own
    image and clicking it opens the product page for ordering. This is a
    simulated payment record, same honesty caveat as orders/subscriptions:
    no real gateway is called.
    """
    if _active_ad_count() >= MAX_ACTIVE_ADS:
        return jsonify({"error": f"All {MAX_ACTIVE_ADS} ad slots are currently full. Pause one first."}), 409

    data = request.get_json(silent=True) or {}
    title = clean_str(data.get("title"), 120)
    method = clean_str(data.get("method"), 20).lower()

    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Select a product to advertise."}), 400

    product = query("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,), one=True)
    if not product:
        return jsonify({"error": "Selected product not found."}), 400
    if not title:
        title = product["name"]
    if method not in ALLOWED_METHODS:
        return jsonify({"error": "Unsupported payment method."}), 400

    owner_id = product["supplier_id"]
    if data.get("for_user_id"):
        try:
            target_id = int(data.get("for_user_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid seller/buyer selected."}), 400
        target = query("SELECT id, role FROM users WHERE id = ?", (target_id,), one=True)
        if not target or target["role"] not in ("supplier", "buyer"):
            return jsonify({"error": "Selected account not found."}), 400
        owner_id = target_id

    price, days = _plan()
    expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()

    ad_id = execute(
        """INSERT INTO ads (user_id, product_id, title, amount, method, status, expires_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?)""",
        (owner_id, product_id, title, price, method, expires_at),
    )
    row = query(AD_SELECT + " WHERE a.id = ?", (ad_id,), one=True)
    return jsonify({"ad": serialize(row), "message": "Payment captured - the ad is live now."}), 201