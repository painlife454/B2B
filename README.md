# B2B Marketplace — Full Stack Demo

A working B2B marketplace built with **Python (Flask) + SQLite + vanilla HTML/CSS/JS**,
covering: authentication, product management, orders, real-time-style chat,
simulated escrow payments, and an admin dashboard.

## Stack
- **Backend:** Python 3 / Flask (REST API, blueprints per feature)
- **Database:** SQLite (file-based, zero setup) — swap for PostgreSQL in production
  by changing `database.py` only; all SQL uses the DB-API `?` placeholder style.
- **Frontend:** Vanilla HTML/CSS/JS single-page app (hash routing, `fetch()` calls,
  no build step required)
- **Auth:** JWT stored in an httpOnly cookie + CSRF double-submit token

## Running it

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**. On first run the server prints a one-time
admin login to the console — copy it immediately, log in at `/#/login`, and
treat that password as compromised the moment you've read it (rotate it).

Everything lives in one SQLite file at `instance/marketplace.db`, created
automatically on first run.

## Feature map

| Area | Where |
|---|---|
| Register / login / logout, account lockout | `auth.py` |
| Product CRUD, search/browse | `products.py` |
| Orders + status state machine | `orders.py` |
| Simulated escrow payments + commission | `payments.py` |
| Order chat + product inquiry (RFQ) chat | `chat.py` |
| Admin overview, user/product moderation, commission settings | `admin.py` |
| Security helpers (JWT, CSRF, rate limiting, validators) | `utils.py` |
| Frontend SPA | `static/js/app.js`, `static/css/style.css`, `templates/app_shell.html` |

## Security measures implemented

- **Password storage:** PBKDF2-SHA256 salted hashes via `werkzeug.security` —
  passwords are never stored or logged in plaintext.
- **SQL injection:** every query is parameterized (`?` placeholders); no string
  formatting is ever used to build SQL.
- **XSS:** the frontend never injects user-supplied text via raw `innerHTML`;
  all dynamic text goes through `textContent` or an HTML-escaping helper.
  Flask's Jinja layer also autoescapes by default.
- **CSRF:** double-submit cookie pattern — a random CSRF token is issued at
  login, stored in the JWT and in a separate readable cookie; every
  state-changing request must echo it back in an `X-CSRF-Token` header.
- **Auth cookie:** the session JWT itself is `httpOnly` (unreachable from JS)
  and `SameSite=Lax`; set `COOKIE_SECURE=true` behind HTTPS in production.
- **Authorization:** every endpoint enforces role checks (`buyer`/`supplier`/
  `admin`) and ownership checks (a supplier can only edit their own products;
  a buyer/supplier can only see orders they're a party to) — verified in
  testing (cross-tenant access returns 404/403, not data).
- **Order state machine:** status transitions are whitelisted per role
  (e.g. only a supplier can mark `confirmed`→`shipped`; only a buyer can
  confirm `delivered`), preventing either party from short-circuiting the flow.
- **Brute-force protection:** login attempts are rate-limited per IP, and
  accounts lock temporarily after repeated failures. Login errors are
  intentionally generic ("Invalid email or password") so failed attempts
  can't be used to enumerate registered emails.
- **Secrets:** the JWT signing key is generated randomly on first run and
  persisted outside source control (`instance/secret.key`, gitignored) —
  never hardcoded.
- **HTTP security headers:** `X-Content-Type-Options`, `X-Frame-Options`,
  `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy` are set
  on every response.
- **Error handling:** unhandled server errors return a generic message —
  stack traces are never exposed to the client (`debug=False`).
- **Request size cap:** bodies are capped at 1 MB to reduce trivial DoS risk.

## Notes / honest limitations

- **Payments are simulated.** No real card or mobile-money data is collected;
  the "gateway" just records an escrow entry and computes commission. Wiring
  up bKash/Nagad/SSLCommerz for real transactions means integrating their
  actual SDKs/APIs with your own merchant credentials — that's a separate,
  larger step this demo intentionally doesn't take.
- **Chat is polling-based**, not WebSocket-based (kept dependency-free). It
  refreshes on send/open rather than pushing instantly — fine for a demo,
  but swap in `Flask-SocketIO` for true real-time chat if you need it.
- **Single-process rate limiting** (in-memory) is fine for one server; behind
  multiple workers/instances you'd want Redis-backed limiting instead.
- Run behind a real WSGI server (gunicorn/uwsgi) + HTTPS before putting this
  in front of real users — the built-in Flask server is dev-only, as it
  warns on startup.
