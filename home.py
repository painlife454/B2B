from flask import Blueprint, jsonify

from database import query
from products import serialize, FEATURED_JOIN, FEATURED_ORDER, STATS_SELECT

bp = Blueprint("home", __name__, url_prefix="/api/home")

PRODUCTS_PER_CATEGORY = 8
FEATURED_LIMIT = 12


@bp.get("")
def home():
    featured_sql = f"""
        SELECT p.*, u.company_name AS supplier_company, 1 AS is_featured, {STATS_SELECT} {FEATURED_JOIN}
        WHERE p.is_active = 1 AND u.is_featured = 1
          AND u.featured_until IS NOT NULL AND u.featured_until > datetime('now')
        ORDER BY p.created_at DESC LIMIT ?
    """
    featured_rows = query(featured_sql, (FEATURED_LIMIT,))

    category_rows = query(
        "SELECT DISTINCT category FROM products WHERE is_active = 1 ORDER BY category"
    )

    categories = []
    for c in category_rows:
        cat = c["category"]
        sql = f"""
            SELECT p.*, u.company_name AS supplier_company,
                (u.is_featured = 1 AND u.featured_until IS NOT NULL
                 AND u.featured_until > datetime('now')) AS is_featured,
                {STATS_SELECT}
            {FEATURED_JOIN}
            WHERE p.is_active = 1 AND p.category = ?
            {FEATURED_ORDER} LIMIT ?
        """
        rows = query(sql, (cat, PRODUCTS_PER_CATEGORY))
        categories.append({
            "category": cat,
            "products": [serialize(r) for r in rows],
        })

    return jsonify({
        "featured": [serialize(r) for r in featured_rows],
        "categories": categories,
    })
