from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import login_required

bp = Blueprint("favorites", __name__, url_prefix="/api/favorites")


@bp.get("")
@login_required
def list_favorites():
    rows = query("SELECT * FROM favorites WHERE user_id = ? ORDER BY created_at DESC", (g.user["id"],))
    products, suppliers = [], []
    for r in rows:
        if r["target_type"] == "product":
            p = query(
                """SELECT p.*, u.company_name AS supplier_company FROM products p
                   JOIN users u ON u.id = p.supplier_id WHERE p.id = ? AND p.is_active = 1""",
                (r["target_id"],), one=True,
            )
            if p:
                products.append(dict(p))
        else:
            s = query(
                "SELECT id, company_name, name FROM users WHERE id = ? AND role = 'supplier'",
                (r["target_id"],), one=True,
            )
            if s:
                suppliers.append(dict(s))
    return jsonify({"products": products, "suppliers": suppliers})


@bp.post("/toggle")
@login_required
def toggle_favorite():
    data = request.get_json(silent=True) or {}
    target_type = data.get("target_type")
    if target_type not in ("product", "supplier"):
        return jsonify({"error": "target_type must be 'product' or 'supplier'."}), 400
    try:
        target_id = int(data.get("target_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid target_id."}), 400

    existing = query(
        "SELECT id FROM favorites WHERE user_id = ? AND target_type = ? AND target_id = ?",
        (g.user["id"], target_type, target_id), one=True,
    )
    if existing:
        execute("DELETE FROM favorites WHERE id = ?", (existing["id"],))
        return jsonify({"favorited": False})

    execute(
        "INSERT INTO favorites (user_id, target_type, target_id) VALUES (?, ?, ?)",
        (g.user["id"], target_type, target_id),
    )
    return jsonify({"favorited": True})


@bp.get("/check")
@login_required
def check_favorites():
    """Bulk-check which of a set of product ids are favorited, for rendering star icons."""
    ids = request.args.get("product_ids", "")
    id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()]
    if not id_list:
        return jsonify({"favorited_ids": []})
    placeholders = ",".join("?" * len(id_list))
    rows = query(
        f"""SELECT target_id FROM favorites WHERE user_id = ? AND target_type = 'product'
            AND target_id IN ({placeholders})""",
        (g.user["id"], *id_list),
    )
    return jsonify({"favorited_ids": [r["target_id"] for r in rows]})