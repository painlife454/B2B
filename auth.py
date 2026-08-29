from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, g, make_response
from werkzeug.security import generate_password_hash, check_password_hash

from database import query, execute
from utils import (
    valid_email, valid_password, valid_role, clean_str,
    issue_token, set_auth_cookies, clear_auth_cookies,
    get_current_user, login_required, role_required, rate_limited,
)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

LOCK_THRESHOLD = 6
LOCK_MINUTES = 15


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = clean_str(data.get("name"), 120)
    company_name = clean_str(data.get("company_name"), 160)
    email = clean_str(data.get("email"), 255).lower()
    password = data.get("password") or ""
    role = clean_str(data.get("role"), 20)
    phone = clean_str(data.get("phone"), 30)

    if not name or not company_name:
        return jsonify({"error": "Name and company name are required."}), 400
    if not valid_email(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if not valid_password(password):
        return jsonify({"error": "Password must be 8+ characters with letters and numbers."}), 400
    if not valid_role(role):
        return jsonify({"error": "Role must be 'supplier' or 'buyer'."}), 400

    existing = query("SELECT id FROM users WHERE email = ?", (email,), one=True)
    if existing:
        return jsonify({"error": "An account with this email already exists."}), 409

    pw_hash = generate_password_hash(password)  # PBKDF2-SHA256, salted
    user_id = execute(
        """INSERT INTO users (name, company_name, email, password_hash, role, phone)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, company_name, email, pw_hash, role, phone),
    )
    user = query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    token, csrf_token = issue_token(user)
    resp = make_response(jsonify({
        "message": "Account created.",
        "user": public_user(user),
        "csrf_token": csrf_token,
    }))
    set_auth_cookies(resp, token, csrf_token)
    return resp, 201


@bp.post("/login")
def login():
    ip = request.remote_addr or "unknown"
    data = request.get_json(silent=True) or {}
    email = clean_str(data.get("email"), 255).lower()
    password = data.get("password") or ""

    if rate_limited(f"login:{ip}"):
        return jsonify({"error": "Too many attempts. Try again in a minute."}), 429

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = query("SELECT * FROM users WHERE email = ?", (email,), one=True)

    # Generic error message on every failure path — never reveal whether the
    # email exists, to avoid user enumeration.
    generic_error = ("Invalid email or password.", 401)

    if user is None:
        return jsonify({"error": generic_error[0]}), generic_error[1]

    if user["locked_until"]:
        locked_until = datetime.fromisoformat(user["locked_until"])
        if datetime.utcnow() < locked_until:
            return jsonify({"error": "Account temporarily locked. Try again later."}), 423

    if not check_password_hash(user["password_hash"], password):
        fails = user["failed_logins"] + 1
        lock_sql = ""
        params = [fails]
        if fails >= LOCK_THRESHOLD:
            locked_until = (datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)).isoformat()
            execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                    (fails, locked_until, user["id"]))
        else:
            execute("UPDATE users SET failed_logins = ? WHERE id = ?", (fails, user["id"]))
        return jsonify({"error": generic_error[0]}), generic_error[1]

    if not user["is_active"]:
        return jsonify({"error": "This account has been disabled."}), 403

    execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", (user["id"],))
    token, csrf_token = issue_token(user)
    resp = make_response(jsonify({
        "message": "Logged in.",
        "user": public_user(user),
        "csrf_token": csrf_token,
    }))
    set_auth_cookies(resp, token, csrf_token)
    return resp


@bp.post("/logout")
@login_required
def logout():
    resp = make_response(jsonify({"message": "Logged out."}))
    clear_auth_cookies(resp)
    return resp


@bp.get("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": public_user(user)})


@bp.post("/change_password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""

    if not check_password_hash(g.user["password_hash"], current_password):
        return jsonify({"error": "Current password is incorrect."}), 403
    if not valid_password(new_password):
        return jsonify({"error": "New password must be 8+ characters with letters and numbers."}), 400

    pw_hash = generate_password_hash(new_password)
    execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, g.user["id"]))
    return jsonify({"message": "Password updated."})


@bp.post("/change_email")
@login_required
def change_email():
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_email = clean_str(data.get("new_email"), 255).lower()

    if not check_password_hash(g.user["password_hash"], current_password):
        return jsonify({"error": "Current password is incorrect."}), 403
    if not valid_email(new_email):
        return jsonify({"error": "Enter a valid email address."}), 400

    existing = query("SELECT id FROM users WHERE email = ? AND id != ?", (new_email, g.user["id"]), one=True)
    if existing:
        return jsonify({"error": "That email is already in use."}), 409

    execute("UPDATE users SET email = ? WHERE id = ?", (new_email, g.user["id"]))
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    return jsonify({"message": "Email updated.", "user": public_user(user)})


def public_user(user_row):
    """Never expose password_hash or lockout internals to the client."""
    return {
        "id": user_row["id"],
        "name": user_row["name"],
        "company_name": user_row["company_name"],
        "email": user_row["email"],
        "role": user_row["role"],
        "phone": user_row["phone"],
        "logo_url": user_row["logo_url"] if "logo_url" in user_row.keys() else None,
    }


@bp.get("/profile")
@login_required
def get_profile():
    u = g.user
    return jsonify({
        "logo_url": u["logo_url"], "address": u["address"], "trade_info": u["trade_info"],
        "business_description": u["business_description"], "delivery_areas": u["delivery_areas"],
        "payment_info": u["payment_info"],
        "business_type": u["business_type"], "billing_address": u["billing_address"], "tax_info": u["tax_info"],
        "kyc_status": u["kyc_status"], "kyc_document_url": u["kyc_document_url"], "kyc_note": u["kyc_note"],
        "notify_new_order": bool(u["notify_new_order"]), "notify_order_status": bool(u["notify_order_status"]),
        "notify_low_stock": bool(u["notify_low_stock"]), "notify_message": bool(u["notify_message"]),
    })


@bp.post("/profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    name = clean_str(data.get("name", g.user["name"]), 120)
    company_name = clean_str(data.get("company_name", g.user["company_name"]), 160)
    phone = clean_str(data.get("phone", g.user["phone"] or ""), 30)
    logo_url = clean_str(data.get("logo_url", g.user["logo_url"] or ""), 1000)
    address = clean_str(data.get("address", g.user["address"] or ""), 500)
    trade_info = clean_str(data.get("trade_info", g.user["trade_info"] or ""), 500)
    business_description = clean_str(data.get("business_description", g.user["business_description"] or ""), 2000)
    delivery_areas = clean_str(data.get("delivery_areas", g.user["delivery_areas"] or ""), 500)
    payment_info = clean_str(data.get("payment_info", g.user["payment_info"] or ""), 500)
    business_type = clean_str(data.get("business_type", g.user["business_type"] or ""), 100)
    billing_address = clean_str(data.get("billing_address", g.user["billing_address"] or ""), 500)
    tax_info = clean_str(data.get("tax_info", g.user["tax_info"] or ""), 200)

    if not name or not company_name:
        return jsonify({"error": "Name and company name are required."}), 400

    execute(
        """UPDATE users SET name=?, company_name=?, phone=?, logo_url=?, address=?, trade_info=?,
           business_description=?, delivery_areas=?, payment_info=?, business_type=?, billing_address=?, tax_info=?
           WHERE id=?""",
        (name, company_name, phone, logo_url or None, address or None, trade_info or None,
         business_description or None, delivery_areas or None, payment_info or None,
         business_type or None, billing_address or None, tax_info or None, g.user["id"]),
    )
    user = query("SELECT * FROM users WHERE id = ?", (g.user["id"],), one=True)
    return jsonify({"message": "Profile updated.", "user": public_user(user)})


@bp.post("/notification_settings")
@login_required
def update_notification_settings():
    data = request.get_json(silent=True) or {}
    fields = ["notify_new_order", "notify_order_status", "notify_low_stock", "notify_message"]
    values = [1 if data.get(f, True) else 0 for f in fields]
    execute(
        f"UPDATE users SET {'=?, '.join(fields)}=? WHERE id=?",
        (*values, g.user["id"]),
    )
    return jsonify({"message": "Notification settings updated."})


@bp.post("/kyc/submit")
@role_required("supplier")
def submit_kyc():
    data = request.get_json(silent=True) or {}
    document_url = clean_str(data.get("document_url"), 1000)
    if not document_url:
        return jsonify({"error": "Upload a document first."}), 400
    if g.user["kyc_status"] == "approved":
        return jsonify({"error": "Your account is already verified."}), 400
    execute(
        "UPDATE users SET kyc_document_url = ?, kyc_status = 'pending', kyc_note = NULL WHERE id = ?",
        (document_url, g.user["id"]),
    )
    return jsonify({"message": "Document submitted for review."})