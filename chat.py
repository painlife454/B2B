from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, clean_str

bp = Blueprint("chat", __name__, url_prefix="/api/chat")


def serialize(m):
    return {
        "id": m["id"], "order_id": m["order_id"], "product_id": m["product_id"],
        "sender_id": m["sender_id"], "receiver_id": m["receiver_id"],
        "content": m["content"], "created_at": m["created_at"], "is_read": bool(m["is_read"]),
    }


@bp.get("/order/<int:order_id>")
@login_required
def order_thread(order_id):
    order = query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)
    if not order or g.user["id"] not in (order["buyer_id"], order["supplier_id"]) and g.user["role"] != "admin":
        return jsonify({"error": "Not found."}), 404
    rows = query(
        "SELECT * FROM messages WHERE order_id = ? ORDER BY created_at ASC LIMIT 500",
        (order_id,),
    )
    # mark incoming messages as read
    execute(
        "UPDATE messages SET is_read = 1 WHERE order_id = ? AND receiver_id = ?",
        (order_id, g.user["id"]),
    )
    return jsonify({"messages": [serialize(r) for r in rows]})


@bp.post("/order/<int:order_id>")
@login_required
def send_order_message(order_id):
    order = query("SELECT * FROM orders WHERE id = ?", (order_id,), one=True)
    if not order or g.user["id"] not in (order["buyer_id"], order["supplier_id"]):
        return jsonify({"error": "Not found."}), 404

    content = clean_str((request.get_json(silent=True) or {}).get("content"), 2000)
    if not content:
        return jsonify({"error": "Message cannot be empty."}), 400

    receiver_id = order["supplier_id"] if g.user["id"] == order["buyer_id"] else order["buyer_id"]
    msg_id = execute(
        """INSERT INTO messages (order_id, product_id, sender_id, receiver_id, content)
           VALUES (?, ?, ?, ?, ?)""",
        (order_id, order["product_id"], g.user["id"], receiver_id, content),
    )
    msg = query("SELECT * FROM messages WHERE id = ?", (msg_id,), one=True)

    from notifications import notify
    notify(receiver_id, "buyer_message", "New message", content[:120], link=f"#/orders/{order_id}")

    return jsonify({"message_obj": serialize(msg)}), 201


@bp.post("/product/<int:product_id>")
@login_required
def send_product_inquiry(product_id):
    """
    Pre-order RFQ / inquiry chat between a buyer and a product's supplier.
    A buyer messages the supplier directly. The supplier can only reply to a
    buyer who has already messaged about this product - they must specify
    which buyer via "to" (a product can have inquiries from many buyers).
    """
    product = query("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,), one=True)
    if not product:
        return jsonify({"error": "Product not found."}), 404

    data = request.get_json(silent=True) or {}
    content = clean_str(data.get("content"), 2000)
    if not content:
        return jsonify({"error": "Message cannot be empty."}), 400

    is_supplier = g.user["id"] == product["supplier_id"]
    if is_supplier:
        try:
            receiver_id = int(data.get("to"))
        except (TypeError, ValueError):
            return jsonify({"error": "Select which buyer you're replying to."}), 400
        existing = query(
            "SELECT id FROM messages WHERE product_id = ? AND order_id IS NULL AND sender_id = ? LIMIT 1",
            (product_id, receiver_id), one=True,
        )
        if not existing:
            return jsonify({"error": "That buyer hasn't messaged you about this product."}), 400
    else:
        receiver_id = product["supplier_id"]

    msg_id = execute(
        """INSERT INTO messages (order_id, product_id, sender_id, receiver_id, content)
           VALUES (NULL, ?, ?, ?, ?)""",
        (product_id, g.user["id"], receiver_id, content),
    )
    msg = query("SELECT * FROM messages WHERE id = ?", (msg_id,), one=True)

    from notifications import notify
    link = f"#/products/{product_id}" + (f"?with={g.user['id']}" if not is_supplier else "")
    notify(receiver_id, "buyer_message", "New message" if is_supplier else "New inquiry", content[:120], link=link)

    return jsonify({"message_obj": serialize(msg)}), 201


@bp.get("/product/<int:product_id>")
@login_required
def product_thread(product_id):
    """
    Thread between the current user and one counterparty for this product.
    A buyer's counterparty is always the supplier. A supplier has many
    possible counterparties (one per buyer who inquired), so they must pass
    ?with=<buyer_id> to pick which conversation to view.
    """
    product = query("SELECT * FROM products WHERE id = ?", (product_id,), one=True)
    if not product:
        return jsonify({"error": "Not found."}), 404

    is_supplier = g.user["id"] == product["supplier_id"]
    counterparty_name = None
    if is_supplier:
        counterparty_id = request.args.get("with", type=int)
        if not counterparty_id:
            return jsonify({"error": "Select which buyer's conversation to view."}), 400
        row = query("SELECT company_name FROM users WHERE id = ?", (counterparty_id,), one=True)
        counterparty_name = row["company_name"] if row else None
    else:
        counterparty_id = product["supplier_id"]

    rows = query(
        """SELECT * FROM messages WHERE product_id = ? AND order_id IS NULL
           AND ((sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?))
           ORDER BY created_at ASC LIMIT 500""",
        (product_id, g.user["id"], counterparty_id, counterparty_id, g.user["id"]),
    )
    execute(
        "UPDATE messages SET is_read = 1 WHERE product_id = ? AND order_id IS NULL AND receiver_id = ? AND sender_id = ?",
        (product_id, g.user["id"], counterparty_id),
    )
    return jsonify({
        "messages": [serialize(r) for r in rows],
        "counterparty_id": counterparty_id,
        "counterparty_name": counterparty_name,
    })


@bp.get("/unread_count")
@login_required
def unread_count():
    row = query(
        "SELECT COUNT(*) AS c FROM messages WHERE receiver_id = ? AND is_read = 0",
        (g.user["id"],), one=True,
    )
    return jsonify({"unread": row["c"] if row else 0})


@bp.get("/threads")
@login_required
def list_threads():
    """
    Every conversation the current account is part of - order chats and
    product-inquiry chats - grouped into threads with the latest message
    and unread count, for a single "Messages" inbox view.
    """
    rows = query(
        "SELECT * FROM messages WHERE sender_id = ? OR receiver_id = ? ORDER BY created_at DESC",
        (g.user["id"], g.user["id"]),
    )
    threads = {}
    for m in rows:
        if m["order_id"]:
            key = ("order", m["order_id"])
        else:
            counterparty = m["receiver_id"] if m["sender_id"] == g.user["id"] else m["sender_id"]
            key = ("product", m["product_id"], counterparty)
        if key not in threads:
            threads[key] = {
                "type": key[0],
                "id": m["order_id"] if key[0] == "order" else m["product_id"],
                "last_message": m["content"],
                "last_at": m["created_at"],
                "unread": 0,
            }
        if m["receiver_id"] == g.user["id"] and not m["is_read"]:
            threads[key]["unread"] += 1

    result = []
    for key, t in threads.items():
        if key[0] == "order":
            order = query("SELECT * FROM orders WHERE id = ?", (t["id"],), one=True)
            if not order:
                continue
            other_id = order["buyer_id"] if order["supplier_id"] == g.user["id"] else order["supplier_id"]
            other = query("SELECT company_name FROM users WHERE id = ?", (other_id,), one=True)
            t["counterparty"] = other["company_name"] if other else "Unknown"
            t["context"] = f"Order #{t['id']}"
            t["link"] = f"#/orders/{t['id']}"
        else:
            other_id = key[2]
            other = query("SELECT company_name FROM users WHERE id = ?", (other_id,), one=True)
            t["counterparty"] = other["company_name"] if other else "Unknown"
            product = query("SELECT name FROM products WHERE id = ?", (t["id"],), one=True)
            t["context"] = product["name"] if product else "Product inquiry"
            t["link"] = f"#/products/{t['id']}?with={other_id}"
        result.append(t)

    result.sort(key=lambda x: x["last_at"], reverse=True)
    return jsonify({"threads": result})