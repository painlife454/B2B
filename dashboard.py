from flask import Blueprint, jsonify, g

from database import query
from utils import role_required

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.get("/overview")
@role_required("supplier")
def overview():
    sid = g.user["id"]

    product_counts = query(
        "SELECT COUNT(*) AS total, SUM(is_active) AS active FROM products WHERE supplier_id = ?",
        (sid,), one=True,
    )

    order_counts = query(
        "SELECT status, COUNT(*) AS c FROM orders WHERE supplier_id = ? GROUP BY status",
        (sid,),
    )
    orders_by_status = {r["status"]: r["c"] for r in order_counts}

    total_sales = query(
        "SELECT COALESCE(SUM(total_price), 0) AS total FROM orders WHERE supplier_id = ? AND status != 'cancelled'",
        (sid,), one=True,
    )["total"]

    pending_payment = query(
        """SELECT COALESCE(SUM(p.amount), 0) AS total FROM payments p
           JOIN orders o ON o.id = p.order_id
           WHERE o.supplier_id = ? AND p.status = 'escrow'""",
        (sid,), one=True,
    )["total"]

    from withdrawals import _available_balance
    available_balance = _available_balance(sid)

    today_orders = query(
        "SELECT COUNT(*) AS c FROM orders WHERE supplier_id = ? AND date(created_at) = date('now')",
        (sid,), one=True,
    )["c"]

    this_month_sales = query(
        """SELECT COALESCE(SUM(total_price), 0) AS total FROM orders
           WHERE supplier_id = ? AND status != 'cancelled'
             AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')""",
        (sid,), one=True,
    )["total"]

    return jsonify({
        "total_products": product_counts["total"] or 0,
        "active_products": product_counts["active"] or 0,
        "orders_by_status": orders_by_status,
        "total_sales": total_sales,
        "pending_payment": pending_payment,
        "available_balance": available_balance,
        "today_orders": today_orders,
        "this_month_sales": this_month_sales,
    })


@bp.get("/sales_by_day")
@role_required("supplier")
def sales_by_day():
    """Last 30 days of sales, for the overview chart. Days with no orders are omitted."""
    rows = query(
        """SELECT date(created_at) AS day, COALESCE(SUM(total_price), 0) AS sales, COUNT(*) AS orders
           FROM orders
           WHERE supplier_id = ? AND status != 'cancelled' AND created_at >= date('now', '-30 days')
           GROUP BY day ORDER BY day""",
        (g.user["id"],),
    )
    return jsonify({"days": [dict(r) for r in rows]})


@bp.get("/reports")
@role_required("supplier")
def reports():
    sid = g.user["id"]

    def sales_since(days):
        row = query(
            """SELECT COALESCE(SUM(total_price), 0) AS total, COUNT(*) AS orders FROM orders
               WHERE supplier_id = ? AND status != 'cancelled' AND created_at >= datetime('now', ?)""",
            (sid, f"-{days} days"), one=True,
        )
        return {"sales": row["total"], "orders": row["orders"]}

    best_sellers = query(
        """SELECT p.id, p.name,
                  COALESCE(SUM(CASE WHEN o.status = 'delivered' THEN o.quantity ELSE 0 END), 0) AS sold
           FROM products p
           LEFT JOIN orders o ON o.product_id = p.id
           WHERE p.supplier_id = ?
           GROUP BY p.id ORDER BY sold DESC LIMIT 5""",
        (sid,),
    )

    low_stock = query(
        """SELECT id, name, stock, low_stock_threshold FROM products
           WHERE supplier_id = ? AND is_active = 1 AND stock <= low_stock_threshold
           ORDER BY stock ASC""",
        (sid,),
    )

    cancelled = query(
        "SELECT COUNT(*) AS c FROM orders WHERE supplier_id = ? AND status = 'cancelled'",
        (sid,), one=True,
    )["c"]

    total_orders = query(
        "SELECT COUNT(*) AS c FROM orders WHERE supplier_id = ?", (sid,), one=True,
    )["c"]

    return jsonify({
        "daily": sales_since(1),
        "weekly": sales_since(7),
        "monthly": sales_since(30),
        "best_sellers": [dict(r) for r in best_sellers],
        "low_stock_products": [dict(r) for r in low_stock],
        "total_orders": total_orders,
        "cancelled_orders": cancelled,
    })


# ---------------------------------------------------------------------
# Buyer-side overview & analytics
# ---------------------------------------------------------------------

@bp.get("/buyer_overview")
@role_required("buyer")
def buyer_overview():
    bid = g.user["id"]

    order_counts = query(
        "SELECT status, COUNT(*) AS c FROM orders WHERE buyer_id = ? GROUP BY status",
        (bid,),
    )
    by_status = {r["status"]: r["c"] for r in order_counts}
    total_orders = sum(by_status.values())
    active_orders = sum(by_status.get(s, 0) for s in ("pending", "confirmed", "processing", "ready_for_delivery", "shipped"))
    completed_orders = by_status.get("delivered", 0)
    cancelled_orders = by_status.get("cancelled", 0)
    active_deliveries = by_status.get("shipped", 0) + by_status.get("ready_for_delivery", 0)

    my_requests = query(
        "SELECT COUNT(*) AS c FROM buyer_requests WHERE buyer_id = ? AND status = 'open'",
        (bid,), one=True,
    )["c"]

    received_quotations = query(
        """SELECT COUNT(*) AS c FROM quotations q
           JOIN buyer_requests r ON r.id = q.request_id WHERE r.buyer_id = ?""",
        (bid,), one=True,
    )["c"]

    pending_payments = query(
        """SELECT COALESCE(SUM(p.amount), 0) AS total FROM payments p
           JOIN orders o ON o.id = p.order_id WHERE o.buyer_id = ? AND p.status = 'escrow'""",
        (bid,), one=True,
    )["total"]

    total_purchase = query(
        "SELECT COALESCE(SUM(total_price), 0) AS total FROM orders WHERE buyer_id = ? AND status != 'cancelled'",
        (bid,), one=True,
    )["total"]

    return jsonify({
        "total_orders": total_orders,
        "active_orders": active_orders,
        "completed_orders": completed_orders,
        "cancelled_orders": cancelled_orders,
        "my_buyer_requests": my_requests,
        "received_quotations": received_quotations,
        "pending_payments": pending_payments,
        "total_purchase": total_purchase,
        "active_deliveries": active_deliveries,
    })


@bp.get("/buyer_analytics")
@role_required("buyer")
def buyer_analytics():
    bid = g.user["id"]

    this_month_spend = query(
        """SELECT COALESCE(SUM(total_price), 0) AS total FROM orders
           WHERE buyer_id = ? AND status != 'cancelled'
             AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')""",
        (bid,), one=True,
    )["total"]

    top_products = query(
        """SELECT p.id, p.name, SUM(o.quantity) AS total_qty, SUM(o.total_price) AS total_spent
           FROM orders o JOIN products p ON p.id = o.product_id
           WHERE o.buyer_id = ? AND o.status != 'cancelled'
           GROUP BY p.id ORDER BY total_spent DESC LIMIT 5""",
        (bid,),
    )

    top_suppliers = query(
        """SELECT u.id, u.company_name, SUM(o.total_price) AS total_spent, COUNT(o.id) AS order_count
           FROM orders o JOIN users u ON u.id = o.supplier_id
           WHERE o.buyer_id = ? AND o.status != 'cancelled'
           GROUP BY u.id ORDER BY total_spent DESC LIMIT 5""",
        (bid,),
    )

    monthly_spend = query(
        """SELECT strftime('%Y-%m', created_at) AS month, SUM(total_price) AS total
           FROM orders WHERE buyer_id = ? AND status != 'cancelled'
             AND created_at >= date('now', '-6 months')
           GROUP BY month ORDER BY month""",
        (bid,),
    )

    return jsonify({
        "this_month_spend": this_month_spend,
        "top_products": [dict(r) for r in top_products],
        "top_suppliers": [dict(r) for r in top_suppliers],
        "monthly_spend": [dict(r) for r in monthly_spend],
    })