from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, clean_str

bp = Blueprint("payments", __name__, url_prefix="/api/payments")

ALLOWED_METHODS = {"bkash", "nagad", "card", "sslcommerz"}


def serialize(p):
    return {
        "id": p["id"], "order_id": p["order_id"], "amount": p["amount"],
        "method": p["method"], "commission_rate": p["commission_rate"],
        "commission_amt": p["commission_amt"], "supplier_net": p["supplier_net"],
        "status": p["status"], "created_at": p["created_at"], "released_at": p["released_at"],
    }


def _commission_rate():
    row = query("SELECT value FROM settings WHERE key = 'commission_rate'", one=True)
    try:
        return float(row["value"]) if row else 0.03
    except (TypeError, ValueError):
        return 0.03


def capture_payment(order, method):
    """
    Core payment-capture logic, reused by both the standalone /pay endpoint
    and the "pay immediately when placing an order" flow in orders.py.
    Returns (payment_row_dict, error_message). error_message is None on success.
    Simulated payment — no real card/mobile-money data is collected or stored.
    """
    method = clean_str(method, 20).lower()
    if method not in ALLOWED_METHODS:
        return None, "Unsupported payment method."
    if order["status"] not in ("pending", "confirmed"):
        return None, "This order is not payable in its current state."

    existing = query("SELECT id FROM payments WHERE order_id = ?", (order["id"],), one=True)
    if existing:
        return None, "Payment already recorded for this order."

    rate = _commission_rate()
    amount = order["total_price"]
    commission = round(amount * rate, 2)
    net = round(amount - commission, 2)

    payment_id = execute(
        """INSERT INTO payments (order_id, amount, method, commission_rate, commission_amt, supplier_net, status)
           VALUES (?, ?, ?, ?, ?, ?, 'escrow')""",
        (order["id"], amount, method, rate, commission, net),
    )
    if order["status"] == "pending":
        execute("UPDATE orders SET status = 'confirmed', updated_at = datetime('now') WHERE id = ?", (order["id"],))

    payment = query("SELECT * FROM payments WHERE id = ?", (payment_id,), one=True)

    from notifications import notify
    notify(
        order["supplier_id"], "payment_received", "Payment received",
        f"Payment of ৳{amount} for order #{order['id']} is held in escrow.",
        link=f"#/orders/{order['id']}",
    )

    return serialize(payment), None


@bp.post("/<int:order_id>/pay")
@login_required
def pay(order_id):
    """
    Simulated payment capture. No real card/mobile-money data is collected or
    stored — this models the escrow step in the marketplace flow only.
    """
    order = query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)
    if not order or order["buyer_id"] != g.user["id"]:
        return jsonify({"error": "Order not found."}), 404

    data = request.get_json(silent=True) or {}
    payment, error = capture_payment(order, data.get("method"))
    if error:
        status = 409 if "already" in error else 400
        return jsonify({"error": error}), status
    return jsonify({"payment": payment, "message": "Payment captured and held in escrow."}), 201


@bp.get("/<int:order_id>")
def get_payment(order_id):
    from utils import get_current_user
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401
    order = query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)
    if not order:
        return jsonify({"error": "Order not found."}), 404
    if user["role"] != "admin" and user["id"] not in (order["buyer_id"], order["supplier_id"]):
        return jsonify({"error": "Order not found."}), 404
    payment = query("SELECT * FROM payments WHERE order_id = ?", (order_id,), one=True)
    if not payment:
        return jsonify({"payment": None})
    return jsonify({"payment": serialize(payment)})