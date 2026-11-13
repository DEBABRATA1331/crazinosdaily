import csv
import io
import json
import os
from datetime import datetime, timedelta
from functools import wraps

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from flask import (
    Flask,
    jsonify,
     make_response,
    render_template,
    request,
    send_file,
    session,
)
from fpdf import FPDF
from werkzeug.security import check_password_hash, generate_password_hash

from database import (
    get_active_db_status,
    get_db_connection,
    init_db,
    is_postgres,
    migrate_local_sqlite_to_supabase,
    reset_sequences,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kit-sales-secret-key-2026-pwa")

# Initialize database on startup
init_db()


def get_settings_dict(conn=None):
    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    if close_after:
        conn.close()
    return settings


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        settings = get_settings_dict()
        require_auth = settings.get("require_auth", "0") == "1"
        is_locked = session.get("is_locked", False)
        if (require_auth and not session.get("admin_logged_in")) or is_locked:
            return jsonify({"error": "Authentication required", "locked": True}), 401
        return f(*args, **kwargs)

    return decorated_function


def format_number(val):
    if val is None:
        return "0"
    if float(val).is_integer():
        return f"{int(val):,}"
    return f"{float(val):,.2f}"


def parse_date_str(date_str):
    """Extract YYYY-MM-DD from an ISO datetime or date string."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    return str(date_str).strip()[:10]


def normalize_sale_datetime(dt_str):
    """Normalize input datetime string to 'YYYY-MM-DD HH:MM:SS'."""
    if not dt_str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cleaned = str(dt_str).strip().replace("T", " ")
    if len(cleaned) == 10:
        cleaned += " " + datetime.now().strftime("%H:%M:%S")
    elif len(cleaned) == 16:
        cleaned += ":00"
    return cleaned[:19]


def format_pretty_date(ymd_str):
    try:
        dt = datetime.strptime(ymd_str[:10], "%Y-%m-%d")
        return dt.strftime("%d %B %Y").lstrip("0")
    except Exception:
        return ymd_str


# ==========================================
# FRONTEND & PWA ROUTES
# ==========================================


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/manifest.json")
def manifest():
    return send_file(
        os.path.join(app.root_path, "static", "manifest.json"),
        mimetype="application/manifest+json",
    )


@app.route("/sw.js")
def service_worker():
    response = make_response(
        send_file(os.path.join(app.root_path, "static", "sw.js"))
    )
    response.headers["Content-Type"] = "application/javascript"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


# ==========================================
# AUTHENTICATION API
# ==========================================


@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    settings = get_settings_dict()
    require_auth = settings.get("require_auth", "0") == "1"
    is_locked = session.get("is_locked", False)
    logged_in = session.get("admin_logged_in", not require_auth) and not is_locked
    return jsonify(
        {
            "authenticated": bool(logged_in),
            "require_auth": require_auth,
            "username": settings.get("admin_username", "admin"),
        }
    )


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    password = str(data.get("password", "")).strip()
    settings = get_settings_dict()
    pw_hash = settings.get("admin_password_hash", "")

    if check_password_hash(pw_hash, password):
        session["admin_logged_in"] = True
        session["is_locked"] = False
        return jsonify({"success": True, "message": "Logged in successfully"})
    return jsonify({"error": "Invalid administrator password"}), 401


@app.route("/api/auth/lock", methods=["POST"])
def auth_lock():
    session["is_locked"] = True
    session["admin_logged_in"] = False
    return jsonify({"success": True, "locked": True})


@app.route("/api/auth/change-password", methods=["POST"])
@admin_required
def auth_change_password():
    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password", "")).strip()
    new_password = str(data.get("new_password", "")).strip()

    if len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters"}), 400

    conn = get_db_connection()
    settings = get_settings_dict(conn)
    pw_hash = settings.get("admin_password_hash", "")

    if current_password and not check_password_hash(pw_hash, current_password):
        conn.close()
        return jsonify({"error": "Current password is incorrect"}), 400

    new_hash = generate_password_hash(new_password)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
        (new_hash,),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Administrator password updated"})


# ==========================================
# INITIAL BOOTSTRAP STATE API
# ==========================================


@app.route("/api/bootstrap", methods=["GET"])
@admin_required
def bootstrap():
    conn = get_db_connection()
    settings = get_settings_dict(conn)
    public_settings = {
        k: v for k, v in settings.items() if k != "admin_password_hash"
    }

    platforms = [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.*,
                   COALESCE(SUM(s.quantity), 0) AS total_kits_sold,
                   COALESCE(SUM(s.total_amount), 0) AS total_revenue,
                   COUNT(s.id) AS total_orders
            FROM platforms p
            LEFT JOIN sales s ON s.platform_id = p.id
            GROUP BY p.id
            ORDER BY p.id ASC
            """
        ).fetchall()
    ]

    kits = [
        dict(row)
        for row in conn.execute(
            """
            SELECT k.*,
                   COALESCE(SUM(s.quantity), 0) AS total_quantity_sold,
                   COALESCE(SUM(s.total_amount), 0) AS total_revenue,
                   COALESCE(SUM(s.estimated_profit), 0) AS total_profit,
                   COUNT(s.id) AS total_orders
            FROM kits k
            LEFT JOIN sales s ON s.kit_id = k.id
            GROUP BY k.id
            ORDER BY k.status ASC, k.kit_name ASC
            """
        ).fetchall()
    ]

    conn.close()
    return jsonify(
        {
            "settings": public_settings,
            "platforms": platforms,
            "kits": kits,
        }
    )


# ==========================================
# DASHBOARD API
# ==========================================


@app.route("/api/dashboard", methods=["GET"])
@admin_required
def get_dashboard():
    target_date = parse_date_str(request.args.get("date"))
    conn = get_db_connection()
    settings = get_settings_dict(conn)

    # Summary statistics for target_date
    summary_row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) AS total_kits_sold,
               COALESCE(SUM(total_amount), 0) AS total_revenue,
               COUNT(id) AS total_orders,
               COALESCE(SUM(estimated_profit), 0) AS total_profit
        FROM sales
        WHERE substr(sale_date, 1, 10) = ?
        """,
        (target_date,),
    ).fetchone()

    # Remaining stock across active kits
    stock_row = conn.execute(
        """
        SELECT COALESCE(SUM(stock_quantity), 0) AS total_remaining_stock,
               COUNT(CASE WHEN stock_quantity IS NOT NULL THEN 1 END) AS tracked_kits_count,
               COUNT(CASE WHEN stock_quantity IS NOT NULL AND stock_quantity <= 5 THEN 1 END) AS low_stock_count
        FROM kits
        WHERE status = 'Active'
        """
    ).fetchone()

    # Today's sales list with kit & platform info
    sales_rows = conn.execute(
        """
        SELECT s.*,
               k.kit_name,
               k.sku,
               k.category,
               p.platform_name
        FROM sales s
        JOIN kits k ON k.id = s.kit_id
        JOIN platforms p ON p.id = s.platform_id
        WHERE substr(s.sale_date, 1, 10) = ?
        ORDER BY s.sale_date DESC, s.id DESC
        """,
        (target_date,),
    ).fetchall()

    sales_list = [dict(r) for r in sales_rows]

    # Group today's sales by platform
    platform_groups_map = {}
    for sale in sales_list:
        pname = sale["platform_name"]
        if pname not in platform_groups_map:
            platform_groups_map[pname] = {
                "platform_id": sale["platform_id"],
                "platform_name": pname,
                "total_quantity": 0,
                "total_amount": 0.0,
                "total_orders": 0,
                "sales": [],
            }
        grp = platform_groups_map[pname]
        grp["total_quantity"] += sale["quantity"]
        grp["total_amount"] += sale["total_amount"]
        grp["total_orders"] += 1
        grp["sales"].append(sale)

    platform_groups = sorted(
        platform_groups_map.values(),
        key=lambda x: (-x["total_amount"], x["platform_name"]),
    )

    conn.close()

    return jsonify(
        {
            "date": target_date,
            "pretty_date": format_pretty_date(target_date),
            "summary": {
                "total_kits_sold": int(summary_row["total_kits_sold"] or 0),
                "total_revenue": float(summary_row["total_revenue"] or 0.0),
                "total_orders": int(summary_row["total_orders"] or 0),
                "total_profit": float(summary_row["total_profit"] or 0.0),
                "total_remaining_stock": int(stock_row["total_remaining_stock"] or 0),
                "tracked_kits_count": int(stock_row["tracked_kits_count"] or 0),
                "low_stock_count": int(stock_row["low_stock_count"] or 0),
                "stock_tracking_enabled": settings.get("stock_tracking_enabled", "1")
                == "1",
                "profit_calculation_enabled": settings.get(
                    "profit_calculation_enabled", "1"
                )
                == "1",
            },
            "platform_groups": platform_groups,
            "sales": sales_list,
        }
    )


# ==========================================
# KIT MANAGEMENT API
# ==========================================


@app.route("/api/kits", methods=["GET"])
@admin_required
def list_kits():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT k.*,
               COALESCE(SUM(s.quantity), 0) AS total_quantity_sold,
               COALESCE(SUM(s.total_amount), 0) AS total_revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS total_profit,
               COUNT(s.id) AS total_orders
        FROM kits k
        LEFT JOIN sales s ON s.kit_id = k.id
        GROUP BY k.id
        ORDER BY k.status ASC, k.kit_name ASC
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/kits/<int:kit_id>", methods=["GET"])
@admin_required
def get_kit_details(kit_id):
    conn = get_db_connection()
    kit_row = conn.execute(
        """
        SELECT k.*,
               COALESCE(SUM(s.quantity), 0) AS total_quantity_sold,
               COALESCE(SUM(s.total_amount), 0) AS total_revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS total_profit,
               COUNT(s.id) AS total_orders
        FROM kits k
        LEFT JOIN sales s ON s.kit_id = k.id
        WHERE k.id = ?
        GROUP BY k.id
        """,
        (kit_id,),
    ).fetchone()

    if not kit_row:
        conn.close()
        return jsonify({"error": "Kit not found"}), 404

    history_rows = conn.execute(
        """
        SELECT s.*, p.platform_name
        FROM sales s
        JOIN platforms p ON p.id = s.platform_id
        WHERE s.kit_id = ?
        ORDER BY s.sale_date DESC, s.id DESC
        LIMIT 200
        """,
        (kit_id,),
    ).fetchall()

    platform_breakdown = conn.execute(
        """
        SELECT p.platform_name,
               COALESCE(SUM(s.quantity), 0) AS quantity_sold,
               COALESCE(SUM(s.total_amount), 0) AS revenue,
               COUNT(s.id) AS orders
        FROM sales s
        JOIN platforms p ON p.id = s.platform_id
        WHERE s.kit_id = ?
        GROUP BY p.id
        ORDER BY revenue DESC
        """,
        (kit_id,),
    ).fetchall()

    conn.close()
    result = dict(kit_row)
    result["sales_history"] = [dict(r) for r in history_rows]
    result["platform_breakdown"] = [dict(r) for r in platform_breakdown]
    return jsonify(result)


@app.route("/api/kits", methods=["POST"])
@admin_required
def create_kit():
    data = request.get_json(silent=True) or {}
    kit_name = str(data.get("kit_name", "")).strip()
    sku = str(data.get("sku", "")).strip().upper()
    description = str(data.get("description", "")).strip()
    category = str(data.get("category", "")).strip() or "General"
    image = str(data.get("image", "")).strip()
    status = str(data.get("status", "Active")).strip()
    if status not in ("Active", "Inactive"):
        status = "Active"

    if not kit_name:
        return jsonify({"error": "Kit Name is required"}), 400
    if not sku:
        return jsonify({"error": "Kit ID / SKU is required"}), 400

    try:
        default_selling_price = float(data.get("default_selling_price"))
        if default_selling_price < 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({"error": "Valid Default Selling Price is required"}), 400

    cost_price = None
    if data.get("cost_price") not in (None, ""):
        try:
            cost_price = float(data.get("cost_price"))
            if cost_price < 0:
                return jsonify({"error": "Cost Price cannot be negative"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid Cost Price"}), 400

    stock_quantity = None
    if data.get("stock_quantity") not in (None, ""):
        try:
            stock_quantity = int(float(data.get("stock_quantity")))
            if stock_quantity < 0:
                return jsonify({"error": "Stock Quantity cannot be negative"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid Stock Quantity"}), 400

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO kits (
                kit_name, sku, description, category, cost_price,
                default_selling_price, stock_quantity, image, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kit_name,
                sku,
                description,
                category,
                cost_price,
                default_selling_price,
                stock_quantity,
                image,
                status,
                now_iso,
                now_iso,
            ),
        )
        kit_id = cursor.lastrowid
        conn.commit()
        kit_row = conn.execute(
            "SELECT * FROM kits WHERE id = ?", (kit_id,)
        ).fetchone()
        conn.close()
        return jsonify({"success": True, "kit": dict(kit_row)}), 201
    except Exception as e:
        conn.close()
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": f"SKU '{sku}' already exists. Please use a unique Kit ID / SKU."}), 400
        return jsonify({"error": str(e)}), 500


@app.route("/api/kits/<int:kit_id>", methods=["PUT"])
@admin_required
def update_kit(kit_id):
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    existing = conn.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Kit not found"}), 404

    kit_name = (
        str(data.get("kit_name", existing["kit_name"])).strip()
        if "kit_name" in data
        else existing["kit_name"]
    )
    sku = (
        str(data.get("sku", existing["sku"])).strip().upper()
        if "sku" in data
        else existing["sku"]
    )
    description = (
        str(data.get("description", existing["description"])).strip()
        if "description" in data
        else existing["description"]
    )
    category = (
        str(data.get("category", existing["category"])).strip() or "General"
        if "category" in data
        else existing["category"]
    )
    image = (
        str(data.get("image", existing["image"])).strip()
        if "image" in data
        else existing["image"]
    )
    status = (
        str(data.get("status", existing["status"])).strip()
        if "status" in data
        else existing["status"]
    )
    if status not in ("Active", "Inactive"):
        status = existing["status"]

    if not kit_name:
        conn.close()
        return jsonify({"error": "Kit Name cannot be empty"}), 400
    if not sku:
        conn.close()
        return jsonify({"error": "Kit ID / SKU cannot be empty"}), 400

    if "default_selling_price" in data:
        try:
            default_selling_price = float(data.get("default_selling_price"))
            if default_selling_price < 0:
                raise ValueError()
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "Valid Default Selling Price is required"}), 400
    else:
        default_selling_price = existing["default_selling_price"]

    if "cost_price" in data:
        if data.get("cost_price") in (None, ""):
            cost_price = None
        else:
            try:
                cost_price = float(data.get("cost_price"))
                if cost_price < 0:
                    conn.close()
                    return jsonify({"error": "Cost Price cannot be negative"}), 400
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"error": "Invalid Cost Price"}), 400
    else:
        cost_price = existing["cost_price"]

    if "stock_quantity" in data:
        if data.get("stock_quantity") in (None, ""):
            stock_quantity = None
        else:
            try:
                stock_quantity = int(float(data.get("stock_quantity")))
                if stock_quantity < 0:
                    conn.close()
                    return jsonify({"error": "Stock Quantity cannot be negative"}), 400
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"error": "Invalid Stock Quantity"}), 400
    else:
        stock_quantity = existing["stock_quantity"]

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # Note: Updating kit default_selling_price or cost_price ONLY affects the kit record.
        # Historical rows in `sales` retain their original `selling_price_at_sale` and `cost_price_at_sale`.
        conn.execute(
            """
            UPDATE kits
            SET kit_name = ?,
                sku = ?,
                description = ?,
                category = ?,
                cost_price = ?,
                default_selling_price = ?,
                stock_quantity = ?,
                image = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                kit_name,
                sku,
                description,
                category,
                cost_price,
                default_selling_price,
                stock_quantity,
                image,
                status,
                now_iso,
                kit_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
        conn.close()
        return jsonify({"success": True, "kit": dict(updated)})
    except Exception as e:
        conn.close()
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": f"SKU '{sku}' is already in use by another kit."}), 400
        return jsonify({"error": str(e)}), 500


# ==========================================
# PLATFORMS API
# ==========================================


@app.route("/api/platforms", methods=["GET"])
@admin_required
def list_platforms():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT p.*,
               COALESCE(SUM(s.quantity), 0) AS total_kits_sold,
               COALESCE(SUM(s.total_amount), 0) AS total_revenue,
               COUNT(s.id) AS total_orders
        FROM platforms p
        LEFT JOIN sales s ON s.platform_id = p.id
        GROUP BY p.id
        ORDER BY p.id ASC
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/platforms", methods=["POST"])
@admin_required
def create_platform():
    data = request.get_json(silent=True) or {}
    platform_name = str(data.get("platform_name", "")).strip()
    if not platform_name:
        return jsonify({"error": "Platform name is required"}), 400

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO platforms (platform_name, status, created_at)
            VALUES (?, 'Active', ?)
            """,
            (platform_name, now_iso),
        )
        pid = cursor.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM platforms WHERE id = ?", (pid,)).fetchone()
        conn.close()
        return jsonify({"success": True, "platform": dict(row)}), 201
    except Exception as e:
        conn.close()
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": f"Platform '{platform_name}' already exists"}), 400
        return jsonify({"error": str(e)}), 500


@app.route("/api/platforms/<int:platform_id>", methods=["PUT"])
@admin_required
def update_platform(platform_id):
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    existing = conn.execute(
        "SELECT * FROM platforms WHERE id = ?", (platform_id,)
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Platform not found"}), 404

    platform_name = (
        str(data.get("platform_name", existing["platform_name"])).strip()
        if "platform_name" in data
        else existing["platform_name"]
    )
    status = (
        str(data.get("status", existing["status"])).strip()
        if "status" in data
        else existing["status"]
    )
    if status not in ("Active", "Inactive"):
        status = existing["status"]

    if not platform_name:
        conn.close()
        return jsonify({"error": "Platform name cannot be empty"}), 400

    try:
        conn.execute(
            "UPDATE platforms SET platform_name = ?, status = ? WHERE id = ?",
            (platform_name, status, platform_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM platforms WHERE id = ?", (platform_id,)
        ).fetchone()
        conn.close()
        return jsonify({"success": True, "platform": dict(updated)})
    except Exception as e:
        conn.close()
        if "UNIQUE constraint failed" in str(e):
            return jsonify({"error": f"Platform '{platform_name}' already exists"}), 400
        return jsonify({"error": str(e)}), 500


# ==========================================
# SALES ENTRY & MANAGEMENT API
# ==========================================


@app.route("/api/sales", methods=["GET"])
@admin_required
def list_sales():
    limit = int(request.args.get("limit", 100))
    date_filter = request.args.get("date")
    kit_id = request.args.get("kit_id")
    platform_id = request.args.get("platform_id")

    query = """
        SELECT s.*,
               k.kit_name,
               k.sku,
               p.platform_name
        FROM sales s
        JOIN kits k ON k.id = s.kit_id
        JOIN platforms p ON p.id = s.platform_id
        WHERE 1=1
    """
    params = []
    if date_filter:
        query += " AND substr(s.sale_date, 1, 10) = ?"
        params.append(parse_date_str(date_filter))
    if kit_id:
        query += " AND s.kit_id = ?"
        params.append(int(kit_id))
    if platform_id:
        query += " AND s.platform_id = ?"
        params.append(int(platform_id))

    query += " ORDER BY s.sale_date DESC, s.id DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales", methods=["POST"])
@admin_required
def create_sale():
    data = request.get_json(silent=True) or {}

    try:
        kit_id = int(data.get("kit_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please select a valid Kit"}), 400

    try:
        platform_id = int(data.get("platform_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please select a valid Selling Platform"}), 400

    try:
        quantity = int(float(data.get("quantity", 0)))
        if quantity <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity Sold must be a positive whole number"}), 400

    conn = get_db_connection()
    settings = get_settings_dict(conn)
    stock_tracking = settings.get("stock_tracking_enabled", "1") == "1"

    kit = conn.execute("SELECT * FROM kits WHERE id = ?", (kit_id,)).fetchone()
    if not kit:
        conn.close()
        return jsonify({"error": "Selected Kit does not exist"}), 404

    platform = conn.execute(
        "SELECT * FROM platforms WHERE id = ?", (platform_id,)
    ).fetchone()
    if not platform:
        conn.close()
        return jsonify({"error": "Selected Platform does not exist"}), 404

    # Selling price defaults to kit's default_selling_price if not specified
    if data.get("selling_price") in (None, ""):
        selling_price_at_sale = float(kit["default_selling_price"])
    else:
        try:
            selling_price_at_sale = float(data.get("selling_price"))
            if selling_price_at_sale < 0:
                raise ValueError()
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "Selling Price must be a valid non-negative amount"}), 400

    # Check and update stock if stock tracking is enabled and kit tracks stock
    if stock_tracking and kit["stock_quantity"] is not None:
        current_stock = int(kit["stock_quantity"])
        if current_stock < quantity and not data.get("allow_negative_stock"):
            conn.close()
            return (
                jsonify(
                    {
                        "error": f"Insufficient stock for '{kit['kit_name']}'. Available: {current_stock}, Requested: {quantity}."
                    }
                ),
                400,
            )

    cost_price_at_sale = (
        float(kit["cost_price"]) if kit["cost_price"] is not None else None
    )
    total_amount = round(quantity * selling_price_at_sale, 2)
    estimated_profit = (
        round((selling_price_at_sale - cost_price_at_sale) * quantity, 2)
        if cost_price_at_sale is not None
        else None
    )

    sale_date = normalize_sale_datetime(data.get("sale_date"))
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor = conn.execute(
            """
            INSERT INTO sales (
                kit_id, platform_id, quantity, selling_price_at_sale,
                cost_price_at_sale, total_amount, estimated_profit,
                sale_date, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kit_id,
                platform_id,
                quantity,
                selling_price_at_sale,
                cost_price_at_sale,
                total_amount,
                estimated_profit,
                sale_date,
                now_iso,
                now_iso,
            ),
        )
        sale_id = cursor.lastrowid

        if stock_tracking and kit["stock_quantity"] is not None:
            new_stock = max(0, int(kit["stock_quantity"]) - quantity)
            conn.execute(
                "UPDATE kits SET stock_quantity = ?, updated_at = ? WHERE id = ?",
                (new_stock, now_iso, kit_id),
            )

        conn.commit()
        sale_row = conn.execute(
            """
            SELECT s.*, k.kit_name, k.sku, p.platform_name
            FROM sales s
            JOIN kits k ON k.id = s.kit_id
            JOIN platforms p ON p.id = s.platform_id
            WHERE s.id = ?
            """,
            (sale_id,),
        ).fetchone()
        conn.close()
        return jsonify({"success": True, "sale": dict(sale_row)}), 201
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/api/sales/<int:sale_id>", methods=["PUT"])
@admin_required
def update_sale(sale_id):
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    settings = get_settings_dict(conn)
    stock_tracking = settings.get("stock_tracking_enabled", "1") == "1"

    existing = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Sale record not found"}), 404

    old_kit_id = existing["kit_id"]
    old_qty = int(existing["quantity"])

    try:
        new_kit_id = int(data.get("kit_id", old_kit_id))
        new_platform_id = int(data.get("platform_id", existing["platform_id"]))
        new_qty = int(float(data.get("quantity", old_qty)))
        new_price = float(
            data.get("selling_price", existing["selling_price_at_sale"])
        )
        if new_qty <= 0 or new_price < 0:
            raise ValueError()
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"error": "Invalid sale parameters"}), 400

    new_kit = conn.execute("SELECT * FROM kits WHERE id = ?", (new_kit_id,)).fetchone()
    if not new_kit:
        conn.close()
        return jsonify({"error": "Selected Kit not found"}), 404

    # Keep original cost_price_at_sale unless the kit itself was changed
    if new_kit_id == old_kit_id:
        cost_price_at_sale = existing["cost_price_at_sale"]
    else:
        cost_price_at_sale = (
            float(new_kit["cost_price"]) if new_kit["cost_price"] is not None else None
        )

    total_amount = round(new_qty * new_price, 2)
    estimated_profit = (
        round((new_price - float(cost_price_at_sale)) * new_qty, 2)
        if cost_price_at_sale is not None
        else None
    )
    sale_date = normalize_sale_datetime(data.get("sale_date", existing["sale_date"]))
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # Adjust stock if enabled
        if stock_tracking:
            if old_kit_id == new_kit_id:
                delta = new_qty - old_qty
                if new_kit["stock_quantity"] is not None:
                    avail = int(new_kit["stock_quantity"])
                    if avail < delta:
                        conn.close()
                        return (
                            jsonify(
                                {
                                    "error": f"Insufficient stock for '{new_kit['kit_name']}'. Available: {avail}, Additional needed: {delta}."
                                }
                            ),
                            400,
                        )
                    conn.execute(
                        "UPDATE kits SET stock_quantity = ?, updated_at = ? WHERE id = ?",
                        (avail - delta, now_iso, new_kit_id),
                    )
            else:
                old_kit = conn.execute(
                    "SELECT * FROM kits WHERE id = ?", (old_kit_id,)
                ).fetchone()
                if old_kit and old_kit["stock_quantity"] is not None:
                    conn.execute(
                        "UPDATE kits SET stock_quantity = stock_quantity + ?, updated_at = ? WHERE id = ?",
                        (old_qty, now_iso, old_kit_id),
                    )
                if new_kit["stock_quantity"] is not None:
                    avail = int(new_kit["stock_quantity"])
                    if avail < new_qty:
                        conn.close()
                        return (
                            jsonify(
                                {
                                    "error": f"Insufficient stock for '{new_kit['kit_name']}'. Available: {avail}, Requested: {new_qty}."
                                }
                            ),
                            400,
                        )
                    conn.execute(
                        "UPDATE kits SET stock_quantity = ?, updated_at = ? WHERE id = ?",
                        (avail - new_qty, now_iso, new_kit_id),
                    )

        conn.execute(
            """
            UPDATE sales
            SET kit_id = ?,
                platform_id = ?,
                quantity = ?,
                selling_price_at_sale = ?,
                cost_price_at_sale = ?,
                total_amount = ?,
                estimated_profit = ?,
                sale_date = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_kit_id,
                new_platform_id,
                new_qty,
                new_price,
                cost_price_at_sale,
                total_amount,
                estimated_profit,
                sale_date,
                now_iso,
                sale_id,
            ),
        )
        conn.commit()
        updated_row = conn.execute(
            """
            SELECT s.*, k.kit_name, k.sku, p.platform_name
            FROM sales s
            JOIN kits k ON k.id = s.kit_id
            JOIN platforms p ON p.id = s.platform_id
            WHERE s.id = ?
            """,
            (sale_id,),
        ).fetchone()
        conn.close()
        return jsonify({"success": True, "sale": dict(updated_row)})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/api/sales/<int:sale_id>", methods=["DELETE"])
@admin_required
def delete_sale(sale_id):
    conn = get_db_connection()
    settings = get_settings_dict(conn)
    stock_tracking = settings.get("stock_tracking_enabled", "1") == "1"

    existing = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Sale record not found"}), 404

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if stock_tracking:
            kit = conn.execute(
                "SELECT * FROM kits WHERE id = ?", (existing["kit_id"],)
            ).fetchone()
            if kit and kit["stock_quantity"] is not None:
                conn.execute(
                    "UPDATE kits SET stock_quantity = stock_quantity + ?, updated_at = ? WHERE id = ?",
                    (int(existing["quantity"]), now_iso, existing["kit_id"]),
                )

        conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "deleted_id": sale_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


# ==========================================
# REPORTS & ANALYTICS ENGINE
# ==========================================


def compute_period_Bounds(report_type, args):
    today = datetime.now()
    if report_type == "daily":
        d = parse_date_str(args.get("date") or today.strftime("%Y-%m-%d"))
        return d, d, f"Daily Report ({format_pretty_date(d)})"

    elif report_type == "weekly":
        week_str = args.get("week")  # e.g. "2026-W46" or start_date
        start_arg = args.get("start_date")
        if week_str and "-W" in week_str:
            try:
                yr, wk = week_str.split("-W")
                dt_start = datetime.fromisocalendar(int(yr), int(wk), 1)
                dt_end = dt_start + timedelta(days=6)
                s = dt_start.strftime("%Y-%m-%d")
                e = dt_end.strftime("%Y-%m-%d")
                return s, e, f"Weekly Report ({s} to {e})"
            except Exception:
                pass
        if start_arg:
            try:
                dt_start = datetime.strptime(parse_date_str(start_arg), "%Y-%m-%d")
                dt_end = dt_start + timedelta(days=6)
                s = dt_start.strftime("%Y-%m-%d")
                e = dt_end.strftime("%Y-%m-%d")
                return s, e, f"Weekly Report ({s} to {e})"
            except Exception:
                pass
        # Default to current week (Monday to Sunday)
        dt_start = today - timedelta(days=today.weekday())
        dt_end = dt_start + timedelta(days=6)
        s = dt_start.strftime("%Y-%m-%d")
        e = dt_end.strftime("%Y-%m-%d")
        return s, e, f"Weekly Report ({s} to {e})"

    elif report_type == "monthly":
        month_str = args.get("month") or today.strftime("%Y-%m")  # YYYY-MM
        try:
            yr, mo = [int(x) for x in month_str[:7].split("-")]
        except Exception:
            yr, mo = today.year, today.month
        dt_start = datetime(yr, mo, 1)
        if mo == 12:
            dt_end = datetime(yr + 1, 1, 1) - timedelta(days=1)
        else:
            dt_end = datetime(yr, mo + 1, 1) - timedelta(days=1)
        s = dt_start.strftime("%Y-%m-%d")
        e = dt_end.strftime("%Y-%m-%d")
        label = dt_start.strftime("%B %Y")
        return s, e, f"Monthly Report ({label})"

    elif report_type == "annual":
        try:
            yr = int(args.get("year") or today.year)
        except Exception:
            yr = today.year
        s = f"{yr:04d}-01-01"
        e = f"{yr:04d}-12-31"
        return s, e, f"Annual Report ({yr})"

    else:
        s = parse_date_str(args.get("start_date") or today.strftime("%Y-%m-%d"))
        e = parse_date_str(args.get("end_date") or s)
        if s > e:
            s, e = e, s
        return s, e, f"Custom Report ({s} to {e})"


def build_eod_shareable_text(
    date_label,
    summary,
    platform_breakdown,
    kit_breakdown,
    currency_symbol="₹",
    profit_enabled=True,
    header_title="CRAZINOS DAILY - SALES REPORT",
):
    lines = [
        header_title,
        "----------------------------------------",
        f"Date: {date_label}",
        "",
        "OVERALL PERFORMANCE",
        f"  Total Kits Sold   : {format_number(summary['total_kits_sold'])}",
        f"  Total Orders      : {format_number(summary['total_orders'])}",
        f"  Total Revenue     : {currency_symbol}{format_number(summary['total_revenue'])}",
    ]
    if profit_enabled:
        lines.append(
            f"  Estimated Profit  : {currency_symbol}{format_number(summary['total_profit'])}"
        )

    lines.append("")
    lines.append("PLATFORM-WISE SALES")
    if platform_breakdown:
        for p in platform_breakdown:
            unit_word = "Kit" if p["quantity_sold"] == 1 else "Kits"
            lines.append(
                f"  - {p['platform_name']}: {format_number(p['quantity_sold'])} {unit_word} ({currency_symbol}{format_number(p['revenue'])})"
            )
    else:
        lines.append("  - No sales recorded")

    lines.append("")
    lines.append("KIT-WISE SALES")
    if kit_breakdown:
        for k in kit_breakdown:
            unit_word = "Unit" if k["quantity_sold"] == 1 else "Units"
            lines.append(
                f"  - {k['kit_name']}: {format_number(k['quantity_sold'])} {unit_word}"
            )
    else:
        lines.append("  - No kits sold")

    lines.append("")
    lines.append("TOP PERFORMING PLATFORM")
    if platform_breakdown:
        lines.append(f"  {platform_breakdown[0]['platform_name']}")
    else:
        lines.append("  N/A")

    lines.extend(
        [
            "",
            "----------------------------------------",
            "Generated automatically by Crazinos Daily.",
        ]
    )
    return "\n".join(lines)


def query_report_data(conn, report_type, start_date, end_date, kit_id=None, platform_id=None, label=""):
    settings = get_settings_dict(conn)
    currency_symbol = settings.get("currency_symbol", "₹")
    profit_enabled = settings.get("profit_calculation_enabled", "1") == "1"

    where_clauses = ["substr(s.sale_date, 1, 10) >= ?", "substr(s.sale_date, 1, 10) <= ?"]
    params = [start_date, end_date]

    if kit_id:
        where_clauses.append("s.kit_id = ?")
        params.append(int(kit_id))
    if platform_id:
        where_clauses.append("s.platform_id = ?")
        params.append(int(platform_id))

    where_sql = " AND ".join(where_clauses)

    # 1. Overall summary
    summary_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(s.quantity), 0) AS total_kits_sold,
               COUNT(s.id) AS total_orders,
               COALESCE(SUM(s.total_amount), 0) AS total_revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS total_profit
        FROM sales s
        WHERE {where_sql}
        """,
        params,
    ).fetchone()

    total_kits_sold = int(summary_row["total_kits_sold"] or 0)
    total_orders = int(summary_row["total_orders"] or 0)
    total_revenue = float(summary_row["total_revenue"] or 0.0)
    total_profit = float(summary_row["total_profit"] or 0.0)

    # Number of days in period
    try:
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        num_days = max(1, (d2 - d1).days + 1)
    except Exception:
        num_days = 1

    avg_daily_revenue = round(total_revenue / num_days, 2)
    avg_daily_kits = round(total_kits_sold / num_days, 2)

    # 2. Platform-wise breakdown
    plat_rows = conn.execute(
        f"""
        SELECT p.id AS platform_id,
               p.platform_name,
               COALESCE(SUM(s.quantity), 0) AS quantity_sold,
               COUNT(s.id) AS orders,
               COALESCE(SUM(s.total_amount), 0) AS revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS profit
        FROM sales s
        JOIN platforms p ON p.id = s.platform_id
        WHERE {where_sql}
        GROUP BY p.id
        ORDER BY revenue DESC, quantity_sold DESC
        """,
        params,
    ).fetchall()

    platform_breakdown = []
    for r in plat_rows:
        rev = float(r["revenue"] or 0.0)
        share = round((rev / total_revenue) * 100, 1) if total_revenue > 0 else 0.0
        platform_breakdown.append(
            {
                "platform_id": r["platform_id"],
                "platform_name": r["platform_name"],
                "quantity_sold": int(r["quantity_sold"] or 0),
                "orders": int(r["orders"] or 0),
                "revenue": rev,
                "profit": float(r["profit"] or 0.0),
                "share_percent": share,
            }
        )

    # 3. Kit-wise breakdown
    kit_rows = conn.execute(
        f"""
        SELECT k.id AS kit_id,
               k.kit_name,
               k.sku,
               k.category,
               COALESCE(SUM(s.quantity), 0) AS quantity_sold,
               COUNT(s.id) AS orders,
               COALESCE(SUM(s.total_amount), 0) AS revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS profit
        FROM sales s
        JOIN kits k ON k.id = s.kit_id
        WHERE {where_sql}
        GROUP BY k.id
        ORDER BY quantity_sold DESC, revenue DESC
        """,
        params,
    ).fetchall()

    kit_breakdown = []
    for r in kit_rows:
        rev = float(r["revenue"] or 0.0)
        share = round((rev / total_revenue) * 100, 1) if total_revenue > 0 else 0.0
        kit_breakdown.append(
            {
                "kit_id": r["kit_id"],
                "kit_name": r["kit_name"],
                "sku": r["sku"],
                "category": r["category"],
                "quantity_sold": int(r["quantity_sold"] or 0),
                "orders": int(r["orders"] or 0),
                "revenue": rev,
                "profit": float(r["profit"] or 0.0),
                "share_percent": share,
            }
        )

    best_selling_kit = kit_breakdown[0] if kit_breakdown else None
    best_performing_platform = platform_breakdown[0] if platform_breakdown else None
    top_5_kits = kit_breakdown[:5]

    # 4. Day-wise breakdown (for weekly, monthly, or custom range)
    day_rows = conn.execute(
        f"""
        SELECT substr(s.sale_date, 1, 10) AS day_date,
               COALESCE(SUM(s.quantity), 0) AS quantity_sold,
               COUNT(s.id) AS orders,
               COALESCE(SUM(s.total_amount), 0) AS revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS profit
        FROM sales s
        WHERE {where_sql}
        GROUP BY day_date
        ORDER BY day_date ASC
        """,
        params,
    ).fetchall()
    day_map = {r["day_date"]: dict(r) for r in day_rows}

    daily_trend = []
    if num_days <= 62:
        try:
            cur = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            while cur <= end_dt:
                ds = cur.strftime("%Y-%m-%d")
                row_d = day_map.get(ds, {})
                daily_trend.append(
                    {
                        "date": ds,
                        "label": cur.strftime("%d %b"),
                        "weekday": cur.strftime("%a"),
                        "quantity_sold": int(row_d.get("quantity_sold", 0) or 0),
                        "orders": int(row_d.get("orders", 0) or 0),
                        "revenue": float(row_d.get("revenue", 0.0) or 0.0),
                        "profit": float(row_d.get("profit", 0.0) or 0.0),
                    }
                )
                cur += timedelta(days=1)
        except Exception:
            pass
    else:
        for r in day_rows:
            daily_trend.append(
                {
                    "date": r["day_date"],
                    "label": r["day_date"],
                    "weekday": "",
                    "quantity_sold": int(r["quantity_sold"] or 0),
                    "orders": int(r["orders"] or 0),
                    "revenue": float(r["revenue"] or 0.0),
                    "profit": float(r["profit"] or 0.0),
                }
            )

    # 5. Monthly breakdown (especially for Annual reports)
    month_rows = conn.execute(
        f"""
        SELECT substr(s.sale_date, 1, 7) AS ym,
               COALESCE(SUM(s.quantity), 0) AS quantity_sold,
               COUNT(s.id) AS orders,
               COALESCE(SUM(s.total_amount), 0) AS revenue,
               COALESCE(SUM(s.estimated_profit), 0) AS profit
        FROM sales s
        WHERE {where_sql}
        GROUP BY ym
        ORDER BY ym ASC
        """,
        params,
    ).fetchall()
    month_map = {r["ym"]: dict(r) for r in month_rows}

    monthly_breakdown = []
    if report_type == "annual":
        yr_prefix = start_date[:4]
        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        for idx in range(1, 13):
            ym = f"{yr_prefix}-{idx:02d}"
            m_row = month_map.get(ym, {})
            monthly_breakdown.append(
                {
                    "month": ym,
                    "month_name": month_names[idx - 1],
                    "quantity_sold": int(m_row.get("quantity_sold", 0) or 0),
                    "orders": int(m_row.get("orders", 0) or 0),
                    "revenue": float(m_row.get("revenue", 0.0) or 0.0),
                    "profit": float(m_row.get("profit", 0.0) or 0.0),
                }
            )
    else:
        for r in month_rows:
            monthly_breakdown.append(
                {
                    "month": r["ym"],
                    "month_name": r["ym"],
                    "quantity_sold": int(r["quantity_sold"] or 0),
                    "orders": int(r["orders"] or 0),
                    "revenue": float(r["revenue"] or 0.0),
                    "profit": float(r["profit"] or 0.0),
                }
            )

    # 6. Detailed sales transactions for the period
    tx_rows = conn.execute(
        f"""
        SELECT s.*, k.kit_name, k.sku, p.platform_name
        FROM sales s
        JOIN kits k ON k.id = s.kit_id
        JOIN platforms p ON p.id = s.platform_id
        WHERE {where_sql}
        ORDER BY s.sale_date DESC, s.id DESC
        LIMIT 500
        """,
        params,
    ).fetchall()
    transactions = [dict(r) for r in tx_rows]

    summary_obj = {
        "total_kits_sold": total_kits_sold,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
        "avg_daily_revenue": avg_daily_revenue,
        "avg_daily_kits": avg_daily_kits,
        "num_days": num_days,
        "best_selling_kit": best_selling_kit,
        "best_performing_platform": best_performing_platform,
    }

    if start_date == end_date:
        date_display = format_pretty_date(start_date)
        header_title = "CRAZINOS DAILY - DAILY SALES REPORT"
    else:
        date_display = f"{format_pretty_date(start_date)} to {format_pretty_date(end_date)}"
        header_title = f"CRAZINOS DAILY - {report_type.upper()} SALES REPORT"

    shareable_text = build_eod_shareable_text(
        date_label=date_display,
        summary=summary_obj,
        platform_breakdown=platform_breakdown,
        kit_breakdown=kit_breakdown,
        currency_symbol=currency_symbol,
        profit_enabled=profit_enabled,
        header_title=header_title,
    )

    return {
        "report_type": report_type,
        "label": label,
        "start_date": start_date,
        "end_date": end_date,
        "pretty_date": date_display,
        "currency_symbol": currency_symbol,
        "profit_calculation_enabled": profit_enabled,
        "summary": summary_obj,
        "platform_breakdown": platform_breakdown,
        "kit_breakdown": kit_breakdown,
        "top_5_kits": top_5_kits,
        "daily_trend": daily_trend,
        "monthly_breakdown": monthly_breakdown,
        "transactions": transactions,
        "shareable_text": shareable_text,
    }


@app.route("/api/reports/analytics", methods=["GET"])
@admin_required
def get_reports_analytics():
    report_type = request.args.get("type", "daily").lower()
    kit_id = request.args.get("kit_id") or None
    platform_id = request.args.get("platform_id") or None
    record_history = request.args.get("record_history", "0") == "1"

    start_date, end_date, label = compute_period_Bounds(report_type, request.args)
    conn = get_db_connection()
    data = query_report_data(
        conn,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        kit_id=kit_id,
        platform_id=platform_id,
        label=label,
    )

    if record_history:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO reports (report_type, start_date, end_date, generated_at)
            VALUES (?, ?, ?, ?)
            """,
            (report_type, start_date, end_date, now_iso),
        )
        conn.commit()

    conn.close()
    return jsonify(data)


@app.route("/api/reports/generate-eod", methods=["POST"])
@admin_required
def generate_eod_report():
    body = request.get_json(silent=True) or {}
    target_date = parse_date_str(body.get("date"))
    report_type = body.get("report_type", "daily")
    start_date = parse_date_str(body.get("start_date") or target_date)
    end_date = parse_date_str(body.get("end_date") or target_date)

    conn = get_db_connection()
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.execute(
        """
        INSERT INTO reports (report_type, start_date, end_date, generated_at)
        VALUES (?, ?, ?, ?)
        """,
        (report_type, start_date, end_date, now_iso),
    )
    report_id = cursor.lastrowid
    conn.commit()

    data = query_report_data(
        conn,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        label=f"Daily Report ({format_pretty_date(start_date)})",
    )
    data["report_id"] = report_id
    data["generated_at"] = now_iso
    conn.close()
    return jsonify(data)


@app.route("/api/reports/history", methods=["GET"])
@admin_required
def get_report_history():
    conn = get_db_connection()
    settings = get_settings_dict(conn)
    currency_symbol = settings.get("currency_symbol", "₹")

    # Fetch saved report history records
    report_rows = conn.execute(
        """
        SELECT * FROM reports
        ORDER BY generated_at DESC, id DESC
        LIMIT 50
        """
    ).fetchall()

    history_items = []
    for r in report_rows:
        agg = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS total_kits_sold,
                   COUNT(id) AS total_orders,
                   COALESCE(SUM(total_amount), 0) AS total_revenue,
                   COALESCE(SUM(estimated_profit), 0) AS total_profit
            FROM sales
            WHERE substr(sale_date, 1, 10) >= ? AND substr(sale_date, 1, 10) <= ?
            """,
            (r["start_date"], r["end_date"]),
        ).fetchone()
        history_items.append(
            {
                "id": r["id"],
                "report_type": r["report_type"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "pretty_period": format_pretty_date(r["start_date"])
                if r["start_date"] == r["end_date"]
                else f"{r['start_date']} to {r['end_date']}",
                "generated_at": r["generated_at"],
                "total_kits_sold": int(agg["total_kits_sold"] or 0),
                "total_orders": int(agg["total_orders"] or 0),
                "total_revenue": float(agg["total_revenue"] or 0.0),
                "total_profit": float(agg["total_profit"] or 0.0),
            }
        )

    # Also fetch dates with recorded sales so user can view any previous active date
    active_dates_rows = conn.execute(
        """
        SELECT substr(sale_date, 1, 10) AS sale_day,
               COALESCE(SUM(quantity), 0) AS total_kits_sold,
               COUNT(id) AS total_orders,
               COALESCE(SUM(total_amount), 0) AS total_revenue,
               COALESCE(SUM(estimated_profit), 0) AS total_profit
        FROM sales
        GROUP BY sale_day
        ORDER BY sale_day DESC
        LIMIT 60
        """
    ).fetchall()

    active_dates = [
        {
            "date": r["sale_day"],
            "pretty_date": format_pretty_date(r["sale_day"]),
            "total_kits_sold": int(r["total_kits_sold"] or 0),
            "total_orders": int(r["total_orders"] or 0),
            "total_revenue": float(r["total_revenue"] or 0.0),
            "total_profit": float(r["total_profit"] or 0.0),
        }
        for r in active_dates_rows
    ]

    conn.close()
    return jsonify(
        {
            "currency_symbol": currency_symbol,
            "generated_reports": history_items,
            "active_sales_dates": active_dates,
        }
    )


@app.route("/api/reports/compare", methods=["GET"])
@admin_required
def compare_periods():
    p1_start = parse_date_str(request.args.get("p1_start"))
    p1_end = parse_date_str(request.args.get("p1_end") or p1_start)
    p2_start = parse_date_str(request.args.get("p2_start"))
    p2_end = parse_date_str(request.args.get("p2_end") or p2_start)

    conn = get_db_connection()
    period_a = query_report_data(
        conn, "custom", p1_start, p1_end, label=f"{p1_start} to {p1_end}"
    )
    period_b = query_report_data(
        conn, "custom", p2_start, p2_end, label=f"{p2_start} to {p2_end}"
    )
    conn.close()

    def calc_delta(a, b):
        diff = round(b - a, 2)
        pct = round(((b - a) / a) * 100, 1) if a > 0 else (100.0 if b > 0 else 0.0)
        return {"diff": diff, "percent": pct}

    sa = period_a["summary"]
    sb = period_b["summary"]

    deltas = {
        "kits_sold": calc_delta(sa["total_kits_sold"], sb["total_kits_sold"]),
        "orders": calc_delta(sa["total_orders"], sb["total_orders"]),
        "revenue": calc_delta(sa["total_revenue"], sb["total_revenue"]),
        "profit": calc_delta(sa["total_profit"], sb["total_profit"]),
    }

    return jsonify(
        {
            "period_a": period_a,
            "period_b": period_b,
            "deltas": deltas,
        }
    )


# ==========================================
# PDF, EXCEL & CSV EXPORT ENDPOINTS
# ==========================================


@app.route("/api/reports/export/pdf", methods=["GET"])
@admin_required
def export_report_pdf():
    report_type = request.args.get("type", "daily").lower()
    kit_id = request.args.get("kit_id") or None
    platform_id = request.args.get("platform_id") or None
    start_date, end_date, label = compute_period_Bounds(report_type, request.args)

    conn = get_db_connection()
    settings = get_settings_dict(conn)
    business_name = settings.get("business_name", "My Kit Store")
    data = query_report_data(
        conn, report_type, start_date, end_date, kit_id, platform_id, label
    )
    conn.close()

    # Record in report history
    conn2 = get_db_connection()
    conn2.execute(
        "INSERT INTO reports (report_type, start_date, end_date, generated_at) VALUES (?, ?, ?, ?)",
        (report_type, start_date, end_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn2.commit()
    conn2.close()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    # Header Banner
    pdf.set_fill_color(21, 101, 192)  # Primary Blue #1565C0
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 14, f"  {business_name.upper()} - {report_type.upper()} SALES REPORT", ln=True, fill=True)

    # Red accent strip
    pdf.set_fill_color(229, 57, 53)  # Primary Red #E53935
    pdf.cell(0, 1.5, "", ln=True, fill=True)

    # Metadata Block
    pdf.set_fill_color(245, 247, 250)  # Light Background #F5F7FA
    pdf.set_text_color(38, 50, 56)     # Dark Text #263238
    pdf.set_font("Helvetica", "", 10)
    pdf.ln(3)
    pdf.cell(95, 7, f"  Report Period: {data['pretty_date']}", fill=True)
    pdf.cell(95, 7, f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}  ", fill=True, align="R", ln=True)
    pdf.cell(95, 7, f"  Date Range: {start_date} to {end_date}", fill=True)
    pdf.cell(95, 7, f"Report Type: {report_type.capitalize()}  ", fill=True, align="R", ln=True)
    pdf.ln(6)

    # Summary Section
    s = data["summary"]
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(21, 101, 192)
    pdf.cell(0, 8, "EXECUTIVE SUMMARY", ln=True)
    pdf.set_draw_color(21, 101, 192)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)

    pdf.set_draw_color(232, 237, 243)
    pdf.set_text_color(38, 50, 56)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(245, 247, 250)
    pdf.cell(47.5, 8, "Total Kits Sold", border=1, fill=True, align="C")
    pdf.cell(47.5, 8, "Total Orders", border=1, fill=True, align="C")
    pdf.cell(47.5, 8, "Total Revenue", border=1, fill=True, align="C")
    right_hdr = "Estimated Profit" if data["profit_calculation_enabled"] else "Avg Daily Revenue"
    pdf.cell(47.5, 8, right_hdr, border=1, fill=True, align="C", ln=True)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(47.5, 10, f"{format_number(s['total_kits_sold'])} Units", border=1, align="C")
    pdf.cell(47.5, 10, format_number(s["total_orders"]), border=1, align="C")
    pdf.set_text_color(229, 57, 53)
    pdf.cell(47.5, 10, f"INR {format_number(s['total_revenue'])}", border=1, align="C")
    pdf.set_text_color(21, 101, 192)
    right_val = (
        f"INR {format_number(s['total_profit'])}"
        if data["profit_calculation_enabled"]
        else f"INR {format_number(s['avg_daily_revenue'])}"
    )
    pdf.cell(47.5, 10, right_val, border=1, align="C", ln=True)
    pdf.ln(6)

    # Platform Breakdown Table
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(21, 101, 192)
    pdf.cell(0, 8, "PLATFORM-WISE PERFORMANCE", ln=True)
    pdf.set_fill_color(21, 101, 192)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(60, 8, "  Platform", border=1, fill=True)
    pdf.cell(30, 8, "Kits Sold", border=1, fill=True, align="R")
    pdf.cell(25, 8, "Orders", border=1, fill=True, align="R")
    pdf.cell(45, 8, "Revenue (INR)", border=1, fill=True, align="R")
    pdf.cell(30, 8, "Share %", border=1, fill=True, align="R", ln=True)

    pdf.set_text_color(38, 50, 56)
    pdf.set_font("Helvetica", "", 9.5)
    if data["platform_breakdown"]:
        for idx, p in enumerate(data["platform_breakdown"]):
            fill_row = idx % 2 == 1
            pdf.set_fill_color(245, 247, 250)
            pdf.cell(60, 7, f"  {str(p['platform_name'])[:28]}", border=1, fill=fill_row)
            pdf.cell(30, 7, format_number(p["quantity_sold"]), border=1, align="R", fill=fill_row)
            pdf.cell(25, 7, format_number(p["orders"]), border=1, align="R", fill=fill_row)
            pdf.cell(45, 7, format_number(p["revenue"]), border=1, align="R", fill=fill_row)
            pdf.cell(30, 7, f"{p['share_percent']}%", border=1, align="R", fill=fill_row, ln=True)
    else:
        pdf.cell(190, 7, "  No platform sales recorded in this period.", border=1, ln=True)
    pdf.ln(6)

    # Kit Breakdown Table
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(21, 101, 192)
    pdf.cell(0, 8, "KIT-WISE SALES BREAKDOWN", ln=True)
    pdf.set_fill_color(21, 101, 192)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(75, 8, "  Kit Name", border=1, fill=True)
    pdf.cell(30, 8, "SKU", border=1, fill=True)
    pdf.cell(30, 8, "Units Sold", border=1, fill=True, align="R")
    pdf.cell(55, 8, "Revenue (INR)", border=1, fill=True, align="R", ln=True)

    pdf.set_text_color(38, 50, 56)
    pdf.set_font("Helvetica", "", 9.5)
    if data["kit_breakdown"]:
        for idx, k in enumerate(data["kit_breakdown"]):
            fill_row = idx % 2 == 1
            pdf.set_fill_color(245, 247, 250)
            pdf.cell(75, 7, f"  {str(k['kit_name'])[:34]}", border=1, fill=fill_row)
            pdf.cell(30, 7, str(k["sku"])[:14], border=1, fill=fill_row)
            pdf.cell(30, 7, format_number(k["quantity_sold"]), border=1, align="R", fill=fill_row)
            pdf.cell(55, 7, format_number(k["revenue"]), border=1, align="R", fill=fill_row, ln=True)
    else:
        pdf.cell(190, 7, "  No kit sales recorded in this period.", border=1, ln=True)
    pdf.ln(6)

    # Transactions Table
    if data["transactions"]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(21, 101, 192)
        pdf.cell(0, 8, "DETAILED SALES TRANSACTIONS", ln=True)
        pdf.set_fill_color(21, 101, 192)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(38, 7, "  Date & Time", border=1, fill=True)
        pdf.cell(57, 7, "Kit Name", border=1, fill=True)
        pdf.cell(35, 7, "Platform", border=1, fill=True)
        pdf.cell(16, 7, "Qty", border=1, fill=True, align="R")
        pdf.cell(21, 7, "Price", border=1, fill=True, align="R")
        pdf.cell(23, 7, "Total", border=1, fill=True, align="R", ln=True)

        pdf.set_text_color(38, 50, 56)
        pdf.set_font("Helvetica", "", 8.5)
        for idx, tx in enumerate(data["transactions"][:100]):
            fill_row = idx % 2 == 1
            pdf.set_fill_color(245, 247, 250)
            pdf.cell(38, 6, f"  {str(tx['sale_date'])[:16]}", border=1, fill=fill_row)
            pdf.cell(57, 6, str(tx["kit_name"])[:28], border=1, fill=fill_row)
            pdf.cell(35, 6, str(tx["platform_name"])[:18], border=1, fill=fill_row)
            pdf.cell(16, 6, str(tx["quantity"]), border=1, align="R", fill=fill_row)
            pdf.cell(21, 6, format_number(tx["selling_price_at_sale"]), border=1, align="R", fill=fill_row)
            pdf.cell(23, 6, format_number(tx["total_amount"]), border=1, align="R", fill=fill_row, ln=True)

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(84, 110, 122)
    pdf.cell(0, 6, f"Generated by {business_name} | Confidential Sales & Inventory Report", align="C")

    pdf_bytes = pdf.output()
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    filename = f"crazinos_daily_{report_type}_{start_date}_to_{end_date}.pdf"
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/reports/export/excel", methods=["GET"])
@admin_required
def export_report_excel():
    report_type = request.args.get("type", "daily").lower()
    kit_id = request.args.get("kit_id") or None
    platform_id = request.args.get("platform_id") or None
    start_date, end_date, label = compute_period_Bounds(report_type, request.args)

    conn = get_db_connection()
    settings = get_settings_dict(conn)
    business_name = settings.get("business_name", "My Kit Store")
    data = query_report_data(
        conn, report_type, start_date, end_date, kit_id, platform_id, label
    )
    conn.close()

    wb = openpyxl.Workbook()
    header_fill = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    bold_font = Font(bold=True)
    thin_border = Border(
        left=Side(style="thin", color="D0D7DE"),
        right=Side(style="thin", color="D0D7DE"),
        top=Side(style="thin", color="D0D7DE"),
        bottom=Side(style="thin", color="D0D7DE"),
    )

    # Sheet 1: Summary & Breakdown
    ws_sum = wb.active
    ws_sum.title = "Report Summary"
    ws_sum.append([f"{business_name} - {label}"])
    ws_sum["A1"].font = Font(size=14, bold=True, color="1565C0")
    ws_sum.append(["Period", f"{start_date} to {end_date}"])
    ws_sum.append(["Generated At", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws_sum.append([])

    s = data["summary"]
    ws_sum.append(["Metric", "Value"])
    for cell in ws_sum[5]:
        cell.fill = header_fill
        cell.font = header_font

    ws_sum.append(["Total Kits Sold", s["total_kits_sold"]])
    ws_sum.append(["Total Orders", s["total_orders"]])
    ws_sum.append(["Total Revenue", s["total_revenue"]])
    if data["profit_calculation_enabled"]:
        ws_sum.append(["Estimated Profit", s["total_profit"]])
    ws_sum.append(["Average Daily Revenue", s["avg_daily_revenue"]])
    ws_sum.append(["Average Daily Kits Sold", s["avg_daily_kits"]])
    ws_sum.append([])

    # Platform table on Summary sheet
    ws_sum.append(["Platform-Wise Breakdown"])
    ws_sum.cell(row=ws_sum.max_row, column=1).font = bold_font
    ws_sum.append(["Platform", "Kits Sold", "Orders", "Revenue", "Estimated Profit", "Share %"])
    for cell in ws_sum[ws_sum.max_row]:
        cell.fill = header_fill
        cell.font = header_font
    for p in data["platform_breakdown"]:
        ws_sum.append([
            p["platform_name"],
            p["quantity_sold"],
            p["orders"],
            p["revenue"],
            p["profit"],
            p["share_percent"],
        ])
    ws_sum.append([])

    # Kit table on Summary sheet
    ws_sum.append(["Kit-Wise Breakdown"])
    ws_sum.cell(row=ws_sum.max_row, column=1).font = bold_font
    ws_sum.append(["Kit Name", "SKU", "Category", "Units Sold", "Orders", "Revenue", "Estimated Profit"])
    for cell in ws_sum[ws_sum.max_row]:
        cell.fill = header_fill
        cell.font = header_font
    for k in data["kit_breakdown"]:
        ws_sum.append([
            k["kit_name"],
            k["sku"],
            k["category"],
            k["quantity_sold"],
            k["orders"],
            k["revenue"],
            k["profit"],
        ])

    # Sheet 2: Detailed Transactions
    ws_tx = wb.create_sheet(title="Sales Transactions")
    headers = [
        "Sale ID",
        "Sale Date & Time",
        "Kit Name",
        "SKU",
        "Platform",
        "Quantity Sold",
        "Selling Price (Unit)",
        "Cost Price (Unit)",
        "Total Sale Amount",
        "Estimated Profit",
    ]
    ws_tx.append(headers)
    for cell in ws_tx[1]:
        cell.fill = header_fill
        cell.font = header_font

    for tx in data["transactions"]:
        ws_tx.append([
            tx["id"],
            tx["sale_date"],
            tx["kit_name"],
            tx["sku"],
            tx["platform_name"],
            tx["quantity"],
            tx["selling_price_at_sale"],
            tx["cost_price_at_sale"] if tx["cost_price_at_sale"] is not None else "",
            tx["total_amount"],
            tx["estimated_profit"] if tx["estimated_profit"] is not None else "",
        ])

    for ws in [ws_sum, ws_tx]:
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=10)
            col_letter = col[0].column_letter
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 42)
            for cell in col:
                cell.border = thin_border

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"kit_sales_{report_type}_{start_date}_to_{end_date}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/reports/export/csv", methods=["GET"])
@admin_required
def export_report_csv():
    report_type = request.args.get("type", "daily").lower()
    kit_id = request.args.get("kit_id") or None
    platform_id = request.args.get("platform_id") or None
    start_date, end_date, label = compute_period_Bounds(report_type, request.args)

    conn = get_db_connection()
    data = query_report_data(
        conn, report_type, start_date, end_date, kit_id, platform_id, label
    )
    conn.close()

    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow([
        "Sale ID",
        "Sale Date",
        "Kit Name",
        "SKU",
        "Platform",
        "Quantity Sold",
        "Selling Price At Sale",
        "Cost Price At Sale",
        "Total Amount",
        "Estimated Profit",
    ])
    for tx in data["transactions"]:
        writer.writerow([
            tx["id"],
            tx["sale_date"],
            tx["kit_name"],
            tx["sku"],
            tx["platform_name"],
            tx["quantity"],
            tx["selling_price_at_sale"],
            tx["cost_price_at_sale"] if tx["cost_price_at_sale"] is not None else "",
            tx["total_amount"],
            tx["estimated_profit"] if tx["estimated_profit"] is not None else "",
        ])

    output = make_response(si.getvalue())
    filename = f"kit_sales_{report_type}_{start_date}_to_{end_date}.csv"
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output


# ==========================================
# SETTINGS, FULL EXPORT, BACKUP & RESTORE
# ==========================================


@app.route("/api/settings", methods=["GET"])
@admin_required
def get_settings():
    settings = get_settings_dict()
    settings.pop("admin_password_hash", None)
    return jsonify(settings)


@app.route("/api/settings", methods=["PUT"])
@admin_required
def update_settings():
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()

    allowed_keys = {
        "business_name",
        "currency",
        "currency_symbol",
        "timezone",
        "stock_tracking_enabled",
        "profit_calculation_enabled",
        "require_auth",
    }

    if "currency" in data and "currency_symbol" not in data:
        curr_str = str(data["currency"]).strip()
        parts = curr_str.split()
        data["currency_symbol"] = parts[-1] if len(parts) > 1 else curr_str

    for k, v in data.items():
        if k in allowed_keys:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (k, str(v)),
            )

    conn.commit()
    updated = get_settings_dict(conn)
    conn.close()
    updated.pop("admin_password_hash", None)
    return jsonify({"success": True, "settings": updated})


@app.route("/api/export/all", methods=["GET"])
@admin_required
def export_all_data():
    fmt = request.args.get("format", "excel").lower()
    conn = get_db_connection()

    kits = [dict(r) for r in conn.execute("SELECT * FROM kits ORDER BY id ASC").fetchall()]
    platforms = [
        dict(r) for r in conn.execute("SELECT * FROM platforms ORDER BY id ASC").fetchall()
    ]
    sales = [
        dict(r)
        for r in conn.execute(
            """
            SELECT s.*, k.kit_name, k.sku, p.platform_name
            FROM sales s
            JOIN kits k ON k.id = s.kit_id
            JOIN platforms p ON p.id = s.platform_id
            ORDER BY s.sale_date DESC, s.id DESC
            """
        ).fetchall()
    ]
    reports = [
        dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
    ]
    conn.close()

    if fmt == "csv":
        si = io.StringIO()
        writer = csv.writer(si)
        writer.writerow([
            "Sale ID",
            "Sale Date",
            "Kit ID",
            "Kit Name",
            "SKU",
            "Platform ID",
            "Platform Name",
            "Quantity",
            "Selling Price At Sale",
            "Cost Price At Sale",
            "Total Amount",
            "Estimated Profit",
        ])
        for s in sales:
            writer.writerow([
                s["id"],
                s["sale_date"],
                s["kit_id"],
                s["kit_name"],
                s["sku"],
                s["platform_id"],
                s["platform_name"],
                s["quantity"],
                s["selling_price_at_sale"],
                s["cost_price_at_sale"] if s["cost_price_at_sale"] is not None else "",
                s["total_amount"],
                s["estimated_profit"] if s["estimated_profit"] is not None else "",
            ])
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=all_kit_sales_export.csv"
        output.headers["Content-type"] = "text/csv; charset=utf-8"
        return output

    # Default: Multi-sheet Excel export of all data
    wb = openpyxl.Workbook()
    header_fill = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    # Sheet 1: All Sales
    ws_sales = wb.active
    ws_sales.title = "All Sales"
    ws_sales.append([
        "ID", "Sale Date", "Kit Name", "SKU", "Platform", "Quantity",
        "Selling Price At Sale", "Cost Price At Sale", "Total Amount", "Estimated Profit",
    ])
    for cell in ws_sales[1]:
        cell.fill = header_fill
        cell.font = header_font
    for s in sales:
        ws_sales.append([
            s["id"], s["sale_date"], s["kit_name"], s["sku"], s["platform_name"],
            s["quantity"], s["selling_price_at_sale"], s["cost_price_at_sale"],
            s["total_amount"], s["estimated_profit"],
        ])

    # Sheet 2: Kits Inventory
    ws_kits = wb.create_sheet("Kits Inventory")
    ws_kits.append([
        "ID", "Kit Name", "SKU", "Category", "Description",
        "Cost Price", "Default Selling Price", "Stock Quantity", "Status", "Created At",
    ])
    for cell in ws_kits[1]:
        cell.fill = header_fill
        cell.font = header_font
    for k in kits:
        ws_kits.append([
            k["id"], k["kit_name"], k["sku"], k["category"], k["description"],
            k["cost_price"], k["default_selling_price"], k["stock_quantity"],
            k["status"], k["created_at"],
        ])

    # Sheet 3: Platforms
    ws_plat = wb.create_sheet("Platforms")
    ws_plat.append(["ID", "Platform Name", "Status", "Created At"])
    for cell in ws_plat[1]:
        cell.fill = header_fill
        cell.font = header_font
    for p in platforms:
        ws_plat.append([p["id"], p["platform_name"], p["status"], p["created_at"]])

    # Sheet 4: Report History
    ws_rep = wb.create_sheet("Report History")
    ws_rep.append(["ID", "Report Type", "Start Date", "End Date", "Generated At"])
    for cell in ws_rep[1]:
        cell.fill = header_fill
        cell.font = header_font
    for r in reports:
        ws_rep.append([r["id"], r["report_type"], r["start_date"], r["end_date"], r["generated_at"]])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"kit_sales_complete_export_{datetime.now().strftime('%Y%m%d')}.xlsx",
    )


@app.route("/api/backup", methods=["GET"])
@admin_required
def backup_database():
    conn = get_db_connection()
    settings = get_settings_dict(conn)
    settings.pop("admin_password_hash", None)

    payload = {
        "backup_version": "1.0",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "settings": settings,
        "platforms": [
            dict(r) for r in conn.execute("SELECT * FROM platforms ORDER BY id ASC").fetchall()
        ],
        "kits": [
            dict(r) for r in conn.execute("SELECT * FROM kits ORDER BY id ASC").fetchall()
        ],
        "sales": [
            dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY id ASC").fetchall()
        ],
        "reports": [
            dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY id ASC").fetchall()
        ],
    }
    conn.close()

    buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"kit_sales_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )


@app.route("/api/restore", methods=["POST"])
@admin_required
def restore_database():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid backup JSON file"}), 400

    if "kits" not in data or "sales" not in data or "platforms" not in data:
        return jsonify({"error": "Backup file is missing required entities (kits, platforms, sales)"}), 400

    conn = get_db_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("DELETE FROM sales;")
        conn.execute("DELETE FROM kits;")
        conn.execute("DELETE FROM platforms;")
        conn.execute("DELETE FROM reports;")

        for p in data.get("platforms", []):
            conn.execute(
                """
                INSERT INTO platforms (id, platform_name, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    p["id"],
                    p["platform_name"],
                    p.get("status", "Active"),
                    p.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )

        for k in data.get("kits", []):
            conn.execute(
                """
                INSERT INTO kits (
                    id, kit_name, sku, description, category, cost_price,
                    default_selling_price, stock_quantity, image, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    k["id"],
                    k["kit_name"],
                    k["sku"],
                    k.get("description", ""),
                    k.get("category", "General"),
                    k.get("cost_price"),
                    k["default_selling_price"],
                    k.get("stock_quantity"),
                    k.get("image", ""),
                    k.get("status", "Active"),
                    k.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    k.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )

        for s in data.get("sales", []):
            conn.execute(
                """
                INSERT INTO sales (
                    id, kit_id, platform_id, quantity, selling_price_at_sale,
                    cost_price_at_sale, total_amount, estimated_profit,
                    sale_date, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s["id"],
                    s["kit_id"],
                    s["platform_id"],
                    s["quantity"],
                    s["selling_price_at_sale"],
                    s.get("cost_price_at_sale"),
                    s["total_amount"],
                    s.get("estimated_profit"),
                    s["sale_date"],
                    s.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    s.get("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )

        for r in data.get("reports", []):
            conn.execute(
                """
                INSERT INTO reports (id, report_type, start_date, end_date, generated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    r["id"],
                    r["report_type"],
                    r["start_date"],
                    r["end_date"],
                    r["generated_at"],
                ),
            )

        for k, v in data.get("settings", {}).items():
            if k != "admin_password_hash":
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )

        conn.execute("PRAGMA foreign_keys = ON;")
        reset_sequences(conn)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Database restored successfully"})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"Failed to restore backup: {str(e)}"}), 500


# ==========================================
# SUPABASE POSTGRESQL STATUS & MIGRATION API
# ==========================================


@app.route("/api/database/status", methods=["GET"])
@admin_required
def api_database_status():
    return jsonify(get_active_db_status())


@app.route("/api/database/migrate-local", methods=["POST"])
@admin_required
def api_database_migrate_local():
    if not is_postgres():
        return jsonify({"error": "DATABASE_URL is not configured for Supabase PostgreSQL."}), 400
    try:
        summary = migrate_local_sqlite_to_supabase()
        if summary is None:
            return jsonify({"success": True, "message": "No local SQLite file found to migrate.", "migration": None})
        return jsonify(
            {
                "success": True,
                "message": "Local SQLite records migrated to Supabase PostgreSQL.",
                "migration": summary,
            }
        )
    except Exception as e:
        return jsonify({"error": f"Migration failed: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

