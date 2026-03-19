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

app = Flask(__name__, template_folder="templates")
app.config['SECRET_KEY'] = 'your-secret-key-here-change-this-in-production'
app.config['PERMANENT_SESSION_LIFETIME'] = 900 
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "clement_package_log",
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
    """Update the enhanced Excel export file with multiple sheets"""
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    # Get all packages data
    cur.execute("""
        SELECT
            p.TrackingID,
            COALESCE(s1.firstName, s2.firstName, '') AS firstName,  
            COALESCE(s1.lastName, s2.lastName, '') AS lastName,     
            COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
            COALESCE(s1.hallName, s2.hallName, '') AS hallName,
            l.checkInDate, l.checkInEmpInitials AS checkinEmpInitials,
            l.type AS package_type,
            l.checkoutStatus, l.checkoutDate, l.checkoutEmpInitials AS checkoutEmpInitials,
            l.perishable,
            l.notes
        FROM postofficelog l
        LEFT JOIN package_log p ON p.TrackingID = l.trackingId
        LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
        LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
        ORDER BY l.checkInDate DESC, l.Id DESC
    """)
    all_packages = cur.fetchall()
    
    # Get packages grouped by month
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
    
    cur.close()
    conn.close()
    
    # Create Excel file with multiple sheets
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_filename = f"package_log_export_{timestamp}.xlsx"
    excel_filepath = os.path.join(tempfile.gettempdir(), excel_filename)
    
    with pd.ExcelWriter(excel_filepath, engine='openpyxl') as writer:
        # Sheet 1: ALL_PACKAGES - All packages
        df_all = pd.DataFrame(all_packages)
        df_all.to_excel(writer, sheet_name='ALL_PACKAGES', index=False)
        
        # Sheet 2: CURRENT_MONTH - Current month packages
        current_month = datetime.now().strftime('%Y-%m')
        current_month_packages = [pkg for pkg in all_packages 
                                if pkg['checkInDate'] and 
                                pkg['checkInDate'].strftime('%Y-%m') == current_month]
        df_current = pd.DataFrame(current_month_packages)
        df_current.to_excel(writer, sheet_name='CURRENT_MONTH', index=False)
        
        # Sheet 3+: Separate sheets for each month
        for month_data in months_data:
            month = month_data['month']
            display_month = month_data['display_month']
            
            # Filter packages for this month
            month_packages = [pkg for pkg in all_packages 
                            if pkg['checkInDate'] and 
                            pkg['checkInDate'].strftime('%Y-%m') == month]
            
            if month_packages:
                df_month = pd.DataFrame(month_packages)
                # Clean sheet name for Excel compatibility
                sheet_name = f"{display_month}"[:31]  # Excel sheet name limit
                df_month.to_excel(writer, sheet_name=sheet_name, index=False)
        
        # Sheet: SUMMARY - Month-wise package counts
        summary_data = []
        for month_data in months_data:
            summary_data.append({
                'Month': month_data['display_month'],
                'Package Count': month_data['package_count']
            })
        
        # Add current month if not in summary
        current_display_month = datetime.now().strftime('%B %Y')
        current_month_exists = any(month_data['display_month'] == current_display_month 
                                 for month_data in months_data)
        if not current_month_exists:
            current_month_count = len(current_month_packages)
            summary_data.insert(0, {
                'Month': current_display_month,
                'Package Count': current_month_count
            })
        
        df_summary = pd.DataFrame(summary_data)
        df_summary.to_excel(writer, sheet_name='SUMMARY', index=False)
    
    # Update the global export file path to point to the Excel file
    global EXPORT_FILE_PATH
    EXPORT_FILE_PATH = excel_filepath
    
    print(f"Enhanced Excel export file updated at {datetime.now()}")
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

def quick_search_packages(query, limit=200):
    """
    Fast search with limits
    """
    if not query:
        return []
    
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    search_term = f"%{query}%"
    
    # Optimized search query
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
        WHERE l.trackingId LIKE %s 
           OR l.roomNumber LIKE %s
           OR s.firstName LIKE %s 
           OR s.lastName LIKE %s
        ORDER BY l.checkInDate DESC
        LIMIT %s
    """, (search_term, search_term, search_term, search_term, limit))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# Add custom filter for Jinja2
@app.template_filter('datetimeformat')
def datetimeformat(value, format='%Y-%m-%d'):
    if isinstance(value, str):
        value = datetime.strptime(value, '%Y-%m-%d')
    return value.strftime(format)

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
    # Handle checkout from search page
    if request.method == "POST":
        tracking_id = normalize_tracking_id(request.form.get("tracking_id"))
        initials = session.get('user_initials', '').strip().upper()

        conn = get_conn()
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("""
            SELECT l.Id AS logId
            FROM postofficelog l
            WHERE l.trackingId=%s
            ORDER BY l.Id DESC
            LIMIT 1
        """, (tracking_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return render_template(
                "search.html",
                search_query="",
                user_initials=session.get('display_name', ''),
                error="No record found for this tracking ID"
            )

        # Perform checkout
        cur.execute("""
            UPDATE postofficelog
            SET checkoutStatus=1, checkoutDate=%s, checkoutEmpInitials=%s
            WHERE Id=%s
        """, (datetime.now(), initials, row["logId"]))
        conn.commit()
        cur.close()
        conn.close()

        # Update export in background
        import threading
        threading.Thread(target=update_export_file).start()
        
        socketio.emit("refresh_recent")
        return render_template("search.html",
                               search_query="",
                               user_initials=session.get('display_name', ''),
                               success=f"Package {tracking_id} checked out successfully!")

    # GET request - FAST: Return only search form
    query = request.args.get('q', '').strip()
    
    if query:
        # Only do search if query exists
        packages = quick_search_packages(query, limit=200)
        return render_template(
            "search.html",
            packages=packages,
            search_query=query,
            user_initials=session.get('display_name', ''),
            total_count=len(packages)
        )
    else:
        # No query - show only form, data loads via JavaScript
        return render_template(
            "search.html",
            search_query="",
            user_initials=session.get('display_name', ''),
            total_count=0
        )

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
def api_student_by_room():
    room = (request.args.get("room") or "").strip()
    if not room:
        return jsonify({"found": False})
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    
    # Try to find by room number first, then by student ID
    cur.execute("""
        SELECT Id, firstName, lastName, preferredName, roomNumber, hallName, academicYear
        FROM studentmaster
        WHERE roomNumber=%s OR Id=%s
        LIMIT 1
    """, (room, room))
    row = cur.fetchone()  
    cur.close()
    conn.close()
    return jsonify({"found": bool(row), "student": row})

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

@app.route("/api/quick_search")
@login_required
def api_quick_search():
    """Fast search API"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    packages = quick_search_packages(query, limit=200)
    
    return jsonify({
        "packages": packages,
        "count": len(packages),
        "query": query
    })

# ----- Admin CSV Update -----

@app.route("/admin/update_students", methods=["GET", "POST"])
@admin_required
def admin_update_students():
    if request.method == "POST":
        if 'csv_file' not in request.files:
            return render_template("admin_update.html", error="No file selected")
        
        file = request.files['csv_file']
        if file.filename == '':
            return render_template("admin_update.html", error="No file selected")
        
        if not file.filename.endswith('.csv'):
            return render_template("admin_update.html", error="Please upload a CSV file")
        
        try:
            # Save file temporarily to detect encoding
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
            file.save(temp_file.name)
            temp_file.close()
            
            # Try multiple encodings to read the file
            encodings_to_try = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1', 'windows-1252']
            
            csv_data = None
            used_encoding = None
            
            for encoding in encodings_to_try:
                try:
                    with open(temp_file.name, 'r', encoding=encoding) as f:
                        content = f.read()
                        # Try to parse as CSV to validate
                        csv_lines = content.splitlines()
                        test_reader = csv.DictReader(csv_lines)
                        if test_reader.fieldnames:  # If we got fieldnames, encoding might be correct
                            csv_data = csv_lines
                            used_encoding = encoding
                            print(f"Successfully read file with {encoding} encoding")
                            break
                except UnicodeDecodeError:
                    continue
                except Exception:
                    continue
            
            # Clean up temp file
            os.unlink(temp_file.name)
            
            if csv_data is None:
                # Last resort: read as binary and decode with error handling
                with open(temp_file.name, 'rb') as f:
                    binary_content = f.read()
                # Try to decode with error replacement
                content = binary_content.decode('utf-8', errors='replace')
                csv_data = content.splitlines()
                used_encoding = 'utf-8 with error replacement'
                print(f"Used utf-8 with error replacement")
            
            # Parse CSV data
            csv_reader = csv.DictReader(csv_data)
            
            # Debug: Print what we're reading
            print(f"CSV HEADERS ({used_encoding}):", csv_reader.fieldnames)
            
            conn = get_conn()
            cur = conn.cursor(dictionary=True, buffered=True)
            
            update_count = 0
            insert_count = 0
            clear_count = 0
            error_count = 0
            
            # Track rooms from CSV
            csv_rooms = set()
            
            # FIRST: Process all rows from CSV
            for row_num, row in enumerate(csv_reader, 2):
                try:
                    # Get data with case-insensitive field access
                    student_id = (row.get('Id') or row.get('id') or '').strip()
                    room = (row.get('roomnumber') or row.get('room') or '').strip()
                    first_name = (row.get('firstname') or row.get('first') or '').strip()
                    last_name = (row.get('lastname') or row.get('last') or '').strip()
                    preferred_name = (row.get('preferredname') or row.get('preferred') or '').strip()
                    hall_name = (row.get('hallname') or row.get('hall') or 'Clement Hall').strip()
                    academic_year = (row.get('academic year') or row.get('academic') or '2025-2026').strip()
                    
                    print(f"Processing row {row_num}: Room {room}, ID {student_id}, Name {first_name} {last_name}")
                    
                    # Validate required fields
                    if not room:
                        print(f"ERROR: Row {row_num} missing room number")
                        error_count += 1
                        continue
                    
                    if not student_id:
                        print(f"ERROR: Row {row_num} missing student ID")
                        error_count += 1
                        continue
                    
                    csv_rooms.add(room)
                    
                    # Check if room exists
                    cur.execute("SELECT * FROM studentmaster WHERE roomNumber = %s", (room,))
                    existing = cur.fetchone()
                    
                    if existing:
                        # UPDATE existing room
                        cur.execute("""
                            UPDATE studentmaster 
                            SET firstName = %s, lastName = %s, preferredName = %s,
                                hallName = %s, academicYear = %s
                            WHERE roomNumber = %s
                        """, (first_name, last_name, preferred_name, hall_name, academic_year, room))
                        update_count += 1
                        print(f"UPDATED room {room}: {first_name} {last_name}")
                    else:
                        # INSERT new room
                        cur.execute("""
                            INSERT INTO studentmaster 
                            (Id, firstName, lastName, preferredName, roomNumber, hallName, academicYear) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (student_id, first_name, last_name, preferred_name, room, hall_name, academic_year))
                        insert_count += 1
                        print(f"INSERTED room {room}: {first_name} {last_name}")
                        
                except Exception as e:
                    print(f"ERROR processing row {row_num}: {str(e)}")
                    error_count += 1
                    continue
            
            # SECOND: Clear rooms NOT in CSV (only if CSV had valid data)
            if csv_rooms:  # Only clear if we found rooms in CSV
                # Get all current rooms
                cur.execute("SELECT roomNumber FROM studentmaster")
                all_rooms = [row['roomNumber'] for row in cur.fetchall()]
                
                for room in all_rooms:
                    if room not in csv_rooms:
                        cur.execute("""
                            UPDATE studentmaster 
                            SET firstName = '', lastName = '', preferredName = '' 
                            WHERE roomNumber = %s
                        """, (room,))
                        clear_count += 1
                        print(f"CLEARED room {room} (not in CSV)")
            
            conn.commit()
            cur.close()
            conn.close()    
            
            success_message = (
                f"Update completed! "
                f"Updated: {update_count}, "
                f"Inserted: {insert_count}, "
                f"Cleared (vacant): {clear_count}, "
                f"Errors: {error_count}"
            )
            
            print(f"FINAL RESULT: {success_message}")
            return render_template("admin_update.html", success=success_message)
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"MAJOR ERROR: {error_details}")
            return render_template("admin_update.html", error=f"Error processing file: {str(e)}")
    
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

# ----- PO Audit -----

@app.route("/po_audit", methods=["GET", "POST"])
@login_required
def po_audit():
    """PO Audit - Compare uploaded file with website export data with date filtering"""
    if request.method == "POST":
        if 'audit_file' not in request.files:
            return render_template("po_audit.html", error="No file selected")
        
        file = request.files['audit_file']
        if file.filename == '':
            return render_template("po_audit.html", error="No file selected")
        
        # Get cutoff date from form
        cutoff_date_str = request.form.get('cutoff_date')
        cutoff_date = None
        if cutoff_date_str:
            try:
                cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d')
            except ValueError:
                return render_template("po_audit.html", error="Invalid date format. Use YYYY-MM-DD")
        
        if not file.filename.endswith(('.csv', '.xlsx')):
            return render_template("po_audit.html", error="Please upload a CSV or Excel file")
        
        try:
            # Read the uploaded file
            if file.filename.endswith('.csv'):
                df_upload = pd.read_csv(file)
            else:  # Excel file
                df_upload = pd.read_excel(file)
            
            # Get tracking IDs from uploaded file
            tracking_col = None
            for col in df_upload.columns:
                if 'tracking' in col.lower() or 'id' in col.lower():
                    tracking_col = col
                    break
            
            if not tracking_col:
                return render_template("po_audit.html", error="No tracking ID column found in uploaded file")
            
            uploaded_tracking_ids = set(df_upload[tracking_col].astype(str).str.strip().dropna())
            
            # Read the existing export Excel file instead of querying database
            if not os.path.exists(EXPORT_FILE_PATH):
                update_export_file()

            # Check if it's an Excel file or CSV based on extension
            if EXPORT_FILE_PATH.endswith('.xlsx') or EXPORT_FILE_PATH.endswith('.xls'):
                # Read the ALL_PACKAGES sheet from the Excel file
                try:
                    df_export = pd.read_excel(EXPORT_FILE_PATH, sheet_name='ALL_PACKAGES')
                except Exception as e:
                    # Fallback: try to read first sheet
                    print(f"Warning: Could not read ALL_PACKAGES sheet: {e}")
                    df_export = pd.read_excel(EXPORT_FILE_PATH)
            else:
                # Fallback for CSV files
                df_export = pd.read_csv(EXPORT_FILE_PATH)
            
            # Apply date filter if provided
            if cutoff_date:
                # Find checkin date column
                checkin_date_col = None
                for col in df_export.columns:
                    if 'checkin' in col.lower() and 'date' in col.lower():
                        checkin_date_col = col
                        break
                
                if checkin_date_col:
                    df_export[checkin_date_col] = pd.to_datetime(df_export[checkin_date_col], errors='coerce')
                    df_export = df_export[df_export[checkin_date_col] <= cutoff_date]
            
            # Get tracking IDs and checkout status from export file
            export_tracking_col = None
            checkout_status_col = None
            
            for col in df_export.columns:
                if 'tracking' in col.lower():
                    export_tracking_col = col
                if 'checkout' in col.lower() and 'status' in col.lower():
                    checkout_status_col = col
            
            if not export_tracking_col:
                return render_template("po_audit.html", error="No tracking ID column found in export file")
            
            # Filter for packages not checked out in our system
            if checkout_status_col:
                not_checked_out_df = df_export[df_export[checkout_status_col] == 0]
            else:
                # Fallback: if no checkout status column, use checkoutDate
                checkout_date_col = None
                for col in df_export.columns:
                    if 'checkout' in col.lower() and 'date' in col.lower():
                        checkout_date_col = col
                        break
                
                if checkout_date_col:
                    not_checked_out_df = df_export[df_export[checkout_date_col].isna()]
                else:
                    not_checked_out_df = df_export
            
            # Get tracking IDs from export file
            export_tracking_ids = set(df_export[export_tracking_col].astype(str).str.strip().dropna())
            not_checked_out_tracking_ids = set(not_checked_out_df[export_tracking_col].astype(str).str.strip().dropna())
            
            # Find missing tracking IDs (in uploaded file but not in our system)
            missing_in_system = uploaded_tracking_ids - export_tracking_ids
            
            # Create a combined dataframe for all packages that need attention
            # 1. Packages not checked out in our system
            attention_packages = not_checked_out_df.copy()
            
            # 2. Add missing packages as new rows with "MISSING" status
            missing_packages_data = []
            for tracking_id in missing_in_system:
                missing_package = {
                    export_tracking_col: tracking_id,
                    'Status': 'MISSING_IN_SYSTEM',
                    'firstName': '',
                    'lastName': '',
                    'roomNumber': '',
                    'checkInDate': '',
                    'checkInEmpInitials': '',
                    'package_type': '',
                    'checkoutStatus': 'MISSING',
                    'checkoutDate': '',
                    'checkoutEmpInitials': '',
                    'perishable': '',
                    'notes': 'Package not found in our system'
                }
                # Add all columns from export file with empty values
                for col in df_export.columns:
                    if col not in missing_package:
                        missing_package[col] = ''
                missing_packages_data.append(missing_package)
            
            if missing_packages_data:
                missing_packages_df = pd.DataFrame(missing_packages_data)
                # Ensure column order matches the export file
                missing_packages_df = missing_packages_df[df_export.columns]
                attention_packages = pd.concat([attention_packages, missing_packages_df], ignore_index=True)
            
            # Generate audit results file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            audit_filename = f"po_audit_results_{timestamp}.xlsx"
            audit_filepath = os.path.join(tempfile.gettempdir(), audit_filename)
            
            with pd.ExcelWriter(audit_filepath, engine='openpyxl') as writer:
                # Sheet 1: ALL packages that need attention (not checked out + missing)
                attention_packages.to_excel(writer, sheet_name='Attention_Required', index=False)
                
                # Sheet 2: Packages not checked out in our system (only)
                not_checked_out_df.to_excel(writer, sheet_name='Not_Checked_Out', index=False)
                
                # Sheet 3: Tracking IDs missing in our system (only)
                missing_df = pd.DataFrame(list(missing_in_system), columns=['TrackingID_Missing_In_System'])
                missing_df.to_excel(writer, sheet_name='Missing_In_System', index=False)
                
                # Sheet 4: Summary
                summary_data = {
                    'Metric': [
                        'Total packages in uploaded file',
                        'Packages found in our system',
                        'Packages missing in our system',
                        'Packages not checked out in our system',
                        'TOTAL ATTENTION REQUIRED'
                    ],
                    'Count': [
                        len(uploaded_tracking_ids),
                        len(uploaded_tracking_ids & export_tracking_ids),
                        len(missing_in_system),
                        len(not_checked_out_df),
                        len(attention_packages)  # Total packages needing attention
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            # Store file path in session for download
            session['audit_file_path'] = audit_filepath
            session['audit_filename'] = audit_filename
            
            # Prepare results for display
            results = {
                'uploaded_count': len(uploaded_tracking_ids),
                'found_count': len(uploaded_tracking_ids & export_tracking_ids),
                'missing_count': len(missing_in_system),
                'not_checked_out_count': len(not_checked_out_df),
                'total_attention_count': len(attention_packages),
                'missing_samples': list(missing_in_system)[:10],  # Show first 10 as samples
            }
            
            return render_template("po_audit.html", results=results, success="Audit completed successfully!")
            
        except Exception as e:
            import traceback
            print(f"PO Audit Error: {traceback.format_exc()}")
            return render_template("po_audit.html", error=f"Error processing file: {str(e)}")
    
    return render_template("po_audit.html")

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
    # FAST: Get minimal student data for autocomplete only
    conn = get_conn()
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("SELECT DISTINCT roomNumber FROM studentmaster WHERE roomNumber IS NOT NULL AND roomNumber != '' ORDER BY roomNumber LIMIT 500")
    rooms = [row['roomNumber'] for row in cur.fetchall()]
    cur.close()
    conn.close()
    
    if request.method == "POST":
        # Handle form submission
        tracking_id = normalize_tracking_id(request.form.get("tracking_id"))
        room = (request.form.get("roomNumber") or "").strip()
        initials = session.get('user_initials', '').strip().upper()
        perishable = request.form.get("perishable", "no")
        package_type = request.form.get("package_type", "Other")
        notes = (request.form.get("notes") or "").strip()

        if not tracking_id or not room or not initials:
            return render_template("checkin.html",
                                   rooms=rooms,
                                   package_types=PACKAGE_TYPES,
                                   user_initials=session.get('display_name', ''),
                                   error="Tracking ID, Room Number, and Initials are required.")

        # Check if tracking ID exists
        if check_tracking_exists(tracking_id):
            return render_template("checkin.html",
                                   rooms=rooms,
                                   package_types=PACKAGE_TYPES,
                                   user_initials=session.get('display_name', ''),
                                   error=f"Tracking ID {tracking_id} already exists in the system.")

        # Create package
        check_dt = datetime.now()
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        cur.execute("INSERT INTO package_log (TrackingID, DateTime) VALUES (%s,%s)", 
                   (tracking_id, check_dt))
        conn.commit()

        cur.execute("""
            INSERT INTO postofficelog
            (trackingId, roomNumber, checkInDate, type, checkInEmpInitials, checkoutStatus, perishable, notes)
            VALUES (%s,%s,%s,%s,%s, 0, %s, %s)
        """, (tracking_id, room, check_dt, package_type, initials, perishable, notes))
        conn.commit()
        cur.close()
        conn.close()

        # Update export file in background
        import threading
        threading.Thread(target=update_export_file).start()
        
        # Notify browsers
        socketio.emit("refresh_recent")
        
        # Return success with minimal data
        return render_template("checkin.html",
                               rooms=rooms,
                               package_types=PACKAGE_TYPES,
                               user_initials=session.get('display_name', ''),
                               success=f"Package {tracking_id} checked in successfully!")

    # GET REQUEST - FAST: Return only form, data loads via JavaScript
    return render_template("checkin.html",
                           rooms=rooms,
                           package_types=PACKAGE_TYPES,
                           user_initials=session.get('display_name', ''))

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
    """Serve the enhanced Excel export file"""
    try:
        # Create/update the enhanced Excel file
        excel_filepath = update_export_file()
        
        # Serve the Excel file
        return send_file(
            excel_filepath,
            as_attachment=True,
            download_name=f"PackageLog_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print(f"Export error: {e}")
        # Fallback to basic CSV if Excel fails
        if not os.path.exists(EXPORT_FILE_PATH):
            # Create basic CSV as fallback
            conn = get_conn()
            cur = conn.cursor(buffered=True)
            cur.execute("""
                SELECT
                    p.TrackingID,
                    COALESCE(s1.firstName, s2.firstName, '') AS firstName,  
                    COALESCE(s1.lastName, s2.lastName, '') AS lastName,     
                    COALESCE(s1.roomNumber, s2.roomNumber, l.roomNumber) AS roomNumber,
                    COALESCE(s1.hallName, s2.hallName, '') AS hallName,
                    l.checkInDate, l.checkInEmpInitials AS checkinEmpInitials,
                    l.type AS package_type,
                    l.checkoutStatus, l.checkoutDate, l.checkoutEmpInitials AS checkoutEmpInitials,
                    l.perishable,
                    l.notes
                FROM postofficelog l
                LEFT JOIN package_log p ON p.TrackingID = l.trackingId
                LEFT JOIN studentmaster s1 ON s1.roomNumber = l.roomNumber
                LEFT JOIN studentmaster s2 ON s2.Id = l.roomNumber
                ORDER BY l.checkInDate DESC, l.Id DESC
            """)
            rows = cur.fetchall()
            headers = [c[0] for c in cur.description]
            cur.close()
            conn.close()

            with open(EXPORT_FILE_PATH, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(headers)
                w.writerows(rows)
        
        return send_file(
            EXPORT_FILE_PATH,
            as_attachment=True,
            download_name="PackageLog.csv",
            mimetype="text/csv"
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
            action = request.form.get('action')
            
            if action == 'update_info':
                # Update basic info
                new_username = request.form.get('username', '').strip()
                initials = request.form.get('initials', '').strip().upper()
                fullname = request.form.get('fullname', '').strip()
                title = request.form.get('title', '').strip().upper()
                is_active = 'is_active' in request.form
                
                # Check username if changed
                if new_username != user['username']:
                    cur.execute("SELECT id FROM initialscheck WHERE hall_id = %s AND username = %s AND id != %s",
                               (user['hall_id'], new_username, user_id))
                    if cur.fetchone():
                        flash("Username already exists in this hall", "error")
                        return redirect(url_for('admin_edit_user_web', user_id=user_id))
                
                # Update permissions based on title or custom
                if title in ['HD', 'AHD']:
                    can_checkin = can_checkout = can_view_other_halls = can_manage_users = can_manage_halls = True
                elif title == 'OA':
                    can_checkin = can_checkout = True
                    can_view_other_halls = can_manage_users = can_manage_halls = False
                else:
                    # For custom roles
                    can_checkin = 'can_checkin' in request.form
                    can_checkout = 'can_checkout' in request.form
                    can_view_other_halls = 'can_view_other_halls' in request.form
                    can_manage_users = 'can_manage_users' in request.form
                    can_manage_halls = 'can_manage_halls' in request.form
                
                cur.execute("""
                    UPDATE initialscheck 
                    SET username = %s, initials = %s, fullName = %s, title = %s,
                        can_checkin = %s, can_checkout = %s, can_view_other_halls = %s,
                        can_manage_users = %s, can_manage_halls = %s, is_active = %s
                    WHERE id = %s
                """, (new_username, initials, fullname, title,
                      can_checkin, can_checkout, can_view_other_halls,
                      can_manage_users, can_manage_halls, is_active, user_id))
                
                conn.commit()
                log_audit(session['user_id'], 'USER_UPDATED', 
                         f"Updated user {user['username']}", request)
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
        
        # Get existing shifts for current week
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())  # Monday
        end_of_week = start_of_week + timedelta(days=6)  # Sunday
        
        cur.execute("""
            SELECT s.*, i.initials, i.fullName
            FROM shift_schedule s
            JOIN initialscheck i ON s.user_id = i.id
            WHERE s.hall_id = %s 
            AND s.shift_date BETWEEN %s AND %s
            ORDER BY s.shift_date, s.start_time
        """, (session['user_hall_id'], start_of_week.date(), end_of_week.date()))
        
        shifts = cur.fetchall()
        
        # Get shift templates
        cur.execute("""
            SELECT * FROM shift_templates 
            WHERE hall_id = %s 
            ORDER BY start_time
        """, (session['user_hall_id'],))
        templates = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Generate week data
        week_days = []
        for i in range(7):
            day_date = start_of_week + timedelta(days=i)
            week_days.append({
                'day_name': day_date.strftime('%A'),
                'date': day_date.strftime('%Y-%m-%d'),
                'display_date': day_date.strftime('%b %d')
            })
        
        # Generate time slots (12am to 11pm, 1-hour slots)
        time_slots = []
        for hour in range(24):
            start_hour = hour
            end_hour = (hour + 1) % 24
            am_pm_start = "AM" if start_hour < 12 else "PM"
            am_pm_end = "AM" if end_hour < 12 else "PM"
            
            display_start = start_hour if start_hour <= 12 else start_hour - 12
            display_end = end_hour if end_hour <= 12 else end_hour - 12
            
            time_slots.append({
                'start_time': f"{start_hour:02d}:00:00",
                'end_time': f"{end_hour:02d}:00:00",
                'display': f"{display_start if display_start != 0 else 12}{am_pm_start} - {display_end if display_end != 0 else 12}{am_pm_end}"
            })
        
        return render_template("shift_scheduler.html",
                             users=users,
                             shifts=shifts,
                             templates=templates,
                             week_days=week_days,
                             time_slots=time_slots,
                             current_week_start=start_of_week.strftime('%Y-%m-%d'))
        
    except Exception as e:
        print(f"SHIFT SCHEDULER ERROR: {e}")
        flash("Error loading shift scheduler", "error")
        return redirect(url_for('checkin'))
@app.route("/api/shifts", methods=["GET", "POST"])
@login_required
def api_shifts():
    try:
        if request.method == "POST":
            if not session.get("can_manage_shifts"):
                return jsonify({"success": False, "error": "Permission denied"}), 403

            data = request.get_json(silent=True)
            if not data:
                return jsonify({"success": False, "error": "Expected JSON body"}), 400

            required_fields = ["user_id", "shift_date", "start_time", "end_time"]
            missing = [f for f in required_fields if f not in data or data[f] in (None, "")]
            if missing:
                return jsonify({"success": False, "error": f"Missing required field(s): {', '.join(missing)}"}), 400

            conn = get_conn()
            cur = conn.cursor(dictionary=True)

            try:
                if data.get("id"):
                    # UPDATE
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
                        data.get("notes", None),
                        data["id"],
                        session["user_hall_id"],
                    ))
                    shift_id = data["id"]

                else:
                    # INSERT
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
                        data.get("notes", None),
                        session["user_id"],
                    ))
                    shift_id = cur.lastrowid

                conn.commit()

                # Return shift — SQL already formats everything as strings
                cur.execute("""
                    SELECT
                        s.id,
                        s.hall_id,
                        s.user_id,
                        DATE_FORMAT(s.shift_date, '%Y-%m-%d') AS shift_date,
                        CAST(s.start_time AS CHAR) AS start_time,
                        CAST(s.end_time   AS CHAR) AS end_time,
                        s.shift_type,
                        s.notes,
                        i.initials,
                        i.fullName
                    FROM shift_schedule s
                    JOIN initialscheck i ON s.user_id = i.id
                    WHERE s.id = %s
                """, (shift_id,))

                shift = cur.fetchone()
                return jsonify({"success": True, "shift": shift})

            finally:
                cur.close()
                conn.close()

    except Exception as e:
        print(f"SHIFTS API ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/shifts/<int:shift_id>", methods=["DELETE"])
@login_required
@permission_required('can_manage_shifts')
def delete_shift(shift_id):
    """Delete a shift"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        
        cur.execute("DELETE FROM shift_schedule WHERE id = %s AND hall_id = %s", 
                   (shift_id, session['user_hall_id']))
        conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"DELETE SHIFT ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/my_schedule")
@login_required
def my_schedule():
    """User's personal schedule view"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get user's shifts for next 2 weeks
        today = datetime.now().date()
        two_weeks_later = today + timedelta(days=14)
        
        cur.execute("""
            SELECT s.*, h.hall_name, h.hall_code
            FROM shift_schedule s
            JOIN halls h ON s.hall_id = h.id
            WHERE s.user_id = %s 
            AND s.shift_date BETWEEN %s AND %s
            ORDER BY s.shift_date, s.start_time
        """, (session['user_id'], today, two_weeks_later))
        
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
    """Export schedule as PDF"""
    try:
        # Get schedule data
        user_id = request.args.get('user_id', session['user_id'])
        start_date = request.args.get('start_date', datetime.now().date().strftime('%Y-%m-%d'))
        end_date = request.args.get('end_date', (datetime.now() + timedelta(days=13)).date().strftime('%Y-%m-%d'))
        
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        
        # Get hall info
        cur.execute("""
            SELECT hall_name
            FROM halls
            WHERE id = %s
        """, (session["user_hall_id"],))
        hall = cur.fetchone()
        hall_name = hall["hall_name"] if hall else "Unknown Hall"

        
        # Get shifts
        cur.execute("""
            SELECT s.*, i.initials, i.fullName
            FROM shift_schedule s
            JOIN initialscheck i ON s.user_id = i.id
            WHERE s.hall_id = %s
            AND s.shift_date BETWEEN %s AND %s
            ORDER BY s.shift_date, s.start_time
        """, (session["user_hall_id"], start_date, end_date))
        
        shifts = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Generate HTML for PDF
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Shift Schedule - {user_info['fullName']}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .header {{ text-align: center; margin-bottom: 30px; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                .header h1 {{ margin: 0; color: #333; }}
                .header .subtitle {{ color: #666; margin-top: 5px; }}
                .schedule-table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                .schedule-table th, .schedule-table td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
                .schedule-table th {{ background-color: #f5f5f5; font-weight: bold; }}
                .shift-cell {{ background-color: #e8f5e8; border-radius: 4px; padding: 5px; margin: 2px 0; }}
                .no-shifts {{ color: #999; font-style: italic; }}
                .footer {{ margin-top: 30px; text-align: center; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Shift Schedule</h1>
                <div class="subtitle">
                    <strong>Name:</strong> {user_info['fullName']} ({user_info['initials']})<br>
                    <strong>Hall:</strong> {user_info['hall_name']}<br>
                    <strong>Period:</strong> {start_date} to {end_date}
                </div>
            </div>
            
            <table class="schedule-table">
                <thead>
                    <tr>
                        <th width="15%">Date</th>
                        <th width="15%">Day</th>
                        <th width="30%">Time Slot</th>
                        <th width="20%">Duration</th>
                        <th width="20%">Type</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        if shifts:
            for shift in shifts:
                shift_date = datetime.strptime(str(shift['shift_date']), '%Y-%m-%d')
                start_time = datetime.strptime(str(shift['start_time']), '%H:%M:%S')
                end_time = datetime.strptime(str(shift['end_time']), '%H:%M:%S')
                
                # Calculate duration
                duration = end_time - start_time
                hours = duration.seconds // 3600
                minutes = (duration.seconds % 3600) // 60
                
                html_content += f"""
                    <tr>
                        <td>{shift_date.strftime('%Y-%m-%d')}</td>
                        <td>{shift_date.strftime('%A')}</td>
                        <td>{start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}</td>
                        <td>{hours}h {minutes}m</td>
                        <td>{shift.get('shift_type', 'Regular')}</td>
                    </tr>
                """
        else:
            html_content += f"""
                <tr>
                    <td colspan="5" class="no-shifts" style="text-align: center; padding: 20px;">
                        No shifts scheduled for this period
                    </td>
                </tr>
            """
        
        html_content += f"""
                </tbody>
            </table>
            
            <div class="footer">
                <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} | Clement Package System</p>
            </div>
        </body>
        </html>
        """
        
        # Generate PDF (requires wkhtmltopdf installed)
        try:
            pdf = pdfkit.from_string(html_content, False)
            
            return send_file(
                BytesIO(pdf),
                as_attachment=True,
                download_name=f"schedule_{user_info['initials']}_{start_date}_to_{end_date}.pdf",
                mimetype="application/pdf"
            )
        except Exception as e:
            # Fallback to HTML if PDF generation fails
            print(f"PDF generation failed: {e}")
            return html_content
            
    except Exception as e:
        print(f"EXPORT SCHEDULE ERROR: {e}")
        return f"Error generating schedule: {str(e)}", 500

# Add navigation link for users
@app.route("/schedule")
@login_required
def schedule_page():
    """Schedule page for all users"""
    return redirect(url_for('my_schedule'))

if __name__ == "__main__":
    print("Starting Flask-SocketIO...")
    
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
        print("   2. Is the database 'clement_package_log_1' created?")
        print("   3. Are the credentials in DB_CONFIG correct?")
        input("Press Enter to exit...")
        sys.exit(1)
    
    # Create initial export file if it doesn't exist
    if not os.path.exists(EXPORT_FILE_PATH):
        print("Creating initial export file...")
        update_export_file()
    
    socketio.run(app, host="127.0.0.1", port=port, debug=True, use_reloader=False)