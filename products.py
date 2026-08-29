from pathlib import Path
import secrets as _secrets

from flask import Blueprint, request, jsonify, g
from PIL import Image, UnidentifiedImageError

from database import query, execute
from utils import login_required, role_required, clean_str, get_current_user

bp = Blueprint("products", __name__, url_prefix="/api/products")

UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "uploads" / "products"
ALLOWED_UPLOAD_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_UPLOAD_DIMENSION = 4000  # px, guards against decompression-bomb style images


@bp.post("/upload_image")
@role_required("supplier")
def upload_image():
    """
    Accepts a direct image file upload (for sellers who don't have an image
    URL to paste in) and returns a URL the product form can use like any
    other image_url. The file itself is re-encoded via Pillow rather than
    saved as-is, so a malicious file with a fake .jpg extension can't slip
    through — if Pillow can't decode it as a real image, it's rejected.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_UPLOAD_EXT:
        return jsonify({"error": "Only JPG, PNG, GIF, or WEBP images are allowed."}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": "Image must be under 5 MB."}), 400
    if size == 0:
        return jsonify({"error": "That file is empty."}), 400

    try:
        img = Image.open(file)
        img.verify()  # confirms it's a genuine, uncorrupted image
        file.seek(0)
        img = Image.open(file)  # reopen — verify() leaves the image unusable
        if img.format not in ALLOWED_PIL_FORMATS:
            return jsonify({"error": "Unsupported image format."}), 400
        if img.width > MAX_UPLOAD_DIMENSION or img.height > MAX_UPLOAD_DIMENSION:
            return jsonify({"error": f"Image dimensions must be under {MAX_UPLOAD_DIMENSION}px."}), 400
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"error": "That file doesn't look like a valid image."}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    save_ext = "jpg" if img.format == "JPEG" else img.format.lower()
    filename = f"{_secrets.token_hex(16)}.{save_ext}"

    # Re-encode from the decoded pixel data rather than writing the
    # original bytes — this strips any non-image payload that might be
    # smuggled inside an otherwise-valid image container, and drops EXIF
    # metadata the uploader may not have meant to share.
    if img.mode in ("RGBA", "P") and save_ext in ("jpg",):
        img = img.convert("RGB")
    img.save(str(UPLOAD_DIR / filename))

    return jsonify({"url": f"/static/uploads/products/{filename}"}), 201


@bp.get("/category-tree")
def category_tree():
    rows = query("SELECT id, name, parent_id FROM categories ORDER BY parent_id IS NOT NULL, id")
    mains = [dict(r) for r in rows if r["parent_id"] is None]
    for m in mains:
        m["children"] = [
            {"id": r["id"], "name": r["name"]} for r in rows if r["parent_id"] == m["id"]
        ]
        del m["parent_id"]
    return jsonify({"categories": mains})


def valid_image_url(url):
    if not url:
        return True  # optional field
    if len(url) > 1000:
        return False
    if url.startswith("/static/uploads/products/"):
        return True  # our own upload endpoint generates these
    return url.startswith("https://") or url.startswith("http://")


MAX_COLORS = 20
MAX_SIZES = 20
MAX_TIERS = 10


def _parse_colors(raw):
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_COLORS:
        raise ValueError(f"Provide at most {MAX_COLORS} colors.")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each color must be an object with a name.")
        name = clean_str(item.get("name"), 60)
        image_url = clean_str(item.get("image_url"), 1000)
        if not name:
            raise ValueError("Each color needs a name.")
        if not valid_image_url(image_url):
            raise ValueError("Color image URL must be a valid http(s) link.")
        out.append((name, image_url or None))
    return out


def _parse_sizes(raw):
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_SIZES:
        raise ValueError(f"Provide at most {MAX_SIZES} sizes.")
    out = []
    for item in raw:
        label = clean_str(item.get("label") if isinstance(item, dict) else item, 30)
        if not label:
            raise ValueError("Size labels can't be empty.")
        out.append(label)
    return out


def _parse_price_tiers(raw):
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_TIERS:
        raise ValueError(f"Provide at most {MAX_TIERS} price tiers.")
    out = []
    seen_qty = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each price tier must be an object with min_qty and price.")
        try:
            min_qty = int(item.get("min_qty"))
            price = float(item.get("price"))
        except (TypeError, ValueError):
            raise ValueError("Tier min_qty must be an integer and price a number.")
        if min_qty < 1 or price < 0:
            raise ValueError("Tier min_qty must be at least 1 and price can't be negative.")
        if min_qty in seen_qty:
            raise ValueError("Each price tier needs a distinct minimum quantity.")
        seen_qty.add(min_qty)
        out.append((min_qty, price))
    out.sort(key=lambda t: t[0])
    return out


def _save_variants(product_id, colors, sizes, tiers):
    execute("DELETE FROM product_colors WHERE product_id = ?", (product_id,))
    execute("DELETE FROM product_sizes WHERE product_id = ?", (product_id,))
    execute("DELETE FROM product_price_tiers WHERE product_id = ?", (product_id,))
    for name, image_url in colors:
        execute("INSERT INTO product_colors (product_id, name, image_url) VALUES (?, ?, ?)",
                (product_id, name, image_url))
    for label in sizes:
        execute("INSERT INTO product_sizes (product_id, label) VALUES (?, ?)", (product_id, label))
    for min_qty, price in tiers:
        execute("INSERT INTO product_price_tiers (product_id, min_qty, price) VALUES (?, ?, ?)",
                (product_id, min_qty, price))


def _fetch_variants(product_id):
    colors = query("SELECT * FROM product_colors WHERE product_id = ? ORDER BY id", (product_id,))
    sizes = query("SELECT * FROM product_sizes WHERE product_id = ? ORDER BY id", (product_id,))
    tiers = query("SELECT * FROM product_price_tiers WHERE product_id = ? ORDER BY min_qty ASC", (product_id,))
    return {
        "colors": [{"id": c["id"], "name": c["name"], "image_url": c["image_url"]} for c in colors],
        "sizes": [{"id": s["id"], "label": s["label"]} for s in sizes],
        "price_tiers": [{"id": t["id"], "min_qty": t["min_qty"], "price": t["price"]} for t in tiers],
    }


FEATURED_JOIN = """
    FROM products p
    JOIN users u ON u.id = p.supplier_id
"""
FEATURED_ORDER = """
    ORDER BY
        (u.is_featured = 1 AND u.featured_until IS NOT NULL
         AND u.featured_until > datetime('now')) DESC,
        p.created_at DESC
"""
STATS_SELECT = """
    (SELECT COALESCE(SUM(o.quantity),0) FROM orders o
     WHERE o.product_id = p.id AND o.status = 'delivered') AS sold_count,
    (SELECT ROUND(AVG(r.rating), 1) FROM reviews r WHERE r.product_id = p.id) AS avg_rating,
    (SELECT COUNT(*) FROM reviews r WHERE r.product_id = p.id) AS review_count
"""


def serialize(p):
    keys = p.keys()
    return {
        "id": p["id"], "supplier_id": p["supplier_id"], "name": p["name"],
        "category": p["category"], "category_id": p["category_id"] if "category_id" in keys else None,
        "description": p["description"],
        "specification": p["specification"] if "specification" in keys else None,
        "price": p["price"], "moq": p["moq"], "stock": p["stock"],
        "low_stock_threshold": p["low_stock_threshold"] if "low_stock_threshold" in keys else 5,
        "image_url": p["image_url"] if "image_url" in keys else None,
        "delivery_method": p["delivery_method"] if "delivery_method" in keys else None,
        "delivery_charge": p["delivery_charge"] if "delivery_charge" in keys else None,
        "delivery_areas": p["delivery_areas"] if "delivery_areas" in keys else None,
        "created_at": p["created_at"],
        "is_featured": bool(p["is_featured"]) if "is_featured" in keys else False,
        "supplier_company": p["supplier_company"] if "supplier_company" in keys else None,
        "supplier_verified": (p["supplier_kyc_status"] == "approved") if "supplier_kyc_status" in keys else None,
        "sold_count": p["sold_count"] if "sold_count" in keys else 0,
        "avg_rating": p["avg_rating"] if "avg_rating" in keys else None,
        "review_count": p["review_count"] if "review_count" in keys else 0,
    }


def _resolve_category_id(category_id):
    """Validate a category_id and return its own name (used as the flat
    category text so all existing browse/home grouping keeps working)."""
    if not category_id:
        return None, None
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid category selected.")
    row = query("SELECT id, name FROM categories WHERE id = ?", (category_id,), one=True)
    if not row:
        raise ValueError("Selected category not found.")
    return category_id, row["name"]


def _category_and_children(name):
    """
    If `name` is a top-level category with subcategories, return [name, *sub
    names] so browsing a main category also surfaces products tagged with
    one of its subcategories. Otherwise just [name].
    """
    children = query(
        """SELECT c.name FROM categories c JOIN categories p ON p.id = c.parent_id
           WHERE p.name = ? AND p.parent_id IS NULL""",
        (name,),
    )
    return [name] + [r["name"] for r in children]


@bp.get("")
def list_products():
    """Public browse/search endpoint (buyers browse without needing to log in).
    Products from currently-subscribed (featured) suppliers are listed first."""
    search = clean_str(request.args.get("q", ""), 120)
    category = clean_str(request.args.get("category", ""), 80)
    exclude_id = request.args.get("exclude", type=int)
    supplier_id = request.args.get("supplier", type=int)
    location = clean_str(request.args.get("location", ""), 120)
    max_moq = request.args.get("max_moq", type=int)
    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    min_rating = request.args.get("min_rating", type=float)
    verified_only = request.args.get("verified", "") in ("1", "true", "yes")
    limit = min(request.args.get("limit", 200, type=int) or 200, 200)

    sql = f"""SELECT p.*, u.company_name AS supplier_company,
                (u.is_featured = 1 AND u.featured_until IS NOT NULL
                 AND u.featured_until > datetime('now')) AS is_featured,
                u.kyc_status AS supplier_kyc_status,
                {STATS_SELECT}
              {FEATURED_JOIN} WHERE p.is_active = 1"""
    params = []
    if search:
        sql += " AND (p.name LIKE ? OR p.description LIKE ?)"
        like = f"%{search}%"
        params += [like, like]
    if category:
        names = _category_and_children(category)
        placeholders = ",".join("?" * len(names))
        sql += f" AND p.category IN ({placeholders})"
        params += names
    if supplier_id:
        sql += " AND p.supplier_id = ?"
        params.append(supplier_id)
    if exclude_id:
        sql += " AND p.id != ?"
        params.append(exclude_id)
    if location:
        sql += " AND (u.address LIKE ? OR u.delivery_areas LIKE ? OR p.delivery_areas LIKE ?)"
        like = f"%{location}%"
        params += [like, like, like]
    if max_moq:
        sql += " AND p.moq <= ?"
        params.append(max_moq)
    if min_price is not None:
        sql += " AND p.price >= ?"
        params.append(min_price)
    if max_price is not None:
        sql += " AND p.price <= ?"
        params.append(max_price)
    if verified_only:
        sql += " AND u.kyc_status = 'approved'"

    if min_rating is not None:
        sql += " HAVING avg_rating >= ?"
        params.append(min_rating)

    sql += FEATURED_ORDER + " LIMIT ?"
    params.append(limit)

    rows = query(sql, tuple(params))
    return jsonify({"products": [serialize(r) for r in rows]})


@bp.get("/<int:product_id>")
def get_product(product_id):
    sql = f"""SELECT p.*, {STATS_SELECT} FROM products p WHERE p.id = ? AND p.is_active = 1"""
    p = query(sql, (product_id,), one=True)
    if not p:
        return jsonify({"error": "Product not found."}), 404
    supplier = query("SELECT id, company_name, name FROM users WHERE id = ?", (p["supplier_id"],), one=True)
    data = serialize(p)
    data["supplier"] = dict(supplier) if supplier else None
    data.update(_fetch_variants(product_id))
    return jsonify({"product": data})


@bp.get("/mine")
@role_required("supplier")
def my_products():
    rows = query("SELECT * FROM products WHERE supplier_id = ? ORDER BY created_at DESC", (g.user["id"],))
    return jsonify({"products": [serialize(r) for r in rows]})


@bp.post("")
@role_required("supplier")
def create_product():
    data = request.get_json(silent=True) or {}
    name = clean_str(data.get("name"), 160)
    category = clean_str(data.get("category"), 80)
    description = clean_str(data.get("description"), 2000)
    specification = clean_str(data.get("specification"), 2000)
    image_url = clean_str(data.get("image_url"), 1000)
    delivery_method = clean_str(data.get("delivery_method"), 100)
    delivery_areas = clean_str(data.get("delivery_areas"), 300)
    try:
        price = float(data.get("price"))
        moq = int(data.get("moq"))
        stock = int(data.get("stock", 0))
        low_stock_threshold = int(data.get("low_stock_threshold", 5))
        delivery_charge = float(data.get("delivery_charge")) if data.get("delivery_charge") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "Price, MOQ, stock, threshold, and delivery charge must be numbers."}), 400

    try:
        category_id, category_name = _resolve_category_id(data.get("category_id"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if category_name:
        category = category_name  # keep the flat text field in sync with the chosen category

    if not name or not category:
        return jsonify({"error": "Name and category are required."}), 400
    if price < 0 or moq < 1 or stock < 0 or low_stock_threshold < 0:
        return jsonify({"error": "Price/stock/threshold cannot be negative, MOQ must be at least 1."}), 400
    if delivery_charge is not None and delivery_charge < 0:
        return jsonify({"error": "Delivery charge can't be negative."}), 400
    if not valid_image_url(image_url):
        return jsonify({"error": "Image URL must be a valid http(s) link."}), 400

    try:
        colors = _parse_colors(data.get("colors"))
        sizes = _parse_sizes(data.get("sizes"))
        tiers = _parse_price_tiers(data.get("price_tiers"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    pid = execute(
        """INSERT INTO products (supplier_id, name, category, category_id, description, specification,
                                  price, moq, stock, low_stock_threshold, image_url,
                                  delivery_method, delivery_charge, delivery_areas)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (g.user["id"], name, category, category_id, description, specification or None,
         price, moq, stock, low_stock_threshold, image_url or None,
         delivery_method or None, delivery_charge, delivery_areas or None),
    )
    _save_variants(pid, colors, sizes, tiers)
    p = query("SELECT * FROM products WHERE id = ?", (pid,), one=True)
    data_out = serialize(p)
    data_out.update(_fetch_variants(pid))
    return jsonify({"product": data_out}), 201


def _owned_product_or_none(product_id, supplier_id):
    return query("SELECT * FROM products WHERE id = ? AND supplier_id = ?", (product_id, supplier_id), one=True)


@bp.put("/<int:product_id>")
@role_required("supplier")
def update_product(product_id):
    p = _owned_product_or_none(product_id, g.user["id"])
    if not p:
        return jsonify({"error": "Product not found."}), 404

    data = request.get_json(silent=True) or {}
    name = clean_str(data.get("name", p["name"]), 160)
    category = clean_str(data.get("category", p["category"]), 80)
    description = clean_str(data.get("description", p["description"]), 2000)
    specification = clean_str(data.get("specification", p["specification"] or ""), 2000)
    image_url = clean_str(data.get("image_url", p["image_url"] or ""), 1000)
    delivery_method = clean_str(data.get("delivery_method", p["delivery_method"] or ""), 100)
    delivery_areas = clean_str(data.get("delivery_areas", p["delivery_areas"] or ""), 300)
    try:
        price = float(data.get("price", p["price"]))
        moq = int(data.get("moq", p["moq"]))
        stock = int(data.get("stock", p["stock"]))
        low_stock_threshold = int(data.get("low_stock_threshold", p["low_stock_threshold"]))
        raw_delivery_charge = data.get("delivery_charge", p["delivery_charge"])
        delivery_charge = float(raw_delivery_charge) if raw_delivery_charge not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"error": "Price, MOQ, stock, threshold, and delivery charge must be numbers."}), 400
    if price < 0 or moq < 1 or stock < 0 or low_stock_threshold < 0:
        return jsonify({"error": "Invalid values."}), 400
    if delivery_charge is not None and delivery_charge < 0:
        return jsonify({"error": "Delivery charge can't be negative."}), 400
    if not valid_image_url(image_url):
        return jsonify({"error": "Image URL must be a valid http(s) link."}), 400

    try:
        category_id = p["category_id"]
        if "category_id" in data:
            category_id, category_name = _resolve_category_id(data.get("category_id"))
            if category_name:
                category = category_name
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        colors = _parse_colors(data.get("colors"))
        sizes = _parse_sizes(data.get("sizes"))
        tiers = _parse_price_tiers(data.get("price_tiers"))
        variants_provided = any(k in data for k in ("colors", "sizes", "price_tiers"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    execute(
        """UPDATE products SET name=?, category=?, category_id=?, description=?, specification=?,
           price=?, moq=?, stock=?, low_stock_threshold=?, image_url=?,
           delivery_method=?, delivery_charge=?, delivery_areas=?
           WHERE id = ? AND supplier_id = ?""",
        (name, category, category_id, description, specification or None,
         price, moq, stock, low_stock_threshold, image_url or None,
         delivery_method or None, delivery_charge, delivery_areas or None,
         product_id, g.user["id"]),
    )
    if variants_provided:
        _save_variants(product_id, colors, sizes, tiers)
    p = query("SELECT * FROM products WHERE id = ?", (product_id,), one=True)
    data_out = serialize(p)
    data_out.update(_fetch_variants(product_id))
    return jsonify({"product": data_out})


@bp.delete("/<int:product_id>")
@role_required("supplier")
def delete_product(product_id):
    p = _owned_product_or_none(product_id, g.user["id"])
    if not p:
        return jsonify({"error": "Product not found."}), 404
    execute("UPDATE products SET is_active = 0 WHERE id = ? AND supplier_id = ?", (product_id, g.user["id"]))
    return jsonify({"message": "Product removed."})


@bp.get("/categories")
def categories():
    rows = query("SELECT DISTINCT category FROM products WHERE is_active = 1 ORDER BY category")
    return jsonify({"categories": [r["category"] for r in rows]})