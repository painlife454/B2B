import os
import secrets

from flask import Flask, render_template, jsonify, g
from werkzeug.security import generate_password_hash

from config import Config
import database
from database import query, execute
from utils import get_current_user

import auth
import products
import orders
import payments
import chat
import admin
import subscription
import home
import reviews
import ads
import notifications
import buyers
import withdrawals
import dashboard
import buyer_requests
import favorites


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    database.init_app(app)

    with app.app_context():
        if not database.DB_PATH.exists():
            database.init_db()
            _seed_admin(app)
        else:
            database.migrate_db()

    app.register_blueprint(auth.bp)
    app.register_blueprint(products.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(payments.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(subscription.bp)
    app.register_blueprint(home.bp)
    app.register_blueprint(reviews.bp)
    app.register_blueprint(ads.bp)
    app.register_blueprint(notifications.bp)
    app.register_blueprint(buyers.bp)
    app.register_blueprint(withdrawals.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(buyer_requests.bp)
    app.register_blueprint(favorites.bp)

    @app.after_request
    def set_security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'"
        )
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        return resp

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({"error": "Not found."}), 404
        return render_template("app_shell.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        # Log the real error to the terminal (never to the client) so it's
        # actually diagnosable instead of vanishing into a generic message.
        app.logger.exception("Unhandled server error")
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    # ---- Page routes (all real UI logic lives client-side, calling /api/*) ----
    @app.get("/")
    @app.get("/login")
    @app.get("/register")
    @app.get("/dashboard")
    @app.get("/products")
    @app.get("/products/<int:_pid>")
    @app.get("/orders/<int:_oid>")
    @app.get("/admin")
    def app_shell(_pid=None, _oid=None):
        return render_template("app_shell.html")

    return app


def _wants_json():
    from flask import request
    return request.path.startswith("/api/")


def _seed_admin(app):
    """Create a default admin account on first run. Change the password immediately."""
    with app.app_context():
        existing = query("SELECT id FROM users WHERE role = 'admin'", one=True)
        if existing:
            return
        default_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(12)
        pw_hash = generate_password_hash(default_password)
        execute(
            """INSERT INTO users (name, company_name, email, password_hash, role, phone)
               VALUES (?, ?, ?, ?, 'admin', ?)""",
            ("Platform Admin", "Marketplace HQ", "admin@marketplace.local", pw_hash, ""),
        )
        print("=" * 60)
        print(" First run: created default admin account")
        print(" Email:    admin@marketplace.local")
        print(f" Password: {default_password}")
        print(" Please log in and change this immediately.")
        print("=" * 60)


app = create_app()

if __name__ == "__main__":
    # debug=False in any shared/production environment to avoid leaking
    # internals via the interactive debugger.
    app.run(host="127.0.0.1", port=5000, debug=False)