from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, role_required

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# Which user preference column gates each notification type. Types not
# listed here (e.g. admin notices) are never suppressed by preferences.
PREFERENCE_COLUMN = {
    "new_order": "notify_new_order",
    "order_cancelled": "notify_order_status",
    "order_status": "notify_order_status",
    "payment_received": "notify_order_status",
    "low_stock": "notify_low_stock",
    "buyer_message": "notify_message",
}


def notify(user_id, type_, title, message="", link=None):
    """
    Create a notification for a user, honoring their notification
    preferences. Call this from wherever a real event happens (order
    placed, status changed, payment captured, stock low, chat message,
    admin notice) - never fabricate notifications for events that didn't
    happen.
    """
    pref_col = PREFERENCE_COLUMN.get(type_)
    if pref_col:
        row = query(f"SELECT {pref_col} AS pref FROM users WHERE id = ?", (user_id,), one=True)
        if row and not row["pref"]:
            return  # user opted out of this notification type
    execute(
        "INSERT INTO notifications (user_id, type, title, message, link) VALUES (?, ?, ?, ?, ?)",
        (user_id, type_, title, message, link),
    )


def serialize(n):
    return {
        "id": n["id"], "type": n["type"], "title": n["title"], "message": n["message"],
        "link": n["link"], "is_read": bool(n["is_read"]), "created_at": n["created_at"],
    }


@bp.get("")
@login_required
def list_notifications():
    rows = query(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
        (g.user["id"],),
    )
    return jsonify({"notifications": [serialize(r) for r in rows]})


@bp.get("/unread_count")
@login_required
def unread_count():
    row = query(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
        (g.user["id"],), one=True,
    )
    return jsonify({"unread": row["c"] if row else 0})


@bp.post("/mark_all_read")
@login_required
def mark_all_read():
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (g.user["id"],))
    return jsonify({"message": "Marked all as read."})


@bp.post("/<int:notif_id>/read")
@login_required
def mark_read(notif_id):
    n = query("SELECT id FROM notifications WHERE id = ? AND user_id = ?", (notif_id, g.user["id"]), one=True)
    if not n:
        return jsonify({"error": "Notification not found."}), 404
    execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    return jsonify({"message": "Marked as read."})


@bp.post("/admin_notice")
@role_required("admin")
def admin_notice():
    """Admin broadcasts a notice to one supplier, or all suppliers if no target given."""
    data = request.get_json(silent=True) or {}
    from utils import clean_str
    title = clean_str(data.get("title"), 160)
    message = clean_str(data.get("message"), 1000)
    target_id = data.get("target_user_id")

    if not title:
        return jsonify({"error": "Title is required."}), 400

    if target_id:
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid target user."}), 400
        target = query("SELECT id FROM users WHERE id = ?", (target_id,), one=True)
        if not target:
            return jsonify({"error": "Target user not found."}), 404
        notify(target_id, "admin_notice", title, message)
        return jsonify({"message": "Notice sent."})

    suppliers = query("SELECT id FROM users WHERE role = 'supplier'")
    for s in suppliers:
        notify(s["id"], "admin_notice", title, message)
    return jsonify({"message": f"Notice sent to {len(suppliers)} suppliers."})