from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, role_required, clean_str

bp = Blueprint("buyer_requests", __name__, url_prefix="/api")


def serialize_request(r, quote_count=None):
    keys = r.keys()
    return {
        "id": r["id"], "buyer_id": r["buyer_id"], "product_name": r["product_name"],
        "category": r["category"], "quantity": r["quantity"], "unit": r["unit"],
        "location": r["location"], "description": r["description"],
        "status": r["status"], "created_at": r["created_at"],
        "buyer_company": r["buyer_company"] if "buyer_company" in keys else None,
        "quote_count": quote_count,
    }


def serialize_quote(q):
    keys = q.keys()
    return {
        "id": q["id"], "request_id": q["request_id"], "supplier_id": q["supplier_id"],
        "product_id": q["product_id"] if "product_id" in keys else None,
        "price": q["price"], "message": q["message"], "status": q["status"], "created_at": q["created_at"],
        "supplier_company": q["supplier_company"] if "supplier_company" in keys else None,
        "avg_rating": q["avg_rating"] if "avg_rating" in keys else None,
    }


# ---- Buyer: post and manage requests ----

@bp.post("/buyer_requests")
@role_required("buyer")
def create_request():
    data = request.get_json(silent=True) or {}
    product_name = clean_str(data.get("product_name"), 160)
    category = clean_str(data.get("category"), 80)
    location = clean_str(data.get("location"), 120)
    description = clean_str(data.get("description"), 1000)
    unit = clean_str(data.get("unit"), 20) or "pcs"
    try:
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a number."}), 400

    if not product_name:
        return jsonify({"error": "Product name is required."}), 400
    if quantity <= 0:
        return jsonify({"error": "Quantity must be greater than zero."}), 400

    rid = execute(
        """INSERT INTO buyer_requests (buyer_id, product_name, category, quantity, unit, location, description, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
        (g.user["id"], product_name, category or None, quantity, unit, location or None, description or None),
    )
    r = query("SELECT * FROM buyer_requests WHERE id = ?", (rid,), one=True)
    return jsonify({"request": serialize_request(r, 0)}), 201


@bp.get("/buyer_requests/mine")
@role_required("buyer")
def my_requests():
    rows = query(
        "SELECT * FROM buyer_requests WHERE buyer_id = ? ORDER BY created_at DESC",
        (g.user["id"],),
    )
    out = []
    for r in rows:
        count = query("SELECT COUNT(*) AS c FROM quotations WHERE request_id = ?", (r["id"],), one=True)["c"]
        out.append(serialize_request(r, count))
    return jsonify({"requests": out})


@bp.post("/buyer_requests/<int:request_id>/cancel")
@role_required("buyer")
def cancel_request(request_id):
    r = query("SELECT * FROM buyer_requests WHERE id = ? AND buyer_id = ?", (request_id, g.user["id"]), one=True)
    if not r:
        return jsonify({"error": "Request not found."}), 404
    execute("UPDATE buyer_requests SET status = 'cancelled' WHERE id = ?", (request_id,))
    return jsonify({"message": "Request cancelled."})


# ---- Public / supplier: browse open requests ----

@bp.get("/buyer_requests/open")
@role_required("supplier")
def open_requests():
    """Open requests suppliers can quote on."""
    rows = query(
        """SELECT r.*, u.company_name AS buyer_company FROM buyer_requests r
           JOIN users u ON u.id = r.buyer_id
           WHERE r.status = 'open' ORDER BY r.created_at DESC LIMIT 200"""
    )
    out = []
    for r in rows:
        count = query("SELECT COUNT(*) AS c FROM quotations WHERE request_id = ?", (r["id"],), one=True)["c"]
        my_quote = query(
            "SELECT id FROM quotations WHERE request_id = ? AND supplier_id = ?", (r["id"], g.user["id"]), one=True
        )
        data = serialize_request(r, count)
        data["already_quoted"] = my_quote is not None
        out.append(data)
    return jsonify({"requests": out})


# ---- Quotations ----

@bp.post("/buyer_requests/<int:request_id>/quotations")
@role_required("supplier")
def submit_quotation(request_id):
    req = query("SELECT * FROM buyer_requests WHERE id = ?", (request_id,), one=True)
    if not req or req["status"] != "open":
        return jsonify({"error": "This request is no longer open."}), 400

    data = request.get_json(silent=True) or {}
    message = clean_str(data.get("message"), 1000)
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return jsonify({"error": "Price must be a number."}), 400
    if price < 0:
        return jsonify({"error": "Price can't be negative."}), 400

    product_id = None
    if data.get("product_id"):
        try:
            product_id = int(data.get("product_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid product."}), 400
        p = query("SELECT id FROM products WHERE id = ? AND supplier_id = ?", (product_id, g.user["id"]), one=True)
        if not p:
            return jsonify({"error": "Select one of your own products, or leave it blank."}), 400

    existing = query(
        "SELECT id FROM quotations WHERE request_id = ? AND supplier_id = ?",
        (request_id, g.user["id"]), one=True,
    )
    if existing:
        execute(
            "UPDATE quotations SET price = ?, message = ?, product_id = ? WHERE id = ?",
            (price, message or None, product_id, existing["id"]),
        )
        qid = existing["id"]
    else:
        qid = execute(
            "INSERT INTO quotations (request_id, supplier_id, price, message, product_id) VALUES (?, ?, ?, ?, ?)",
            (request_id, g.user["id"], price, message or None, product_id),
        )

    from notifications import notify
    notify(
        req["buyer_id"], "quotation_received", "New quotation received",
        f"A supplier quoted ৳{price} for your request: {req['product_name']}.",
        link=f"#/dashboard/quotations/{request_id}",
    )

    q = query("SELECT * FROM quotations WHERE id = ?", (qid,), one=True)
    return jsonify({"quotation": serialize_quote(q)}), 201


@bp.get("/buyer_requests/<int:request_id>/quotations")
@login_required
def list_quotations(request_id):
    req = query("SELECT * FROM buyer_requests WHERE id = ?", (request_id,), one=True)
    if not req:
        return jsonify({"error": "Request not found."}), 404
    if g.user["role"] != "admin" and g.user["id"] != req["buyer_id"]:
        return jsonify({"error": "Not found."}), 404

    rows = query(
        """SELECT q.*, u.company_name AS supplier_company,
                  (SELECT ROUND(AVG(rv.rating), 1) FROM reviews rv
                   JOIN products p ON p.id = rv.product_id WHERE p.supplier_id = q.supplier_id) AS avg_rating
           FROM quotations q JOIN users u ON u.id = q.supplier_id
           WHERE q.request_id = ? ORDER BY q.price ASC""",
        (request_id,),
    )
    return jsonify({"request": serialize_request(req), "quotations": [serialize_quote(r) for r in rows]})


@bp.get("/quotations/mine")
@role_required("supplier")
def my_quotations():
    rows = query(
        """SELECT q.*, r.product_name, r.quantity, r.unit, r.status AS request_status
           FROM quotations q JOIN buyer_requests r ON r.id = q.request_id
           WHERE q.supplier_id = ? ORDER BY q.created_at DESC""",
        (g.user["id"],),
    )
    return jsonify({"quotations": [dict(r) for r in rows]})


@bp.post("/quotations/<int:quotation_id>/accept")
@role_required("buyer")
def accept_quotation(quotation_id):
    q = query("SELECT * FROM quotations WHERE id = ?", (quotation_id,), one=True)
    if not q:
        return jsonify({"error": "Quotation not found."}), 404
    req = query("SELECT * FROM buyer_requests WHERE id = ?", (q["request_id"],), one=True)
    if not req or req["buyer_id"] != g.user["id"]:
        return jsonify({"error": "Quotation not found."}), 404
    if req["status"] != "open":
        return jsonify({"error": "This request is no longer open."}), 400

    execute("UPDATE quotations SET status = 'accepted' WHERE id = ?", (quotation_id,))
    execute(
        "UPDATE quotations SET status = 'declined' WHERE request_id = ? AND id != ?",
        (req["id"], quotation_id),
    )
    execute("UPDATE buyer_requests SET status = 'closed' WHERE id = ?", (req["id"],))

    from notifications import notify
    notify(
        q["supplier_id"], "quotation_accepted", "Your quotation was accepted!",
        f"{req['product_name']} - ৳{q['price']}. Contact the buyer to arrange the order.",
        link="#/dashboard/buyer_requests",
    )
    return jsonify({"message": "Quotation accepted. The buyer can now place an order with this supplier."})