import sqlite3
from pathlib import Path
from werkzeug.security import check_password_hash

DB_PATH = Path(__file__).resolve().parent / "instance" / "marketplace.db"
TEST_PASSWORD = "Admin@12345"

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM users WHERE role = 'admin'").fetchall()

if not rows:
    print("No admin account exists in this database.")
else:
    for r in rows:
        matches = check_password_hash(r["password_hash"], TEST_PASSWORD)
        print("id:", r["id"])
        print("email:", r["email"])
        print("is_active:", r["is_active"])
        print("failed_logins:", r["failed_logins"])
        print("locked_until:", r["locked_until"])
        print("password matches:", matches)
conn.close()