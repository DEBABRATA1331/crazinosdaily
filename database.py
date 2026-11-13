"""
Crazinos Daily - data access layer.

Primary store : Supabase PostgreSQL (schema "crazinos"), configured via DATABASE_URL in .env
Fallback      : local SQLite file (only used when DATABASE_URL is not set, e.g. offline dev/tests)

The application code is written once; this module translates the small set of
SQLite-specific constructs into PostgreSQL and normalises result types
(Decimal -> float, datetime/date -> ISO strings) so both engines behave the same.
"""

import os
import queue
import re
import sqlite3
import ssl
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))

DB_PATH = os.environ.get("KIT_SALES_DB_PATH", os.path.join(BASE_DIR, "kit_sales.db"))
DB_SCHEMA = os.environ.get("DB_SCHEMA", "crazinos")

PREDEFINED_PLATFORMS = [
    "Website",
    "Amazon",
    "Flipkart",
    "Meesho",
    "Offline Sales",
    "Direct / WhatsApp",
    "Other",
]

DEFAULT_SETTINGS = {
    "business_name": "Crazinos Daily",
    "currency": "INR ₹",
    "currency_symbol": "₹",
    "timezone": "Asia/Kolkata",
    "stock_tracking_enabled": "1",
    "profit_calculation_enabled": "1",
    "admin_username": "admin",
    "admin_password_hash": generate_password_hash("admin123"),
    "require_auth": "0",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def parse_database_url(url_str):
    u = urlparse(url_str)
    qs = parse_qs(u.query)
    return {
        "engine": "postgresql",
        "host": u.hostname or "127.0.0.1",
        "port": int(u.port or 5432),
        "database": (u.path or "/postgres").lstrip("/") or "postgres",
        "user": unquote(u.username or "postgres"),
        "password": unquote(u.password or ""),
        "ssl_mode": qs.get("sslmode", ["require"])[0],
    }


def get_pg_config():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith(("postgres://", "postgresql://")):
        return parse_database_url(url)
    return None


def is_postgres():
    return get_pg_config() is not None


# ---------------------------------------------------------------------------
# PostgreSQL wrapper (sqlite3-compatible surface)
# ---------------------------------------------------------------------------


def _normalise(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


class DictRow(dict):
    """Row supporting row['col'] and row[idx], like sqlite3.Row."""

    def __init__(self, columns, values):
        values = [_normalise(v) for v in values]
        super().__init__(zip(columns, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self):  # noqa: D401 - keep dict(row) working
        return super().keys()


class PostgresCursorWrapper:
    def __init__(self, raw_cursor, lastrowid=None):
        self._cur = raw_cursor
        self.lastrowid = lastrowid

    def _cols(self):
        return [col[0] for col in (self._cur.description or [])]

    def fetchone(self):
        if not self._cur.description:
            return None
        row = self._cur.fetchone()
        return DictRow(self._cols(), row) if row is not None else None

    def fetchall(self):
        if not self._cur.description:
            return []
        cols = self._cols()
        return [DictRow(cols, r) for r in self._cur.fetchall()]


_SUBSTR_DAY = re.compile(r"substr\(\s*((?:\w+\.)?)sale_date\s*,\s*1\s*,\s*10\s*\)", re.IGNORECASE)
_SUBSTR_MONTH = re.compile(r"substr\(\s*((?:\w+\.)?)sale_date\s*,\s*1\s*,\s*7\s*\)", re.IGNORECASE)


def translate_sql(sql):
    s = sql.strip()
    if s.upper().startswith("PRAGMA"):
        return None, False

    s = _SUBSTR_DAY.sub(lambda m: f"{m.group(1)}sale_day", s)
    s = _SUBSTR_MONTH.sub(lambda m: f"to_char({m.group(1)}sale_day, 'YYYY-MM')", s)

    ignore = False
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", s, re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        ignore = True

    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+settings", s, re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO\s+settings", "INSERT INTO settings", s, flags=re.IGNORECASE)
        s = s.rstrip().rstrip(";") + " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"

    s = s.replace("?", "%s")

    returning = False
    if (
        not ignore
        and re.match(r"^\s*INSERT\s+INTO\s+(kits|platforms|sales|reports)\b", s, re.IGNORECASE)
        and "RETURNING" not in s.upper()
        and "ON CONFLICT" not in s.upper()
    ):
        s = s.rstrip().rstrip(";") + " RETURNING id"
        returning = True
    return s, returning


class PostgresConnectionWrapper:
    engine = "postgresql"

    def __init__(self, raw_conn, pool=None):
        self._conn = raw_conn
        self._pool = pool
        self.last_used = time.time()

    def execute(self, sql, params=None):
        translated, returning = translate_sql(sql)
        cur = self._conn.cursor()
        if translated is None:
            return PostgresCursorWrapper(cur)
        if params:
            cur.execute(translated, tuple(params))
        else:
            cur.execute(translated)
        lastrowid = None
        if returning:
            row = cur.fetchone()
            lastrowid = row[0] if row else None
        return PostgresCursorWrapper(cur, lastrowid)

    def cursor(self):
        return self

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        """Return the connection to the pool (rolling back any open transaction)."""
        try:
            self._conn.rollback()
        except Exception:
            self._really_close()
            return
        self.last_used = time.time()
        if self._pool is not None:
            self._pool.release(self)
        else:
            self._really_close()

    def _really_close(self):
        try:
            self._conn.close()
        except Exception:
            pass


def _open_raw_pg(cfg):
    import pg8000.dbapi

    ssl_mode = str(cfg.get("ssl_mode", "require")).lower()
    if ssl_mode in ("disable", "allow", "false", "0"):
        ctx = False
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    conn = pg8000.dbapi.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        database=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        ssl_context=ctx,
        timeout=20,
        application_name="crazinos_daily",
    )
    cur = conn.cursor()
    cur.execute(f"SET search_path TO {DB_SCHEMA}")
    conn.commit()
    return conn


class PgPool:
    """Tiny thread-safe pool so each request does not pay a new TLS handshake."""

    def __init__(self, cfg, size=5, idle_ping_seconds=45):
        self.cfg = cfg
        self.size = size
        self.idle_ping_seconds = idle_ping_seconds
        self._q = queue.LifoQueue(maxsize=size)
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            try:
                wrapper = self._q.get_nowait()
            except queue.Empty:
                return PostgresConnectionWrapper(_open_raw_pg(self.cfg), self)
            if time.time() - wrapper.last_used > self.idle_ping_seconds:
                try:
                    c = wrapper._conn.cursor()
                    c.execute("SELECT 1")
                    c.fetchall()
                    wrapper._conn.rollback()
                except Exception:
                    wrapper._really_close()
                    continue
            return wrapper

    def release(self, wrapper):
        try:
            self._q.put_nowait(wrapper)
        except queue.Full:
            wrapper._really_close()


_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = PgPool(get_pg_config())
        return _pool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_sqlite_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_db_connection():
    if is_postgres():
        return _get_pool().acquire()
    return get_sqlite_connection()


def reset_sequences(conn):
    """Align identity sequences after inserting explicit ids (restore/migration)."""
    if getattr(conn, "engine", "sqlite") != "postgresql":
        return
    for table in ("platforms", "kits", "sales", "reports"):
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{DB_SCHEMA}.{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {DB_SCHEMA}.{table}), 0) + 1, false)"
        )


def get_active_db_status():
    cfg = get_pg_config()
    if cfg:
        try:
            conn = get_db_connection()
            row = conn.execute(
                "SELECT version() AS ver, current_user AS usr, current_schema() AS sch"
            ).fetchone()
            conn.close()
            return {
                "engine": "postgresql",
                "provider": "Supabase PostgreSQL",
                "connected": True,
                "host": cfg["host"],
                "database": cfg["database"],
                "schema": row["sch"],
                "user": row["usr"],
                "ssl_mode": cfg["ssl_mode"],
                "version": (row["ver"] or "").split(" on ")[0],
            }
        except Exception as exc:  # pragma: no cover - network dependent
            return {
                "engine": "postgresql",
                "provider": "Supabase PostgreSQL",
                "connected": False,
                "host": cfg["host"],
                "error": str(exc),
            }
    return {
        "engine": "sqlite",
        "provider": "Local SQLite (offline mode)",
        "connected": True,
        "db_path": DB_PATH,
    }


def _seed(conn):
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name in PREDEFINED_PLATFORMS:
        conn.execute(
            "INSERT OR IGNORE INTO platforms (platform_name, status, created_at) VALUES (?, 'Active', ?)",
            (name, now_iso),
        )
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()


def init_db():
    """Schema for Supabase is managed by migrations; here we only seed reference rows."""
    if is_postgres():
        conn = get_db_connection()
        try:
            _seed(conn)
        finally:
            conn.close()
        return

    conn = get_sqlite_connection()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS kits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_name TEXT NOT NULL,
            sku TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'General',
            cost_price REAL DEFAULT NULL,
            default_selling_price REAL NOT NULL,
            stock_quantity INTEGER DEFAULT NULL,
            image TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS platforms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_name TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kit_id INTEGER NOT NULL REFERENCES kits(id),
            platform_id INTEGER NOT NULL REFERENCES platforms(id),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            selling_price_at_sale REAL NOT NULL CHECK(selling_price_at_sale >= 0),
            cost_price_at_sale REAL DEFAULT NULL,
            total_amount REAL NOT NULL,
            estimated_profit REAL DEFAULT NULL,
            sale_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);
        CREATE INDEX IF NOT EXISTS idx_sales_kit ON sales(kit_id);
        CREATE INDEX IF NOT EXISTS idx_sales_platform ON sales(platform_id);
        """
    )
    _seed(conn)
    conn.close()


def migrate_local_sqlite_to_supabase():
    """One-time copy of any existing local SQLite data into Supabase."""
    if not is_postgres() or not os.path.exists(DB_PATH):
        return None
    sq = get_sqlite_connection()
    data = {t: [dict(r) for r in sq.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()]
            for t in ("platforms", "kits", "sales", "reports")}
    sq.close()

    pg = get_db_connection()
    try:
        plat_map = {}
        for p in data["platforms"]:
            row = pg.execute("SELECT id FROM platforms WHERE platform_name = ?", (p["platform_name"],)).fetchone()
            if row:
                plat_map[p["id"]] = row["id"]
            else:
                cur = pg.execute(
                    "INSERT INTO platforms (platform_name, status, created_at) VALUES (?, ?, ?)",
                    (p["platform_name"], p["status"], p["created_at"]),
                )
                plat_map[p["id"]] = cur.lastrowid
        kit_map = {}
        for k in data["kits"]:
            row = pg.execute("SELECT id FROM kits WHERE sku = ?", (k["sku"],)).fetchone()
            if row:
                kit_map[k["id"]] = row["id"]
                continue
            cur = pg.execute(
                """INSERT INTO kits (kit_name, sku, description, category, cost_price, default_selling_price,
                   stock_quantity, image, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (k["kit_name"], k["sku"], k["description"] or "", k["category"] or "General", k["cost_price"],
                 k["default_selling_price"], k["stock_quantity"], k["image"] or "", k["status"],
                 k["created_at"], k["updated_at"]),
            )
            kit_map[k["id"]] = cur.lastrowid
        moved_sales = 0
        for s in data["sales"]:
            if s["kit_id"] not in kit_map or s["platform_id"] not in plat_map:
                continue
            pg.execute(
                """INSERT INTO sales (kit_id, platform_id, quantity, selling_price_at_sale, cost_price_at_sale,
                   total_amount, estimated_profit, sale_date, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kit_map[s["kit_id"]], plat_map[s["platform_id"]], s["quantity"], s["selling_price_at_sale"],
                 s["cost_price_at_sale"], s["total_amount"], s["estimated_profit"], s["sale_date"],
                 s["created_at"], s["updated_at"]),
            )
            moved_sales += 1
        pg.commit()
        return {"kits": len(kit_map), "platforms": len(plat_map), "sales": moved_sales}
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
