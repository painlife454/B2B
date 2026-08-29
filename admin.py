from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import role_required, clean_str

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/overview")
@role_required("admin")
def overview():
    users = query("SELECT role, COUNT(*) AS c FROM users GROUP BY role")
    products = query("SELECT COUNT(*) AS c FROM products WHERE is_active = 1", one=True)
    orders_by_status = query("SELECT status, COUNT(*) AS c FROM orders GROUP BY status")
    revenue = query(
        "SELECT COALESCE(SUM(commission_amt),0) AS total FROM payments WHERE status IN ('escrow','released')",
        one=True,
    )
    gmv = query("SELECT COALESCE(SUM(total_price),0) AS total FROM orders WHERE status != 'cancelled'", one=True)
    sub_revenue = query("SELECT COALESCE(SUM(amount),0) AS total FROM subscriptions", one=True)
    featured_now = query(
        """SELECT COUNT(*) AS c FROM users WHERE role = 'supplier' AND is_featured = 1
           AND featured_until IS NOT NULL AND featured_until > datetime('now')""",
        one=True,
    )

    return jsonify({
        "users_by_role": {r["role"]: r["c"] for r in users},
        "active_products": products["c"],
        "orders_by_status": {r["status"]: r["c"] for r in orders_by_status},
        "commission_revenue": revenue["total"],
        "gross_merchandise_value": gmv["total"],
        "subscription_revenue": sub_revenue["total"],
        "featured_suppliers_now": featured_now["c"],
    })


@bp.get("/users")
@role_required("admin")
def list_users():
    rows = query(
        "SELECT id, name, company_name, email, role, phone, is_active, created_at FROM users ORDER BY created_at DESC"
    )
    return jsonify({"users": [dict(r) for r in rows]})


@bp.post("/users/<int:user_id>/toggle_active")
@role_required("admin")
def toggle_user(user_id):
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if not user:
        return jsonify({"error": "User not found."}), 404
    if user["role"] == "admin":
        return jsonify({"error": "Cannot disable an admin account."}), 400
    new_state = 0 if user["is_active"] else 1
    execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
    return jsonify({"message": "Updated.", "is_active": bool(new_state)})


@bp.get("/products")
@role_required("admin")
def list_products():
    rows = query(
        """SELECT p.*, u.company_name AS supplier_name FROM products p
           JOIN users u ON u.id = p.supplier_id ORDER BY p.created_at DESC"""
    )
    return jsonify({"products": [dict(r) for r in rows]})


@bp.post("/products/<int:product_id>/toggle_active")
@role_required("admin")
def toggle_product(product_id):
    product = query("SELECT * FROM products WHERE id = ?", (product_id,), one=True)
    if not product:
        return jsonify({"error": "Product not found."}), 404
    new_state = 0 if product["is_active"] else 1
    execute("UPDATE products SET is_active = ? WHERE id = ?", (new_state, product_id))
    return jsonify({"message": "Updated.", "is_active": bool(new_state)})


@bp.get("/orders")
@role_required("admin")
def list_orders():
    rows = query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 500")
    return jsonify({"orders": [dict(r) for r in rows]})


@bp.get("/settings")
@role_required("admin")
def get_settings():
    rows = query("SELECT key, value FROM settings")
    return jsonify({"settings": {r["key"]: r["value"] for r in rows}})


@bp.post("/settings/commission_rate")
@role_required("admin")
def set_commission_rate():
    data = request.get_json(silent=True) or {}
    try:
        rate = float(data.get("rate"))
    except (TypeError, ValueError):
        return jsonify({"error": "Rate must be a number."}), 400
    if not (0 <= rate <= 0.2):
        return jsonify({"error": "Commission rate must be between 0% and 20%."}), 400
    execute(
        "INSERT INTO settings (key, value) VALUES ('commission_rate', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(rate),),
    )
    return jsonify({"message": "Commission rate updated.", "commission_rate": rate})


@bp.post("/settings/subscription_plan")
@role_required("admin")
def set_subscription_plan():
    data = request.get_json(silent=True) or {}
    try:
        price = float(data.get("price"))
        days = int(data.get("days"))
    except (TypeError, ValueError):
        return jsonify({"error": "Price and days must be numbers."}), 400
    if price < 0 or days < 1:
        return jsonify({"error": "Price cannot be negative; days must be at least 1."}), 400

    execute(
        "INSERT INTO settings (key, value) VALUES ('subscription_price', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(price),),
    )
    execute(
        "INSERT INTO settings (key, value) VALUES ('subscription_days', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(days),),
    )
    return jsonify({"message": "Subscription plan updated.", "price": price, "days": days})


@bp.get("/subscriptions")
@role_required("admin")
def list_subscriptions():
    rows = query(
        """SELECT s.*, u.company_name AS supplier_name, u.email AS supplier_email
           FROM subscriptions s JOIN users u ON u.id = s.supplier_id
           ORDER BY s.started_at DESC LIMIT 300"""
    )
    return jsonify({"subscriptions": [dict(r) for r in rows]})


@bp.get("/ads")
@role_required("admin")
def list_ads():
    rows = query(
        """SELECT a.*, u.company_name AS advertiser_name FROM ads a
           JOIN users u ON u.id = a.user_id ORDER BY a.created_at DESC LIMIT 300"""
    )
    return jsonify({"ads": [dict(r) for r in rows]})


@bp.post("/ads/<int:ad_id>/toggle_active")
@role_required("admin")
def toggle_ad(ad_id):
    ad = query("SELECT * FROM ads WHERE id = ?", (ad_id,), one=True)
    if not ad:
        return jsonify({"error": "Ad not found."}), 404
    new_status = "inactive" if ad["status"] == "active" else "active"
    execute("UPDATE ads SET status = ? WHERE id = ?", (new_status, ad_id))
    return jsonify({"message": "Updated.", "status": new_status})


@bp.post("/settings/ad_plan")
@role_required("admin")
def set_ad_plan():
    data = request.get_json(silent=True) or {}
    try:
        price = float(data.get("price"))
        days = int(data.get("days"))
    except (TypeError, ValueError):
        return jsonify({"error": "Price and days must be numbers."}), 400
    if price < 0 or days < 1:
        return jsonify({"error": "Price cannot be negative; days must be at least 1."}), 400

    execute(
        "INSERT INTO settings (key, value) VALUES ('ad_price', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(price),),
    )
    execute(
        "INSERT INTO settings (key, value) VALUES ('ad_days', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(days),),
    )
    return jsonify({"message": "Ad plan updated.", "price": price, "days": days})


@bp.get("/kyc")
@role_required("admin")
def list_kyc():
    rows = query(
        """SELECT id, company_name, email, kyc_status, kyc_document_url, kyc_note
           FROM users WHERE role = 'supplier' AND kyc_status != 'unsubmitted'
           ORDER BY (kyc_status = 'pending') DESC, id DESC"""
    )
    return jsonify({"submissions": [dict(r) for r in rows]})


@bp.post("/kyc/<int:user_id>/review")
@role_required("admin")
def review_kyc(user_id):
    user = query("SELECT * FROM users WHERE id = ? AND role = 'supplier'", (user_id,), one=True)
    if not user:
        return jsonify({"error": "Supplier not found."}), 404

    data = request.get_json(silent=True) or {}
    decision = clean_str(data.get("decision"), 20)
    note = clean_str(data.get("note"), 300)
    if decision not in ("approved", "rejected"):
        return jsonify({"error": "Decision must be 'approved' or 'rejected'."}), 400

    execute("UPDATE users SET kyc_status = ?, kyc_note = ? WHERE id = ?", (decision, note or None, user_id))

    from notifications import notify
    notify(
        user_id, "kyc_reviewed", f"Verification {decision}",
        note or f"Your business verification was {decision}.",
    )
    return jsonify({"message": "KYC decision recorded."})