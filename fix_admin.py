import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).resolve().parent / "instance" / "marketplace.db"
NEW_PASSWORD = "Admin@12345"

conn = sqlite3.connect(str(DB_PATH))
pw_hash = generate_password_hash(NEW_PASSWORD)
conn.execute(
    "UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE role = 'admin'",
    (pw_hash,)
)
conn.commit()
conn.close()
print("Done. Password set to:", NEW_PASSWORD)