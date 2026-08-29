from flask import Blueprint, request, jsonify, g

from database import query, execute
from utils import role_required, clean_str

bp = Blueprint("reviews", __name__, url_prefix="/api")


def serialize(r):
    return {
        "id": r["id"], "product_id": r["product_id"], "buyer_id": r["buyer_id"],
        "rating": r["rating"], "comment": r["comment"], "created_at": r["created_at"],
        "buyer_company": r["buyer_company"] if "buyer_company" in r.keys() else None,
    }


@bp.get("/products/<int:product_id>/reviews")
def list_reviews(product_id):
    rows = query(
        """SELECT r.*, u.company_name AS buyer_company FROM reviews r
           JOIN users u ON u.id = r.buyer_id
           WHERE r.product_id = ? ORDER BY r.created_at DESC LIMIT 100""",
        (product_id,),
    )
    return jsonify({"reviews": [serialize(r) for r in rows]})


@bp.post("/reviews")
@role_required("buyer")
def submit_review():
    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "product_id and rating are required."}), 400
    comment = clean_str(data.get("comment"), 1000)

    if not (1 <= rating <= 5):
        return jsonify({"error": "Rating must be between 1 and 5."}), 400

    # Only buyers who actually received the product may review it — this
    # keeps ratings tied to a real transaction rather than being fakeable.
    delivered = query(
        """SELECT id FROM orders WHERE buyer_id = ? AND product_id = ? AND status = 'delivered' LIMIT 1""",
        (g.user["id"], product_id), one=True,
    )
    if not delivered:
        return jsonify({"error": "You can only review products from a delivered order."}), 403

    existing = query(
        "SELECT id FROM reviews WHERE product_id = ? AND buyer_id = ?",
        (product_id, g.user["id"]), one=True,
    )
    if existing:
        execute("UPDATE reviews SET rating = ?, comment = ? WHERE id = ?", (rating, comment, existing["id"]))
        review_id = existing["id"]
    else:
        review_id = execute(
            "INSERT INTO reviews (product_id, buyer_id, rating, comment) VALUES (?, ?, ?, ?)",
            (product_id, g.user["id"], rating, comment),
        )

    row = query(
        """SELECT r.*, u.company_name AS buyer_company FROM reviews r
           JOIN users u ON u.id = r.buyer_id WHERE r.id = ?""",
        (review_id,), one=True,
    )
    return jsonify({"review": serialize(row), "message": "Review saved."}), 201
