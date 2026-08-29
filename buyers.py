from flask import Blueprint, jsonify, g

from database import query
from utils import role_required

bp = Blueprint("buyers", __name__, url_prefix="/api/buyers")


@bp.get("/mine")
@role_required("supplier")
def my_buyers():
    """
    Every buyer who has ordered from this supplier, with real aggregate
    stats computed from actual orders - no fabricated numbers.
    """
    rows = query(
        """
        SELECT
            u.id, u.name, u.company_name, u.phone,
            COUNT(o.id) AS total_orders,
            COALESCE(SUM(CASE WHEN o.status != 'cancelled' THEN o.total_price ELSE 0 END), 0) AS total_purchase,
            COALESCE(SUM(CASE WHEN p.status = 'escrow' THEN o.total_price ELSE 0 END), 0) AS outstanding_payment,
            MAX(o.created_at) AS last_order_at
        FROM orders o
        JOIN users u ON u.id = o.buyer_id
        LEFT JOIN payments p ON p.order_id = o.id
        WHERE o.supplier_id = ?
        GROUP BY u.id
        ORDER BY last_order_at DESC
        """,
        (g.user["id"],),
    )
    return jsonify({"buyers": [dict(r) for r in rows]})


@bp.get("/<int:buyer_id>/orders")
@role_required("supplier")
def buyer_orders(buyer_id):
    """This buyer's order history with the current supplier, with delivery address."""
    rows = query(
        """SELECT id, product_id, quantity, total_price, status, created_at,
                  recipient_address FROM orders
           WHERE supplier_id = ? AND buyer_id = ? ORDER BY created_at DESC""",
        (g.user["id"], buyer_id),
    )
    return jsonify({"orders": [dict(r) for r in rows]})