from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_socketio import SocketIO
import mysql.connector
from datetime import datetime, date, time, timedelta
import csv
import tempfile
from io import StringIO
import os
import functools
import pandas as pd
from io import BytesIO
import sys
import hashlib
import secrets
import bcrypt
from functools import wraps
from flask import flash
import json
import pdfkit
import smtplib
from email.message import EmailMessage
import threading

app = Flask(__name__, template_folder="templates")
app.config['SECRET_KEY'] = 'your-secret-key-here-change-this-in-production'
app.config['PERMANENT_SESSION_LIFETIME'] = 900 
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "clement_package_log_1",
}

# Add these functions after DB_CONFIG in WebApp.py
def hash_password(password):
    """Hash a password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt)

def verify_password(password, hashed_password):
    """Verify a password against its hash"""
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password)

def generate_temp_password(length=12):
    """Generate a temporary password"""
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*'
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def log_audit(user_id, action, description, request):
    """Log user actions for auditing"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs (user_id, action, description, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, action, description, request.remote_addr, request.user_agent))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"AUDIT LOG ERROR: {e}")

DEFAULT_PERMISSIONS = {
    'OA': {'can_checkin': True, 'can_checkout': True, 'can_view_other_halls': False, 'can_manage_users': False, 'can_manage_halls': False, 'can_manage_shifts': False},
    'AHD': {'can_checkin': True, 'can_checkout': True, 'can_view_other_halls': True, 'can_manage_users': True, 'can_manage_halls': True, 'can_manage_shifts': True},
    'HD': {'can_checkin': True, 'can_checkout': True, 'can_view_other_halls': True, 'can_manage_users': True, 'can_manage_halls': True, 'can_manage_shifts': True},
}

PACKAGE_TYPES = [
    "Small Box-White",
    "Small Box-Brown", 
    "Small Box-Other Color",
    "Medium Box-White",
    "Medium Box-Brown",
    "Medium Box-Other Color",
    "Large Box-White",
    "Large Box-Brown", 
    "Large Box-Other Color",
    "Small Pack",
    "Medium Pack",
    "Large Pack",
    "Envelope",
    "Other",
    "Water Bottle Box"
]

# Export file configuration
EXPORT_FILE_PATH = "package_log_export.xlsx"

def get_conn():
    """Get database connection with proper error handling"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        print(f"DATABASE CONNECTION ERROR: {e}")
        print(f"Error details: {e.errno} - {e.msg}")
        raise

def normalize_tracking_id(s: str) -> str:
    return s.strip()

# ---------- Helper Functions ----------

def convert_to_mysql_datetime(date_obj):
    """Convert any date object to MySQL-compatible datetime"""
    if date_obj is None:
        return None
    
    # Convert pandas Timestamp
    if hasattr(date_obj, 'to_pydatetime'):
        return date_obj.to_pydatetime()
    
    # Already a Python datetime
    if isinstance(date_obj, datetime):
        return date_obj
    
    # Already a string in correct format
    if isinstance(date_obj, str):
        return date_obj
    
    # Fallback
    return datetime.now()


def _json_safe_value(v):
    """Convert values (datetime/date/time) to JSON-safe primitives."""
    if v is None:
        return None
    # datetime/date/time
    if isinstance(v, (datetime, date, time)):
        try:
            # Prefer ISO format; time needs no timezone here.
            return v.isoformat(sep=' ')
        except TypeError:
            return v.isoformat()
    return v

def json_safe_row(row: dict) -> dict:
    """Return a shallow-copied dict with JSON-safe values."""
    return {k: _json_safe_value(v) for k, v in (row or {}).items()}

def json_safe_rows(rows):
    """Convert a list of dict rows to JSON-safe values."""
    return [json_safe_row(r) for r in (rows or [])]

# ---------- Login Decorator ----------

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission=None):
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # If no specific permission required, just check if user can manage users
            if permission is None:
                if not session.get('can_manage_users', False):
                    flash("You don't have permission to access user management.", 'error')
                    return redirect(url_for('checkin'))
            # If specific permission required, check that permission
            elif not session.get(permission, False):
                flash(f"You don't have permission to access this resource.", 'error')
                return redirect(url_for('checkin'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('checkin'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def before_request():
    # Refresh session to keep it alive
    if 'user_id' in session:
        session.modified = True
        
    # Ensure required session variables exist
    if 'user_id' in session:
        # Set default values 
        session.setdefault('can_checkin', True)
        session.setdefault('can_checkout', True)
        session.setdefault('can_view_other_halls', False)
        session.setdefault('can_manage_users', False)
        session.setdefault('can_manage_halls', False)
        session.setdefault('can_manage_shifts', False) 
        session.setdefault('temporary_password', False)
        session.setdefault('is_admin', False)

def get_all_hall_codes():
    """Get all active hall codes for the help popup"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT hall_code, hall_name FROM halls ORDER BY hall_code")
        halls = cur.fetchall()
        cur.close()
        conn.close()
        return halls
    except Exception as e:
        print(f"Error fetching hall codes: {e}")
        return []
# ---------- Database Helpers ----------

def get_recent_packages(limit=50):
    """
    Return recent log rows (each delivery), including latest checkout info and student info.
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("""
        SELECT
            p.ID               AS pkgId,
            p.TrackingID       AS TrackingID,
            COALESCE(s1.firstName, s2.firstName, '') AS firstName, 
            COALESCE(s1.lastName, s2.lastName, '') AS lastName,   
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.Id               AS logId,
            l.checkInDate,
            l.checkInEmpInitials AS checkInEmpInitials,
            l.type AS package_type,  
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable       AS perishable,
            l.notes            AS notes
        FROM postofficelog l
        LEFT JOIN package_log   p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        ORDER BY l.checkInDate DESC, l.Id DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_all_packages():
    """
    Return ALL log rows without limit for search page.
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("""
        SELECT
            p.ID               AS pkgId,
            p.TrackingID       AS TrackingID,
            COALESCE(s1.firstName, s2.firstName, '') AS firstName, 
            COALESCE(s1.lastName, s2.lastName, '') AS lastName,     
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.Id               AS logId,
            l.checkInDate,
            l.checkInEmpInitials AS checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable       AS perishable,
            l.notes            AS notes,
            CASE 
                WHEN l.checkoutStatus = 1 OR l.checkoutDate IS NOT NULL THEN 1
                ELSE 0 
            END AS is_checked_out
        FROM postofficelog l
        LEFT JOIN package_log   p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        ORDER BY l.checkInDate DESC, l.Id DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_packages_grouped_by_month_data():
    """
    Return packages grouped by month with actual package data
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    # Get packages grouped by month with counts
    cur.execute("""
        SELECT 
            DATE_FORMAT(checkInDate, '%Y-%m') as month,
            DATE_FORMAT(checkInDate, '%M %Y') as display_month,
            COUNT(*) as package_count
        FROM postofficelog 
        WHERE checkInDate IS NOT NULL
        GROUP BY DATE_FORMAT(checkInDate, '%Y-%m'), DATE_FORMAT(checkInDate, '%M %Y')
        ORDER BY month DESC
    """)
    months_data = cur.fetchall()
    
    # Get all packages
    all_packages = get_all_packages()
    
    # Group packages by month
    for month_data in months_data:
        month = month_data['month']
        month_data['packages'] = [pkg for pkg in all_packages 
                                if pkg['checkInDate'] and 
                                pkg['checkInDate'].strftime('%Y-%m') == month]
    
    cur.close()
    conn.close()
    return months_data

def get_packages_by_month(month):
    """
    Return packages for a specific month (YYYY-MM format)
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    cur.execute("""
        SELECT
            p.ID               AS pkgId,
            p.TrackingID       AS TrackingID,
            COALESCE(s1.firstName, s2.firstName, '') AS firstName, 
            COALESCE(s1.lastName, s2.lastName, '') AS lastName,     
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.Id               AS logId,
            l.checkInDate,
            l.checkInEmpInitials AS checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable       AS perishable,
            l.notes            AS notes,
            CASE 
                WHEN l.checkoutStatus = 1 OR l.checkoutDate IS NOT NULL THEN 1
                ELSE 0 
            END AS is_checked_out
        FROM postofficelog l
        LEFT JOIN package_log   p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        WHERE DATE_FORMAT(l.checkInDate, '%%Y-%%m') = %s
        ORDER BY l.checkInDate DESC, l.Id DESC
    """, (month,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def search_packages(query):
    """
    Search packages by tracking ID, student name, or room number.
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    search_term = f"%{query}%"
    cur.execute("""
        SELECT
            p.ID               AS pkgId,
            p.TrackingID       AS TrackingID,
            COALESCE(s1.firstName, s2.firstName, 'None') AS firstName,
            COALESCE(s1.lastName, s2.lastName, 'None') AS lastName,
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.Id               AS logId,
            l.checkInDate,
            l.checkInEmpInitials AS checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable       AS perishable,
            l.notes            AS notes,
            CASE 
                WHEN l.checkoutStatus = 1 OR l.checkoutDate IS NOT NULL THEN 1
                ELSE 0 
            END AS is_checked_out
        FROM postofficelog l
        LEFT JOIN package_log   p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        WHERE p.TrackingID LIKE %s 
           OR s1.firstName LIKE %s OR s2.firstName LIKE %s
           OR s1.lastName LIKE %s OR s2.lastName LIKE %s
           OR s1.roomNumber LIKE %s OR s2.roomNumber LIKE %s
           OR l.roomNumber LIKE %s
        ORDER BY l.checkInDate DESC, l.Id DESC
    """, (search_term, search_term, search_term, search_term, search_term, search_term, search_term, search_term))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def fetch_latest_record_by_tracking(tracking_id: str):
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("""
        SELECT
            p.ID AS pkgId, p.TrackingID, p.DateTime AS checkInDate,
            COALESCE(s1.firstName, s2.firstName, '') AS firstName,     
            COALESCE(s1.lastName, s2.lastName, '') AS lastName,     
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.Id AS logId, l.checkInEmpInitials AS checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus, l.checkoutDate, l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable AS perishable,
            l.notes AS notes
        FROM package_log p
        LEFT JOIN postofficelog l ON l.trackingId = p.TrackingID
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        WHERE p.TrackingID=%s
        ORDER BY l.Id DESC
        LIMIT 1
    """, (tracking_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def check_tracking_exists(tracking_id: str):
    """Check if tracking ID already exists in package_log"""
    conn = get_conn()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT ID FROM package_log WHERE TrackingID=%s LIMIT 1", (tracking_id,))
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists

def update_export_file():
    """
    Update the Excel export file.
    Includes normal packages + RTS history in ONE unified export.
    """

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    # =======================
    # MAIN EXPORT QUERY
    # =======================
    cur.execute("""
        SELECT
            l.Id AS postoffice_log_id,
            l.trackingId AS tracking_id,

            COALESCE(s.firstName, '') AS first_name,
            COALESCE(s.lastName, '') AS last_name,
            l.roomNumber,
            COALESCE(s.hallName, '') AS hall_name,

            l.checkInDate,
            l.checkInEmpInitials,
            l.type AS package_type,

            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials,

            l.perishable,
            l.notes,

            -- RTS fields (NULL if not RTS)
            r.rts_type,
            r.address AS rts_address,
            r.date_submitted AS rts_date,
            r.title_initials AS rts_processed_by,

            -- FINAL STATUS (for Excel clarity)
            CASE
                WHEN r.id IS NOT NULL THEN 'RTS'
                WHEN l.checkoutStatus = 1 OR l.checkoutDate IS NOT NULL THEN 'Checked Out'
                ELSE 'Checked In'
            END AS final_status

        FROM postofficelog l
        LEFT JOIN studentmaster s
            ON s.roomNumber = l.roomNumber
        LEFT JOIN return_to_sender r
            ON r.postoffice_log_id = l.Id

        ORDER BY l.checkInDate DESC, l.Id DESC
    """)

    all_packages = cur.fetchall()

    cur.close()
    conn.close()

    # =======================
    # CREATE EXCEL FILE
    # =======================
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_filename = f"package_log_export_{timestamp}.xlsx"
    excel_filepath = os.path.join(tempfile.gettempdir(), excel_filename)

    with pd.ExcelWriter(excel_filepath, engine="openpyxl") as writer:

        # ---- ALL PACKAGES (MASTER SHEET)
        df_all = pd.DataFrame(all_packages)
        df_all.to_excel(writer, sheet_name="ALL_PACKAGES", index=False)

        # ---- CURRENT MONTH
        current_month = datetime.now().strftime('%Y-%m')
        current_month_rows = [
            row for row in all_packages
            if row["checkInDate"] and row["checkInDate"].strftime('%Y-%m') == current_month
        ]

        if current_month_rows:
            df_current = pd.DataFrame(current_month_rows)
            df_current.to_excel(writer, sheet_name="CURRENT_MONTH", index=False)

        # ---- RTS ONLY (VERY USEFUL FOR ADMINS)
        rts_rows = [row for row in all_packages if row["final_status"] == "RTS"]
        if rts_rows:
            df_rts = pd.DataFrame(rts_rows)
            df_rts.to_excel(writer, sheet_name="RTS_HISTORY", index=False)

    # Update global path
    global EXPORT_FILE_PATH
    EXPORT_FILE_PATH = excel_filepath

    print(f"[EXPORT] Excel updated with RTS data at {excel_filepath}")
    return excel_filepath


def get_available_months():
    """Get distinct months from the database for dropdown"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT DATE_FORMAT(checkInDate, '%Y-%m') as month 
        FROM postofficelog 
        WHERE checkInDate IS NOT NULL 
        ORDER BY month DESC
    """)
    months = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return months

# ---------- Optimized Database Helpers ----------

def get_quick_recent_packages(limit=50):
    """
    Return ONLY essential recent package data - FAST VERSION
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    # Optimized query - minimal joins, only essential fields
    cur.execute("""
        SELECT
            l.Id AS logId,
            l.trackingId AS TrackingID,
            l.roomNumber,
            l.checkInDate,
            l.checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials,
            l.perishable,
            l.notes,
            COALESCE(s.firstName, '') AS firstName,
            COALESCE(s.lastName, '') AS lastName,
            COALESCE(s.hallName, '') AS hallName
        FROM postofficelog l
        LEFT JOIN studentmaster s ON s.roomNumber = l.roomNumber
        ORDER BY l.checkInDate DESC
        LIMIT %s
    """, (limit,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_recent_months_packages(months=3):
    """
    Get packages from recent N months only
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    # Calculate date cutoff
    from datetime import datetime, timedelta
    cutoff_date = datetime.now() - timedelta(days=months*30)
    
    cur.execute("""
        SELECT
            l.Id AS logId,
            l.trackingId AS TrackingID,
            l.roomNumber,
            l.checkInDate,
            l.checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials,
            l.perishable,
            l.notes,
            COALESCE(s.firstName, '') AS firstName,
            COALESCE(s.lastName, '') AS lastName,
            COALESCE(s.hallName, '') AS hallName,
            DATE_FORMAT(l.checkInDate, '%%Y-%%m') as month,
            DATE_FORMAT(l.checkInDate, '%%M %%Y') as display_month
        FROM postofficelog l
        LEFT JOIN studentmaster s ON s.roomNumber = l.roomNumber
        WHERE l.checkInDate >= %s
        ORDER BY l.checkInDate DESC
    """, (cutoff_date,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # Group by month
    months_data = []
    current_month = None
    current_month_data = None
    
    for row in rows:
        month = row['month']
        if month != current_month:
            if current_month_data:
                months_data.append(current_month_data)
            current_month = month
            current_month_data = {
                'month': month,
                'display_month': row['display_month'],
                'packages': [],
                'package_count': 0
            }
        current_month_data['packages'].append(row)
        current_month_data['package_count'] += 1
    
    if current_month_data:
        months_data.append(current_month_data)
    
    return months_data

def quick_search_packages(query, limit=1000):
    """
    Live package search.
    Displays: RoomNumber FirstName LastName
    """
    if not query:
        return []

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    search_term = f"%{query}%"

    cur.execute("""
        SELECT
            -- NORMALIZED ROOM NUMBER (THIS IS WHAT YOU WANT)
            REPLACE(l.roomNumber, 'Clement Hall-', '') AS roomNumber,

            -- STUDENT INFO (OPTIONAL)
            COALESCE(s.firstName, '') AS firstName,
            COALESCE(s.lastName, '') AS lastName,

            -- PACKAGE INFO (KEPT FOR INTERNAL USE)
            l.Id AS logId,
            l.trackingId,
            l.checkInDate,
            l.type AS package_type,
            l.checkoutStatus

        FROM postofficelog l

        LEFT JOIN studentmaster s
          ON s.roomNumber = REPLACE(l.roomNumber, 'Clement Hall-', '')

        WHERE
            REPLACE(l.roomNumber, 'Clement Hall-', '') LIKE %s
            OR l.trackingId LIKE %s
            OR s.firstName LIKE %s
            OR s.lastName LIKE %s

        ORDER BY l.checkInDate DESC
        LIMIT %s
    """, (
        search_term,
        search_term,
        search_term,
        search_term,
        limit
    ))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def send_package_email(to_email, student_name, room_number):
    import smtplib, os
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "Package Ready for Pickup"
    msg["From"] = os.environ["MAIL_FROM"]
    msg["To"] = to_email

    msg.set_content(f"""
Hi {student_name},

You have a package ready for pickup at the front desk.

Room: {room_number}

Please bring your VolCard.

– University Housing
""")

    # SENDGRID SMTP (THIS IS THE IMPORTANT PART)
    with smtplib.SMTP("smtp.sendgrid.net", 587) as smtp:
        smtp.starttls()
        smtp.login("apikey", os.environ["SENDGRID_API_KEY"])
        smtp.send_message(msg)


# ---------- Socket.IO ----------

@socketio.on("connect")
def _on_connect():
    print("[SOCKET] Browser connected")

# ---------- API from bridge ----------

@app.route("/api/recent_packages_separated")
def api_recent_packages_separated():
    """Return recent packages separated into checked-in and checked-out"""
    limit = int(request.args.get("limit", 100))
    all_packages = get_recent_packages(limit)
    
    # Separate packages
    checked_in_packages = []
    checked_out_packages = []
    
    for pkg in all_packages:
        # Check if package is checked out
        is_checked_out = (
            pkg.get('checkoutStatus') == 1 or 
            pkg.get('checkoutStatus') is True or
            pkg.get('checkoutDate') is not None
        )
        
        if is_checked_out:
            checked_out_packages.append(pkg)
        else:
            checked_in_packages.append(pkg)
    
    return jsonify({
        "checked_in_packages": checked_in_packages,
        "checked_out_packages": checked_out_packages
    })

@app.route("/api/receive_scan", methods=["POST"])
def receive_scan():
    data = request.get_json(force=True, silent=True) or {}
    tracking_id = data.get("tracking_id", "")
    print(f"[SC LOGIC] → {tracking_id}")

    # This sends the scan to all connected browser pages
    socketio.emit("new_scan", {"tracking_id": tracking_id})
    print(f"[EMIT] Sent 'new_scan' → {tracking_id}")

    return jsonify({"ok": True, "tracking_id": tracking_id})

# Small JSON endpoint so clients can refresh the table when signaled.
@app.route("/api/recent_packages")
def api_recent_packages():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_recent_packages(limit))

# ---------- Login Routes ----------

@app.route("/")
def home():
    if 'user_initials' in session:
        return redirect(url_for('checkin'))  
    return redirect(url_for('login'))


@app.route("/login", methods=["GET", "POST"])
def login():
    # Get all active hall codes for the help popup
    def get_all_hall_codes():
        """Get all active hall codes for the help popup"""
        try:
            conn = get_conn()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT hall_code, hall_name FROM halls ORDER BY hall_code")
            halls = cur.fetchall()
            cur.close()
            conn.close()
            return halls
        except Exception as e:
            print(f"Error fetching hall codes: {e}")
            return []
    
    # POST request - handle login
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        hall_code = request.form.get("hall_code", "").strip().upper()
        
        if not username or not password or not hall_code:
            hall_codes = get_all_hall_codes()
            return render_template("login.html", 
                                 hall_codes=hall_codes,
                                 error="All fields are required")
        
        try:
            conn = get_conn()
            cur = conn.cursor(dictionary=True)
            
            # Get hall ID
            cur.execute("SELECT id, hall_name FROM halls WHERE hall_code = %s", (hall_code,))
            hall = cur.fetchone()
            
            if not hall:
                cur.close()
                conn.close()
                hall_codes = get_all_hall_codes()
                return render_template("login.html", 
                                     hall_codes=hall_codes,
                                     error="Invalid hall code or hall is inactive")
            
            # Get user with hall check
            cur.execute("""
                SELECT i.*, h.hall_name, h.hall_code 
                FROM initialscheck i
                JOIN halls h ON i.hall_id = h.id
                WHERE i.username = %s AND i.hall_id = %s AND i.is_active = 1
            """, (username, hall['id']))
            user = cur.fetchone()
            
            if not user:
                cur.close()
                conn.close()
                hall_codes = get_all_hall_codes()
                return render_template("login.html", 
                                     hall_codes=hall_codes,
                                     error="Invalid credentials or account inactive")
            
            # Verify password
            if not verify_password(password, user['password_hash']):
                log_audit(user['id'], 'LOGIN_FAILED', 'Invalid password', request)
                cur.close()
                conn.close()
                hall_codes = get_all_hall_codes()
                return render_template("login.html", 
                                     hall_codes=hall_codes,
                                     error="Invalid credentials")
            
            # LOGIN SUCCESS
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']  # Add this for clarity
            session['user_initials'] = user['initials']
            session['user_full_name'] = user['fullName']
            session['user_title'] = user.get('title', '')
            session['user_hall_id'] = user['hall_id']
            session['user_hall_name'] = user['hall_name']
            session['user_hall_code'] = user['hall_code']
            session['temporary_password'] = bool(user['temporary_password'])
            
            # ===== FIXED PERMISSION HANDLING =====
            # First, get ALL permission values from database (these should be 1/0 or NULL)
            can_checkin_db = user.get('can_checkin')
            can_checkout_db = user.get('can_checkout')
            can_view_other_halls_db = user.get('can_view_other_halls')
            can_manage_users_db = user.get('can_manage_users')
            can_manage_halls_db = user.get('can_manage_halls')
            can_manage_shifts_db = user.get('can_manage_shifts')  # ADD THIS LINE

            print(f"DEBUG: User {username} ({user['title']}) database permissions:")
            print(f"  can_checkin: {can_checkin_db}")
            print(f"  can_checkout: {can_checkout_db}")
            print(f"  can_view_other_halls: {can_view_other_halls_db}")
            print(f"  can_manage_users: {can_manage_users_db}")
            print(f"  can_manage_halls: {can_manage_halls_db}")
            print(f"  can_manage_shifts: {can_manage_shifts_db}")  # ADD THIS LINE

            # Get defaults based on title
            title = user['title']
            defaults = DEFAULT_PERMISSIONS.get(title, {})

            # Set session permissions with proper fallback logic
            # If database has a value (1/0), use it. Otherwise use defaults.
            session['can_checkin'] = bool(can_checkin_db) if can_checkin_db is not None else defaults.get('can_checkin', False)
            session['can_checkout'] = bool(can_checkout_db) if can_checkout_db is not None else defaults.get('can_checkout', False)
            session['can_view_other_halls'] = bool(can_view_other_halls_db) if can_view_other_halls_db is not None else defaults.get('can_view_other_halls', False)
            session['can_manage_users'] = bool(can_manage_users_db) if can_manage_users_db is not None else defaults.get('can_manage_users', False)
            session['can_manage_halls'] = bool(can_manage_halls_db) if can_manage_halls_db is not None else defaults.get('can_manage_halls', False)
            session['can_manage_shifts'] = bool(can_manage_shifts_db) if can_manage_shifts_db is not None else defaults.get('can_manage_shifts', False)  # ADD THIS LINE

            print(f"DEBUG: Final session permissions:")
            print(f"  can_checkin: {session['can_checkin']}")
            print(f"  can_checkout: {session['can_checkout']}")
            print(f"  can_view_other_halls: {session['can_view_other_halls']}")
            print(f"  can_manage_users: {session['can_manage_users']}")
            print(f"  can_manage_halls: {session['can_manage_halls']}")
            print(f"  can_manage_shifts: {session['can_manage_shifts']}")  # ADD THIS LINE
            # ===== END FIXED PERMISSION HANDLING =====
            
            # Create display name
            if session['user_title']:
                session['display_name'] = f"{session['user_title']} {session['user_initials']}"
            else:
                session['display_name'] = session['user_initials']
            
            # Admin check - based on title AND permissions
            session['is_admin'] = (session['user_title'] in ['HD', 'AHD']) or session['can_manage_users']
            
            # Log successful login
            log_audit(user['id'], 'LOGIN_SUCCESS', f"Logged into {user['hall_name']} with title {user['title']}", request)
            
            cur.close()
            conn.close()
            
            # Redirect to password change if temporary password
            if session.get('temporary_password'):
                return redirect(url_for('change_password'))
            
            return redirect(url_for('checkin'))
                
        except Exception as e:
            print(f"LOGIN ERROR: {e}")
            import traceback
            traceback.print_exc()
            hall_codes = get_all_hall_codes()
            return render_template("login.html", 
                                 hall_codes=hall_codes,
                                 error=f"System error: {str(e)}")
    
    # GET request - show login form with hall codes
    hall_codes = get_all_hall_codes()
    return render_template("login.html", hall_codes=hall_codes)

# ----- Search & Check-Out Page -----

@app.route("/search", methods=["GET", "POST"])
@login_required
def search():
    # -------------------------------
    # POST: single checkout (UNCHANGED)
    # -------------------------------
    if request.method == "POST":
        tracking_id = normalize_tracking_id(request.form.get("tracking_id"))
        initials = session.get('user_initials', '').strip().upper()

        conn = get_conn()
        cur = conn.cursor(dictionary=True, buffered=True)

        cur.execute("""
            SELECT l.Id AS logId
            FROM postofficelog l
            WHERE l.trackingId = %s
            ORDER BY l.Id DESC
            LIMIT 1
        """, (tracking_id,))
        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()

            packages_by_month = get_packages_grouped_by_month_data()
            total_count = sum(len(m.get("packages", [])) for m in packages_by_month)

            packages_by_month = [
                m for m in packages_by_month
                if m.get("packages") and len(m["packages"]) > 0
            ]

            return render_template(
                "search.html",
                packages_by_month=packages_by_month,
                total_count=total_count,
                search_query="",
                user_initials=session.get('display_name', ''),
                error="No record found for this tracking ID"
            )

        # Perform checkout
        cur.execute("""
            UPDATE postofficelog
            SET checkoutStatus = 1,
                checkoutDate = %s,
                checkoutEmpInitials = %s
            WHERE Id = %s
        """, (datetime.now(), initials, row["logId"]))

        conn.commit()
        cur.close()
        conn.close()

        # Update export + notify
        import threading
        threading.Thread(target=update_export_file, daemon=True).start()
        socketio.emit("refresh_recent")

        # notify all pages
        socketio.emit("refresh_recent")

        # respond immediately (NO HTML reload)
        return jsonify({"ok": True})


    # -------------------------------
    # GET: FAST LOAD (NO DATA)
    # -------------------------------
    if request.method == "GET":
        return render_template(
            "search.html",
            packages_by_month=[],   # 👈 EMPTY ON LOAD
            total_count=0,
            search_query="",
            user_initials=session.get('display_name', '')
        )

@app.route("/api/search_initial_data")
@login_required
def api_search_initial_data():
    packages_by_month = get_packages_grouped_by_month_data()

    # remove empty months
    packages_by_month = [
        m for m in packages_by_month
        if m.get("packages")
    ]

    # JSON-safe conversion
    for m in packages_by_month:
        m["packages"] = json_safe_rows(m.get("packages", []))

    total_count = sum(len(m["packages"]) for m in packages_by_month)

    return jsonify({
        "packages_by_month": packages_by_month,
        "total_count": total_count
    })

@app.route("/api/rts_packages")
@login_required
def api_rts_packages():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            l.Id AS logId,
            p.TrackingID,
            COALESCE(s.firstName, '') AS firstName,
            COALESCE(s.lastName, '') AS lastName,
            l.roomNumber,
            COALESCE(s.hallName, '') AS hallName,
            l.checkInDate,
            l.type AS package_type
        FROM postofficelog l
        LEFT JOIN package_log p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s ON s.roomNumber = l.roomNumber
        WHERE l.checkoutStatus = 0 OR l.checkoutStatus IS NULL
        ORDER BY l.checkInDate DESC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return jsonify(rows)


@app.route("/search_bulk_checkout", methods=["POST"])
@login_required
def search_bulk_checkout():
    """
    Bulk checkout from Search page (RTS-style multi-select), WITHOUT changing existing /search single checkout.
    Accepts JSON: { "tracking_ids": ["...","..."] }
    """
    data = request.get_json(silent=True) or {}
    tracking_ids = data.get("tracking_ids") or []

    # Normalize + de-dupe
    cleaned = []
    seen = set()
    for t in tracking_ids:
        if t is None:
            continue
        tid = normalize_tracking_id(str(t))
        if not tid:
            continue
        if tid not in seen:
            seen.add(tid)
            cleaned.append(tid)

    if not cleaned:
        return jsonify({"ok": False, "message": "No packages selected"}), 400

    initials = session.get('user_initials', '').strip().upper()
    if not initials:
        return jsonify({"ok": False, "message": "Initials missing from session"}), 400

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    now = datetime.now()
    updated = 0
    not_found = []
    already_out = []

    for tid in cleaned:
        # Find latest log row for that tracking id (matches your existing single-checkout logic)
        cur.execute("""
            SELECT l.Id AS logId, l.checkoutStatus, l.checkoutDate
            FROM postofficelog l
            WHERE l.trackingId=%s
            ORDER BY l.Id DESC
            LIMIT 1
        """, (tid,))
        row = cur.fetchone()

        if not row:
            not_found.append(tid)
            continue

        is_out = (row.get("checkoutStatus") == 1) or (row.get("checkoutStatus") is True) or (row.get("checkoutDate") is not None)
        if is_out:
            already_out.append(tid)
            continue

        cur.execute("""
            UPDATE postofficelog
            SET checkoutStatus=1, checkoutDate=%s, checkoutEmpInitials=%s
            WHERE Id=%s
        """, (now, initials, row["logId"]))
        updated += 1

    conn.commit()
    cur.close()
    conn.close()

    # Update export in background (matches your existing behavior)
    import threading
    threading.Thread(target=update_export_file, daemon=True).start()
    socketio.emit("refresh_recent")

    return jsonify({
        "ok": True,
        "updated": updated,
        "not_found": not_found,
        "already_out": already_out
    })


@app.route("/api/validate_initials", methods=["POST"])
def api_validate_initials():
    data = request.get_json()
    initials = data.get("initials", "").strip().upper()
    
    if not initials:
        return jsonify({"valid": False, "message": "Initials are required"})
    
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("SELECT fullName FROM initialscheck WHERE initials = %s", (initials,))
    result = cur.fetchone()  
    cur.close()
    conn.close()
    
    if result:
        return jsonify({
            "valid": True, 
            "full_name": result['fullName'],
            "initials": initials
        })
    else:
        return jsonify({"valid": False, "message": "Invalid initials"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/api/student_by_room")
@login_required
def api_student_by_room():
    room = (request.args.get("room") or "").strip()
    if not room:
        return jsonify({"found": False})

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    cur.execute("""
        SELECT
            Id,
            firstName,
            lastName,
            preferredName,
            roomNumber,
            hallName,
            academicYear
        FROM studentmaster
        WHERE roomNumber = %s
        LIMIT 1
    """, (room,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    return jsonify({
        "found": bool(row),
        "student": row
    })


# ---------- Fast API Endpoints for Async Loading ----------

@app.route("/api/checkin_data")
@login_required
def api_checkin_data():
    """Fast endpoint for check-in page data"""
    limit = int(request.args.get("limit", 50))
    
    # Get recent packages
    packages = get_quick_recent_packages(limit)
    
    # Separate checked in/out
    checked_in = []
    checked_out = []
    
    for pkg in packages:
        if pkg.get('checkoutStatus') == 1 or pkg.get('checkoutDate'):
            checked_out.append(pkg)
        else:
            checked_in.append(pkg)
    
    return jsonify({
        "checked_in": checked_in[:30],  # Limit to 30 each
        "checked_out": checked_out[:30],
        "total": len(packages)
    })

@app.route("/api/search_data")
@login_required
def api_search_data():
    """Fast endpoint for search page initial data"""
    # Get recent months data
    months_data = get_recent_months_packages(months=3)  # Last 3 months
    
    # Calculate totals
    total_count = sum(month['package_count'] for month in months_data)
    
    return jsonify({
        "months": months_data,
        "total_count": total_count
    })

@app.route("/api/search_preload")
def api_search_preload():
    packages_by_month, total_count = get_packages_grouped_by_month_data()

    return jsonify({
        "packages_by_month": packages_by_month,
        "total_count": total_count
    })


@app.route("/api/quick_search")
@login_required
def api_quick_search():
    """
    Fast search endpoint for the search page.
    Returns results grouped by month to match the UI.
    """
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify({"packages_by_month": [], "total_count": 0, "query": ""})

    results = quick_search_packages(query, limit=1000)
    results = json_safe_rows(results)

    # Group by YYYY-MM from checkInDate
    from collections import defaultdict
    month_map = defaultdict(list)

    for r in results:
        check_in = r.get("checkInDate") or r.get("checkinDate")  # be forgiving
        # check_in is ISO string "YYYY-MM-DD HH:MM:SS" or similar
        month_key = ""
        display_month = ""
        try:
            if isinstance(check_in, str) and len(check_in) >= 7:
                month_key = check_in[:7]
                # Derive display month
                dt_obj = datetime.strptime(check_in[:10], "%Y-%m-%d")
                display_month = dt_obj.strftime("%B %Y")
        except Exception:
            pass

        if not month_key:
            month_key = "Unknown"
            display_month = "Unknown"

        month_map[(month_key, display_month)].append(r)

    # Sort months newest first (Unknown last)
    def sort_key(item):
        (month_key, _disp), _pkgs = item
        if month_key == "Unknown":
            return "0000-00"
        return month_key

    grouped = []
    for (month_key, display_month), pkgs in sorted(month_map.items(), key=sort_key, reverse=True):
        grouped.append({
            "month": month_key,
            "display_month": display_month,
            "packages": pkgs,
            "package_count": len(pkgs)
        })

    return jsonify({
        "packages_by_month": grouped,
        "total_count": sum(g["package_count"] for g in grouped),
        "query": query
    })

    results = quick_search_packages(query, limit=1000)

    return jsonify({
        "packages": results,
        "count": len(results),
        "query": query
    })

# ----- Admin CSV Update -----

@app.route("/admin/update_students", methods=["GET", "POST"])
@admin_required
def admin_update_students():
    if request.method == "POST":

        # ---------- FILE VALIDATION ----------
        if 'csv_file' not in request.files:
            return render_template("admin_update.html", error="No file selected")

        file = request.files['csv_file']
        if file.filename == '':
            return render_template("admin_update.html", error="No file selected")

        if not file.filename.endswith(('.csv', '.xlsx')):
            return render_template(
                "admin_update.html",
                error="Please upload a CSV or XLSX file"
            )

        # ---------- READ FILE (CSV or XLSX) ----------
        try:
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            return render_template(
                "admin_update.html",
                error=f"Unable to read file: {str(e)}"
            )

        # Normalize column names
        df.columns = [str(c).strip() for c in df.columns]

        # ---------- REQUIRED OFFICIAL COLUMNS ----------
        REQUIRED_COLUMNS = [
            'Name Last',
            'Name First Legal',
            'Name Chosen',
            'Room Location Description',
            'Room Space Description'
        ]

        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            return render_template(
                "admin_update.html",
                error=f"Missing required columns: {missing}"
            )

        # ---------- CLEAN ROOM NUMBERS (CTRL+F LOGIC) ----------
        df['roomNumber'] = (
            df['Room Space Description']
            .astype(str)
            .str.replace("Clement Hall-", "", regex=False)
            .str.strip()
        )

        # ---------- PREVENT DUPLICATE ROOMS ----------
        base_rooms = set(df['roomNumber'].str.split('-').str[0])
        split_rooms = set(
            df['roomNumber']
            .loc[df['roomNumber'].str.contains('-')]
            .str.split('-')
            .str[0]
        )

        invalid_bases = base_rooms & split_rooms

        df = df[
            ~df['roomNumber'].isin(invalid_bases)
        ]

        # ---------- DATABASE UPSERT ----------
        conn = get_conn()
        cur = conn.cursor()

        csv_rooms = set()
        insert_count = 0
        update_count = 0

        for _, r in df.iterrows():
            room = r['roomNumber']
            csv_rooms.add(room)

            first_name = str(r['Name First Legal']).strip()
            last_name = str(r['Name Last']).strip()
            email = str(r['email']).strip().lower()
            preferred = str(r['Name Chosen']).strip()
            hall = str(r['Room Location Description']).strip()

            cur.execute(
                "SELECT 1 FROM studentmaster WHERE roomNumber = %s",
                (room,)
            )

            if cur.fetchone():
                cur.execute("""
                    UPDATE studentmaster
                    SET firstName = %s,
                        lastName = %s,
                        email = %s,
                        preferredName = %s,
                        hallName = %s
                    WHERE roomNumber = %s
                """, (first_name, last_name, email, preferred, hall, room))
                update_count += 1
            else:
                cur.execute("""
                    INSERT INTO studentmaster
                    (firstName, lastName, email, preferredName, roomNumber, hallName)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    first_name,
                    last_name,
                    email,
                    preferred,
                    room,
                    hall
                ))

                insert_count += 1

        # ---------- HARD CLEANUP: REMOVE BASE ROOMS (MYSQL SAFE) ----------

        # Step 1: Collect base rooms that have split rooms (e.g. 256 from 256-1, 256-2)
        cur.execute("""
            CREATE TEMPORARY TABLE temp_base_rooms AS
            SELECT DISTINCT
                SUBSTRING_INDEX(roomNumber, '-', 1) AS base_room
            FROM studentmaster
            WHERE roomNumber LIKE '%-%'
        """)

        # Step 2: Delete base rooms safely using the temp table
        cur.execute("""
            DELETE FROM studentmaster
            WHERE roomNumber IN (
                SELECT base_room FROM temp_base_rooms
            )
        """)

        # Step 3: Drop temporary table
        cur.execute("DROP TEMPORARY TABLE temp_base_rooms")

        # ---------- COMMIT & CLOSE ----------
        conn.commit()
        cur.close()
        conn.close()

        # ---------- SUCCESS ----------
        return render_template(
            "admin_update.html",
            success=(
                f"Student update complete. "
                f"Inserted: {insert_count}, "
                f"Updated: {update_count}"
            )
        )

    # ---------- GET ----------
    return render_template("admin_update.html")


@app.route("/admin/download_template")
@admin_required
def download_template():
    """Download a CSV template for student data with proper column names"""
    template_data = "Id,firstname,lastname,preferredname,roomnumber,hallname,academic year\n1,John,Doe,Johnny,112-1,Clement Hall,2024\n2,Jane,Smith,Janie,112-2,Clement Hall,2024\n3,Bob,Johnson,Bobby,113-1,Clement Hall,2024"
    
    return send_file(
        StringIO(template_data),
        as_attachment=True,
        download_name="student_data_template.csv",
        mimetype="text/csv"
    )

# ----- Admin Package Update -----

@app.route("/admin/update_packages", methods=["GET", "POST"])
@admin_required
def admin_update_packages():
    if request.method == "POST":
        if 'excel_file' not in request.files:
            return render_template("admin_update_package.html", error="No file selected")
        
        file = request.files['excel_file']
        if file.filename == '':
            return render_template("admin_update_package.html", error="No file selected")
        
        if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
            return render_template("admin_update_package.html", error="Please upload Excel or CSV file")
        
        try:
            # Read the Excel file
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            print(f"DEBUG: Excel columns found: {list(df.columns)}")
            print(f"DEBUG: First few rows:\n{df.head()}")
            
            # Map Excel columns to database fields
            column_mapping = {
                'tracking_id': ['trackingid', 'tracking_id', 'tracking'],
                'first_name': ['firstname', 'first', 'fname'],
                'last_name': ['lastname', 'last', 'lname'],
                'room_number': ['roomnumber', 'room', 'room_num'],
                'hall_name': ['hallname', 'hall'],
                'checkin_date': ['checkindate', 'checkin', 'date'],
                'checkin_initials': ['checkinempinitials', 'checkininitials', 'initials', 'empinitials'],
                'package_type': ['package_type', 'packagetype', 'type', 'size'],
                'checkout_status': ['checkoutstatus', 'status'],
                'checkout_date': ['checkoutdate', 'checkout'],
                'checkout_initials': ['checkoutempinitials', 'checkoutinitials', 'checkoutemp'],
                'perishable': ['perishable'],
                'notes': ['notes']
            }
            
            # Find actual column names with case-insensitive matching
            actual_columns = {}
            df_columns_lower = [str(col).lower().strip() for col in df.columns]
            
            for db_field, possible_names in column_mapping.items():
                found = False
                for possible_name in possible_names:
                    if possible_name in df_columns_lower:
                        # Find the actual column name with original case
                        for original_col in df.columns:
                            if str(original_col).lower().strip() == possible_name:
                                actual_columns[db_field] = original_col
                                found = True
                                print(f"DEBUG: Mapped '{db_field}' to column '{original_col}'")
                                break
                    if found:
                        break
            
            print(f"DEBUG: Mapped columns: {actual_columns}")
            
            # Validate required columns
            required = ['tracking_id', 'room_number', 'checkin_date', 'checkin_initials']
            missing = [field for field in required if field not in actual_columns]
            
                        # Create error tracking list
            errors = []
            error_rows = []
            
            if missing:
                error_msg = f"Missing required columns: {', '.join(missing)}. Found columns: {list(df.columns)}"
                errors.append(error_msg)
                # Create error report
                error_df = pd.DataFrame({'Error': [error_msg]})
                error_file_path = create_error_report(error_df)
                return render_template("admin_update_package.html", 
                                    error=error_msg, 
                                    error_file=error_file_path)
            
            conn = get_conn()
            cur = conn.cursor(dictionary=True, buffered=True)
            
            imported_count = 0
            updated_count = 0
            error_count = 0
            unprocessed_count = 0
            unprocessed_rows = []
            total_rows = len(df)
            
            # Get all existing tracking IDs from the database for comparison
            cur.execute("SELECT trackingId FROM postofficelog")
            existing_tracking_ids = {row['trackingId'] for row in cur.fetchall()}
            print(f"DEBUG: Found {len(existing_tracking_ids)} existing tracking IDs in database")
            
            # Track tracking IDs from the uploaded file
            uploaded_tracking_ids = set()
            
            for index, row in df.iterrows():
                try:
                    # Extract data with proper column access - HANDLE SCIENTIFIC NOTATION
                    tracking_id_raw = row[actual_columns['tracking_id']]
                    
                    # Convert scientific notation (like 1.23456E+12) to regular string
                    if isinstance(tracking_id_raw, float):
                        # If it's a float with scientific notation, convert to int then string
                        if tracking_id_raw >= 1e10:  # Likely a scientific notation tracking ID
                            tracking_id = str(int(tracking_id_raw))
                        else:
                            tracking_id = str(tracking_id_raw)
                    else:
                        tracking_id = normalize_tracking_id(str(tracking_id_raw))
                    
                    # Skip if tracking ID is empty
                    if not tracking_id or str(tracking_id).lower() in ['nan', 'none', 'null', '']:
                        error_msg = f"Row {index+2}: Empty tracking ID"
                        errors.append(error_msg)
                        error_rows.append({
                            'Row': index+2,
                            'TrackingID': str(tracking_id_raw),
                            'Error': error_msg,
                            'Type': 'Error'
                        })
                        error_count += 1
                        continue
                    
                    uploaded_tracking_ids.add(tracking_id)
                    
                    # ========== NEW LOGIC: Check if tracking ID exists in system ==========
                    if tracking_id in existing_tracking_ids:
                        # ========== UPDATE EXISTING: Only change checkout status ==========
                        try:
                            # Get checkout status from uploaded file
                            checkout_status = 0  # Default to checked in
                            if 'checkout_status' in actual_columns:
                                status_val = row[actual_columns['checkout_status']]
                                if not pd.isna(status_val):
                                    status_str = str(status_val).lower().strip()
                                    # Handle various TRUE/FALSE representations
                                    if status_str in ['true', '1', 'yes', 'checked in', 'in', '0']:
                                        checkout_status = 0  # Checked IN
                                    elif status_str in ['false', '0', 'no', 'checked out', 'out', '1']:
                                        checkout_status = 1  # Checked OUT
                                        # Also set checkout date to current time if checking out
                                        checkout_date_db = datetime.now()
                                    else:
                                        # Try to parse as integer
                                        try:
                                            checkout_status = int(float(status_str))
                                        except:
                                            checkout_status = 0
                            
                            # Update only checkout status in postofficelog
                            if checkout_status == 1:
                                # If checking out, set checkout date
                                cur.execute("""
                                    UPDATE postofficelog 
                                    SET checkoutStatus = %s, checkoutDate = %s
                                    WHERE trackingId = %s
                                """, (checkout_status, checkout_date_db, tracking_id))
                            else:
                                # If checking in, just update status
                                cur.execute("""
                                    UPDATE postofficelog 
                                    SET checkoutStatus = %s
                                    WHERE trackingId = %s
                                """, (checkout_status, tracking_id))
                            
                            updated_count += 1
                            print(f"DEBUG: UPDATED checkout status for {tracking_id} to {checkout_status}")
                            
                            # Skip the rest of processing for existing tracking IDs
                            continue
                            
                        except Exception as e:
                            error_count += 1
                            error_msg = f"Row {index+2}: Error updating checkout status - {str(e)}"
                            errors.append(error_msg)
                            error_rows.append({
                                'Row': index+2,
                                'TrackingID': tracking_id,
                                'Error': error_msg,
                                'Type': 'Error'
                            })
                            continue
                    
                    # ========== PROCESS AS NEW ENTRY (existing code continues) ==========
                    # Extract other data with defaults
                    room_number = str(row[actual_columns['room_number']]).strip() if 'room_number' in actual_columns else ''
                    if not room_number or room_number.lower() in ['nan', 'none', '']:
                        error_msg = f"Row {index+2}: Missing room number"
                        errors.append(error_msg)
                        error_rows.append({
                            'Row': index+2,
                            'TrackingID': tracking_id,
                            'Error': error_msg,
                            'Type': 'Error'
                        })
                        error_count += 1
                        continue
                    
                    # Handle checkin date - CONVERT VARIOUS DATE FORMATS
                    checkin_date_raw = row[actual_columns['checkin_date']]
                    checkin_initials = str(row[actual_columns['checkin_initials']]).strip().upper() if 'checkin_initials' in actual_columns else ''
                    
                    # Parse checkin_date with multiple format support
                    checkin_date = None
                    if pd.isna(checkin_date_raw):
                        checkin_date = datetime.now()
                    elif isinstance(checkin_date_raw, str):
                        date_str = checkin_date_raw.strip()
                        
                        # Try multiple date formats including 12/7/25 and 12.7.25
                        date_formats = [
                            '%Y-%m-%d %H:%M:%S',
                            '%Y-%m-%d',
                            '%m/%d/%Y %H:%M:%S',
                            '%m/%d/%Y',
                            '%d/%m/%Y %H:%M:%S',
                            '%d/%m/%Y',
                            '%m/%d/%y',  # 12/7/25
                            '%m.%d.%y',  # 12.7.25
                            '%d/%m/%y',  # 7/12/25
                            '%d.%m.%y',  # 7.12.25
                            '%m/%d/%y %H:%M:%S',
                            '%m.%d.%y %H:%M:%S'
                        ]
                        
                        parsed_date = None
                        for fmt in date_formats:
                            try:
                                parsed_date = datetime.strptime(date_str, fmt)
                                break
                            except ValueError:
                                continue
                        
                        if parsed_date:
                            checkin_date = parsed_date
                        else:
                            error_msg = f"Row {index+2}: Could not parse date '{date_str}'"
                            errors.append(error_msg)
                            error_rows.append({
                                'Row': index+2,
                                'TrackingID': tracking_id,
                                'Error': error_msg,
                                'Type': 'Error'
                            })
                            error_count += 1
                            continue
                    else:
                        # Already a datetime object
                        checkin_date = checkin_date_raw
                    
                    # Convert date to MySQL compatible format
                    checkin_date_db = convert_to_mysql_datetime(checkin_date)
                    
                    # Get optional fields with defaults
                    package_type = 'Other'
                    if 'package_type' in actual_columns:
                        package_type_val = row[actual_columns['package_type']]
                        if not pd.isna(package_type_val):
                            package_type = str(package_type_val).strip()
                            if not package_type or package_type.lower() in ['nan', 'none']:
                                package_type = 'Other'
                    
                    # Get other optional fields
                    first_name = ''
                    if 'first_name' in actual_columns:
                        first_name_val = row[actual_columns['first_name']]
                        if not pd.isna(first_name_val):
                            first_name = str(first_name_val).strip()
                    
                    last_name = ''
                    if 'last_name' in actual_columns:
                        last_name_val = row[actual_columns['last_name']]
                        if not pd.isna(last_name_val):
                            last_name = str(last_name_val).strip()
                    
                    hall_name = 'Clement Hall'
                    if 'hall_name' in actual_columns:
                        hall_name_val = row[actual_columns['hall_name']]
                        if not pd.isna(hall_name_val):
                            hall_name = str(hall_name_val).strip()
                    
                    # Handle checkout status and dates with date format conversion
                    checkout_date = None
                    checkout_initials = None
                    checkout_status = 0  # Default to checked in
                    
                    if 'checkout_status' in actual_columns:
                        status_val = row[actual_columns['checkout_status']]
                        if not pd.isna(status_val):
                            status_str = str(status_val).lower().strip()
                            # Handle various TRUE/FALSE representations
                            if status_str in ['true', '1', 'yes', 'checked in', 'in', '0']:
                                checkout_status = 0  # Checked IN
                            elif status_str in ['false', '0', 'no', 'checked out', 'out', '1']:
                                checkout_status = 1  # Checked OUT
                                checkout_date = datetime.now()
                    
                    # Handle checkout date specifically with format conversion
                    if 'checkout_date' in actual_columns:
                        checkout_date_val = row[actual_columns['checkout_date']]
                        if not pd.isna(checkout_date_val):
                            if isinstance(checkout_date_val, str):
                                date_str = checkout_date_val.strip()
                                
                                # Try multiple date formats for checkout date
                                parsed_checkout_date = None
                                for fmt in date_formats:  # Use same date_formats list
                                    try:
                                        parsed_checkout_date = datetime.strptime(date_str, fmt)
                                        break
                                    except ValueError:
                                        continue
                                
                                if parsed_checkout_date:
                                    checkout_date = parsed_checkout_date
                                else:
                                    checkout_date = datetime.now()
                            else:
                                checkout_date = checkout_date_val
                    
                    # Convert checkout date to MySQL compatible format
                    checkout_date_db = convert_to_mysql_datetime(checkout_date) if checkout_date else None
                    
                    # Get checkout initials if checkout occurred
                    if checkout_status == 1 and 'checkout_initials' in actual_columns:
                        checkout_initials_val = row[actual_columns['checkout_initials']]
                        if not pd.isna(checkout_initials_val):
                            checkout_initials = str(checkout_initials_val).strip().upper()
                    
                    # Handle perishable
                    perishable = 'no'
                    if 'perishable' in actual_columns:
                        perish_val = row[actual_columns['perishable']]
                        if not pd.isna(perish_val):
                            perish_str = str(perish_val).lower().strip()
                            perishable = 'yes' if perish_str in ['yes', 'y', '1', 'true'] else 'no'
                    
                    # Handle notes
                    notes = ''
                    if 'notes' in actual_columns:
                        notes_val = row[actual_columns['notes']]
                        if not pd.isna(notes_val):
                            notes = str(notes_val).strip()
                    
                    print(f"DEBUG: Processing NEW row {index}: TrackingID={tracking_id}, Room={room_number}, Checkin={checkin_date_db}")
                    
                    # CHECK IF ROOM EXISTS AND UPDATE STUDENT DATA (LIKE STUDENT UPDATE CODE)
                    # Check if room exists in studentmaster
                    cur.execute("SELECT * FROM studentmaster WHERE roomNumber = %s", (room_number,))
                    existing_student = cur.fetchone()
                    
                    if existing_student:
                        # UPDATE existing student - EXACTLY LIKE STUDENT UPDATE CODE
                        if first_name or last_name:  # Only update if we have names
                            cur.execute("""
                                UPDATE studentmaster 
                                SET firstName = %s, lastName = %s, hallName = %s
                                WHERE roomNumber = %s
                            """, (first_name, last_name, hall_name, room_number))
                            print(f"DEBUG: UPDATED student for room {room_number}: {first_name} {last_name}")
                    else:
                        # INSERT new student if we have names
                        if first_name or last_name:
                            # Generate a unique ID using hash of tracking ID
                            hash_obj = hashlib.md5(tracking_id.encode())
                            student_id = int(hash_obj.hexdigest()[:8], 16)  # Use first 8 chars as ID
                            cur.execute("""
                                INSERT INTO studentmaster 
                                (Id, firstName, lastName, roomNumber, hallName, academicYear) 
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (student_id, first_name, last_name, room_number, hall_name, '2025-2026'))
                            print(f"DEBUG: INSERTED new student for room {room_number}: {first_name} {last_name}")
                    
                    # Check if tracking ID already exists in package_log
                    cur.execute("SELECT ID FROM package_log WHERE TrackingID = %s", (tracking_id,))
                    existing_package = cur.fetchone()
                    
                    if not existing_package:
                        # Insert into package_log
                        cur.execute("INSERT INTO package_log (TrackingID, DateTime) VALUES (%s, %s)", 
                                  (tracking_id, checkin_date_db))
                        imported_count += 1
                        print(f"DEBUG: INSERTED into package_log: {tracking_id}")
                    else:
                        # Update package_log with new date
                        cur.execute("UPDATE package_log SET DateTime = %s WHERE TrackingID = %s", 
                                  (checkin_date_db, tracking_id))
                        imported_count += 1
                        print(f"DEBUG: UPDATED package_log: {tracking_id}")
                    
                    # Check if log entry exists in postofficelog
                    cur.execute("SELECT Id, trackingId, roomNumber, checkInDate, checkInEmpInitials, type, checkoutStatus, checkoutDate, checkoutEmpInitials, perishable, notes FROM postofficelog WHERE trackingId = %s", (tracking_id,))
                    existing_log = cur.fetchone()
                    
                    if not existing_log:
                        # Insert into postofficelog
                        cur.execute("""
                            INSERT INTO postofficelog 
                            (trackingId, roomNumber, checkInDate, type, checkInEmpInitials, 
                             checkoutStatus, checkoutDate, checkoutEmpInitials, perishable, notes)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (tracking_id, room_number, checkin_date_db, package_type, checkin_initials,
                             checkout_status, checkout_date_db, checkout_initials, perishable, notes))
                        print(f"DEBUG: INSERTED into postofficelog: {tracking_id} for room {room_number}")
                    else:
                        # For new entries that somehow exist, update all data
                        cur.execute("""
                            UPDATE postofficelog 
                            SET roomNumber = %s, checkInDate = %s, type = %s, checkInEmpInitials = %s,
                                checkoutStatus = %s, checkoutDate = %s, checkoutEmpInitials = %s,
                                perishable = %s, notes = %s
                            WHERE trackingId = %s
                        """, (room_number, checkin_date_db, package_type, checkin_initials,
                             checkout_status, checkout_date_db, checkout_initials, 
                             perishable, notes, tracking_id))
                        imported_count += 1
                        print(f"DEBUG: UPDATED postofficelog: {tracking_id}")
                    
                except Exception as e:
                    error_count += 1
                    error_msg = f"Row {index+2}: {str(e)}"
                    errors.append(error_msg)
                    error_rows.append({
                        'Row': index+2,
                        'TrackingID': tracking_id if 'tracking_id' in locals() else 'N/A',
                        'Error': error_msg,
                        'Type': 'Error'
                    })
                    print(f"DEBUG: Error processing row {index}: {e}")
                    continue
            
            # NEW: Track which rows couldn't be processed at all
            processed_tracking_ids = set()
            for row_data in error_rows:
                if row_data['TrackingID'] != 'N/A':
                    processed_tracking_ids.add(row_data['TrackingID'])
            
            # Also add successfully processed tracking IDs
            processed_tracking_ids.update(uploaded_tracking_ids)
            
            # Check each row in the original dataframe
            for index, row in df.iterrows():
                try:
                    tracking_id_raw = row[actual_columns['tracking_id']]
                    if isinstance(tracking_id_raw, float):
                        if tracking_id_raw >= 1e10:
                            tracking_id = str(int(tracking_id_raw))
                        else:
                            tracking_id = str(tracking_id_raw)
                    else:
                        tracking_id = normalize_tracking_id(str(tracking_id_raw))
                    
                    if tracking_id and str(tracking_id).lower() not in ['nan', 'none', 'null', '']:
                        # Check if this tracking ID was processed
                        if tracking_id not in processed_tracking_ids:
                            unprocessed_count += 1
                            unprocessed_rows.append({
                                'Row': index + 2,
                                'TrackingID': tracking_id,
                                'Error': 'Not processed',
                                'Type': 'Unprocessed'
                            })
                except Exception as e:
                    # Skip if we can't even extract tracking ID
                    unprocessed_count += 1
                    unprocessed_rows.append({
                        'Row': index + 2,
                        'TrackingID': 'Unknown',
                        'Error': f'Could not extract tracking ID: {str(e)}',
                        'Type': 'Unprocessed'
                    })
            
            # After processing all rows in the uploaded file
            # Check for tracking IDs that are in the database but not in the uploaded file
            missing_in_upload = existing_tracking_ids - uploaded_tracking_ids
            cleared_count = 0
            
            print(f"DEBUG: Found {len(missing_in_upload)} tracking IDs in database but not in upload")
            
            # For each tracking ID missing in upload, we should mark it as checked out if not already
            for tracking_id in missing_in_upload:
                try:
                    # Check current checkout status
                    cur.execute("SELECT checkoutStatus FROM postofficelog WHERE trackingId = %s", (tracking_id,))
                    log_entry = cur.fetchone()
                    
                    if log_entry and log_entry['checkoutStatus'] == 0:
                        # Package is checked in but not in upload - mark as checked out
                        cur.execute("""
                            UPDATE postofficelog 
                            SET checkoutStatus = 1, 
                                checkoutDate = %s,
                                checkoutEmpInitials = %s
                            WHERE trackingId = %s AND checkoutStatus = 0
                        """, (datetime.now(), 'SYSTEM', tracking_id))
                        cleared_count += 1
                        print(f"DEBUG: MARKED AS CHECKED OUT (missing in upload): {tracking_id}")
                except Exception as e:
                    print(f"DEBUG: Error processing missing tracking ID {tracking_id}: {e}")
                    continue
            
            conn.commit()
            cur.close()
            conn.close()
            
            # Update export file
            update_export_file()
            
            success_msg = f"Package update completed!<br>"
            success_msg += f"• Total rows in file: {total_rows}<br>"
            success_msg += f"• Imported new packages: {imported_count}<br>"
            success_msg += f"• Updated checkout status for existing packages: {updated_count}<br>"
            success_msg += f"• Marked as checked out (missing in upload): {cleared_count}<br>"
            success_msg += f"• Rows with errors: {error_count}<br>"
            success_msg += f"• Rows not processed: {unprocessed_count}"
            
            # Create error report if there were errors OR unprocessed rows
            error_file_path = None
            if error_rows or unprocessed_rows:
                # Combine error rows and unprocessed rows
                all_issue_rows = error_rows.copy()
                all_issue_rows.extend(unprocessed_rows)
                
                error_df = pd.DataFrame(all_issue_rows)
                error_file_path = create_error_report(error_df)
            
            return render_template("admin_update_package.html", 
                                 success=success_msg, 
                                 error_file=error_file_path)
            
        except Exception as e:
            import traceback
            print(f"Package update error: {traceback.format_exc()}")
            return render_template("admin_update_package.html", error=f"Error processing file: {str(e)}")
    
    return render_template("admin_update_package.html")

def create_error_report(error_df):
    """Create an Excel file with error details"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_filename = f"package_update_report_{timestamp}.xlsx"
        error_filepath = os.path.join(tempfile.gettempdir(), error_filename)
        
        with pd.ExcelWriter(error_filepath, engine='openpyxl') as writer:
            # Separate sheets based on Type column
            if 'Type' in error_df.columns:
                # Errors sheet
                errors_df = error_df[error_df['Type'] == 'Error']
                if not errors_df.empty:
                    errors_df.to_excel(writer, sheet_name='Errors', index=False)
                
                # Unprocessed sheet
                unprocessed_df = error_df[error_df['Type'] == 'Unprocessed']
                if not unprocessed_df.empty:
                    unprocessed_df.to_excel(writer, sheet_name='Unprocessed_Rows', index=False)
                
                # Summary sheet
                summary_data = {
                    'Type': ['Errors', 'Unprocessed Rows', 'Total'],
                    'Count': [len(errors_df), len(unprocessed_df), len(error_df)]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
            else:
                error_df.to_excel(writer, sheet_name='All_Issues', index=False)
        
        # Create a URL for the error file
        error_url = f"/download_error_report/{os.path.basename(error_filepath)}"
        return error_url
    except Exception as e:
        print(f"Error creating error report: {e}")
        return None

@app.route("/download_error_report/<filename>")
@admin_required
def download_error_report(filename):
    """Download error report file"""
    file_path = os.path.join(tempfile.gettempdir(), filename)
    
    if not os.path.exists(file_path):
        return "Error report not found", 404
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/admin/download_package_template")
@admin_required
def download_package_template():
    """Download an Excel template for package data matching your actual format"""
    template_data = {
        'ID': [1, 2, 3],
        'TrackingID': ['TRK123456789', 'TRK987654321', 'TRK555555555'],
        'firstName': ['John', 'Jane', 'Mike'],
        'lastName': ['Smith', 'Johnson', 'Williams'],
        'roomNumber': ['101-A', '202-B', '303-C'],
        'hallName': ['Clement Hall', 'Clement Hall', 'Clement Hall'],
        'checkInDate': ['2025-08-10 14:30:00', '2025-08-10 15:45:00', '2025-08-11 10:15:00'],
        'checkinEmpInitials': ['ABC', 'DEF', 'GHI'],
        'package_type': ['Medium Box-Brown', 'Small Pack', 'Large Box-White'],
        'checkoutStatus': [0, 1, 0],
        'checkoutDate': ['', '2025-08-11 16:20:00', ''],
        'checkoutEmpInitials': ['', 'XYZ', ''],
        'perishable': ['no', 'no', 'yes'],
        'notes': ['', 'Fragile', 'Needs refrigeration']
    }
    
    df = pd.DataFrame(template_data)
    
    # Create Excel file in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Package_Template', index=False)
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="package_data_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ----- Import Excel Data -----

@app.route("/import_excel_data", methods=["GET", "POST"])
@login_required
def import_excel_data():
    """Import packages from old Excel system into the database"""
    if request.method == "POST":
        if 'excel_file' not in request.files:
            return render_template("import_excel.html", error="No file selected")
        
        file = request.files['excel_file']
        if file.filename == '':
            return render_template("import_excel.html", error="No file selected")
        
        if not file.filename.endswith(('.csv', '.xlsx')):
            return render_template("import_excel.html", error="Please upload CSV or Excel file")
        
        try:
            # Read the Excel file
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            # Map Excel columns to database fields
            column_mapping = {
                'tracking_id': ['tracking', 'id', 'trackingid', 'tracking_id'],
                'last_name': ['lastname', 'last', 'lname'],
                'first_name': ['firstname', 'first', 'fname'],
                'room_number': ['room', 'roomnumber', 'room_num'],
                'package_type': ['type', 'packagetype', 'size'],
                'checkin_date': ['checkin', 'checkindate', 'date'],
                'checkin_initials': ['checkininitials', 'initials', 'empinitials'],
                'checkout_date': ['checkout', 'checkoutdate'],
                'checkout_initials': ['checkoutinitials', 'checkoutemp'],
                'perishable': ['perishable', 'perish']
            }
            
            # Find actual column names
            actual_columns = {}
            for db_field, possible_names in column_mapping.items():
                for col in df.columns:
                    if col.lower() in possible_names:
                        actual_columns[db_field] = col
                        break
            
            # Validate required columns
            required = ['tracking_id', 'room_number', 'checkin_date', 'checkin_initials']
            missing = [field for field in required if field not in actual_columns]
            if missing:
                return render_template("import_excel.html", 
                                    error=f"Missing required columns: {', '.join(missing)}")
            
            conn = get_conn()
            cur = conn.cursor(dictionary=True)
            
            imported_count = 0
            updated_count = 0
            error_count = 0
            
            for index, row in df.iterrows():
                try:
                    # Extract data
                    tracking_id = normalize_tracking_id(str(row[actual_columns['tracking_id']]))
                    room_number = str(row[actual_columns['room_number']]).strip()
                    checkin_date = row[actual_columns['checkin_date']]
                    checkin_initials = str(row[actual_columns['checkin_initials']]).strip().upper()
                    
                    # Handle dates
                    if isinstance(checkin_date, str):
                        try:
                            checkin_date = datetime.strptime(checkin_date, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            try:
                                checkin_date = datetime.strptime(checkin_date, '%Y-%m-%d')
                            except ValueError:
                                checkin_date = datetime.now()
                    elif pd.isna(checkin_date):
                        checkin_date = datetime.now()
                    
                    # Convert date to MySQL compatible format
                    checkin_date_db = convert_to_mysql_datetime(checkin_date)
                    
                    # Get optional fields
                    package_type = 'Other'
                    if 'package_type' in actual_columns:
                        package_type = str(row[actual_columns['package_type']]).strip()
                        if not package_type or package_type == 'nan':
                            package_type = 'Other'
                    
                    last_name = ''
                    if 'last_name' in actual_columns:
                        last_name = str(row[actual_columns['last_name']]).strip()
                    
                    first_name = ''
                    if 'first_name' in actual_columns:
                        first_name = str(row[actual_columns['first_name']]).strip()
                    
                    perishable = 'no'
                    if 'perishable' in actual_columns:
                        perish_value = str(row[actual_columns['perishable']]).lower()
                        perishable = 'yes' if perish_value in ['yes', 'y', '1', 'true'] else 'no'
                    
                    # Check if already exists in package_log
                    cur.execute("SELECT ID FROM package_log WHERE TrackingID = %s", (tracking_id,))
                    existing_package = cur.fetchone()
                    
                    if not existing_package:
                        # Insert into package_log
                        cur.execute("INSERT INTO package_log (TrackingID, DateTime) VALUES (%s, %s)", 
                                  (tracking_id, checkin_date_db))
                        imported_count += 1
                    else:
                        updated_count += 1
                    
                    # Check if log entry exists
                    cur.execute("SELECT Id FROM postofficelog WHERE trackingId = %s", (tracking_id,))
                    existing_log = cur.fetchone()
                    
                    # Handle checkout status
                    checkout_date = None
                    checkout_initials = None
                    checkout_status = 0
                    
                    if 'checkout_date' in actual_columns:
                        checkout_date = row[actual_columns['checkout_date']]
                        if checkout_date and not pd.isna(checkout_date):
                            if isinstance(checkout_date, str):
                                try:
                                    checkout_date = datetime.strptime(checkout_date, '%Y-%m-%d %H:%M:%S')
                                except ValueError:
                                    try:
                                        checkout_date = datetime.strptime(checkout_date, '%Y-%m-%d')
                                    except ValueError:
                                        checkout_date = datetime.now()
                            checkout_status = 1
                            if 'checkout_initials' in actual_columns:
                                checkout_initials = str(row[actual_columns['checkout_initials']]).strip().upper()
                    
                    # Convert checkout date to MySQL compatible format
                    checkout_date_db = convert_to_mysql_datetime(checkout_date) if checkout_date else None
                    
                    if not existing_log:
                        # Insert into postofficelog
                        cur.execute("""
                            INSERT INTO postofficelog 
                            (trackingId, roomNumber, checkInDate, type, checkInEmpInitials, 
                             checkoutStatus, checkoutDate, checkoutEmpInitials, perishable)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (tracking_id, room_number, checkin_date_db, package_type, checkin_initials,
                             checkout_status, checkout_date_db, checkout_initials, perishable))
                        print(f"Imported package: {tracking_id} for room {room_number}")
                    
                except Exception as e:
                    error_count += 1
                    print(f"Error importing row {index}: {e}")
                    continue
            
            conn.commit()
            cur.close()
            conn.close()
            
            # Update export file
            update_export_file()
            
            success_msg = f"Import completed! Imported: {imported_count}, Updated: {updated_count}, Errors: {error_count}"
            return render_template("import_excel.html", success=success_msg)
            
        except Exception as e:
            import traceback
            print(f"Import error: {traceback.format_exc()}")
            return render_template("import_excel.html", error=f"Error processing file: {str(e)}")
    
    return render_template("import_excel.html")

def find_best_system_match(official_tid, system_map):
    if not official_tid:
        return None, "EMPTY_OFFICIAL_ID"

    if official_tid in system_map:
        return official_tid, "EXACT_MATCH"

    if len(official_tid) < 6:
        return None, "TOO_SHORT_FOR_MATCH"

    candidates = []
    for sys_tid in system_map.keys():
        if official_tid in sys_tid or sys_tid in official_tid:
            candidates.append(sys_tid)

    if len(candidates) == 1:
        return candidates[0], "PARTIAL_MATCH"

    if len(candidates) > 1:
        return None, "AMBIGUOUS_PARTIAL_MATCH"

    return None, "NO_DB_MATCH"


# ----- PO Audit -----

@app.route("/po_audit", methods=["GET", "POST"])
@login_required
def po_audit():
    if request.method == "GET":
        session.pop("audit_file_path", None)
        session.pop("audit_filename", None)

    if request.method == "POST":
        if "audit_file" not in request.files:
            return render_template("po_audit.html", error="No file selected")

        file = request.files["audit_file"]
        if file.filename == "":
            return render_template("po_audit.html", error="No file selected")

        try:
            df = pd.read_excel(file) if not file.filename.lower().endswith(".csv") else pd.read_csv(file)
        except Exception as e:
            return render_template("po_audit.html", error=f"Unable to read file: {str(e)}")

        if df.empty:
            return render_template("po_audit.html", error="Uploaded file is empty")

        df.columns = [str(c).strip() for c in df.columns]

        REQUIRED_COLS = [
            "Item #", "Status", "Fullname (Destination)", "External Carrier",
            "Location", "Internal Carrier", "SENDER", "Print Name", "Date"
        ]

        missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing_cols:
            return render_template(
                "po_audit.html",
                error=f"Missing required columns: {', '.join(missing_cols)}"
            )

        import re

        def extract_tracking(val):
            if val is None:
                return ""
            m = re.search(r"[A-Za-z0-9]{6,}", str(val))
            return m.group(0) if m else ""

        def parse_status(val):
            s = str(val).strip().lower()
            if s == "housing - picked up":
                return 1
            if s == "housing - checked in":
                return 0
            return None

        official = {}
        for _, row in df.iterrows():
            tid = extract_tracking(row["Item #"])
            st = parse_status(row["Status"])
            if tid and st is not None:
                official[tid] = st

        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT trackingId, checkoutStatus, checkoutDate FROM postofficelog")
        system = {}
        for r in cur.fetchall():
            tid = extract_tracking(r["trackingId"])
            if tid:
                system[tid] = 1 if r["checkoutStatus"] == 1 or r["checkoutDate"] else 0
        cur.close()
        conn.close()

        not_checked_out = 0
        auto_checkout_ids = []
        missing = []
        mismatches = []

        for official_tid, official_status in official.items():
            matched_sys_tid, reason = find_best_system_match(official_tid, system)

            if not matched_sys_tid:
                missing.append({
                    "TrackingID": official_tid,
                    "Reason": reason
                })
                continue

            system_status = system[matched_sys_tid]

            # 🔑 ONLY care about Official OUT & System IN
            if official_status == 1 and system_status == 0:
                not_checked_out += 1
                auto_checkout_ids.append(matched_sys_tid)

                mismatches.append({
                    "OfficialTrackingID": official_tid,
                    "MatchedSystemTrackingID": matched_sys_tid,
                    "OfficialStatus": official_status,
                    "SystemStatus": system_status,
                    "Issue": "OFFICIAL_OUT_SYSTEM_IN"
                })

            # ❌ Ignore Official IN & System OUT completely

        # ---------- BUILD AUDIT FILE ----------
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audit_filename = f"po_audit_results_{timestamp}.xlsx"
        audit_filepath = os.path.join(tempfile.gettempdir(), audit_filename)

        with pd.ExcelWriter(audit_filepath, engine="openpyxl") as writer:
            pd.DataFrame([{
                "NotCheckedOutCount": not_checked_out,
                "MissingInSystemCount": len(missing)
            }]).to_excel(writer, sheet_name="Summary", index=False)

            pd.DataFrame(mismatches).to_excel(writer, sheet_name="Status_Mismatches", index=False)
            pd.DataFrame(missing).to_excel(writer, sheet_name="Missing_In_System", index=False)

        session["audit_file_path"] = audit_filepath
        session["audit_filename"] = audit_filename

        return render_template(
            "po_audit.html",
            results={
                "not_checked_out_count": not_checked_out,
                "found_in_both_count": len(official) - len(missing),
                "not_found_in_both_count": len(missing),
            },
            show_auto_checkout_prompt=not_checked_out > 0,
            auto_checkout_count=not_checked_out,
            auto_checkout_ids=",".join(auto_checkout_ids),
            success="Audit complete. Download the results below."
        )

    return render_template("po_audit.html")


@app.route("/po_audit_apply_checkout", methods=["POST"])
@login_required
def po_audit_apply_checkout():
    raw = request.form.get("tracking_ids", "")
    tracking_ids = [t for t in raw.split(",") if t]

    if not tracking_ids:
        return redirect(url_for("po_audit"))

    initials = session.get("user_initials", "").strip().upper()
    if not initials:
        return redirect(url_for("po_audit"))

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    now = datetime.now()
    updated = 0

    for tid in tracking_ids:
        cur.execute("""
            SELECT Id FROM postofficelog
            WHERE trackingId = %s
            ORDER BY Id DESC
            LIMIT 1
        """, (tid,))
        row = cur.fetchone()

        if not row:
            continue

        cur.execute("""
            UPDATE postofficelog
            SET checkoutStatus = 1,
                checkoutDate = %s,
                checkoutEmpInitials = %s
            WHERE Id = %s
        """, (now, initials, row["Id"]))

        updated += 1

    conn.commit()
    cur.close()
    conn.close()

    threading.Thread(target=update_export_file, daemon=True).start()
    socketio.emit("refresh_recent")

    return render_template(
        "po_audit.html",
        success=f"{updated} packages were successfully checked out."
    )

@app.route("/download_audit_results")
@login_required
def download_audit_results():
    """Download the generated audit results file"""
    file_path = session.get('audit_file_path')
    filename = session.get('audit_filename', 'po_audit_results.xlsx')
    
    if not file_path or not os.path.exists(file_path):
        return "Audit results file not found or expired", 404
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



# ----- File Comparison -----

@app.route("/file_comparison", methods=["GET", "POST"])
@login_required
def file_comparison():
    """Compare two files to find differences in tracking IDs"""
    if request.method == "POST":
        if 'file1' not in request.files or 'file2' not in request.files:
            return render_template("po_audit.html", comparison_error="Please select both files")
        
        file1 = request.files['file1']
        file2 = request.files['file2']
        
        if file1.filename == '' or file2.filename == '':
            return render_template("po_audit.html", comparison_error="Please select both files")
        
        if not (file1.filename.endswith(('.csv', '.xlsx')) and file2.filename.endswith(('.csv', '.xlsx'))):
            return render_template("po_audit.html", comparison_error="Please upload CSV or Excel files")
        
        try:
            # Read both files
            def read_tracking_ids(file):
                if file.filename.endswith('.csv'):
                    df = pd.read_csv(file)
                else:  # Excel file
                    df = pd.read_excel(file)
                
                # Find tracking ID column
                tracking_col = None
                for col in df.columns:
                    if 'tracking' in col.lower() or 'id' in col.lower():
                        tracking_col = col
                        break
                
                if not tracking_col:
                    return None, "No tracking ID column found"
                
                tracking_ids = set(df[tracking_col].astype(str).str.strip().dropna())
                return tracking_ids, None
            
            # Get tracking IDs from both files
            file1_ids, error1 = read_tracking_ids(file1)
            file2_ids, error2 = read_tracking_ids(file2)
            
            if error1:
                return render_template("po_audit.html", comparison_error=f"File 1: {error1}")
            if error2:
                return render_template("po_audit.html", comparison_error=f"File 2: {error2}")
            
            # Perform comparisons
            only_in_file1 = file1_ids - file2_ids
            only_in_file2 = file2_ids - file1_ids
            in_both_files = file1_ids & file2_ids
            
            # Generate comparison results file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            comparison_filename = f"file_comparison_results_{timestamp}.xlsx"
            comparison_filepath = os.path.join(tempfile.gettempdir(), comparison_filename)
            
            with pd.ExcelWriter(comparison_filepath, engine='openpyxl') as writer:
                # Sheet 1: Only in File 1
                only_file1_df = pd.DataFrame(list(only_in_file1), columns=['TrackingID_Only_In_File1'])
                only_file1_df.to_excel(writer, sheet_name='Only_In_File1', index=False)
                
                # Sheet 2: Only in File 2
                only_file2_df = pd.DataFrame(list(only_in_file2), columns=['TrackingID_Only_In_File2'])
                only_file2_df.to_excel(writer, sheet_name='Only_In_File2', index=False)
                
                # Sheet 3: In Both Files
                both_files_df = pd.DataFrame(list(in_both_files), columns=['TrackingID_In_Both_Files'])
                both_files_df.to_excel(writer, sheet_name='In_Both_Files', index=False)
                
                # Sheet 4: Summary
                summary_data = {
                    'Metric': [
                        'Total packages in File 1',
                        'Total packages in File 2',
                        'Packages only in File 1',
                        'Packages only in File 2',
                        'Packages in both files'
                    ],
                    'Count': [
                        len(file1_ids),
                        len(file2_ids),
                        len(only_in_file1),
                        len(only_in_file2),
                        len(in_both_files)
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            # Store file path in session for download
            session['comparison_file_path'] = comparison_filepath
            session['comparison_filename'] = comparison_filename
            
            # Prepare results for display
            comparison_results = {
                'file1_count': len(file1_ids),
                'file2_count': len(file2_ids),
                'only_in_file1_count': len(only_in_file1),
                'only_in_file2_count': len(only_in_file2),
                'in_both_count': len(in_both_files),
                'only_in_file1_samples': list(only_in_file1)[:10],
                'only_in_file2_samples': list(only_in_file2)[:10],
            }
            
            return render_template("po_audit.html", 
                                 comparison_results=comparison_results, 
                                 comparison_success="File comparison completed successfully!")
            
        except Exception as e:
            import traceback
            print(f"File Comparison Error: {traceback.format_exc()}")
            return render_template("po_audit.html", comparison_error=f"Error comparing files: {str(e)}")
    
    return render_template("po_audit.html")

@app.route("/download_comparison_results")
@login_required
def download_comparison_results():
    """Download the generated file comparison results"""
    file_path = session.get('comparison_file_path')
    filename = session.get('comparison_filename', 'file_comparison_results.xlsx')
    
    if not file_path or not os.path.exists(file_path):
        return "Comparison results file not found or expired", 404
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ----- Check-In -----

@app.route("/checkin", methods=["GET", "POST"])
@login_required
def checkin():
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    # Fetch ALL rooms with names (NO LIMIT, NO ID ORDERING)
    cur.execute("""
        SELECT
            roomNumber,
            COALESCE(firstName, '') AS firstName,
            COALESCE(lastName, '') AS lastName,
            COALESCE(email, '') AS email
        FROM studentmaster
        WHERE roomNumber IS NOT NULL
          AND roomNumber <> ''
        ORDER BY
            CAST(SUBSTRING_INDEX(roomNumber, '-', 1) AS UNSIGNED),
            roomNumber
    """)
    rooms = cur.fetchall()
    cur.close()
    conn.close()

    if request.method == "POST":
        raw_tracking = (request.form.get("tracking_id") or "").strip()
        room = (request.form.get("roomNumber") or "").strip()
        initials = session.get('user_initials', '').strip().upper()
        perishable = request.form.get("perishable", "no")
        package_type = request.form.get("package_type", "Other")
        notes = (request.form.get("notes") or "").strip()

        # Split tracking IDs by newline / comma / whitespace (works for single too)
        tracking_list = []
        for part in raw_tracking.replace(",", "\n").splitlines():
            tid = normalize_tracking_id(part)
            if tid:
                tracking_list.append(tid)

        # De-dupe while preserving order
        seen = set()
        tracking_ids = []
        for t in tracking_list:
            if t not in seen:
                seen.add(t)
                tracking_ids.append(t)

        if not tracking_ids or not room or not initials:
            return render_template(
                "checkin.html",
                rooms=rooms,
                package_types=PACKAGE_TYPES,
                user_initials=session.get('display_name', ''),
                error="Tracking ID(s), Room Number, and Initials are required."
            )

        # Stop if ANY tracking already exists (safe + predictable)
        duplicates = [t for t in tracking_ids if check_tracking_exists(t)]
        if duplicates:
            return render_template(
                "checkin.html",
                rooms=rooms,
                package_types=PACKAGE_TYPES,
                user_initials=session.get('display_name', ''),
                error=f"These Tracking IDs already exist: {', '.join(duplicates)}"
            )

        # Look up student once (same as your current logic)
        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        cur.execute("""
            SELECT firstName, lastName, email
            FROM studentmaster
            WHERE roomNumber = %s
            LIMIT 1
        """, (room,))
        student = cur.fetchone()

        if not student or not student.get("email"):
            cur.close()
            conn.close()
            return render_template(
                "checkin.html",
                rooms=rooms,
                package_types=PACKAGE_TYPES,
                user_initials=session.get('display_name', ''),
                error="Selected room does not have a valid student email."
            )

        student_name = f"{student['firstName']} {student['lastName']}"
        student_email = student['email']

        check_dt = datetime.now()

        # Insert ALL tracking IDs (same room/type/etc)
        for tid in tracking_ids:
            cur.execute(
                "INSERT INTO package_log (TrackingID, DateTime) VALUES (%s,%s)",
                (tid, check_dt)
            )
            cur.execute("""
                INSERT INTO postofficelog
                (trackingId, roomNumber, checkInDate, type, checkInEmpInitials, checkoutStatus, perishable, notes)
                VALUES (%s,%s,%s,%s,%s,0,%s,%s)
            """, (tid, room, check_dt, package_type, initials, perishable, notes))

        conn.commit()
        cur.close()
        conn.close()

        # Send ONE email (same behavior pattern; not spamming 5 emails for 5 packages)
        import threading

        def send_async_email():
            try:
                send_package_email(student_email, student_name, room)
            except Exception as e:
                print("EMAIL ERROR:", e)

        threading.Thread(target=send_async_email, daemon=True).start()
        threading.Thread(target=update_export_file, daemon=True).start()

        socketio.emit("refresh_recent")

        if len(tracking_ids) == 1:
            msg = f"Package {tracking_ids[0]} checked in successfully!"
        else:
            msg = f"{len(tracking_ids)} packages checked in successfully!"

        return render_template(
            "checkin.html",
            rooms=rooms,
            package_types=PACKAGE_TYPES,
            user_initials=session.get('display_name', ''),
            success=msg
        )

    return render_template(
        "checkin.html",
        rooms=rooms,
        package_types=PACKAGE_TYPES,
        user_initials=session.get('display_name', '')
    )



# ----- Delete Entry -----

@app.route("/delete_entry", methods=["POST"])
@login_required
def delete_entry():
    log_id = request.form.get("log_id")
    if not log_id:
        return jsonify({"error": "Missing log_id"}), 400

    conn = get_conn()
    cur = conn.cursor(buffered=True)
    
    try:
        # First get the tracking ID before deleting
        cur.execute("SELECT trackingId FROM postofficelog WHERE Id=%s", (log_id,))
        result = cur.fetchone() 
        tracking_id = result[0] if result else None
        
        # Delete from postofficelog
        cur.execute("DELETE FROM postofficelog WHERE Id=%s", (log_id,))
        
        # Also delete from package_log if no other references exist
        if tracking_id:
            cur.execute("SELECT COUNT(*) FROM postofficelog WHERE trackingId=%s", (tracking_id,))
            count_result = cur.fetchone() 
            count = count_result[0] if count_result else 0
            if count == 0:
                cur.execute("DELETE FROM package_log WHERE TrackingID=%s", (tracking_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Update export file
        update_export_file()
        
        socketio.emit("refresh_recent")
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 500

# ----- Export CSV -----

@app.route("/export_csv")
@login_required
def export_csv():
    from io import BytesIO
    import pandas as pd
    from flask import send_file
    from datetime import datetime

    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)

    cur.execute("""
        SELECT
            l.Id AS logId,
            l.trackingId AS TrackingID,
            COALESCE(s.firstName, '') AS firstName,
            COALESCE(s.lastName, '') AS lastName,
            l.roomNumber,
            l.checkInDate,
            l.checkInEmpInitials,
            l.type AS package_type,
            l.checkoutStatus,
            l.checkoutDate,
            l.checkoutEmpInitials,
            l.perishable,
            l.notes
        FROM postofficelog l
        LEFT JOIN studentmaster s
            ON s.roomNumber = l.roomNumber
        ORDER BY l.checkInDate DESC, l.Id DESC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    df = pd.DataFrame(rows)

    # Ensure datetime
    df["checkInDate"] = pd.to_datetime(df["checkInDate"])

    # Month helpers
    df["month_key"] = df["checkInDate"].dt.strftime("%Y-%m")
    df["month_label"] = df["checkInDate"].dt.strftime("%B %Y")

    current_month_key = datetime.now().strftime("%Y-%m")

    output = BytesIO()

    # ✅ Use existing Excel writer behavior
    with pd.ExcelWriter(output) as writer:

        # ----------------------------
        # 1. ALL PACKAGES (existing)
        # ----------------------------
        df.drop(columns=["month_key", "month_label"]).to_excel(
            writer, index=False, sheet_name="ALL_PACKAGES"
        )

        # ----------------------------
        # 2. CURRENT MONTH
        # ----------------------------
        current_df = df[df["month_key"] == current_month_key]
        if not current_df.empty:
            current_df.drop(columns=["month_key", "month_label"]).to_excel(
                writer, index=False, sheet_name="CURRENT_MONTH"
            )

        # ----------------------------
        # 3. MONTHLY SHEETS (newest → oldest)
        # ----------------------------
        for month_key in sorted(df["month_key"].unique(), reverse=True):
            month_df = df[df["month_key"] == month_key]
            month_label = month_df["month_label"].iloc[0]

            month_df.drop(columns=["month_key", "month_label"]).to_excel(
                writer,
                index=False,
                sheet_name=month_label[:31]
            )

        # ----------------------------
        # 4. SUMMARY (last tab)
        # ----------------------------
        summary_df = (
            df.groupby("month_label")
            .agg(
                total_packages=("logId", "count"),
                checked_out=("checkoutStatus", lambda x: (x == 1).sum()),
                pending=("checkoutStatus", lambda x: (x == 0).sum())
            )
            .reset_index()
            .rename(columns={"month_label": "Month"})
        )

        summary_df.to_excel(
            writer, index=False, sheet_name="SUMMARY"
        )

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="packages_export.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def test_database_connection():
    """Test if we can connect to the database"""
    try:
        print("Testing database connection...")
        print(f"Using config: {DB_CONFIG}")
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Test basic queries
        cursor.execute("SELECT DATABASE()")
        db_name = cursor.fetchone()
        print(f"Connected to database: {db_name[0]}")
        
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        print(f"Available tables: {[table[0] for table in tables]}")
        
        # Check if initialscheck table has data
        cursor.execute("SELECT COUNT(*) FROM initialscheck")
        count = cursor.fetchone()
        print(f"Initials records count: {count[0]}")
        
        cursor.close()
        conn.close()
        print("Database connection test PASSED!")
        return True
        
    except mysql.connector.Error as e:
        print(f"DATABASE CONNECTION FAILED:")
        print(f"   Error: {e}")
        print(f"   Error Code: {e.errno}")
        print(f"   SQL State: {e.sqlstate}")
        return False

# Add these routes after the existing routes
@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if not session.get('temporary_password'):
        return redirect(url_for('checkin'))
    
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        if not all([current_password, new_password, confirm_password]):
            return render_template("change_password.html", error="All fields are required")
        
        if new_password != confirm_password:
            return render_template("change_password.html", error="New passwords do not match")
        
        if len(new_password) < 8:
            return render_template("change_password.html", error="Password must be at least 8 characters")
        
        try:
            conn = get_conn()
            cur = conn.cursor(dictionary=True)
            
            # Get current user
            cur.execute("SELECT password_hash FROM initialscheck WHERE id = %s", (session['user_id'],))
            user = cur.fetchone()
            
            # Verify current password
            if not verify_password(current_password, user['password_hash']):
                cur.close()
                conn.close()
                return render_template("change_password.html", error="Current password is incorrect")
            
            # Update password
            new_password_hash = hash_password(new_password)
            cur.execute("""
                UPDATE initialscheck 
                SET password_hash = %s, temporary_password = FALSE, last_password_change = NOW()
                WHERE id = %s
            """, (new_password_hash.decode('utf-8'), session['user_id']))
            conn.commit()
            
            # Log password change
            log_audit(session['user_id'], 'PASSWORD_CHANGED', 'Changed temporary password', request)
            
            cur.close()
            conn.close()
            
            # Update session
            session['temporary_password'] = False
            flash("Password changed successfully!", "success")
            return redirect(url_for('checkin'))
            
        except Exception as e:
            print(f"PASSWORD CHANGE ERROR: {e}")
            return render_template("change_password.html", error=f"System error: {str(e)}")
    
    return render_template("change_password.html")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def user_profile():
    """Allow users to edit their own info"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get current user info
        cur.execute("""
            SELECT i.*, h.hall_name, h.hall_code
            FROM initialscheck i
            JOIN halls h ON i.hall_id = h.id
            WHERE i.id = %s
        """, (session['user_id'],))
        user = cur.fetchone()
        
        if not user:
            flash("User not found", "error")
            return redirect(url_for('checkin'))
        
        if request.method == "POST":
            action = request.form.get("action")
            
            if action == "update_info":
                # Update user info
                new_username = request.form.get("username", "").strip()
                new_initials = request.form.get("initials", "").strip().upper()
                new_fullname = request.form.get("fullname", "").strip()
                
                # Check if username changed and if it's unique
                if new_username != user['username']:
                    cur.execute("SELECT id FROM initialscheck WHERE username = %s AND hall_id = %s AND id != %s",
                               (new_username, user['hall_id'], user['id']))
                    if cur.fetchone():
                        flash("Username already exists in your hall", "error")
                        cur.close()
                        conn.close()
                        return render_template("profile.html", user=user)
                
                # Update user
                cur.execute("""
                    UPDATE initialscheck 
                    SET username = %s, initials = %s, fullName = %s
                    WHERE id = %s
                """, (new_username, new_initials, new_fullname, user['id']))
                
                conn.commit()
                
                # Update session
                session['username'] = new_username
                session['user_initials'] = new_initials
                session['user_full_name'] = new_fullname
                
                flash("Profile updated successfully!", "success")
                return redirect(url_for('user_profile'))
                
            elif action == "change_password":
                # Change password
                current_password = request.form.get("current_password", "")
                new_password = request.form.get("new_password", "")
                confirm_password = request.form.get("confirm_password", "")
                
                # Verify current password
                if not verify_password(current_password, user['password_hash']):
                    flash("Current password is incorrect", "error")
                    cur.close()
                    conn.close()
                    return render_template("profile.html", user=user)
                
                if new_password != confirm_password:
                    flash("New passwords do not match", "error")
                    cur.close()
                    conn.close()
                    return render_template("profile.html", user=user)
                
                if len(new_password) < 8:
                    flash("Password must be at least 8 characters", "error")
                    cur.close()
                    conn.close()
                    return render_template("profile.html", user=user)
                
                # Update password
                new_password_hash = hash_password(new_password)
                cur.execute("""
                    UPDATE initialscheck 
                    SET password_hash = %s, temporary_password = FALSE, last_password_change = NOW()
                    WHERE id = %s
                """, (new_password_hash.decode('utf-8'), user['id']))
                
                conn.commit()
                
                # Update session
                session['temporary_password'] = False
                
                flash("Password changed successfully!", "success")
                return redirect(url_for('user_profile'))
        
        cur.close()
        conn.close()
        
        return render_template("profile.html", user=user)
        
    except Exception as e:
        print(f"USER PROFILE ERROR: {e}")
        flash(f"Error: {str(e)}", "error")
        return redirect(url_for('checkin'))

def handle_username_change():
    """Handle username change request"""
    new_username = request.form.get("new_username", "").strip()
    password = request.form.get("password", "")
    
    if not new_username:
        return render_template("profile.html", error="New username is required")
    
    if not password:
        return render_template("profile.html", error="Password is required to change username")
    
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Verify current password first
        cur.execute("SELECT password_hash FROM initialscheck WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        if not verify_password(password, user['password_hash']):
            cur.close()
            conn.close()
            return render_template("profile.html", error="Current password is incorrect")
        
        # Check if new username already exists in the same hall
        cur.execute("SELECT id FROM initialscheck WHERE username = %s AND hall_id = %s AND id != %s", 
                   (new_username, session['user_hall_id'], session['user_id']))
        if cur.fetchone():
            cur.close()
            conn.close()
            return render_template("profile.html", error="Username already exists in your hall")
        
        # Update username
        cur.execute("UPDATE initialscheck SET username = %s WHERE id = %s", 
                   (new_username, session['user_id']))
        conn.commit()
        
        # Log the action
        log_audit(session['user_id'], 'USERNAME_CHANGED', 
                 f"Changed username to {new_username}", request)
        
        cur.close()
        conn.close()
        
        # Update session
        session.pop('username', None)
        flash("Username changed successfully! Please login again with your new username.", "success")
        return redirect(url_for('logout'))
        
    except Exception as e:
        print(f"USERNAME CHANGE ERROR: {e}")
        return render_template("profile.html", error=f"System error: {str(e)}")

def handle_password_change():
    """Handle password change request (already implemented, but using in profile)"""
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    
    if not all([current_password, new_password, confirm_password]):
        return render_template("profile.html", error="All password fields are required")
    
    if new_password != confirm_password:
        return render_template("profile.html", error="New passwords do not match")
    
    if len(new_password) < 8:
        return render_template("profile.html", error="Password must be at least 8 characters")
    
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get current user
        cur.execute("SELECT password_hash FROM initialscheck WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        
        # Verify current password
        if not verify_password(current_password, user['password_hash']):
            cur.close()
            conn.close()
            return render_template("profile.html", error="Current password is incorrect")
        
        # Update password
        new_password_hash = hash_password(new_password)
        cur.execute("""UPDATE initialscheck 
                      SET password_hash = %s, temporary_password = FALSE, last_password_change = NOW()
                      WHERE id = %s""", 
                   (new_password_hash.decode('utf-8'), session['user_id']))
        conn.commit()
        
        # Log password change
        log_audit(session['user_id'], 'PASSWORD_CHANGED', 'Changed password', request)
        
        cur.close()
        conn.close()
        
        # Update session
        session['temporary_password'] = False
        flash("Password changed successfully!", "success")
        return redirect(url_for('user_profile'))
        
    except Exception as e:
        print(f"PASSWORD CHANGE ERROR: {e}")
        return render_template("profile.html", error=f"System error: {str(e)}")
    
def ensure_shift_schedule_table():
    """Create shift_schedule table if it doesn't exist."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shift_schedule (
                id INT AUTO_INCREMENT PRIMARY KEY,
                hall_id INT NOT NULL,
                user_id INT NOT NULL,
                shift_date DATE NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                shift_type VARCHAR(50) DEFAULT 'Regular',
                notes TEXT,
                created_by INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (hall_id) REFERENCES halls(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES initialscheck(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES initialscheck(id) ON DELETE SET NULL
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("shift_schedule table verified/created")
        return True
    except Exception as e:
        print(f"ensure_shift_schedule_table error: {e}")
        import traceback
        traceback.print_exc()
        return False

@app.route("/admin/halls", endpoint='admin_halls')
@login_required
@permission_required('can_manage_halls')
def admin_halls_list():
    """List all halls"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Check if created_by column exists
        cur.execute("SHOW COLUMNS FROM halls LIKE 'created_by'")
        has_created_by = cur.fetchone()
        
        if has_created_by:
            # Query with created_by join
            cur.execute("""
                SELECT h.*, COUNT(i.id) as user_count,
                       creator.initials as created_by_initials
                FROM halls h
                LEFT JOIN initialscheck i ON h.id = i.hall_id
                LEFT JOIN initialscheck creator ON h.created_by = creator.id
                GROUP BY h.id
                ORDER BY h.hall_name
            """)
        else:
            # Query without created_by
            cur.execute("""
                SELECT h.*, COUNT(i.id) as user_count,
                       NULL as created_by_initials
                FROM halls h
                LEFT JOIN initialscheck i ON h.id = i.hall_id
                GROUP BY h.id
                ORDER BY h.hall_name
            """)
        
        halls = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return render_template("admin_halls.html", halls=halls)
        
    except Exception as e:
        print(f"HALL MANAGEMENT ERROR: {e}")
        flash("Error loading halls", "error")
        return redirect(url_for('checkin'))

@app.route("/admin/halls/add", methods=["GET", "POST"])
@login_required
@permission_required('can_manage_halls')
def admin_halls_add():
    """Add a new hall"""
    if request.method == "GET":
        # Just render the form
        return render_template("admin_halls_add.html")
    
    elif request.method == "POST":
        hall_name = request.form.get("hall_name", "").strip()
        hall_code = request.form.get("hall_code", "").strip().upper()
        
        if not hall_name or not hall_code:
            flash("All fields are required", "error")
            return redirect(url_for('admin_halls_add'))
        
        if len(hall_code) > 10:
            flash("Hall code must be 10 characters or less", "error")
            return redirect(url_for('admin_halls_add'))
        
        try:
            conn = get_conn()
            cur = conn.cursor()
            
            # Check if hall code already exists
            cur.execute("SELECT id FROM halls WHERE hall_code = %s", (hall_code,))
            if cur.fetchone():
                flash("Hall code already exists", "error")
                cur.close()
                conn.close()
                return redirect(url_for('admin_halls_add'))
            
            # Check if created_by column exists
            cur.execute("SHOW COLUMNS FROM halls LIKE 'created_by'")
            has_created_by = cur.fetchone()
            
            if has_created_by:
                # Insert with created_by
                cur.execute("""
                    INSERT INTO halls (hall_name, hall_code, created_by)
                    VALUES (%s, %s, %s)
                """, (hall_name, hall_code, session['user_id']))
            else:
                # Insert without created_by
                cur.execute("""
                    INSERT INTO halls (hall_name, hall_code)
                    VALUES (%s, %s)
                """, (hall_name, hall_code))
            
            hall_id = cur.lastrowid
            
            # Create default admin user for the new hall
            admin_username = f"admin_{hall_code.lower()}"
            temp_password = generate_temp_password()
            password_hash = hash_password(temp_password)
            
            cur.execute("""
                INSERT INTO initialscheck 
                (hall_id, username, initials, fullName, title, password_hash,
                 can_checkin, can_checkout, can_view_other_halls,
                 can_manage_users, can_manage_halls, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, TRUE, TRUE, TRUE, TRUE, %s)
            """, (hall_id, admin_username, 'ADM', 'Hall Administrator', 'HD',
                 password_hash.decode('utf-8'), session['user_id']))
            
            conn.commit()
            
            # Log the action
            log_audit(session['user_id'], 'HALL_CREATED', 
                     f"Created hall {hall_name} ({hall_code})", request)
            
            cur.close()
            conn.close()
            
            flash(f"Hall created successfully! Admin credentials: {admin_username} / {temp_password}", "success")
            return redirect(url_for('admin_halls'))
            
        except Exception as e:
            print(f"ADD HALL ERROR: {e}")
            flash(f"Error creating hall: {str(e)}", "error")
            return redirect(url_for('admin_halls_add'))

# User Management
@app.route("/admin/users")
@login_required
@permission_required('can_manage_users')
def admin_users():  
    """List all users in the current hall"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        cur.execute("""
            SELECT i.id, i.username, i.initials, i.fullName, i.title, 
                   i.temporary_password, i.can_checkin, i.can_checkout,
                   i.can_view_other_halls, i.can_manage_users, i.can_manage_halls,
                   i.created_at, creator.initials as created_by_initials
            FROM initialscheck i
            LEFT JOIN initialscheck creator ON i.created_by = creator.id
            WHERE i.hall_id = %s
            ORDER BY i.title DESC, i.initials ASC
        """, (session['user_hall_id'],))
        
        users = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return render_template("admin_users.html", users=users)
        
    except Exception as e:
        print(f"USER MANAGEMENT ERROR: {e}")
        flash("Error loading users", "error")
        return redirect(url_for('checkin'))
    
@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required('can_manage_users')
def admin_edit_user(user_id):
    """Admin can edit any user's username and reset their password"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get the user to edit
        cur.execute("""
            SELECT i.*, h.hall_name 
            FROM initialscheck i 
            JOIN halls h ON i.hall_id = h.id
            WHERE i.id = %s
        """, (user_id,))
        
        user = cur.fetchone()
        
        if not user:
            flash("User not found", "error")
            return redirect(url_for('admin_users'))
        
        # Check if admin has permission to edit this user (same hall)
        if user['hall_id'] != session['user_hall_id'] and not session.get('can_view_other_halls'):
            flash("You don't have permission to edit users from other halls", "error")
            return redirect(url_for('admin_users'))
        
        if request.method == "POST":
            action = request.form.get("action")
            
            if action == "change_username":
                new_username = request.form.get("new_username", "").strip()
                
                if not new_username:
                    flash("Username is required", "error")
                    return render_template("admin_edit_user.html", user=user)
                
                # Check if username already exists in the same hall
                cur.execute("SELECT id FROM initialscheck WHERE username = %s AND hall_id = %s AND id != %s", 
                           (new_username, user['hall_id'], user_id))
                if cur.fetchone():
                    flash("Username already exists in this hall", "error")
                    return render_template("admin_edit_user.html", user=user)
                
                # Update username
                cur.execute("UPDATE initialscheck SET username = %s WHERE id = %s", 
                           (new_username, user_id))
                conn.commit()
                
                # Log the action
                log_audit(session['user_id'], 'ADMIN_USERNAME_CHANGE', 
                         f"Changed username for user {user['username']} to {new_username}", request)
                
                flash("Username updated successfully", "success")
                return redirect(url_for('admin_users'))
                
            elif action == "reset_password":
                # Generate temporary password
                temp_password = generate_temp_password()
                password_hash = hash_password(temp_password)
                
                # Reset password to temporary
                cur.execute("""
                    UPDATE initialscheck 
                    SET password_hash = %s, temporary_password = TRUE, last_password_change = NOW()
                    WHERE id = %s
                """, (password_hash.decode('utf-8'), user_id))
                conn.commit()
                
                # Log the action
                log_audit(session['user_id'], 'ADMIN_PASSWORD_RESET', 
                         f"Reset password for user {user['username']}", request)
                
                flash(f"Password reset! Temporary password: {temp_password}", "success")
                return redirect(url_for('admin_users'))
                
            elif action == "update_permissions":
                # Update user permissions
                can_checkin = 1 if request.form.get("can_checkin") else 0
                can_checkout = 1 if request.form.get("can_checkout") else 0
                can_view_other_halls = 1 if request.form.get("can_view_other_halls") else 0
                can_manage_users = 1 if request.form.get("can_manage_users") else 0
                can_manage_halls = 1 if request.form.get("can_manage_halls") else 0
                is_active = 1 if request.form.get("is_active") else 0
                
                cur.execute("""
                    UPDATE initialscheck 
                    SET can_checkin = %s, can_checkout = %s, can_view_other_halls = %s,
                        can_manage_users = %s, can_manage_halls = %s, is_active = %s
                    WHERE id = %s
                """, (can_checkin, can_checkout, can_view_other_halls, 
                      can_manage_users, can_manage_halls, is_active, user_id))
                conn.commit()
                
                log_audit(session['user_id'], 'ADMIN_PERMISSIONS_UPDATE', 
                         f"Updated permissions for user {user['username']}", request)
                
                flash("Permissions updated successfully", "success")
                return redirect(url_for('admin_users'))
        
        cur.close()
        conn.close()
        
        return render_template("admin_edit_user.html", user=user)
        
    except Exception as e:
        print(f"ADMIN EDIT USER ERROR: {e}")
        flash("Error editing user", "error")
        return redirect(url_for('admin_users'))
    
# Add these routes to WebApp.py
# ========== USER MANAGEMENT ROUTES ==========

@app.route("/admin/user_management", methods=["GET"])
@login_required
@permission_required('can_manage_users')
def admin_user_management():
    """Load user management page - for admins"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get all halls for dropdown
        cur.execute("SELECT id, hall_code, hall_name FROM halls ORDER BY hall_code")
        halls = cur.fetchall()
        
        # Get selected hall (default to user's hall)
        selected_hall_id = request.args.get('hall_id', session.get('user_hall_id'))
        
        # Get selected hall info
        cur.execute("SELECT * FROM halls WHERE id = %s", (selected_hall_id,))
        selected_hall = cur.fetchone()
        
        # Get users for selected hall
        users = []
        if selected_hall:
            cur.execute("""
                SELECT i.*, h.hall_name, h.hall_code,
                       CASE 
                           WHEN i.title = 'HD' THEN 'Hall Director'
                           WHEN i.title = 'AHD' THEN 'Assistant Hall Director'
                           WHEN i.title = 'OA' THEN 'Office Assistant'
                           ELSE i.title
                       END as role_description
                FROM initialscheck i
                JOIN halls h ON i.hall_id = h.id
                WHERE i.hall_id = %s
                ORDER BY i.title DESC, i.username
            """, (selected_hall_id,))
            users = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return render_template("user_management.html",
                             halls=halls,
                             selected_hall=selected_hall,
                             users=users)
        
    except Exception as e:
        print(f"USER MANAGEMENT ERROR: {e}")
        flash("Error loading user management", "error")
        return redirect(url_for('checkin'))
    
@app.route("/admin/user_management/create", methods=["POST"])
@login_required
@permission_required('can_manage_users')
def admin_create_user():
    """Create a new user via web interface"""
    try:
        hall_id = request.form.get('hall_id')
        username = request.form.get('username', '').strip()
        initials = request.form.get('initials', '').strip().upper()
        fullname = request.form.get('fullname', '').strip()
        title = request.form.get('title', 'OA').strip().upper()
        password_type = request.form.get('password_type', 'temporary')
        
        # Validate inputs
        if not all([hall_id, username, initials, fullname, title]):
            flash("All fields are required", "error")
            return redirect(url_for('admin_user_management', hall_id=hall_id))
        
        if len(initials) < 2 or len(initials) > 4:
            flash("Initials must be 2-4 letters", "error")
            return redirect(url_for('admin_user_management', hall_id=hall_id))
        
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Check if username exists in this hall
        cur.execute("SELECT id FROM initialscheck WHERE hall_id = %s AND username = %s", 
                   (hall_id, username))
        if cur.fetchone():
            flash("Username already exists in this hall", "error")
            cur.close()
            conn.close()
            return redirect(url_for('admin_user_management', hall_id=hall_id))
        
        # Generate password
        if password_type == 'temporary':
            password = generate_temp_password()
            temp_password = True
        else:
            password = request.form.get('password', '')
            if len(password) < 8:
                flash("Password must be at least 8 characters", "error")
                cur.close()
                conn.close()
                return redirect(url_for('admin_user_management', hall_id=hall_id))
            temp_password = False
        
        # Set permissions based on role
        if title in ['HD', 'AHD']:
            can_checkin = can_checkout = can_view_other_halls = can_manage_users = can_manage_halls = True
        elif title == 'OA':
            can_checkin = can_checkout = True
            can_view_other_halls = can_manage_users = can_manage_halls = False
        else:
            # For custom roles, use form values
            can_checkin = 'can_checkin' in request.form
            can_checkout = 'can_checkout' in request.form
            can_view_other_halls = 'can_view_other_halls' in request.form
            can_manage_users = 'can_manage_users' in request.form
            can_manage_halls = 'can_manage_halls' in request.form
        
        # Hash password
        password_hash = hash_password(password)
        
        # Insert user
        cur.execute("""
            INSERT INTO initialscheck 
            (hall_id, username, initials, fullName, title, password_hash,
             can_checkin, can_checkout, can_view_other_halls,
             can_manage_users, can_manage_halls, is_active, temporary_password, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        """, (hall_id, username, initials, fullname, title, password_hash.decode('utf-8'),
              can_checkin, can_checkout, can_view_other_halls,
              can_manage_users, can_manage_halls, temp_password, session['user_id']))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Log the action
        log_audit(session['user_id'], 'USER_CREATED', 
                 f"Created user {username} in hall {hall_id}", request)
        
        # If temporary password, show it to admin
        message = f"User {username} created successfully!"
        if temp_password:
            message += f" Temporary password: {password}"
        
        flash(message, "success")
        return redirect(url_for('admin_user_management', hall_id=hall_id))
        
    except Exception as e:
        print(f"CREATE USER ERROR: {e}")
        flash(f"Error creating user: {str(e)}", "error")
        return redirect(url_for('admin_user_management', hall_id=hall_id))

@app.route("/admin/user_management/edit/<int:user_id>", methods=["GET", "POST"])
@login_required
@permission_required('can_manage_users')
def admin_edit_user_web(user_id):
    """Edit user via web interface"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get user
        cur.execute("""
            SELECT i.*, h.hall_name, h.hall_code
            FROM initialscheck i
            JOIN halls h ON i.hall_id = h.id
            WHERE i.id = %s
        """, (user_id,))
        user = cur.fetchone()
        
        if not user:
            flash("User not found", "error")
            return redirect(url_for('admin_user_management'))
        
        if request.method == "POST":
            # Default to update_info because the form doesn't send "action"
            action = request.form.get('action', 'update_info')

            if action == 'update_info':
                new_username = request.form.get('username', '').strip()
                initials = request.form.get('initials', '').strip().upper()
                fullname = request.form.get('fullname', '').strip()
                title = request.form.get('title', '').strip().upper()
                is_active = (request.form.get('is_active', '1') == '1')

                # Password update (only if admin typed one)
                new_password = (request.form.get('password') or '').strip()
                if new_password:
                    if len(new_password) < 8:
                        flash("Password must be at least 8 characters.", "error")
                        return redirect(url_for('admin_edit_user_web', user_id=user_id))

                    hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    cur.execute("""
                        UPDATE initialscheck
                        SET password_hash=%s,
                            temporary_password=0,
                            last_password_change=NOW()
                        WHERE id=%s
                    """, (hashed, user_id))

                # Permissions logic
                if title in ['HD', 'AHD']:
                    can_checkin = can_checkout = can_view_other_halls = can_manage_users = can_manage_halls = True
                elif title == 'OA':
                    can_checkin = can_checkout = True
                    can_view_other_halls = can_manage_users = can_manage_halls = False
                else:
                    can_checkin = 'can_checkin' in request.form
                    can_checkout = 'can_checkout' in request.form
                    can_view_other_halls = 'can_view_other_halls' in request.form
                    can_manage_users = 'can_manage_users' in request.form
                    can_manage_halls = 'can_manage_halls' in request.form

                cur.execute("""
                    UPDATE initialscheck
                    SET username=%s, initials=%s, fullName=%s, title=%s,
                        can_checkin=%s, can_checkout=%s, can_view_other_halls=%s,
                        can_manage_users=%s, can_manage_halls=%s, is_active=%s
                    WHERE id=%s
                """, (new_username, initials, fullname, title,
                    can_checkin, can_checkout, can_view_other_halls,
                    can_manage_users, can_manage_halls, is_active, user_id))

                conn.commit()
                log_audit(session['user_id'], 'USER_UPDATED', f"Updated user {user['username']}", request)
                flash("User updated successfully", "success")

                
            elif action == 'reset_password':
                # Reset password
                password_type = request.form.get('password_type_reset', 'temporary')
                
                if password_type == 'temporary':
                    new_password = generate_temp_password()
                    temp_password = True
                else:
                    new_password = request.form.get('new_password', '')
                    if len(new_password) < 8:
                        flash("Password must be at least 8 characters", "error")
                        return redirect(url_for('admin_edit_user_web', user_id=user_id))
                    temp_password = False
                
                password_hash = hash_password(new_password)
                
                cur.execute("""
                    UPDATE initialscheck 
                    SET password_hash = %s, temporary_password = %s, last_password_change = NOW()
                    WHERE id = %s
                """, (password_hash.decode('utf-8'), temp_password, user_id))
                
                conn.commit()
                log_audit(session['user_id'], 'PASSWORD_RESET', 
                         f"Reset password for user {user['username']}", request)
                
                message = "Password reset successfully!"
                if temp_password:
                    message += f" Temporary password: {new_password}"
                flash(message, "success")
            
            return redirect(url_for('admin_edit_user_web', user_id=user_id))
        
        cur.close()
        conn.close()
        
        return render_template("admin_edit_user_web.html", user=user)
        
    except Exception as e:
        print(f"EDIT USER ERROR: {e}")
        flash(f"Error editing user: {str(e)}", "error")
        return redirect(url_for('admin_user_management'))

@app.route("/admin/user_management/delete/<int:user_id>", methods=["POST"])
@login_required
@permission_required('can_manage_users')
def admin_delete_user(user_id):
    """Delete user via web interface"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get user info for logging
        cur.execute("SELECT username, hall_id FROM initialscheck WHERE id = %s", (user_id,))
        user = cur.fetchone()
        
        if user:
            # Delete user
            cur.execute("DELETE FROM initialscheck WHERE id = %s", (user_id,))
            conn.commit()
            
            log_audit(session['user_id'], 'USER_DELETED', 
                     f"Deleted user {user['username']}", request)
            
            flash(f"User {user['username']} deleted successfully", "success")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"DELETE USER ERROR: {e}")
        flash("Error deleting user", "error")
    
    return redirect(url_for('admin_user_management'))

@app.route('/admin/halls/delete/<int:hall_id>', methods=['POST'])
@admin_required
def admin_delete_hall(hall_id):
    if not session.get('can_manage_halls'):
        flash("Unauthorized action", "error")
        return redirect(url_for('admin_halls'))

    conn = get_conn()
    cur = conn.cursor(dictionary=True)

    # Prevent deleting halls that still have users
    cur.execute("SELECT COUNT(*) AS cnt FROM initialscheck WHERE hall_id=%s", (hall_id,))
    if cur.fetchone()['cnt'] > 0:
        cur.close()
        conn.close()
        flash("Cannot delete hall with active users. Remove users first.", "error")
        return redirect(url_for('admin_halls'))

    cur.execute("DELETE FROM halls WHERE id=%s", (hall_id,))
    conn.commit()

    cur.close()
    conn.close()
    flash("Hall deleted successfully.", "success")
    return redirect(url_for('admin_halls'))


# ========== SHIFT CHANGE REQUESTS ==========

def ensure_shift_schedule_table():
    """Create shift_schedule table if it doesn't exist."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        
        # Check if table exists
        cur.execute("SHOW TABLES LIKE 'shift_schedule'")
        if not cur.fetchone():
            print("Creating shift_schedule table...")
            cur.execute("""
                CREATE TABLE shift_schedule (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    hall_id INT NOT NULL,
                    user_id INT NOT NULL,
                    shift_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    shift_type VARCHAR(50) DEFAULT 'Regular',
                    notes TEXT,
                    created_by INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (hall_id) REFERENCES halls(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES initialscheck(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES initialscheck(id) ON DELETE SET NULL
                )
            """)
            print("shift_schedule table created successfully")
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"ensure_shift_schedule_table error: {e}")
        import traceback
        traceback.print_exc()

@app.route("/submit_shift_request", methods=["POST"])
@login_required
def submit_shift_request():
    """User submits a request to admins (HD/AHD) about schedule changes."""
    try:
        ensure_shift_schedule_table()

        message = (request.form.get("message") or "").strip()
        if not message:
            flash("Please enter a request message.", "error")
            return redirect(url_for("my_schedule"))

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO shift_change_requests (hall_id, user_id, message)
            VALUES (%s, %s, %s)
        """, (session.get("user_hall_id"), session.get("user_id"), message))
        conn.commit()
        cur.close()
        conn.close()

        # Live update to admins in this hall
        try:
            socketio.emit("new_shift_request", {
                "hall_id": session.get("user_hall_id"),
                "user": session.get("display_name"),
                "message": message,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
            }, room=f"admin_hall_{session.get('user_hall_id')}")
        except Exception:
            pass

        flash("Request sent to admins.", "success")
        return redirect(url_for("my_schedule"))

    except Exception as e:
        print(f"submit_shift_request error: {e}")
        flash("Error submitting request.", "error")
        return redirect(url_for("my_schedule"))

@app.route("/admin/requests")
@login_required
@admin_required
def admin_requests():
    """Admin view: see all requests for this hall."""
    try:
        ensure_shift_schedule_table()

        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT r.id, r.message, r.status, r.created_at,
                   i.initials, i.fullName
            FROM shift_change_requests r
            JOIN initialscheck i ON r.user_id = i.id
            WHERE r.hall_id = %s
            ORDER BY r.created_at DESC
        """, (session.get("user_hall_id"),))
        requests_list = cur.fetchall()
        cur.close()
        conn.close()

        return render_template("admin_requests.html", requests=requests_list)

    except Exception as e:
        print(f"admin_requests error: {e}")
        flash("Error loading requests.", "error")
        return redirect(url_for("checkin"))

@app.route("/admin/requests/<int:req_id>/close", methods=["POST"])
@login_required
@admin_required
def close_request(req_id):
    """Admin marks a request as closed."""
    try:
        ensure_shift_schedule_table()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE shift_change_requests
            SET status='closed'
            WHERE id=%s AND hall_id=%s
        """, (req_id, session.get("user_hall_id")))
        conn.commit()
        cur.close()
        conn.close()

        try:
            socketio.emit("requests_updated", {"hall_id": session.get("user_hall_id")}, room=f"admin_hall_{session.get('user_hall_id')}")
        except Exception:
            pass

        flash("Request closed.", "success")
        return redirect(url_for("admin_requests"))

    except Exception as e:
        print(f"close_request error: {e}")
        flash("Error closing request.", "error")
        return redirect(url_for("admin_requests"))

@socketio.on("join_admin_room")
def join_admin_room(data):
    """Admins join a hall-specific room for live requests updates."""
    try:
        hall_id = (data or {}).get("hall_id")
        if hall_id and session.get("is_admin"):
            from flask_socketio import join_room
            join_room(f"admin_hall_{hall_id}")
    except Exception:
        pass
# ========== SHIFT SCHEDULER ROUTES ==========

@app.route("/admin/shift_scheduler")
@login_required
@permission_required('can_manage_shifts')
def admin_shift_scheduler():
    """Main shift scheduler interface"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get users for the current hall
        cur.execute("""
            SELECT i.id, i.username, i.initials, i.fullName, i.title
            FROM initialscheck i
            WHERE i.hall_id = %s AND i.is_active = 1
            ORDER BY i.title DESC, i.initials
        """, (session['user_hall_id'],))
        users = cur.fetchall()
        
        # Get current week start (Monday)
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())  # Monday
        current_week_start = start_of_week.strftime('%Y-%m-%d')
        
        # Generate time slots (12:00 AM to 11:00 PM, 1-hour slots)
        time_slots = []
        
        for hour in range(24):  # 0 to 23
            start_hour = hour
            end_hour = (hour + 1) % 24
            
            # Format for display (12-hour format)
            if start_hour == 0:
                start_display = "12:00 AM"
            elif start_hour < 12:
                start_display = f"{start_hour}:00 AM"
            elif start_hour == 12:
                start_display = "12:00 PM"
            else:
                start_display = f"{start_hour - 12}:00 PM"
                
            if end_hour == 0:
                end_display = "12:00 AM"
            elif end_hour < 12:
                end_display = f"{end_hour}:00 AM"
            elif end_hour == 12:
                end_display = "12:00 PM"
            else:
                end_display = f"{end_hour - 12}:00 PM"
            
            time_slots.append({
                'display': f"{start_display} - {end_display}",
                'start_time': f"{start_hour:02d}:00:00",
                'end_time': f"{end_hour:02d}:00:00"
            })
        
        cur.close()
        conn.close()
        
        print(f"DEBUG: Sending {len(users)} users to template")
        print(f"DEBUG: Generated {len(time_slots)} time slots (12am-11pm)")
        print(f"DEBUG: Week starts: {current_week_start}")
        
        return render_template("shift_scheduler.html",
                             users=users,
                             time_slots=time_slots,
                             current_week_start=current_week_start)
        
    except Exception as e:
        print(f"SHIFT SCHEDULER ERROR: {e}")
        flash("Error loading shift scheduler", "error")
        return redirect(url_for('checkin'))


@app.route("/api/shifts", methods=["GET", "POST", "DELETE"])
@login_required
def api_shifts():
    """Handle shift operations"""
    try:
        # =====================================================
        # GET SHIFTS
        # =====================================================
        if request.method == "GET":
            start_date = request.args.get("start_date")
            end_date = request.args.get("end_date")

            if not start_date or not end_date:
                return jsonify({
                    "success": False,
                    "error": "start_date and end_date are required"
                }), 400

            print(
                f"DEBUG: Fetching shifts from {start_date} "
                f"to {end_date} for hall {session['user_hall_id']}"
            )

            conn = get_conn()
            cur = conn.cursor(dictionary=True)

            cur.execute("""
                SELECT 
                    s.id,
                    s.user_id,
                    s.shift_date,
                    s.start_time,
                    s.end_time,
                    s.shift_type,
                    s.notes,
                    i.initials,
                    i.fullName
                FROM shift_schedule s
                JOIN initialscheck i ON s.user_id = i.id
                WHERE s.hall_id = %s
                  AND s.shift_date BETWEEN %s AND %s
                ORDER BY s.shift_date, s.start_time
            """, (session["user_hall_id"], start_date, end_date))

            shifts = cur.fetchall()
            cur.close()
            conn.close()

            # 🔥 CRITICAL FIX: JSON-safe conversion
            for shift in shifts:
                # date
                if shift.get("shift_date") is not None:
                    shift["shift_date"] = shift["shift_date"].strftime("%Y-%m-%d")

                # start_time
                if isinstance(shift.get("start_time"), timedelta):
                    total = int(shift["start_time"].total_seconds())
                    h = total // 3600
                    m = (total % 3600) // 60
                    shift["start_time"] = f"{h:02d}:{m:02d}:00"
                elif shift.get("start_time") is not None:
                    shift["start_time"] = str(shift["start_time"])

                # end_time
                if isinstance(shift.get("end_time"), timedelta):
                    total = int(shift["end_time"].total_seconds())
                    h = total // 3600
                    m = (total % 3600) // 60
                    shift["end_time"] = f"{h:02d}:{m:02d}:00"
                elif shift.get("end_time") is not None:
                    shift["end_time"] = str(shift["end_time"])

            print(f"DEBUG: Returning {len(shifts)} shifts")
            return jsonify({"success": True, "shifts": shifts})
            
        elif request.method == "POST":
            print(f"DEBUG: Received shift POST request")
            print(f"DEBUG: Session can_manage_shifts: {session.get('can_manage_shifts')}")
            print(f"DEBUG: Session user_hall_id: {session.get('user_hall_id')}")
            
            if not session.get("can_manage_shifts"):
                print("DEBUG: Permission denied - user cannot manage shifts")
                return jsonify({"success": False, "error": "Permission denied"}), 403

            data = request.get_json()
            if not data:
                print("DEBUG: No JSON data received")
                return jsonify({"success": False, "error": "Expected JSON body"}), 400

            print(f"DEBUG: Received shift data: {data}")

            # Validate required fields
            required_fields = ["user_id", "shift_date", "start_time", "end_time"]
            missing = [f for f in required_fields if not data.get(f)]
            if missing:
                print(f"DEBUG: Missing required fields: {missing}")
                return jsonify({"success": False, "error": f"Missing required field(s): {', '.join(missing)}"}), 400

            conn = get_conn()
            cur = conn.cursor(dictionary=True)

            try:
                shift_id = data.get("id")
                
                # Validate user belongs to the same hall
                cur.execute("SELECT hall_id FROM initialscheck WHERE id = %s AND hall_id = %s", 
                           (data["user_id"], session["user_hall_id"]))
                
                user_check = cur.fetchone()
                if not user_check:
                    print(f"DEBUG: User {data['user_id']} not found in hall {session['user_hall_id']}")
                    return jsonify({"success": False, "error": "User not found in your hall"}), 400
                
                if shift_id:
                    # UPDATE existing shift
                    cur.execute("""
                        UPDATE shift_schedule 
                        SET user_id = %s,
                            shift_date = %s,
                            start_time = %s,
                            end_time = %s,
                            shift_type = %s,
                            notes = %s,
                            updated_at = NOW()
                        WHERE id = %s AND hall_id = %s
                    """, (
                        data["user_id"],
                        data["shift_date"],
                        data["start_time"],
                        data["end_time"],
                        data.get("shift_type", "Regular"),
                        data.get("notes", ""),
                        shift_id,
                        session["user_hall_id"]
                    ))
                    
                    print(f"DEBUG: Updated shift {shift_id}, rows affected: {cur.rowcount}")
                    
                    if cur.rowcount == 0:
                        return jsonify({"success": False, "error": "Shift not found or access denied"}), 404
                        
                else:
                    # INSERT new shift
                    cur.execute("""
                        INSERT INTO shift_schedule 
                        (hall_id, user_id, shift_date, start_time, end_time, shift_type, notes, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        session["user_hall_id"],
                        data["user_id"],
                        data["shift_date"],
                        data["start_time"],
                        data["end_time"],
                        data.get("shift_type", "Regular"),
                        data.get("notes", ""),
                        session["user_id"]
                    ))
                    shift_id = cur.lastrowid
                    print(f"DEBUG: Inserted new shift with ID: {shift_id}")

                conn.commit()
                
                cur.execute("""
                    SELECT 
                        s.id,
                        s.user_id,
                        s.shift_date,
                        s.start_time,
                        s.end_time,
                        s.shift_type,
                        s.notes,
                        i.initials,
                        i.fullName
                    FROM shift_schedule s
                    JOIN initialscheck i ON s.user_id = i.id
                    WHERE s.id = %s
                """, (shift_id,))

                shift = cur.fetchone()

                if shift:
                    # JSON-safe formatting
                    if shift.get("shift_date") is not None:
                        shift["shift_date"] = shift["shift_date"].strftime("%Y-%m-%d")
                    if shift.get("start_time") is not None:
                        shift["start_time"] = str(shift["start_time"])
                    if shift.get("end_time") is not None:
                        shift["end_time"] = str(shift["end_time"])

                
                return jsonify({"success": True, "shift": shift})

            except Exception as e:
                print(f"DEBUG: Database error in shift operation: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"success": False, "error": f"Database error: {str(e)}"}), 500
                
            finally:
                cur.close()
                conn.close()
                
        elif request.method == "DELETE":
            print(f"DEBUG: Received shift DELETE request")
            
            if not session.get("can_manage_shifts"):
                return jsonify({"success": False, "error": "Permission denied"}), 403
                
            shift_id = request.args.get("shift_id")
            if not shift_id:
                return jsonify({"success": False, "error": "shift_id is required"}), 400
            
            print(f"DEBUG: Deleting shift {shift_id} from hall {session['user_hall_id']}")
                
            conn = get_conn()
            cur = conn.cursor()
            
            cur.execute("DELETE FROM shift_schedule WHERE id = %s AND hall_id = %s", 
                       (shift_id, session["user_hall_id"]))
            conn.commit()
            
            print(f"DEBUG: Deleted {cur.rowcount} rows")
            
            cur.close()
            conn.close()
            
            return jsonify({"success": True})

    except Exception as e:
        print(f"API SHIFTS ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/my_schedule")
@login_required
def my_schedule():
    """User's personal schedule view"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get user's shifts (recent + upcoming)
        today = datetime.now().date()
        start_window = today - timedelta(days=7)
        end_window = today + timedelta(days=60)
        
        # SIMPLIFIED: Just select the raw values
        cur.execute("""
            SELECT 
                s.id,
                s.user_id,
                s.shift_date,
                s.start_time,
                s.end_time,
                s.shift_type,
                s.notes,
                h.hall_name,
                h.hall_code
            FROM shift_schedule s
            JOIN halls h ON s.hall_id = h.id
            WHERE s.user_id = %s 
            AND s.shift_date BETWEEN %s AND %s
            ORDER BY s.shift_date, s.start_time
        """, (session['user_id'], start_window, end_window))
        
        shifts = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return render_template("my_schedule.html", shifts=shifts)
        
    except Exception as e:
        print(f"MY SCHEDULE ERROR: {e}")
        flash("Error loading schedule", "error")
        return redirect(url_for('checkin'))

@app.route("/export_schedule_pdf")
@login_required
def export_schedule_pdf():
    """Export shift schedule as a simple grid PDF (course-schedule style)."""
    try:
        # Date range (default: current week Mon-Sun)
        today = datetime.now().date()
        default_start = today - timedelta(days=today.weekday())
        default_end = default_start + timedelta(days=6)

        start_date = request.args.get("start_date", default_start.strftime("%Y-%m-%d"))
        end_date = request.args.get("end_date", default_end.strftime("%Y-%m-%d"))

        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

        # Which schedule to export?
        # - Admin (HD/AHD) can export everyone in hall
        # - Non-admin exports only self
        if session.get("is_admin"):
            export_mode = "admin_all"
        else:
            export_mode = "single"
            target_user_id = session["user_id"]

        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        # Hall info
        cur.execute("""
            SELECT hall_name
            FROM halls
            WHERE id = %s
        """, (session.get("user_hall_id"),))
        hall = cur.fetchone()
        hall_name = hall["hall_name"] if hall else "Unknown Hall"

        # Pull shifts
        if export_mode == "admin_all":
            cur.execute("""
                SELECT s.*, i.initials, i.fullName
                FROM shift_schedule s
                JOIN initialscheck i ON s.user_id = i.id
                WHERE s.hall_id = %s
                AND s.shift_date BETWEEN %s AND %s
                ORDER BY s.shift_date, s.start_time
            """, (session.get("user_hall_id"), start_dt, end_dt))
        else:
            cur.execute("""
                SELECT s.*, i.initials, i.fullName
                FROM shift_schedule s
                JOIN initialscheck i ON s.user_id = i.id
                WHERE s.hall_id = %s
                AND s.user_id = %s
                AND s.shift_date BETWEEN %s AND %s
                ORDER BY s.shift_date, s.start_time
            """, (session.get("user_hall_id"), target_user_id, start_dt, end_dt))

        shifts = cur.fetchall()

        # Generate day columns
        days = []
        d = start_dt
        while d <= end_dt:
            days.append(d)
            d += timedelta(days=1)

        # Group shifts by day and time
        schedule_data = {}
        for shift in shifts:
            date_str = shift['shift_date'].strftime('%Y-%m-%d') if isinstance(shift['shift_date'], datetime) else shift['shift_date']
            time_key = f"{shift['start_time']}-{shift['end_time']}"
            
            if date_str not in schedule_data:
                schedule_data[date_str] = {}
            
            if time_key not in schedule_data[date_str]:
                schedule_data[date_str][time_key] = []
            
            schedule_data[date_str][time_key].append(f"{shift['initials']}")

        # Get unique time slots
        time_slots = set()
        for date_data in schedule_data.values():
            time_slots.update(date_data.keys())
        time_slots = sorted(list(time_slots))

        # Create HTML for the table
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Shift Schedule - {hall_name}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ text-align: center; margin-bottom: 5px; }}
                .period {{ text-align: center; color: #666; margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 10px; text-align: center; }}
                th {{ background-color: #f5f5f5; font-weight: bold; }}
                .time-col {{ background-color: #f9f9f9; font-weight: bold; }}
                .shift-cell {{ background-color: #e3f2fd; padding: 5px; margin: 2px; border-radius: 3px; }}
                .footer {{ text-align: center; margin-top: 20px; color: #999; font-size: 12px; }}
            </style>
        </head>
        <body>
            <h1>Shift Schedule - {hall_name}</h1>
            <div class="period">
                Period: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')}
                {f"<br>Export Mode: {export_mode}" if export_mode == "admin_all" else ""}
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th class="time-col">Time</th>
        """
        
        # Add day headers
        for day in days:
            html_content += f'<th>{day.strftime("%a")}<br>{day.strftime("%m/%d")}</th>'
        
        html_content += """
                    </tr>
                </thead>
                <tbody>
        """
        
        # Add time rows
        for time_slot in time_slots:
            start_time, end_time = time_slot.split('-')
            # Format time for display
            try:
                start_display = datetime.strptime(start_time, "%H:%M:%S").strftime("%I:%M %p")
                end_display = datetime.strptime(end_time, "%H:%M:%S").strftime("%I:%M %p")
                time_display = f"{start_display}<br>to<br>{end_display}"
            except:
                time_display = f"{start_time}<br>to<br>{end_time}"
            
            html_content += f'<tr><td class="time-col">{time_display}</td>'
            
            for day in days:
                date_str = day.strftime('%Y-%m-%d')
                if date_str in schedule_data and time_slot in schedule_data[date_str]:
                    shifts_list = schedule_data[date_str][time_slot]
                    html_content += f'<td><div class="shift-cell">{"<br>".join(shifts_list)}</div></td>'
                else:
                    html_content += '<td></td>'
            
            html_content += '</tr>'
        
        html_content += """
                </tbody>
            </table>
            <div class="footer">
                Generated on """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """ | Clement Package System
            </div>
        </body>
        </html>
        """
        
        cur.close()
        conn.close()
        
        # Try to generate PDF
        try:
            import pdfkit
            
            # Try to find wkhtmltopdf path
            wkhtmltopdf_paths = [
                r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe',
                r'C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe',
                '/usr/local/bin/wkhtmltopdf',
                '/usr/bin/wkhtmltopdf',
                'wkhtmltopdf'
            ]
            
            config = None
            for path in wkhtmltopdf_paths:
                if os.path.exists(path):
                    config = pdfkit.configuration(wkhtmltopdf=path)
                    break
            
            if config is None:
                # Try to use system PATH
                config = pdfkit.configuration()
            
            options = {
                'page-size': 'A4',
                'orientation': 'Landscape',
                'margin-top': '0.5in',
                'margin-right': '0.5in',
                'margin-bottom': '0.5in',
                'margin-left': '0.5in',
                'encoding': "UTF-8",
                'quiet': ''
            }
            
            # Create PDF in memory
            pdf_bytes = pdfkit.from_string(html_content, False, configuration=config, options=options)
            
            # Return PDF
            from flask import send_file
            from io import BytesIO
            
            return send_file(
                BytesIO(pdf_bytes),
                as_attachment=True,
                download_name=f"shift_schedule_{start_date}_to_{end_date}.pdf",
                mimetype='application/pdf'
            )
            
        except ImportError:
            # Fallback to HTML download
            from flask import make_response
            response = make_response(html_content)
            response.headers['Content-Type'] = 'text/html'
            response.headers['Content-Disposition'] = f'attachment; filename=shift_schedule_{start_date}_to_{end_date}.html'
            return response
            
        except Exception as pdf_error:
            print(f"PDF generation error: {pdf_error}")
            # Fallback to HTML
            from flask import make_response
            response = make_response(html_content)
            response.headers['Content-Type'] = 'text/html'
            response.headers['Content-Disposition'] = f'attachment; filename=shift_schedule_{start_date}_to_{end_date}.html'
            return response
            
    except Exception as e:
        print(f"Export schedule error: {e}")
        import traceback
        traceback.print_exc()
        flash("Error generating schedule export", "error")
        return redirect(url_for("admin_shift_scheduler"))

@app.route("/schedule")
@login_required
def schedule_page():
    """Schedule page for all users"""
    return redirect(url_for('my_schedule'))

#============= RETURN TO SENDER ROUTES =============

@app.route("/return_to_sender")
@login_required
def return_to_sender_select():
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        cur.execute("""
            SELECT
                l.Id AS logId,
                l.trackingId AS TrackingID,
                COALESCE(s.firstName, '') AS firstName,
                COALESCE(s.lastName, '') AS lastName,
                REPLACE(l.roomNumber, 'Clement Hall-', '') AS roomNumber,
                COALESCE(s.hallName, '') AS hallName,
                l.checkInDate,
                l.type AS package_type,
                l.perishable,
                l.notes
            FROM postofficelog l
            LEFT JOIN studentmaster s
              ON s.roomNumber = REPLACE(l.roomNumber, 'Clement Hall-', '')
            WHERE l.checkoutStatus = 0
            ORDER BY l.checkInDate DESC
        """)

        packages = cur.fetchall()
        cur.close()
        conn.close()

        return render_template("rts_select.html", packages=packages)

    except Exception as e:
        print("RTS SELECT ERROR:", e)
        flash("Error loading packages", "error")
        return redirect(url_for("checkin"))


@app.route("/return_to_sender/process", methods=["POST"])
@login_required
def return_to_sender_process():
    try:
        selected_ids = request.form.getlist("selected_packages")

        print("RTS SELECTED IDS:", selected_ids)  # ← keep this for now

        if not selected_ids:
            flash("No packages selected", "error")
            return redirect(url_for("return_to_sender_select"))

        placeholders = ",".join(["%s"] * len(selected_ids))

        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        cur.execute(f"""
            SELECT
                l.Id AS logId,
                l.trackingId AS TrackingID,
                COALESCE(s.firstName, '') AS firstName,
                COALESCE(s.lastName, '') AS lastName,
                REPLACE(l.roomNumber, 'Clement Hall-', '') AS roomNumber
            FROM postofficelog l
            LEFT JOIN studentmaster s
              ON s.roomNumber = REPLACE(l.roomNumber, 'Clement Hall-', '')
            WHERE l.Id IN ({placeholders})
        """, selected_ids)

        selected_packages = cur.fetchall()
        cur.close()
        conn.close()

        return render_template(
            "rts_form.html",
            packages=selected_packages,
            user_initials=session.get("user_initials", ""),
            user_title=session.get("user_title", "")
        )

    except Exception as e:
        print("RTS PROCESS ERROR:", e)
        flash("Error processing selection", "error")
        return redirect(url_for("return_to_sender_select"))

@app.route("/return_to_sender/submit", methods=["POST"])
@login_required
def return_to_sender_submit():
    """Submit RTS records"""
    try:
        conn = get_conn()
        cur = conn.cursor()

        log_ids = request.form.getlist("log_id[]")
        last_names = request.form.getlist("last_name[]")
        first_names = request.form.getlist("first_name[]")
        rooms = request.form.getlist("room[]")
        rts_types = request.form.getlist("rts_type[]")
        addresses = request.form.getlist("address[]")
        date_submitted = request.form.get("date")
        title_initials = request.form.get("title_initials")

        for i in range(len(log_ids)):
            log_id = log_ids[i]

            # Get tracking ID
            cur.execute(
                "SELECT trackingId FROM postofficelog WHERE Id = %s",
                (log_id,)
            )
            tracking_row = cur.fetchone()
            tracking_id = tracking_row[0] if tracking_row else ""

            # Insert RTS record
            cur.execute("""
                INSERT INTO return_to_sender
                (postoffice_log_id, tracking_id, last_name, first_name, room,
                 rts_type, address, date_submitted, title_initials)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                log_id,
                tracking_id,
                last_names[i],
                first_names[i],
                rooms[i],
                rts_types[i],
                addresses[i],
                date_submitted,
                title_initials
            ))

            # Mark package as checked out
            cur.execute("""
                UPDATE postofficelog
                SET checkoutStatus = 1,
                    checkoutDate = NOW(),
                    checkoutEmpInitials = %s,
                    notes = CONCAT(COALESCE(notes, ''), ' [RTS: ', %s, ']')
                WHERE Id = %s
            """, (title_initials, rts_types[i], log_id))

        conn.commit()
        cur.close()
        conn.close()

        flash("Return to Sender processed successfully", "success")
        return redirect(url_for("checkin"))

    except Exception as e:
        print(f"Error submitting RTS: {e}")
        import traceback
        traceback.print_exc()
        flash("Error submitting Return to Sender records", "error")
        return redirect(url_for("return_to_sender_select"))

@app.route("/return_to_sender/history")
@login_required
def rts_history():
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)

        cur.execute("""
            SELECT
                r.id,
                r.tracking_id,
                r.first_name,
                r.last_name,
                r.room,
                r.rts_type,
                r.address,
                r.date_submitted,
                r.title_initials,
                r.created_at
            FROM return_to_sender r
            ORDER BY r.created_at DESC
        """)

        records = cur.fetchall()
        cur.close()
        conn.close()

        return render_template("rts_history.html", records=records)

    except Exception as e:
        print("RTS HISTORY ERROR:", e)
        flash("Error loading RTS history", "error")
        return redirect(url_for("checkin"))

import csv
from flask import Response

@app.route("/return_to_sender/export")
@login_required
def export_rts_history():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            date_submitted,
            tracking_id,
            first_name,
            last_name,
            room,
            rts_type,
            title_initials
        FROM return_to_sender
        ORDER BY date_submitted DESC
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    def generate():
        header = [
            "Date Submitted",
            "Tracking ID",
            "First Name",
            "Last Name",
            "Room",
            "RTS Type",
            "Processed By"
        ]
        yield ",".join(header) + "\n"

        for r in rows:
            yield ",".join([
                str(r["date_submitted"]),
                r["tracking_id"] or "",
                r["first_name"] or "",
                r["last_name"] or "",
                r["room"] or "",
                r["rts_type"] or "",
                r["title_initials"] or ""
            ]) + "\n"

    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=return_to_sender_history.csv"
        }
    )



if __name__ == "__main__":
    print("Starting Flask-SocketIO...")

    # Ensure shift schedule table exists
    print("Checking shift schedule table...")
    if not ensure_shift_schedule_table():
        print("WARNING: Could not create shift_schedule table!")
    
    # Get port from command line or find available one
    import socket
    
    def find_available_port(start_port=5300, max_attempts=50):
        """Find an available port starting from start_port"""
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.bind(('127.0.0.1', port))
                    return port
            except OSError:
                continue
        return start_port  # Fallback
    
    # Try to read port from file created by C3 installer
    port_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_port.txt")
    port = 5300  # Default
    
    if os.path.exists(port_file_path):
        try:
            with open(port_file_path, 'r') as f:
                saved_port = f.read().strip()
                if saved_port.isdigit():
                    port = int(saved_port)
                    print(f"Using port from web_port.txt: {port}")
                else:
                    print(f"Invalid port in web_port.txt: {saved_port}, using default 5300")
        except Exception as e:
            print(f"Error reading web_port.txt: {e}, using default 5300")
    
    # If port is busy, find available one
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(('127.0.0.1', port))
            print(f"Port {port} is available")
    except OSError:
        print(f"Port {port} is busy, finding available port...")
        port = find_available_port(port)
        print(f"Using port {port} instead")
    
    print(f"Starting on port {port}")
    print(f"WebApp will be available at: http://127.0.0.1:{port}")
    
    # Test database connection first
    if not test_database_connection():
        print("CRITICAL: Cannot connect to database. Please check:")
        print("   1. Is XAMPP MySQL running?")
        print("   2. Is the database 'clement_package_log' created?")
        print("   3. Are the credentials in DB_CONFIG correct?")
        input("Press Enter to exit...")
        sys.exit(1)
    
    # Create initial export file if it doesn't exist
    if not os.path.exists(EXPORT_FILE_PATH):
        print("Creating initial export file...")
        update_export_file()
    
    socketio.run(app, host="127.0.0.1", port=port, debug=True, use_reloader=False)