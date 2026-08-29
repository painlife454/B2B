from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required, role_required, clean_str

bp = Blueprint("withdrawals", __name__, url_prefix="/api/withdrawals")


def serialize(w):
    return {
        "id": w["id"], "supplier_id": w["supplier_id"], "amount": w["amount"],
        "method": w["method"], "account_details": w["account_details"],
        "status": w["status"], "note": w["note"],
        "requested_at": w["requested_at"], "processed_at": w["processed_at"],
    }


def _available_balance(supplier_id):
    """
    Released escrow (net of commission) minus amounts already withdrawn or
    pending withdrawal - the real, computed balance, not a stored figure
    that could drift from reality.
    """
    released = query(
        """SELECT COALESCE(SUM(p.supplier_net), 0) AS total FROM payments p
           JOIN orders o ON o.id = p.order_id
           WHERE o.supplier_id = ? AND p.status = 'released'""",
        (supplier_id,), one=True,
    )["total"]
    withdrawn = query(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM withdrawals
           WHERE supplier_id = ? AND status IN ('pending', 'completed')""",
        (supplier_id,), one=True,
    )["total"]
    return round(released - withdrawn, 2)


@bp.get("/balance")
@role_required("supplier")
def balance():
    return jsonify({"available_balance": _available_balance(g.user["id"])})


@bp.get("/mine")
@role_required("supplier")
def my_withdrawals():
    rows = query(
        "SELECT * FROM withdrawals WHERE supplier_id = ? ORDER BY requested_at DESC",
        (g.user["id"],),
    )
    return jsonify({"withdrawals": [serialize(r) for r in rows]})


@bp.post("")
@role_required("supplier")
def request_withdrawal():
    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number."}), 400
    method = clean_str(data.get("method"), 30)
    account_details = clean_str(data.get("account_details"), 200)

    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero."}), 400
    if not method or not account_details:
        return jsonify({"error": "Method and account details are required."}), 400

    available = _available_balance(g.user["id"])
    if amount > available:
        return jsonify({"error": f"Amount exceeds your available balance of ৳{available}."}), 400

    wid = execute(
        """INSERT INTO withdrawals (supplier_id, amount, method, account_details, status)
           VALUES (?, ?, ?, ?, 'pending')""",
        (g.user["id"], amount, method, account_details),
    )
    w = query("SELECT * FROM withdrawals WHERE id = ?", (wid,), one=True)
    return jsonify({"withdrawal": serialize(w), "message": "Withdrawal requested — an admin will process it."}), 201


# ---- Admin processing ----

@bp.get("/admin/all")
@role_required("admin")
def admin_list():
    rows = query(
        """SELECT w.*, u.company_name AS supplier_name FROM withdrawals w
           JOIN users u ON u.id = w.supplier_id ORDER BY w.requested_at DESC"""
    )
    return jsonify({"withdrawals": [dict(r) for r in rows]})


@bp.post("/admin/<int:withdrawal_id>/process")
@role_required("admin")
def admin_process(withdrawal_id):
    w = query("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,), one=True)
    if not w:
        return jsonify({"error": "Withdrawal not found."}), 404
    if w["status"] != "pending":
        return jsonify({"error": "This withdrawal was already processed."}), 400

    data = request.get_json(silent=True) or {}
    new_status = clean_str(data.get("status"), 20)
    note = clean_str(data.get("note"), 300)
    if new_status not in ("completed", "rejected"):
        return jsonify({"error": "Status must be 'completed' or 'rejected'."}), 400

    execute(
        "UPDATE withdrawals SET status = ?, note = ?, processed_at = datetime('now') WHERE id = ?",
        (new_status, note or None, withdrawal_id),
    )
    from notifications import notify
    notify(
        w["supplier_id"], "withdrawal_processed",
        f"Withdrawal {'approved' if new_status == 'completed' else 'rejected'}",
        f"Your withdrawal of ৳{w['amount']} was {new_status}." + (f" Note: {note}" if note else ""),
    )
    return jsonify({"message": "Withdrawal updated."})