from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, clean_str, valid_email

bp = Blueprint("orders", __name__, url_prefix="/api/orders")

# Legal status transitions, keyed by (current_status) -> {allowed next statuses}
TRANSITIONS = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"processing", "cancelled"},
    "processing": {"ready_for_delivery", "cancelled"},
    "ready_for_delivery": {"shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}
SUPPLIER_ONLY_TRANSITIONS = {"confirmed", "processing", "ready_for_delivery", "shipped"}
BUYER_ONLY_TRANSITIONS = {"delivered"}

MAX_CUSTOMIZATION_LEN = 1000


def serialize(o):
    keys = o.keys()
    return {
        "id": o["id"], "buyer_id": o["buyer_id"], "supplier_id": o["supplier_id"],
        "product_id": o["product_id"], "quantity": o["quantity"],
        "unit_price": o["unit_price"], "total_price": o["total_price"],
        "color_id": o["color_id"] if "color_id" in keys else None,
        "customization_note": o["customization_note"] if "customization_note" in keys else None,
        "recipient_name": o["recipient_name"] if "recipient_name" in keys else None,
        "recipient_phone": o["recipient_phone"] if "recipient_phone" in keys else None,
        "recipient_email": o["recipient_email"] if "recipient_email" in keys else None,
        "recipient_address": o["recipient_address"] if "recipient_address" in keys else None,
        "courier_name": o["courier_name"] if "courier_name" in keys else None,
        "tracking_number": o["tracking_number"] if "tracking_number" in keys else None,
        "status": o["status"], "created_at": o["created_at"], "updated_at": o["updated_at"],
    }


def _order_or_404(order_id):
    return query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)


def _is_party(order, user):
    return user["role"] == "admin" or order["buyer_id"] == user["id"] or order["supplier_id"] == user["id"]


def _order_items(order_id):
    rows = query("SELECT size_label, quantity FROM order_items WHERE order_id = ? ORDER BY id", (order_id,))
    return [{"size_label": r["size_label"], "quantity": r["quantity"]} for r in rows]


def _color_name(color_id):
    if not color_id:
        return None
    row = query("SELECT name FROM product_colors WHERE id = ?", (color_id,), one=True)
    return row["name"] if row else None


def _full_order(order_id):
    order = _order_or_404(order_id)
    if not order:
        return None
    data = serialize(order)
    data["items"] = _order_items(order_id)
    data["color_name"] = _color_name(order["color_id"])
    return data


def _tiered_unit_price(product, total_qty):
    """Highest-min_qty price tier the total quantity qualifies for, else the base price."""
    tiers = query(
        "SELECT min_qty, price FROM product_price_tiers WHERE product_id = ? ORDER BY min_qty ASC",
        (product["id"],),
    )
    price = product["price"]
    for t in tiers:
        if total_qty >= t["min_qty"]:
            price = t["price"]
    return price


@bp.post("")
@login_required
def place_order():
    if g.user["role"] not in ("buyer", "supplier"):
        return jsonify({"error": "Only buyers and suppliers can place orders."}), 403

    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "product_id is required."}), 400

    product = query("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,), one=True)
    if not product:
        return jsonify({"error": "Product not found."}), 404
    if product["supplier_id"] == g.user["id"]:
        return jsonify({"error": "You can't order your own product."}), 400

    available_sizes = query("SELECT label FROM product_sizes WHERE product_id = ?", (product_id,))
    size_labels = {r["label"] for r in available_sizes}

    size_breakdown = []
    if size_labels:
        sizes_input = data.get("sizes")
        if not isinstance(sizes_input, dict) or not sizes_input:
            return jsonify({"error": "Select a quantity for at least one size."}), 400
        total_quantity = 0
        for label, qty in sizes_input.items():
            if label not in size_labels:
                return jsonify({"error": f"'{label}' is not a valid size for this product."}), 400
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                return jsonify({"error": "Size quantities must be integers."}), 400
            if qty < 0:
                return jsonify({"error": "Size quantities can't be negative."}), 400
            if qty > 0:
                size_breakdown.append((label, qty))
                total_quantity += qty
        if total_quantity == 0:
            return jsonify({"error": "Select a quantity for at least one size."}), 400
    else:
        try:
            total_quantity = int(data.get("quantity"))
        except (TypeError, ValueError):
            return jsonify({"error": "quantity is required."}), 400

    if total_quantity < product["moq"]:
        return jsonify({"error": f"Minimum order quantity is {product['moq']}."}), 400
    if total_quantity > product["stock"]:
        return jsonify({"error": "Not enough stock available."}), 400

    color_id = None
    if data.get("color_id"):
        try:
            color_id = int(data.get("color_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid color."}), 400
        color = query("SELECT id FROM product_colors WHERE id = ? AND product_id = ?", (color_id, product_id), one=True)
        if not color:
            return jsonify({"error": "Selected color isn't available for this product."}), 400

    customization_note = clean_str(data.get("customization_note"), MAX_CUSTOMIZATION_LEN) or None

    recipient_name = clean_str(data.get("recipient_name"), 120)
    recipient_phone = clean_str(data.get("recipient_phone"), 30)
    recipient_email = clean_str(data.get("recipient_email"), 255)
    recipient_address = clean_str(data.get("recipient_address"), 500)
    if not recipient_name or not recipient_phone or not recipient_address:
        return jsonify({"error": "Name, phone, and address are required to place an order."}), 400
    if not recipient_email or not valid_email(recipient_email):
        return jsonify({"error": "Enter a valid email address."}), 400

    pay_method = clean_str(data.get("payment_method"), 20).lower()
    if pay_method not in {"bkash", "nagad", "card", "sslcommerz"}:
        return jsonify({"error": "Select a payment method."}), 400

    unit_price = _tiered_unit_price(product, total_quantity)
    total_price = round(unit_price * total_quantity, 2)

    order_id = execute(
        """INSERT INTO orders (buyer_id, supplier_id, product_id, quantity, unit_price, total_price,
                                color_id, customization_note, recipient_name, recipient_phone,
                                recipient_email, recipient_address, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (g.user["id"], product["supplier_id"], product_id, total_quantity, unit_price, total_price,
         color_id, customization_note, recipient_name, recipient_phone, recipient_email, recipient_address),
    )
    for label, qty in size_breakdown:
        execute("INSERT INTO order_items (order_id, size_label, quantity) VALUES (?, ?, ?)", (order_id, label, qty))

    new_stock = product["stock"] - total_quantity
    execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))

    from notifications import notify
    notify(
        product["supplier_id"], "new_order", "New order received",
        f"{recipient_name} ordered {total_quantity} x {product['name']}.",
        link=f"#/orders/{order_id}",
    )
    if new_stock <= product["low_stock_threshold"]:
        notify(
            product["supplier_id"], "low_stock", "Product running low on stock",
            f"{product['name']} has only {new_stock} left in stock.",
            link=f"#/dashboard/products",
        )

    # Placing an order always captures payment in the same step now - the
    # order is confirmed immediately rather than left pending for a
    # separate payment action.
    from payments import capture_payment
    order_row = _order_or_404(order_id)
    payment, error = capture_payment(order_row, pay_method)

    result = {"order": _full_order(order_id)}
    if error:
        result["payment_error"] = error
    else:
        result["payment"] = payment
        result["order"] = _full_order(order_id)

    return jsonify(result), 201


@bp.get("")
@login_required
def list_orders():
    role = g.user["role"]
    if role == "buyer":
        rows = query("SELECT * FROM orders WHERE buyer_id = ? ORDER BY created_at DESC", (g.user["id"],))
    elif role == "supplier":
        rows = query("SELECT * FROM orders WHERE supplier_id = ? ORDER BY created_at DESC", (g.user["id"],))
    else:
        rows = query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 500")
    return jsonify({"orders": [serialize(r) for r in rows]})


@bp.get("/mine_as_buyer")
@login_required
def my_purchases():
    """
    Orders the current account has placed as a buyer - works for buyer AND
    supplier accounts, since suppliers can also purchase from each other.
    """
    rows = query("SELECT * FROM orders WHERE buyer_id = ? ORDER BY created_at DESC", (g.user["id"],))
    return jsonify({"orders": [serialize(r) for r in rows]})


@bp.get("/<int:order_id>")
@login_required
def get_order(order_id):
    order = _order_or_404(order_id)
    if not order or not _is_party(order, g.user):
        return jsonify({"error": "Order not found."}), 404
    return jsonify({"order": _full_order(order_id)})


@bp.post("/<int:order_id>/status")
@login_required
def update_status(order_id):
    order = _order_or_404(order_id)
    if not order or not _is_party(order, g.user):
        return jsonify({"error": "Order not found."}), 404

    new_status = clean_str((request.get_json(silent=True) or {}).get("status"), 20)
    allowed = TRANSITIONS.get(order["status"], set())
    if new_status not in allowed:
        return jsonify({"error": f"Cannot move order from '{order['status']}' to '{new_status}'."}), 400

    if new_status in SUPPLIER_ONLY_TRANSITIONS and g.user["id"] != order["supplier_id"] and g.user["role"] != "admin":
        return jsonify({"error": "Only the supplier can do that."}), 403
    if new_status in BUYER_ONLY_TRANSITIONS and g.user["id"] != order["buyer_id"] and g.user["role"] != "admin":
        return jsonify({"error": "Only the buyer can do that."}), 403

    if new_status == "cancelled":
        # restock
        execute("UPDATE products SET stock = stock + ? WHERE id = ?", (order["quantity"], order["product_id"]))

    execute("UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?", (new_status, order_id))

    if new_status == "delivered":
        _release_escrow(order_id)

    from notifications import notify
    # Notify the OTHER party than whoever made the change.
    actor_is_supplier = g.user["id"] == order["supplier_id"]
    other_party_id = order["buyer_id"] if actor_is_supplier else order["supplier_id"]
    status_labels = {
        "confirmed": "confirmed", "processing": "now being processed",
        "ready_for_delivery": "ready for delivery", "shipped": "shipped",
        "delivered": "marked as delivered", "cancelled": "cancelled",
    }
    notify(
        other_party_id,
        "order_cancelled" if new_status == "cancelled" else "order_status",
        f"Order #{order_id} {status_labels.get(new_status, new_status)}",
        f"Order #{order_id} status changed to {new_status}.",
        link=f"#/orders/{order_id}",
    )

    return jsonify({"order": _full_order(order_id)})


@bp.post("/<int:order_id>/shipping")
@login_required
def update_shipping(order_id):
    """Supplier records courier name and tracking number for a shipped order."""
    order = _order_or_404(order_id)
    if not order or order["supplier_id"] != g.user["id"]:
        return jsonify({"error": "Order not found."}), 404

    data = request.get_json(silent=True) or {}
    courier_name = clean_str(data.get("courier_name"), 100)
    tracking_number = clean_str(data.get("tracking_number"), 100)

    execute(
        "UPDATE orders SET courier_name = ?, tracking_number = ? WHERE id = ?",
        (courier_name or None, tracking_number or None, order_id),
    )
    return jsonify({"order": _full_order(order_id)})


def _release_escrow(order_id):
    payment = query("SELECT * FROM payments WHERE order_id = ? AND status = 'escrow'", (order_id,), one=True)
    if payment:
        execute(
            "UPDATE payments SET status = 'released', released_at = datetime('now') WHERE id = ?",
            (payment["id"],),
        )