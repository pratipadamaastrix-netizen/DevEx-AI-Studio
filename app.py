#!/usr/bin/env python3
"""
Devex AI Studios Platform - v1.0.0
Studio Operations + Client Comms Pipeline + Quote Wizard + Delivery Documentation Engine
"""

try:
    from dotenv import load_dotenv
    load_dotenv()  # Loads .env if python-dotenv is installed
except ImportError:
    pass  # python-dotenv not installed — use environment variables directly

from flask import (Flask, render_template, request, jsonify, send_file,
                   send_from_directory, redirect, session, url_for, abort)
import sys
import sqlite3
import os
import json
from datetime import datetime
from io import BytesIO
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import uuid
import functools
import urllib
import base64
import requests
from urllib.parse import quote as urlquote
import hashlib
import random
import string
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from app_quote import register_quote_routes, run_quote_migrations
import google.generativeai as genai
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

import PyPDF2
# import app_spec
# import app_rooms

# ── Rate limiting ─────────────────────────────────────────────────────────
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _limiter_available = True
except ImportError:
    _limiter_available = False
    print("  ! Flask-Limiter not installed — rate limiting disabled. Run: pip install Flask-Limiter")

# ── Quote Portal (QP1.0) ──────────────────────────────────────────────────

# ── Spec Scope Builder + Meeting Rooms ────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Project'))

from app_spec import register_spec_routes, run_spec_migrations
from app_rooms import register_room_routes, run_room_migrations

app = Flask(__name__)

from flask_session import Session

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True

Session(app)

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'photos'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'devex-change-this-in-production-' + str(uuid.uuid4()))

# Ensure upload and static directories exist at startup
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), 'static'), exist_ok=True)

# ── Rate limiter init ─────────────────────────────────────────────────────
if _limiter_available:
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[],
        storage_uri="memory://"
    )
else:
    # Stub so route decorators don't fail
    class _NoopLimiter:
        def limit(self, *a, **kw):
            return lambda f: f
        def exempt(self, f):
            return f
    limiter = _NoopLimiter()

# ── Quote Portal routes (QP1.0) ────────────────────────────────────────

# ── Spec Scope Builder routes ─────────────────────────────────────────────
register_spec_routes(app)

# ── Meeting Rooms routes ──────────────────────────────────────────────────
register_room_routes(app)

# --------- RUN DATABASE MIGRATIONS ON STARTUP ---------
############
# ---------------- DATABASE PATH ----------------


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

ENGINE_DB_PATH = os.path.join(DATA_DIR, "engine.db")
QUOTE_DB = ENGINE_DB_PATH

# ---------------- DB CONNECTION ----------------

def get_engine_db():
    """Get database connection for Engine/Artefact system"""
    conn = sqlite3.connect(ENGINE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn
def get_quote_db():
    """Database connection for Quote Portal"""
    conn = sqlite3.connect(ENGINE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
from app_quote import register_quote_routes
register_quote_routes(app, get_quote_db)

WA_DB_PATH = os.path.join(DATA_DIR, "wa.db")
def wa_get_db():
    """Get database connection for WhatsApp sessions"""
    conn = sqlite3.connect(WA_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
# --------- RUN DATABASE MIGRATIONS ON STARTUP ---------
with app.app_context():

    conn_quote = sqlite3.connect(QUOTE_DB)

    run_quote_migrations(conn_quote)

    conn_quote.close()
# ---------------- DATABASE INIT ----------------

def init_db():

    conn = get_engine_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS fm_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT,
    estate TEXT,
    unit TEXT,
    customer TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    source TEXT,
    priority TEXT,
    category TEXT,
    status TEXT,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


# run database setup
with app.app_context():
    init_db()
# =========================================================================
# DEVEX AUTH LAYER
# =========================================================================


DX_ADMIN_USER = os.environ.get('DX_ADMIN_USER', 'admin')
DX_ADMIN_PASS = os.environ.get('DX_ADMIN_PASS', 'devex2024')  # Override via .env

def dx_admin_required(f):
    """Decorator: require admin session. Redirects to login if not authenticated."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('dx_authenticated') or session.get('dx_role') != 'admin':
            return redirect(url_for('dx_login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def dx_client_required(f):
    """Decorator: require client session scoped to a project ref."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('dx_authenticated') or session.get('dx_role') not in ('admin', 'client'):
            return redirect(url_for('dx_login', type='client', next=request.path))
        return f(*args, **kwargs)
    return decorated

@app.route('/login')
def dx_login():
    """Devex AI Studios login page."""
    login_type = request.args.get('type', 'admin')
    next_url   = request.args.get('next', '')
    return render_template('public/login.html',
                           login_type=login_type,
                           next=next_url)

@app.route('/login', methods=['POST'])
@limiter.limit("10 per minute")
def dx_login_post():
    """Handle login form submission."""
    login_type   = request.form.get('login_type', 'admin')
    password     = request.form.get('password', '').strip()
    next_url     = request.form.get('next', '').strip()
    form_username = request.form.get('username', '').strip()
    form_ref      = request.form.get('project_ref', '').strip().upper()

    def _fail(msg):
        return render_template('public/login.html',
                               login_type=login_type,
                               error=msg,
                               next=next_url,
                               form_username=form_username,
                               form_ref=form_ref)

    if login_type == 'client':
        # Client: validate project ref + access code
        if not form_ref or not password:
            return _fail('Project reference and access code are required.')
        conn = get_engine_db()
        row = conn.execute(
            "SELECT ref, access_code, contact_name FROM quote_requests WHERE ref = ?",
            (form_ref,)
        ).fetchone()
        conn.close()
        if not row:
            return _fail('Project reference not found.')
        stored_code = (row['access_code'] or '').strip()
        if not stored_code or stored_code != password:
            return _fail('Access code is incorrect.')
        session.clear()
        session['dx_authenticated'] = True
        session['dx_role']          = 'client'
        session['dx_user']          = row['contact_name'] or form_ref
        session['dx_project_ref']   = form_ref
        return redirect(next_url or url_for('dx_client_portal'))

    else:
        # Admin login
        if not form_username or not password:
            return _fail('Username and password are required.')
        if form_username != DX_ADMIN_USER or password != DX_ADMIN_PASS:
            return _fail('Invalid credentials.')
        session.clear()
        session['dx_authenticated'] = True
        session['dx_role']          = 'admin'
        session['dx_user']          = form_username
        return redirect(next_url or url_for('dx_studio_dashboard'))

@app.route('/logout')
def dx_logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for('dx_login'))

# =========================================================================
# STUDIO ROUTES  (/studio/*)
# Admin-facing Devex routes that wrap existing handlers
# =========================================================================

@app.route('/studio')
@app.route('/studio/dashboard')
def dx_studio_dashboard():
    """Devex AI Studios — main admin dashboard."""
    # Quote stats
    conn = get_engine_db()
    quote_stats = {}
    try:
        quote_stats['total']     = conn.execute("SELECT COUNT(*) FROM quote_requests").fetchone()[0]
        quote_stats['new']       = conn.execute("SELECT COUNT(*) FROM quote_requests WHERE status='NEW'").fetchone()[0]
        quote_stats['reviewing'] = conn.execute("SELECT COUNT(*) FROM quote_requests WHERE status='REVIEWING'").fetchone()[0]
        quote_stats['won']       = conn.execute("SELECT COUNT(*) FROM quote_requests WHERE status='WON'").fetchone()[0]
        recent_quotes = [dict(r) for r in conn.execute(
            "SELECT ref, contact_name, project_name, status, complexity_score "
            "FROM quote_requests ORDER BY created_at DESC LIMIT 6"
        ).fetchall()]
    except Exception:
        quote_stats = {'total':0,'new':0,'reviewing':0,'won':0}
        recent_quotes = []

    # Ticket stats
    ticket_stats = {}
    try:
        ticket_stats['open']   = conn.execute(
            "SELECT COUNT(*) FROM fm_tickets WHERE status NOT IN ('DONE','CANCELLED')"
        ).fetchone()[0]
        ticket_stats['urgent'] = conn.execute(
            "SELECT COUNT(*) FROM fm_tickets WHERE priority='urgent' AND status NOT IN ('DONE','CANCELLED')"
        ).fetchone()[0]
        recent_tickets = [dict(r) for r in conn.execute(
            "SELECT ref, customer, summary, priority, status FROM fm_tickets "
            "ORDER BY updated_at DESC LIMIT 6"
        ).fetchall()]
    except Exception:
        ticket_stats = {'open':0,'urgent':0}
        recent_tickets = []

    # Recent artefacts
    try:
        recent_artefacts = [dict(r) for r in conn.execute(
            "SELECT id, title, artefact_type, status FROM artefacts ORDER BY updated_at DESC LIMIT 6"
        ).fetchall()]
    except Exception:
        recent_artefacts = []

    conn.close()

    # Comms sessions
    try:
        wa_conn = wa_get_db()
        comms_sessions = [dict(r) for r in wa_conn.execute(
            "SELECT wa_from, display_name, message_count, last_message_at, status "
            "FROM wa_sessions ORDER BY last_message_at DESC LIMIT 5"
        ).fetchall()]
        wa_conn.close()
    except Exception:
        comms_sessions = []

    return render_template('studio/dashboard.html',
                           quote_stats=quote_stats,
                           ticket_stats=ticket_stats,
                           recent_quotes=recent_quotes,
                           recent_tickets=recent_tickets,
                           recent_artefacts=recent_artefacts,
                           comms_sessions=comms_sessions,
                           active_nav='dashboard')


@app.route('/studio/pipeline')
def dx_studio_pipeline():
    """Alias: Studio Operations > Pipeline Console → existing FM dashboard."""
    return redirect('/fm/dashboard')


@app.route('/studio/import')
def dx_studio_import():
    """Alias: Studio Operations > Message Import → FM dashboard import tab."""
    return redirect('/fm/dashboard#import')


@app.route('/studio/comms')
def dx_studio_comms():
    """Alias: Studio Operations > Comms Monitor → WA monitor."""
    return render_template('wa/monitor.html', active_nav='wa_monitor')


@app.route('/studio/quotes')
def dx_studio_quotes():
    """
    Studio quotes pipeline.
    Registered as alias for /ops/quotes — calls the same handler
    after app_quote.py registers ops_quotes.
    We use a redirect here to avoid forward-reference before registration.
    The redirect is transparent to the browser.
    """
    qs = request.query_string.decode()
    target = '/ops/quotes'
    if qs:
        target += '?' + qs
    return redirect(target)


@app.route('/studio/quotes/<ref>')
def dx_studio_quote_detail(ref):
    """Alias: Studio quote detail → ops quote detail."""
    return redirect(f'/ops/quotes/{ref}')


# =========================================================================
# CLIENT PORTAL — Phase 5
# /projects/* — client-facing, scoped to their project ref
# =========================================================================

@app.route('/projects')
@dx_client_required
def dx_client_portal():
    """Client portal home — shows the client's project quote status."""
    ref = session.get('dx_project_ref')
    if not ref:
        return redirect(url_for('dx_login', type='client'))

    conn = get_engine_db()
    try:
        quote = conn.execute(
            "SELECT * FROM quote_requests WHERE ref = ?", (ref,)
        ).fetchone()
    except Exception:
        quote = None

    if not quote:
        conn.close()
        return render_template('projects/portal_error.html',
                               message='Project not found. Please contact us.')

    quote = dict(quote)

    # Parse scope JSON
    scope = {}
    try:
        raw = quote.get('deepseek_scope_json') or '{}'
        scope = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        scope = {}

    conn.close()
    return render_template('projects/portal.html', quote=quote, scope=scope)


@app.route('/studio/projects')
def dx_studio_projects():
    """Active projects overview (quotes in active states)."""
    conn = get_engine_db()
    try:
        projects = [dict(r) for r in conn.execute(
            """SELECT ref, contact_name, project_name, project_type,
                      complexity_score, status, created_at
               FROM quote_requests
               WHERE status IN ('REVIEWING','SCOPED','SENT','WON')
               ORDER BY created_at DESC"""
        ).fetchall()]
    except Exception:
        projects = []
    conn.close()
    return render_template('studio/projects.html',
                           projects=projects,
                           active_nav='projects')


# ── Old /fm/* and /wa/* UI aliases (keep working + add studio aliases) ──

@app.route('/fm/dashboard-redirect')
def fm_dashboard_redirect():
    """Backward-compat: redirect to studio pipeline."""
    return redirect('/studio/pipeline')

@app.route('/wa/monitor-redirect')
def wa_monitor_redirect():
    """Backward-compat: redirect to studio comms."""
    return redirect('/studio/comms')

# ===== DATABASE PATHS - CF1.1 PHYSICAL SPLIT =====
# Fire Door system uses its own database
FIRE_DOOR_DB_PATH = 'fire_door_reports.db'
# Engine system uses separate database (CF1.1: Physical split complete)


# ===== UTILITIES =====
def create_ticket(name, flat, address, phone, email, issue, urgency):

    conn = get_engine_db()

    # Ensure table exists (important for server)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS fm_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT,
        estate TEXT,
        unit TEXT,
        customer TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        source TEXT,
        priority TEXT,
        category TEXT,
        status TEXT,
        summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    ref = "FM-" + str(uuid.uuid4())[:8].upper()

    conn.execute(
    """
    INSERT INTO fm_tickets
    (ref, estate, unit, customer, phone, email, address, source, priority, category, status, summary)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
    ref,
    address,
    flat,
    name,
    phone,
    email,
    address,
    "webchat",
    urgency,
    "general",
    "NEW",
    issue
    )
    )

    conn.commit()
    conn.close()

    return ref

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

##########################################################
def extract_text_from_document(file):

    try:
        filename = file.filename.lower()

        # ===== PDF FILE =====
        if filename.endswith(".pdf"):

            from PyPDF2 import PdfReader

            # Reset file pointer (important)
            file.seek(0)

            reader = PdfReader(file)
            text = ""

            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text

            if not text.strip():
                return "\n[Document]: No readable text found in PDF."

            return "\n[Document]: " + text[:1000]

        # ===== OTHER FILE TYPES =====
        else:
            return "\n[Document uploaded]"

    except Exception as e:
        print("Doc error:", e)
        return "\n[Document]: Failed to read file."

def analyze_image_from_url(image_url):

    try:
        from PIL import Image
        from io import BytesIO

        response = requests.get(image_url)

        if response.status_code != 200:
            print("Image download failed")
            return ""

        img = Image.open(BytesIO(response.content))

        models = [
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b"
        ]

        prompt = """
Analyze this image or document and describe the software project requirement or feature shown.
"""

        for model_name in models:

            try:

                print("Trying model:", model_name)

                model = genai.GenerativeModel(model_name)

                result = model.generate_content([prompt, img])

                if result and result.text:
                    return result.text

            except Exception as e:
                print("Model failed:", model_name, e)
                continue

        return "Unable to analyze image."

    except Exception as e:
        print("Gemini error:", e)
        return ""
    
def analyze_uploaded_image(file):

    try:
        from PIL import Image

        img = Image.open(file)

        models = [
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b"
        ]

        prompt = """
Analyze this image or document and describe the software project requirement or feature shown.
"""

        for model_name in models:

            try:

                print("Trying model:", model_name)

                model = genai.GenerativeModel(model_name)

                result = model.generate_content([prompt, img])

                if result and result.text:
                    return "\n[Image Analysis]: " + result.text

            except Exception as e:

                print("Model failed:", model_name, e)

                continue

        return ""

    except Exception as e:

        print("Gemini error:", e)

        return ""
    ##############################################
def get_fire_door_db():
    """Get database connection for Fire Door legacy system"""
    conn = sqlite3.connect(FIRE_DOOR_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# def get_engine_db():
    """Get database connection for Engine/Artefact system"""
    conn = sqlite3.connect(ENGINE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_db():
    """DEPRECATED: Use get_fire_door_db() or get_engine_db() instead
    For backward compatibility, defaults to engine DB"""
    return get_engine_db()

def init_db():
    """Initialize databases - CF1.1 Physical split"""
    print("Initializing databases...")
    
    # ===== ENGINE DATABASE =====
    engine_exists = os.path.exists(ENGINE_DB_PATH)
    
    if not engine_exists:
        print(f"  Creating Engine DB: {ENGINE_DB_PATH}")
        conn = sqlite3.connect(ENGINE_DB_PATH)
        with open('schema_engine.sql', 'r') as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
        print(f"  ✓ Engine database created: {ENGINE_DB_PATH}")
    else:
        print(f"  ✓ Engine database exists: {ENGINE_DB_PATH}")
    
    # Run Engine migrations
    conn_engine = sqlite3.connect(ENGINE_DB_PATH)
    run_engine_migrations(conn_engine)
    conn_engine.commit()
    conn_engine.close()
    print("  ✓ Engine migrations complete")
    
    # ===== FIRE DOOR DATABASE =====
    fire_door_exists = os.path.exists(FIRE_DOOR_DB_PATH)
    
    if not fire_door_exists:
        print(f"  Creating Fire Door DB: {FIRE_DOOR_DB_PATH}")
        conn = sqlite3.connect(FIRE_DOOR_DB_PATH)
        with open('schema_fire_door.sql', 'r') as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
        print(f"  ✓ Fire Door database created: {FIRE_DOOR_DB_PATH}")
    else:
        print(f"  ✓ Fire Door database exists: {FIRE_DOOR_DB_PATH}")
    
    # Run Fire Door migrations
    conn_fire_door = sqlite3.connect(FIRE_DOOR_DB_PATH)
    run_fire_door_migrations(conn_fire_door)
    conn_fire_door.commit()
    conn_fire_door.close()
    print("  ✓ Fire Door migrations complete")
    # ===== FM OPERATIONS MODULE =====
    conn_fm = sqlite3.connect(ENGINE_DB_PATH)
    run_fm_migrations(conn_fm)
    conn_fm.commit()
    conn_fm.close()
    print("  ✓ FM Operations migrations complete")

    # ===== QUOTE PORTAL (QP1.0) =====
    conn_quote = sqlite3.connect(ENGINE_DB_PATH)
    run_quote_migrations(conn_quote)
    conn_quote.commit()
    conn_quote.close()
    print("  ✓ Quote Portal migrations complete")

    # ===== SPEC SCOPE BUILDER =====
    conn_spec = sqlite3.connect(ENGINE_DB_PATH)
    run_spec_migrations(conn_spec)
    conn_spec.commit()
    conn_spec.close()
    print("  ✓ Spec Scope Builder migrations complete")

    # ===== MEETING ROOMS =====
    conn_rooms = sqlite3.connect(ENGINE_DB_PATH)
    run_room_migrations(conn_rooms)
    conn_rooms.commit()
    conn_rooms.close()
    print("  ✓ Meeting Rooms migrations complete")

    print("✓ All databases initialized")

def table_exists(conn, table_name):
    """Check if table exists in database"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None

def column_exists(conn, table, column):
    """Check if column exists in table"""
    if not table_exists(conn, table):
        return False
    cursor = conn.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    return column in cols

def ensure_column(conn, table, col_def_sql):
    """Add column if it doesn't exist"""
    if not table_exists(conn, table):
        print(f"  ! Table {table} does not exist, skipping column")
        return
    col_name = col_def_sql.split()[0]
    if not column_exists(conn, table, col_name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def_sql}")
        print(f"  + Added {table}.{col_name}")

def run_engine_migrations(conn):
    """Run Engine database migrations"""
    print("  Running Engine migrations...")
    # Currently no Engine-specific migrations needed
    # Schema v1.0 is stable
    # Future migrations would go here
    pass

def run_fire_door_migrations(conn):
    """Run Fire Door database migrations"""
    print("  Running Fire Door migrations...")
    
    # Reports table migrations
    ensure_column(conn, 'reports', "workflow_status TEXT DEFAULT 'Draft'")
    ensure_column(conn, 'reports', "client_total DECIMAL")
    
    # Properties table migrations
    ensure_column(conn, 'properties', "project_type TEXT DEFAULT 'fire_door'")
    ensure_column(conn, 'properties', "site_plan_notes TEXT")
    
    # Photos table migrations
    ensure_column(conn, 'photos', "photo_role TEXT DEFAULT 'door_evidence'")
    
    # Backfill photo_role from photo_type
    if table_exists(conn, 'photos') and column_exists(conn, 'photos', 'photo_role'):
        cursor = conn.execute("SELECT COUNT(*) FROM photos WHERE photo_role IS NULL OR TRIM(photo_role) = ''")
        count = cursor.fetchone()[0]
        
        if count > 0:
            conn.execute("""
                UPDATE photos
                SET photo_role = CASE
                    WHEN photo_type IN ('site_plan') THEN 'property_plan'
                    WHEN photo_type IN ('location') THEN 'location_access'
                    ELSE 'door_evidence'
                END
                WHERE photo_role IS NULL OR TRIM(photo_role) = ''
            """)
            print(f"  + Backfilled photo_role for {count} photos")


def run_fm_migrations(conn):
    """Run FM Operations migrations — idempotent, safe to call on every start."""
    print("  Running FM migrations...")
    ensure_column(conn, 'fm_tickets', "phone TEXT")
    ensure_column(conn, 'fm_tickets', "address TEXT")
    ensure_column(conn, 'fm_tickets', "email TEXT")
    if not table_exists(conn, 'fm_tickets'):
        print("  Creating FM tables from schema_fm.sql...")
        with open('schema_fm.sql', 'r') as f:
            conn.executescript(f.read())
        print("  ✓ FM tables created")
    else:
        print("  FM tables already exist")
    # WA bridge tables
    if not table_exists(conn, 'wa_sessions'):
        print("  Creating WA bridge tables from schema_wa.sql...")
        with open('schema_wa.sql', 'r') as f:
            conn.executescript(f.read())
        print("  ✓ WA bridge tables created")
    else:
        print("  WA bridge tables already exist")


def generate_filename(original_filename, site_name, internal_location):
    """Generate unique filename with metadata"""
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'jpg'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = str(uuid.uuid4())[:8]
    
    # Sanitize for filename
    site = secure_filename(site_name.replace(' ', '_'))[:20]
    location = secure_filename(internal_location.replace(' ', '_'))[:20] if internal_location else 'general'
    
    return f"{site}_{location}_{timestamp}_{unique_id}.{ext}"

# ===== ROUTES =====

# ===== LEGACY ROUTES (NOT USED BY ENGINE - KEPT FOR BACKWARD COMPATIBILITY) =====
# These routes are from the old Fire Door report builder system.
# They use legacy tables (reports, properties, fire_doors, etc.) NOT the engine.
# DO NOT LINK TO THESE FROM THE MAIN UI. Engine uses /artefacts/* instead.
# ==================================================================================

@app.route('/contractor-reports')
def contractor_reports():
    """LEGACY DEPRECATED: Use /fire-door/reports instead"""
    conn = get_fire_door_db()  # CF1.2: Fix - Use Fire Door DB for reports table
    cursor = conn.execute('''
        SELECT id, report_title, client_name, site_name, 
               inspection_date, status, workflow_status, project_type
        FROM reports 
        ORDER BY created_at DESC
    ''')
    reports = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('contractor_reports.html', reports=reports)

@app.route('/report/new')
def new_report():
    """LEGACY: Create new report"""
    return render_template('editor.html', report_id=None)

@app.route('/report/<int:report_id>')
def edit_report(report_id):
    """LEGACY DEPRECATED: Use /fire-door/report/<id> instead"""
    return render_template('editor.html', report_id=report_id)

# ===== END LEGACY ROUTES =====

# ===== FIRE DOOR ISOLATED ROUTES (CF1) =====
# All Fire Door functionality now lives under /fire-door/* namespace
# These routes use get_fire_door_db() exclusively
# =================================================

@app.route('/fire-door')
def fire_door_home():
    """Fire Door Toolkit Landing Page"""
    return render_template('tools/fire_door_home.html', active_nav='fire_door')

@app.route('/fire-door/reports')
def fire_door_reports():
    """Fire Door Reports List - Namespaced"""
    conn = get_fire_door_db()
    cursor = conn.execute('''
        SELECT id, report_title, client_name, site_name, 
               inspection_date, status, workflow_status, project_type
        FROM reports 
        ORDER BY created_at DESC
    ''')
    reports = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('contractor_reports.html', reports=reports)

@app.route('/fire-door/report/new')
def fire_door_new_report():
    """Fire Door: Create new report - Namespaced"""
    return render_template('editor.html', report_id=None)

@app.route('/fire-door/report/<int:report_id>')
def fire_door_edit_report(report_id):
    """Fire Door: Edit existing report - Namespaced"""
    return render_template('editor.html', report_id=report_id)

# Note: Fire Door API routes (/api/reports, /api/doors, etc.) remain at current paths
# for backward compatibility with existing JavaScript. They now use get_fire_door_db().

# ===== END FIRE DOOR ISOLATED ROUTES =====

# ===== ACTIVE ROUTES (ENGINE-CENTRIC) =====

@app.route('/fm/risk-assessment')
def risk_assessment():
    """Risk assessment is delivered via Assessment reports in the FM MVP."""
    return redirect('/artefacts/new', code=302)

@app.route('/tools/threat-modelling')
def risk_assessment_redirect():
    # Backwards-compatible redirect
    return redirect('/fm/risk-assessment', code=302)

@app.route('/fm/contractor-review')
def contractor_review_tool():
    """Contractor review is delivered via Investigation reports in the FM MVP."""
    return redirect('/artefacts/new', code=302)

@app.route('/tools/post-mortem')
def incident_review_redirect():
    # Backwards-compatible redirect
    return redirect('/fm/contractor-review', code=302)

@app.route('/tools/contractor-spec')
def contractor_spec_tool():
    """Legacy contractor spec route - redirects to FM artefacts wizard."""
    return redirect('/artefacts/new', code=302)

@app.route('/tools/fire-door')
def fire_door_tool():
    """Fire Door tooling entry - redirects to isolated namespace"""
    return redirect('/fire-door')

# ===== API: FIRE DOOR REPORTS (CF1: Uses get_fire_door_db) =====
# These API routes remain at /api/* for backward compatibility with existing JS
# But they now explicitly use get_fire_door_db() for database isolation
# ===========================================================================

# ===== CF1.1: NAMESPACED FIRE DOOR API ROUTES =====
# New routes under /fire-door/api/* - Same handlers as legacy /api/* routes
# Recommended for new development, but legacy routes maintained for compatibility
# ==================================================================

# Reports API - Namespaced
@app.route('/fire-door/api/reports', methods=['GET', 'POST'])
def fire_door_api_reports():
    """Fire Door Reports API - Namespaced (CF1.1)"""
    return handle_reports()

@app.route('/fire-door/api/reports/<int:report_id>', methods=['GET', 'PUT', 'DELETE'])
def fire_door_api_report(report_id):
    """Fire Door Report by ID API - Namespaced (CF1.1)"""
    return handle_report(report_id)

# Properties API - Namespaced
@app.route('/fire-door/api/properties', methods=['POST'])
def fire_door_api_properties_post():
    """Fire Door Properties POST API - Namespaced (CF1.1)"""
    return add_property()

@app.route('/fire-door/api/properties', methods=['GET'])
def fire_door_api_properties_get():
    """Fire Door Properties GET API - Namespaced (CF1.1)"""
    return get_properties()

@app.route('/fire-door/api/properties/<int:property_id>', methods=['PUT', 'DELETE'])
def fire_door_api_property(property_id):
    """Fire Door Property by ID API - Namespaced (CF1.1)"""
    return update_delete_property(property_id)

@app.route('/fire-door/api/properties/<int:property_id>/plan-photo', methods=['POST'])
def fire_door_api_property_plan_photo(property_id):
    """Fire Door Property Plan Photo API - Namespaced (CF1.1)"""
    return upload_property_plan(property_id)

# Locations API - Namespaced
@app.route('/fire-door/api/locations', methods=['GET', 'POST'])
def fire_door_api_locations():
    """Fire Door Locations API - Namespaced (CF1.1)"""
    return handle_locations()

@app.route('/fire-door/api/locations/<int:location_id>', methods=['PUT', 'DELETE'])
def fire_door_api_location(location_id):
    """Fire Door Location by ID API - Namespaced (CF1.1)"""
    return update_delete_location(location_id)

@app.route('/fire-door/api/locations/<int:location_id>/access-photo', methods=['POST'])
def fire_door_api_location_access_photo(location_id):
    """Fire Door Location Access Photo API - Namespaced (CF1.1)"""
    return upload_location_access_photo(location_id)

# Doors API - Namespaced
@app.route('/fire-door/api/doors', methods=['GET', 'POST'])
def fire_door_api_doors():
    """Fire Door Doors API - Namespaced (CF1.1)"""
    return handle_doors()

@app.route('/fire-door/api/doors/<int:door_id>', methods=['PUT', 'DELETE'])
def fire_door_api_door(door_id):
    """Fire Door Door by ID API - Namespaced (CF1.1)"""
    return update_delete_door(door_id)

# Work Items API - Namespaced
@app.route('/fire-door/api/work-items', methods=['GET', 'POST'])
def fire_door_api_work_items():
    """Fire Door Work Items API - Namespaced (CF1.1)"""
    return handle_work_items()

@app.route('/fire-door/api/work-items/<int:item_id>', methods=['PUT', 'DELETE'])
def fire_door_api_work_item(item_id):
    """Fire Door Work Item by ID API - Namespaced (CF1.1)"""
    return update_delete_work_item(item_id)

# Photos API - Namespaced
@app.route('/fire-door/api/photos/upload', methods=['POST'])
def fire_door_api_photos_upload():
    """Fire Door Photo Upload API - Namespaced (CF1.1)"""
    return upload_photo()

@app.route('/fire-door/api/photos/<int:photo_id>', methods=['GET'])
def fire_door_api_photo_get(photo_id):
    """Fire Door Photo Get API - Namespaced (CF1.1)"""
    return get_photo(photo_id)

@app.route('/fire-door/api/photos/<int:photo_id>/metadata', methods=['GET'])
def fire_door_api_photo_metadata(photo_id):
    """Fire Door Photo Metadata API - Namespaced (CF1.1)"""
    return get_photo_metadata(photo_id)

@app.route('/fire-door/api/photos/<int:photo_id>', methods=['DELETE'])
def fire_door_api_photo_delete(photo_id):
    """Fire Door Photo Delete API - Namespaced (CF1.1)"""
    return delete_photo(photo_id)

@app.route('/fire-door/api/photos', methods=['GET'])
def fire_door_api_photos_list():
    """Fire Door Photos List API - Namespaced (CF1.1)"""
    return get_photos_filtered()

@app.route('/fire-door/api/photos/door/<int:door_id>', methods=['GET'])
def fire_door_api_photos_door(door_id):
    """Fire Door Photos by Door API - Namespaced (CF1.1)"""
    return get_door_photos(door_id)

@app.route('/fire-door/api/photos/location/<int:location_id>', methods=['GET'])
def fire_door_api_photos_location(location_id):
    """Fire Door Photos by Location API - Namespaced (CF1.1)"""
    return get_location_photos(location_id)

@app.route('/fire-door/api/photos/search', methods=['GET'])
def fire_door_api_photos_search():
    """Fire Door Photos Search API - Namespaced (CF1.1)"""
    return search_photos()

# Export API - Namespaced
@app.route('/fire-door/api/export/<int:report_id>/<export_type>', methods=['GET'])
def fire_door_api_export(report_id, export_type):
    """Fire Door Export API - Namespaced (CF1.1)"""
    return export_pdf(report_id, export_type)

# ===== END CF1.1 NAMESPACED ROUTES =====

# ===== LEGACY FIRE DOOR API ROUTES (BACKWARD COMPATIBILITY) =====
# These routes remain for existing JavaScript compatibility
# New development should use /fire-door/api/* routes above
# =================================================================

@app.route('/api/reports', methods=['GET', 'POST'])
def handle_reports():
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'POST':
        data = request.json
        cursor = conn.execute('''
            INSERT INTO reports (
                report_title, quote_reference, site_name, site_address,
                client_name, inspector_name, inspection_date, project_type, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('report_title', 'Draft Report'),
            data.get('quote_reference', ''),
            data.get('site_name', 'Site'),
            data.get('site_address', ''),
            data.get('client_name', ''),
            data.get('inspector_name', 'Inspector'),
            data.get('inspection_date', datetime.now().strftime('%Y-%m-%d')),
            data.get('project_type', 'fire_door'),
            'draft'
        ))
        conn.commit()
        report_id = cursor.lastrowid
        conn.close()
        return jsonify({'id': report_id, 'status': 'success'})
    
    else:  # GET
        cursor = conn.execute('SELECT * FROM reports ORDER BY created_at DESC')
        reports = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(reports)

@app.route('/api/reports/<int:report_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_report(report_id):
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'DELETE':
        # Delete associated photos from filesystem
        photos = conn.execute('SELECT filename FROM photos WHERE report_id = ?', (report_id,)).fetchall()
        for photo in photos:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
            if os.path.exists(filepath):
                os.remove(filepath)
        
        conn.execute('DELETE FROM reports WHERE id = ?', (report_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'deleted'})
    
    elif request.method == 'PUT':
        data = request.json
        
        # Validate workflow_status
        valid_statuses = ['Draft', 'Sent to Contractor', 'Priced', 'Sent to Client', 'Approved']
        workflow_status = data.get('workflow_status', 'Draft')
        if workflow_status not in valid_statuses:
            return jsonify({'error': f'workflow_status must be one of: {", ".join(valid_statuses)}'}), 400
        
        conn.execute('''
            UPDATE reports SET
                report_title = ?, quote_reference = ?, site_name = ?,
                site_address = ?, client_name = ?, inspector_name = ?,
                inspection_date = ?, project_type = ?,
                workflow_status = ?, contractor_prices_received = ?,
                profit_margin = ?, client_total = ?,
                updated_at = datetime('now')
            WHERE id = ?
        ''', (
            data.get('report_title'),
            data.get('quote_reference'),
            data.get('site_name'),
            data.get('site_address'),
            data.get('client_name'),
            data.get('inspector_name'),
            data.get('inspection_date'),
            data.get('project_type'),
            workflow_status,
            data.get('contractor_prices_received', 0),
            data.get('profit_margin', 20.0),
            data.get('client_total'),
            report_id
        ))
        conn.commit()
        
        # Fetch updated report
        report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'report': dict(report)
        })
    
    else:  # GET
        report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
        if not report:
            conn.close()
            return jsonify({'error': 'Report not found'}), 404
        
        # Get properties with locations
        properties = conn.execute('''
            SELECT * FROM properties 
            WHERE report_id = ? 
            ORDER BY display_order, id
        ''', (report_id,)).fetchall()
        
        result = {
            'report': dict(report),
            'properties': []
        }
        
        for prop in properties:
            prop_dict = dict(prop)
            
            # Get internal locations
            locations = conn.execute('''
                SELECT * FROM internal_locations 
                WHERE property_id = ? 
                ORDER BY display_order, id
            ''', (prop['id'],)).fetchall()
            
            prop_dict['locations'] = []
            
            for loc in locations:
                loc_dict = dict(loc)
                
                # Get fire doors for this location
                doors = conn.execute('''
                    SELECT * FROM fire_doors 
                    WHERE internal_location_id = ? 
                    ORDER BY display_order, id
                ''', (loc['id'],)).fetchall()
                
                loc_dict['doors'] = []
                
                for door in doors:
                    door_dict = dict(door)
                    
                    # Get work items
                    work_items = conn.execute('''
                        SELECT * FROM work_items 
                        WHERE fire_door_id = ? 
                        ORDER BY display_order, id
                    ''', (door['id'],)).fetchall()
                    door_dict['work_items'] = [dict(wi) for wi in work_items]
                    
                    # Get photos
                    photos = conn.execute('''
                        SELECT id, filename, caption, site_name, internal_location, specs, notes
                        FROM photos 
                        WHERE fire_door_id = ? 
                        ORDER BY display_order, id
                    ''', (door['id'],)).fetchall()
                    door_dict['photos'] = [dict(p) for p in photos]
                    
                    loc_dict['doors'].append(door_dict)
                
                # Get location photos
                loc_photos = conn.execute('''
                    SELECT id, filename, caption
                    FROM photos 
                    WHERE internal_location_id = ? AND fire_door_id IS NULL
                    ORDER BY display_order, id
                ''', (loc['id'],)).fetchall()
                loc_dict['photos'] = [dict(p) for p in loc_photos]
                
                prop_dict['locations'].append(loc_dict)
            
            result['properties'].append(prop_dict)
        
        conn.close()
        return jsonify(result)

# ===== API: PROPERTIES =====

@app.route('/api/properties', methods=['POST'])
def add_property():
    data = request.json
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    cursor = conn.execute('''
        INSERT INTO properties (report_id, property_name, property_address)
        VALUES (?, ?, ?)
    ''', (
        data['report_id'],
        data.get('property_name', 'Property'),
        data.get('property_address', '')
    ))
    
    conn.commit()
    property_id = cursor.lastrowid
    conn.close()
    
    return jsonify({'id': property_id, 'status': 'success'})

@app.route('/api/properties', methods=['GET'])
def get_properties():
    """Get all properties for a report"""
    report_id = request.args.get('report_id')
    if not report_id:
        return jsonify({'error': 'report_id required'}), 400
    
    conn = get_fire_door_db()  # CF1: Fire Door DB
    properties = conn.execute('''
        SELECT * FROM properties 
        WHERE report_id = ? 
        ORDER BY display_order, id
    ''', (report_id,)).fetchall()
    conn.close()
    
    return jsonify([dict(p) for p in properties])

@app.route('/api/properties/<int:property_id>', methods=['PUT', 'DELETE'])
def update_delete_property(property_id):
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'DELETE':
        conn.execute('DELETE FROM properties WHERE id = ?', (property_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'deleted'})
    
    else:  # PUT
        data = request.json
        
        # Validate project_type
        valid_types = ['fire_door', 'gardening', 'decoration', 'fence', 'other']
        project_type = data.get('project_type', 'fire_door')
        if project_type not in valid_types:
            return jsonify({'error': f'project_type must be one of: {", ".join(valid_types)}'}), 400
        
        conn.execute('''
            UPDATE properties SET
                property_name = ?,
                property_address = ?,
                project_type = ?,
                site_plan_notes = ?
            WHERE id = ?
        ''', (
            data.get('property_name'),
            data.get('property_address'),
            project_type,
            data.get('site_plan_notes'),
            property_id
        ))
        conn.commit()
        
        # Fetch updated property
        prop = conn.execute('SELECT * FROM properties WHERE id = ?', (property_id,)).fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'property': dict(prop)
        })

@app.route('/api/properties/<int:property_id>/plan-photo', methods=['POST'])
def upload_property_plan(property_id):
    """Upload property site plan photo"""
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo provided'}), 400
    
    file = request.files['photo']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    # Get property details
    prop = conn.execute('SELECT * FROM properties WHERE id = ?', (property_id,)).fetchone()
    if not prop:
        conn.close()
        return jsonify({'error': 'Property not found'}), 404
    
    # Generate filename
    filename = generate_filename(file.filename, prop['property_name'], 'SitePlan')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Save file
    file.save(filepath)
    
    # Save to photos table
    caption = request.form.get('caption', 'Site plan')
    notes = request.form.get('notes', '')
    
    cursor = conn.execute('''
        INSERT INTO photos (
            filename, original_filename, site_name, 
            photo_role, caption, notes, property_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        filename,
        file.filename,
        prop['property_name'],
        'property_plan',
        caption,
        notes,
        property_id
    ))
    
    photo_id = cursor.lastrowid
    
    # Update property.site_plan_photo for backwards compatibility
    conn.execute('UPDATE properties SET site_plan_photo = ? WHERE id = ?', (filename, property_id))
    
    conn.commit()
    
    # Get inserted photo
    photo = conn.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    conn.close()
    
    return jsonify({
        'success': True,
        'photo': dict(photo)
    })

# ===== API: INTERNAL LOCATIONS =====

@app.route('/api/locations', methods=['GET', 'POST'])
def handle_locations():
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'POST':
        data = request.json
        cursor = conn.execute('''
            INSERT INTO internal_locations (property_id, location_name, access_instructions)
            VALUES (?, ?, ?)
        ''', (
            data['property_id'],
            data.get('location_name', 'Location'),
            data.get('access_instructions', '')
        ))
        
        conn.commit()
        location_id = cursor.lastrowid
        conn.close()
        
        return jsonify({'id': location_id, 'status': 'success'})
    
    else:  # GET
        property_id = request.args.get('property_id')
        if not property_id:
            return jsonify({'error': 'property_id required'}), 400
        
        locations = conn.execute('''
            SELECT * FROM internal_locations 
            WHERE property_id = ? 
            ORDER BY display_order, id
        ''', (property_id,)).fetchall()
        conn.close()
        
        return jsonify([dict(loc) for loc in locations])

@app.route('/api/locations/<int:location_id>', methods=['PUT', 'DELETE'])
def update_delete_location(location_id):
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'DELETE':
        conn.execute('DELETE FROM internal_locations WHERE id = ?', (location_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'deleted'})
    
    else:  # PUT
        data = request.json
        conn.execute('''
            UPDATE internal_locations SET
                location_name = ?,
                access_instructions = ?
            WHERE id = ?
        ''', (
            data.get('location_name'),
            data.get('access_instructions'),
            location_id
        ))
        conn.commit()
        
        # Fetch updated location
        loc = conn.execute('SELECT * FROM internal_locations WHERE id = ?', (location_id,)).fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'internal_location': dict(loc)
        })

@app.route('/api/locations/<int:location_id>/access-photo', methods=['POST'])
def upload_location_access_photo(location_id):
    """Upload internal location access photo"""
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo provided'}), 400
    
    file = request.files['photo']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    # Get location details
    loc = conn.execute('SELECT * FROM internal_locations WHERE id = ?', (location_id,)).fetchone()
    if not loc:
        conn.close()
        return jsonify({'error': 'Location not found'}), 404
    
    # Generate filename
    filename = generate_filename(file.filename, 'Location', loc['location_name'])
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Save file
    file.save(filepath)
    
    # Save to photos table
    caption = request.form.get('caption', 'Access photo')
    notes = request.form.get('notes', '')
    
    cursor = conn.execute('''
        INSERT INTO photos (
            filename, original_filename, internal_location,
            photo_role, caption, notes, internal_location_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        filename,
        file.filename,
        loc['location_name'],
        'location_access',
        caption,
        notes,
        location_id
    ))
    
    photo_id = cursor.lastrowid
    
    # Update location.location_photo for backwards compatibility
    conn.execute('UPDATE internal_locations SET location_photo = ? WHERE id = ?', (filename, location_id))
    
    conn.commit()
    
    # Get inserted photo
    photo = conn.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    conn.close()
    
    return jsonify({
        'success': True,
        'photo': dict(photo)
    })

# ===== API: FIRE DOORS =====

@app.route('/api/doors', methods=['GET', 'POST'])
def handle_doors():
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'POST':
        data = request.json
        cursor = conn.execute('''
            INSERT INTO fire_doors (
                internal_location_id, door_reference, fd_rating,
                frame_condition, door_condition, seal_condition, closer_condition,
                gaps_ok, intumescent_ok, pass_fail, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['internal_location_id'],
            data.get('door_reference', 'FD-001'),
            data.get('fd_rating', 'FD30'),
            data.get('frame_condition', ''),
            data.get('door_condition', ''),
            data.get('seal_condition', ''),
            data.get('closer_condition', ''),
            data.get('gaps_ok', 0),
            data.get('intumescent_ok', 0),
            data.get('pass_fail', 'Pass'),
            data.get('notes', '')
        ))
        
        conn.commit()
        door_id = cursor.lastrowid
        conn.close()
        
        return jsonify({'id': door_id, 'status': 'success'})
    
    else:  # GET
        location_id = request.args.get('location_id')
        if not location_id:
            return jsonify({'error': 'location_id required'}), 400
        
        doors = conn.execute('''
            SELECT * FROM fire_doors 
            WHERE internal_location_id = ? 
            ORDER BY display_order, id
        ''', (location_id,)).fetchall()
        conn.close()
        
        return jsonify([dict(door) for door in doors])

@app.route('/api/doors/<int:door_id>', methods=['PUT', 'DELETE'])
def update_delete_door(door_id):
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'DELETE':
        conn.execute('DELETE FROM fire_doors WHERE id = ?', (door_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'deleted'})
    
    else:  # PUT
        data = request.json
        conn.execute('''
            UPDATE fire_doors SET
                door_reference = ?, fd_rating = ?,
                frame_condition = ?, door_condition = ?, seal_condition = ?,
                closer_condition = ?, gaps_ok = ?, intumescent_ok = ?,
                pass_fail = ?, notes = ?,
                updated_at = datetime('now')
            WHERE id = ?
        ''', (
            data.get('door_reference'),
            data.get('fd_rating'),
            data.get('frame_condition'),
            data.get('door_condition'),
            data.get('seal_condition'),
            data.get('closer_condition'),
            data.get('gaps_ok', 0),
            data.get('intumescent_ok', 0),
            data.get('pass_fail'),
            data.get('notes'),
            door_id
        ))
        conn.commit()
        conn.close()
        return jsonify({'status': 'updated'})

# ===== API: WORK ITEMS =====

@app.route('/api/work-items', methods=['GET', 'POST'])
def handle_work_items():
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'POST':
        data = request.json
        cursor = conn.execute('''
            INSERT INTO work_items (
                fire_door_id, work_item, specification_scope,
                client_description, mat_cost, lab_cost, contractor_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['fire_door_id'],
            data.get('work_item', ''),
            data.get('specification_scope', ''),
            data.get('client_description', ''),
            data.get('mat_cost', 0),
            data.get('lab_cost', 0),
            data.get('contractor_notes', '')
        ))
        
        conn.commit()
        work_item_id = cursor.lastrowid
        conn.close()
        
        return jsonify({'id': work_item_id, 'status': 'success'})
    
    else:  # GET
        door_id = request.args.get('door_id')
        if not door_id:
            return jsonify({'error': 'door_id required'}), 400
        
        items = conn.execute('''
            SELECT * FROM work_items 
            WHERE fire_door_id = ? 
            ORDER BY display_order, id
        ''', (door_id,)).fetchall()
        conn.close()
        
        return jsonify([dict(item) for item in items])

@app.route('/api/work-items/<int:item_id>', methods=['PUT', 'DELETE'])
def update_delete_work_item(item_id):
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    if request.method == 'DELETE':
        conn.execute('DELETE FROM work_items WHERE id = ?', (item_id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'deleted'})
    
    else:  # PUT
        data = request.json
        conn.execute('''
            UPDATE work_items SET
                work_item = ?,
                specification_scope = ?,
                client_description = ?,
                mat_cost = ?,
                lab_cost = ?,
                contractor_notes = ?
            WHERE id = ?
        ''', (
            data.get('work_item'),
            data.get('specification_scope'),
            data.get('client_description'),
            data.get('mat_cost', 0),
            data.get('lab_cost', 0),
            data.get('contractor_notes'),
            item_id
        ))
        conn.commit()
        
        # Fetch updated work item
        item = conn.execute('SELECT * FROM work_items WHERE id = ?', (item_id,)).fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'work_item': dict(item)
        })

# ===== API: PHOTOS (Filesystem + Metadata) =====

@app.route('/api/photos/upload', methods=['POST'])
def upload_photo():
    """
    Upload photo with metadata
    Form data:
    - photo: file
    - site_name: text
    - internal_location: text
    - specs: text (FD rating, etc)
    - notes: text
    - photo_type: text (site_plan, location, condition, work_item)
    - report_id, property_id, internal_location_id, fire_door_id: integers
    """
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo provided'}), 400
    
    file = request.files['photo']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    
    # Get metadata from form
    site_name = request.form.get('site_name', 'Unknown Site')
    internal_location = request.form.get('internal_location', '')
    specs = request.form.get('specs', '')
    notes = request.form.get('notes', '')
    photo_type = request.form.get('photo_type', 'condition')
    caption = request.form.get('caption', '')
    
    # Generate unique filename with metadata
    filename = generate_filename(file.filename, site_name, internal_location)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Save file to filesystem
    file.save(filepath)
    
    # Save metadata to database
    conn = get_fire_door_db()  # CF1: Fire Door DB
    cursor = conn.execute('''
        INSERT INTO photos (
            filename, original_filename, site_name, internal_location,
            specs, notes, photo_type, caption,
            report_id, property_id, internal_location_id, fire_door_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        filename,
        file.filename,
        site_name,
        internal_location,
        specs,
        notes,
        photo_type,
        caption,
        request.form.get('report_id', None),
        request.form.get('property_id', None),
        request.form.get('internal_location_id', None),
        request.form.get('fire_door_id', None)
    ))
    
    conn.commit()
    photo_id = cursor.lastrowid
    conn.close()
    
    return jsonify({
        'id': photo_id,
        'filename': filename,
        'status': 'uploaded'
    })

@app.route('/api/photos/<int:photo_id>')
def get_photo(photo_id):
    """Get photo file"""
    conn = get_fire_door_db()  # CF1: Fire Door DB
    photo = conn.execute('SELECT filename FROM photos WHERE id = ?', (photo_id,)).fetchone()
    conn.close()
    
    if not photo:
        return 'Photo not found', 404
    
    return send_from_directory(app.config['UPLOAD_FOLDER'], photo['filename'])

@app.route('/api/photos/<int:photo_id>/metadata')
def get_photo_metadata(photo_id):
    """Get photo metadata"""
    conn = get_fire_door_db()  # CF1: Fire Door DB
    photo = conn.execute('SELECT * FROM photos WHERE id = ?', (photo_id,)).fetchone()
    conn.close()
    
    if not photo:
        return jsonify({'error': 'Photo not found'}), 404
    
    return jsonify(dict(photo))

@app.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def delete_photo(photo_id):
    """Delete photo from filesystem and database"""
    conn = get_fire_door_db()  # CF1: Fire Door DB
    photo = conn.execute('SELECT filename FROM photos WHERE id = ?', (photo_id,)).fetchone()
    
    if photo:
        # Delete from filesystem
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        
        # Delete from database
        conn.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
        conn.commit()
    
    conn.close()
    return jsonify({'status': 'deleted'})

@app.route('/api/photos', methods=['GET'])
def get_photos_filtered():
    """
    Get photos with filters
    Query params: property_id, internal_location_id, fire_door_id, photo_role
    """
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    query = 'SELECT * FROM photos WHERE 1=1'
    params = []
    
    if request.args.get('property_id'):
        query += ' AND property_id = ?'
        params.append(request.args.get('property_id'))
    
    if request.args.get('internal_location_id'):
        query += ' AND internal_location_id = ?'
        params.append(request.args.get('internal_location_id'))
    
    if request.args.get('fire_door_id'):
        query += ' AND fire_door_id = ?'
        params.append(request.args.get('fire_door_id'))
    
    if request.args.get('photo_role'):
        query += ' AND photo_role = ?'
        params.append(request.args.get('photo_role'))
    
    query += ' ORDER BY display_order, id'
    
    photos = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify({
        'success': True,
        'photos': [dict(p) for p in photos]
    })

@app.route('/api/photos/door/<int:door_id>')
def get_door_photos(door_id):
    """Get all photos for a fire door"""
    conn = get_fire_door_db()  # CF1: Fire Door DB
    photos = conn.execute('''
        SELECT id, filename, caption, site_name, internal_location, specs, notes, photo_type
        FROM photos 
        WHERE fire_door_id = ? 
        ORDER BY display_order, id
    ''', (door_id,)).fetchall()
    conn.close()
    
    return jsonify([dict(p) for p in photos])

@app.route('/api/photos/location/<int:location_id>')
def get_location_photos(location_id):
    """Get all photos for an internal location"""
    conn = get_fire_door_db()  # CF1: Fire Door DB
    photos = conn.execute('''
        SELECT id, filename, caption, site_name, internal_location, specs, notes, photo_type
        FROM photos 
        WHERE internal_location_id = ? AND fire_door_id IS NULL
        ORDER BY display_order, id
    ''', (location_id,)).fetchall()
    conn.close()
    
    return jsonify([dict(p) for p in photos])

@app.route('/api/photos/search')
def search_photos():
    """
    Search photos by metadata
    Query params: site_name, internal_location, specs
    """
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    query = 'SELECT * FROM photos WHERE 1=1'
    params = []
    
    if request.args.get('site_name'):
        query += ' AND site_name LIKE ?'
        params.append(f"%{request.args.get('site_name')}%")
    
    if request.args.get('internal_location'):
        query += ' AND internal_location LIKE ?'
        params.append(f"%{request.args.get('internal_location')}%")
    
    if request.args.get('specs'):
        query += ' AND specs LIKE ?'
        params.append(f"%{request.args.get('specs')}%")
    
    query += ' ORDER BY created_at DESC'
    
    photos = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify([dict(p) for p in photos])

# ===== PDF EXPORT =====

@app.route('/api/export/<int:report_id>/<export_type>')
def export_pdf(report_id, export_type):
    """
    Export report as PDF
    export_type: 'contractor' or 'client'
    """
    # Strict validation
    if export_type not in ['contractor', 'client']:
        return jsonify({'success': False, 'error': 'Invalid export type'}), 404
    
    conn = get_fire_door_db()  # CF1: Fire Door DB
    
    # Get report with full data structure
    report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
    if not report:
        conn.close()
        return jsonify({'error': 'Report not found'}), 404
    
    report_dict = dict(report)
    
    # Get all properties with nested data
    properties = conn.execute('''
        SELECT * FROM properties 
        WHERE report_id = ? 
        ORDER BY display_order, id
    ''', (report_id,)).fetchall()
    
    properties_data = []
    for prop in properties:
        prop_dict = dict(prop)
        
        # Get locations
        locations = conn.execute('''
            SELECT * FROM internal_locations 
            WHERE property_id = ? 
            ORDER BY display_order, id
        ''', (prop['id'],)).fetchall()
        
        locations_data = []
        for loc in locations:
            loc_dict = dict(loc)
            
            # Get doors
            doors = conn.execute('''
                SELECT * FROM fire_doors 
                WHERE internal_location_id = ? 
                ORDER BY display_order, id
            ''', (loc['id'],)).fetchall()
            
            doors_data = []
            for door in doors:
                door_dict = dict(door)
                
                # Get work items
                work_items = conn.execute('''
                    SELECT * FROM work_items 
                    WHERE fire_door_id = ? 
                    ORDER BY display_order, id
                ''', (door['id'],)).fetchall()
                door_dict['work_items'] = [dict(wi) for wi in work_items]
                
                # Get photos
                photos = conn.execute('''
                    SELECT * FROM photos 
                    WHERE fire_door_id = ? 
                    ORDER BY display_order, id
                ''', (door['id'],)).fetchall()
                door_dict['photos'] = [dict(p) for p in photos]
                
                doors_data.append(door_dict)
            
            loc_dict['doors'] = doors_data
            locations_data.append(loc_dict)
        
        prop_dict['locations'] = locations_data
        properties_data.append(prop_dict)
    
    conn.close()
    
    # Generate PDF based on type
    if export_type == 'contractor':
        buffer = generate_contractor_pdf(report_dict, properties_data)
        filename = f"contractor_spec_{report_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
    else:  # client
        buffer = generate_client_pdf(report_dict, properties_data)
        filename = f"client_proposal_{report_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
    
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )
    
# =====================================================
# AI CHATBOT ROUTES (DeepSeek + Gemini)
# =====================================================

@app.route("/ai/reset", methods=["POST"])
def ai_reset():

    # clear chat history
    session["history"] = []

    # reset ticket data
    session["project_data"] = {
    "name": None,
    "email": None,
    "company": None,
    "phone": None,
    "project_type": None,
    "project_name": None,
    "project_description": None,
    "features": None,
    "tech_stack": None,
    "integrations": None,
    "timeline": None,
    "budget": None
}

    session.modified = True

    return jsonify({"status": "reset"})

@app.route('/ai/chat', methods=['POST'])
def ai_chat():

    if "history" not in session:
        session["history"] = []

    user_message = ""

    if request.is_json:
        data = request.get_json()
        user_message = data.get("message", "")
    else:
        user_message = request.form.get("message", "")

    # =============================
    # FIRST GREETING MESSAGE
    # =============================

    if len(session["history"]) == 0:

        session["history"].append({
            "role": "assistant",
            "content": "Hello I'm your AI assistant.\n\nMay I know your name?"
        })

        return jsonify({
            "reply": "Hello I'm your AI assistant.\n\nMay I know your name?"
        })

    import re

    if "project_data" not in session:
        session["project_data"] = {
            "name": None,
            "email": None,
            "company": None,
            "phone": None,
            "project_type": None,
            "project_name": None,
            "project_description": None,
            "features": None,
            "tech_stack": None,
            "integrations": None,
            "timeline": None,
            "budget": None
        }

    msg = user_message.lower()
    data = session["project_data"]

    # =============================
    # AUTO DETECT USER DATA
    # =============================

    greetings = ["hello", "hi", "hey", "good morning", "good evening"]

    # Detect name
    if data["name"] is None:
        if user_message.lower() not in greetings:
            if len(user_message.split()) <= 3 and "@" not in user_message:
                if not re.search(r"\d", user_message):
                    data["name"] = user_message

    # Detect company
    if data["company"] is None and len(user_message.split()) <= 2:
        if user_message.isupper() or user_message.istitle():
            data["company"] = user_message

    # Detect project name
    if data["project_name"] is None and len(user_message.split()) <= 4:
        if "app" in msg or "ai" in msg:
            data["project_name"] = user_message

    # Detect project description
    if data["project_description"] is None:
        if len(user_message) > 40:
            data["project_description"] = user_message

    # Detect features
    if data["features"] is None:
        if "feature" in msg or "include" in msg:
            data["features"] = user_message

    # Detect tech stack
    if data["tech_stack"] is None:
        if "react" in msg or "flutter" in msg or "python" in msg:
            data["tech_stack"] = user_message

    # Detect integrations
    if data["integrations"] is None:
        if "integration" in msg or "whatsapp" in msg or "slack" in msg:
            data["integrations"] = user_message

    # Detect timeline
    if data["timeline"] is None:
        if "month" in msg:
            data["timeline"] = user_message

    # Detect budget
    if data["budget"] is None:
        if re.search(r"\d{4,}", user_message):
            data["budget"] = user_message

    # Detect email
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message)
    if email:
        data["email"] = email.group()

    # detect company
    if "company" in msg:
        data["company"] = user_message

    # detect phone
    phone = re.search(r"\b\d{10}\b", user_message)
    if phone:
        data["phone"] = phone.group()

    # detect project type
    if "website" in msg:
        data["project_type"] = "website"

    if "web app" in msg:
        data["project_type"] = "web_app"

    if "mobile app" in msg:
        data["project_type"] = "mobile_app"

    if "ecommerce" in msg:
        data["project_type"] = "ecommerce"

    session["project_data"] = data
    session.modified = True

    # =============================
    # IMAGE ANALYSIS
    # =============================

    images = request.files.getlist("images")

    if images:
        for img in images:
            analysis = analyze_uploaded_image(img)
            if analysis:
                user_message += analysis

    session["history"].append({
        "role": "user",
        "content": user_message
    })

    # =============================
    # SYSTEM PROMPT
    # =============================

    system_prompt = """
You are a Devex Studios AI project assistant.

Your job is to collect project requirements from the client.

Collect these fields:

name
email
company
phone
project_type
project_name
project_description
features
tech_stack
integrations
timeline
budget

Rules:
Ask one question at a time.
Do not repeat questions.
Never ask if data already exists.

When all data is collected return exactly:

CREATE_PROJECT:

name=...
email=...
company=...
phone=...
project_type=...
project_name=...
project_description=...
features=...
tech_stack=...
integrations=...
timeline=...
budget=...
"""

    project_data = session["project_data"]

    context = f"""
Collected project information:

name: {project_data.get('name')}
email: {project_data.get('email')}
company: {project_data.get('company')}
phone: {project_data.get('phone')}
project_type: {project_data.get('project_type')}
project_name: {project_data.get('project_name')}
project_description: {project_data.get('project_description')}
features: {project_data.get('features')}
tech_stack: {project_data.get('tech_stack')}
integrations: {project_data.get('integrations')}
timeline: {project_data.get('timeline')}
budget: {project_data.get('budget')}

Ask only for missing information.
"""

    messages = [
        {"role": "system", "content": system_prompt + context}
    ] + session["history"]

    api_key = os.getenv("DEEPSEEK_API_KEY")

    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.6
        }
    )

    result = response.json()
    reply = result["choices"][0]["message"]["content"]

    session["history"].append({
        "role": "assistant",
        "content": reply
    })

    session["history"] = session["history"][-8:]
    session.modified = True

    # =============================
    # CREATE PROJECT REQUEST
    # =============================

    if "CREATE_PROJECT:" in reply:

        try:

            lines = reply.split("\n")
            extracted = {}

            for line in lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    extracted[k.strip().lower()] = v.strip()

            name = extracted.get("name")
            email = extracted.get("email")
            company = extracted.get("company")
            phone = extracted.get("phone")
            project_type = extracted.get("project_type")
            project_name = extracted.get("project_name")
            project_description = extracted.get("project_description")
            budget = extracted.get("budget")

            conn = get_quote_db()

            ref = "REQ-" + str(uuid.uuid4())[:8].upper()

            conn.execute("""
            INSERT INTO quote_requests
            (ref,status,contact_name,contact_email,contact_phone,
            company_name,project_name,project_type,brief_description,
            budget_range,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                ref,
                "NEW",
                name,
                email,
                phone,
                company,
                project_name,
                project_type,
                project_description,
                budget,
                "web"
            ))

            conn.commit()
            conn.close()

            session["history"] = []
            session["project_data"] = {}

            return jsonify({
                "reply": f"""✅ Project request created successfully.

Reference ID: {ref}

Our team will review your project and contact you soon."""
            })

        except Exception as e:

            print("Project creation error:", e)

            return jsonify({
                "reply": "❌ Project request creation failed. Please try again."
            })

    return jsonify({"reply": reply})

@app.route('/ai/image', methods=['POST'])
def ai_image():

    file = request.files["image"]
    image_bytes = file.read()

    api_key = os.getenv("GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Analyze this image and explain what it shows."},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(image_bytes).decode()
                        }
                    }
                ]
            }
        ]
    }

    r = requests.post(url, json=payload)

    result = r.json()

    analysis = result["candidates"][0]["content"]["parts"][0]["text"]

    return jsonify({"analysis": analysis})

def generate_contractor_pdf(report, properties):
    """Generate contractor specification PDF"""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    navy = colors.HexColor('#0f172a')
    orange = colors.HexColor('#f97316')
    gray = colors.HexColor('#f3f4f6')
    
    y = height - 40*mm
    
    # Header
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(30*mm, y, "PROPOSAL &")
    c.drawString(30*mm, y - 12*mm, "SPECIFICATION")
    
    c.setFillColor(orange)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30*mm, y - 20*mm, "RENOVATION CONTRACTOR SERVICES")
    
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 30*mm, y, f"QUOTE REF: {report['quote_reference'] or 'DRAFT'}")
    c.setFillColor(orange)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 30*mm, y - 7*mm, report['inspection_date'])
    
    y -= 35*mm
    
    # General Information Table
    c.setFillColor(navy)
    c.rect(30*mm, y - 10*mm, width - 60*mm, 10*mm, fill=True)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(35*mm, y - 6*mm, "GENERAL INFORMATION")
    
    y -= 10*mm
    
    info_rows = [
        ("Quote Reference", report['quote_reference'] or 'DRAFT'),
        ("Client Name", report['client_name'] or ''),
        ("Project Type", report['project_type'].replace('_', ' ').title()),
        ("Date Generated", report['inspection_date'])
    ]
    
    for i, (label, value) in enumerate(info_rows):
        if i % 2 == 1:
            c.setFillColor(gray)
            c.rect(30*mm, y - 8*mm, width - 60*mm, 8*mm, fill=True, stroke=False)
        
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(35*mm, y - 5*mm, label)
        
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        c.drawString(90*mm, y - 5*mm, str(value))
        
        y -= 8*mm
    
    # Properties and locations
    for prop in properties:
        if y < 120*mm:
            c.showPage()
            y = height - 30*mm
        
        y -= 15*mm
        
        # Property header
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(30*mm, y, prop['property_name'])
        
        y -= 10*mm
        c.setStrokeColor(orange)
        c.setLineWidth(2)
        c.line(30*mm, y, width - 30*mm, y)
        
        y -= 10*mm
        
        # Property plan image and notes
        conn_temp = get_db()
        plan_photo = conn_temp.execute('''
            SELECT filename FROM photos 
            WHERE property_id = ? AND photo_role = 'property_plan'
            ORDER BY created_at DESC LIMIT 1
        ''', (prop['id'],)).fetchone()
        conn_temp.close()
        
        plan_filename = plan_photo['filename'] if plan_photo else prop.get('site_plan_photo')
        
        if plan_filename or prop.get('site_plan_notes'):
            if y < 120*mm:
                c.showPage()
                y = height - 30*mm
            
            c.setFillColor(navy)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(35*mm, y, "SITE PLAN & NOTES")
            y -= 7*mm
            
            # Render plan image if exists
            if plan_filename:
                try:
                    photo_path = os.path.join(app.config['UPLOAD_FOLDER'], plan_filename)
                    if os.path.exists(photo_path):
                        img = ImageReader(photo_path)
                        c.drawImage(img, 35*mm, y - 60*mm, width=60*mm, height=50*mm, preserveAspectRatio=True)
                        y -= 62*mm
                except:
                    pass
            
            # Render plan notes if exists
            if prop.get('site_plan_notes'):
                c.setFont("Helvetica", 8)
                c.setFillColor(colors.black)
                # Wrap text
                notes_text = prop['site_plan_notes']
                if len(notes_text) > 120:
                    notes_text = notes_text[:120] + "..."
                c.drawString(35*mm, y, f"Notes: {notes_text}")
                y -= 7*mm
            
            y -= 5*mm
        
        # Locations and doors
        for loc in prop['locations']:
            if y < 120*mm:
                c.showPage()
                y = height - 30*mm
            
            c.setFillColor(orange)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(35*mm, y, f"LOCATION: {loc['location_name']}")
            
            y -= 8*mm
            
            # Access instructions and photo
            if loc.get('access_instructions') or loc.get('location_photo'):
                conn_temp = get_db()
                access_photo = conn_temp.execute('''
                    SELECT filename FROM photos 
                    WHERE internal_location_id = ? AND photo_role = 'location_access'
                    ORDER BY created_at DESC LIMIT 1
                ''', (loc['id'],)).fetchone()
                conn_temp.close()
                
                access_filename = access_photo['filename'] if access_photo else loc.get('location_photo')
                
                if access_filename:
                    if y < 120*mm:
                        c.showPage()
                        y = height - 30*mm
                    
                    try:
                        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], access_filename)
                        if os.path.exists(photo_path):
                            c.setFont("Helvetica-Bold", 8)
                            c.setFillColor(colors.black)
                            c.drawString(40*mm, y, "Access Photo:")
                            y -= 5*mm
                            img = ImageReader(photo_path)
                            c.drawImage(img, 40*mm, y - 40*mm, width=50*mm, height=35*mm, preserveAspectRatio=True)
                            y -= 42*mm
                    except:
                        pass
                
                if loc.get('access_instructions'):
                    c.setFont("Helvetica", 8)
                    c.setFillColor(colors.black)
                    instructions = loc['access_instructions']
                    if len(instructions) > 100:
                        instructions = instructions[:100] + "..."
                    c.drawString(40*mm, y, f"Access: {instructions}")
                    y -= 7*mm
                
                y -= 5*mm
            
            for door in loc['doors']:
                if y < 120*mm:
                    c.showPage()
                    y = height - 30*mm
                
                # Door details box
                c.setFillColor(gray)
                c.rect(35*mm, y - 20*mm, width - 70*mm, 20*mm, fill=True)
                c.setStrokeColor(orange)
                c.setLineWidth(3)
                c.line(35*mm, y - 20*mm, 35*mm, y)
                
                c.setFillColor(navy)
                c.setFont("Helvetica-Bold", 10)
                c.drawString(40*mm, y - 5*mm, f"Door: {door['door_reference']}")
                c.setFont("Helvetica", 9)
                c.drawString(40*mm, y - 10*mm, f"Rating: {door['fd_rating']} | Status: {door['pass_fail']}")
                c.drawString(40*mm, y - 15*mm, f"Frame: {door['frame_condition']} | Door: {door['door_condition']} | Seal: {door['seal_condition']}")
                
                y -= 25*mm
                
                # Work items table
                if door['work_items']:
                    c.setFont("Helvetica-Bold", 9)
                    c.drawString(40*mm, y, "WORK REQUIRED:")
                    y -= 5*mm
                    
                    for item in door['work_items']:
                        if y < 40*mm:
                            c.showPage()
                            y = height - 30*mm
                        
                        c.setFont("Helvetica-Bold", 8)
                        c.drawString(45*mm, y, item['work_item'])
                        y -= 4*mm
                        c.setFont("Helvetica", 8)
                        
                        # Wrap specification text
                        spec_text = item['specification_scope'] or ''
                        if len(spec_text) > 80:
                            spec_text = spec_text[:80] + "..."
                        c.drawString(45*mm, y, spec_text)
                        
                        y -= 4*mm
                        c.drawString(45*mm, y, f"MAT: £{item['mat_cost']:.2f}  |  LAB: £{item['lab_cost']:.2f}")
                        y -= 8*mm
                
                # Photos (if any)
                if door['photos']:
                    photo_count = min(len(door['photos']), 4)
                    if y < 40*mm:
                        c.showPage()
                        y = height - 30*mm
                    
                    c.setFont("Helvetica-Bold", 8)
                    c.drawString(40*mm, y, f"Reference Photos ({photo_count}):")
                    y -= 30*mm
                    
                    x_photo = 40*mm
                    for i, photo in enumerate(door['photos'][:4]):
                        try:
                            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo['filename'])
                            if os.path.exists(photo_path):
                                img = ImageReader(photo_path)
                                c.drawImage(img, x_photo, y, width=35*mm, height=25*mm, preserveAspectRatio=True)
                                x_photo += 37*mm
                        except:
                            pass
                    
                    y -= 5*mm
                
                y -= 10*mm
    
    c.save()
    buffer.seek(0)
    return buffer

def generate_client_pdf(report, properties):
    """Generate client-facing proposal PDF"""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    navy = colors.HexColor('#0f172a')
    orange = colors.HexColor('#f97316')
    
    y = height - 40*mm
    
    # Header
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 32)
    c.drawString(30*mm, y, "FIRE DOOR")
    c.drawString(30*mm, y - 14*mm, "SAFETY REPORT")
    
    c.setFillColor(orange)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30*mm, y - 24*mm, report['site_name'])
    
    y -= 40*mm
    
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(30*mm, y, f"Prepared for: {report['client_name']}")
    y -= 6*mm
    c.drawString(30*mm, y, f"Inspector: {report['inspector_name']}")
    y -= 6*mm
    c.drawString(30*mm, y, f"Date: {report['inspection_date']}")
    
    y -= 20*mm
    c.setStrokeColor(orange)
    c.setLineWidth(2)
    c.line(30*mm, y, width - 30*mm, y)
    
    y -= 15*mm
    
    # Executive Summary
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(30*mm, y, "EXECUTIVE SUMMARY")
    
    y -= 10*mm
    
    # Count doors
    total_doors = sum(len(loc['doors']) for prop in properties for loc in prop['locations'])
    pass_count = sum(1 for prop in properties for loc in prop['locations'] for door in loc['doors'] if door['pass_fail'] == 'Pass')
    fail_count = total_doors - pass_count
    
    c.setFont("Helvetica", 11)
    c.drawString(35*mm, y, f"We inspected {total_doors} fire door{'s' if total_doors != 1 else ''} at your property.")
    y -= 7*mm
    c.drawString(35*mm, y, f"• {pass_count} door{'s' if pass_count != 1 else ''} PASSED inspection")
    y -= 7*mm
    c.setFillColor(colors.red if fail_count > 0 else colors.black)
    c.drawString(35*mm, y, f"• {fail_count} door{'s' if fail_count != 1 else ''} FAILED and require immediate repair")
    
    c.setFillColor(colors.black)
    
    # Calculate total cost with margin
    subtotal = sum(
        float(item['mat_cost'] or 0) + float(item['lab_cost'] or 0)
        for prop in properties 
        for loc in prop['locations'] 
        for door in loc['doors'] 
        for item in door['work_items']
    )
    
    profit_margin = float(report.get('profit_margin', 20.0))
    margin_amount = subtotal * (profit_margin / 100.0)
    client_total = subtotal + margin_amount
    
    # Save client_total back to database
    conn_temp = get_db()
    conn_temp.execute('UPDATE reports SET client_total = ? WHERE id = ?', (client_total, report['id']))
    conn_temp.commit()
    conn_temp.close()
    
    y -= 10*mm
    c.setFont("Helvetica", 10)
    c.drawString(35*mm, y, f"Subtotal (Materials + Labour): £{subtotal:,.2f}")
    y -= 6*mm
    c.drawString(35*mm, y, f"Service Margin ({profit_margin:.1f}%): £{margin_amount:,.2f}")
    y -= 6*mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(35*mm, y, f"Total Estimated Cost: £{client_total:,.2f}")
    
    y -= 20*mm
    
    # Detailed findings
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(30*mm, y, "FINDINGS & RECOMMENDATIONS")
    
    y -= 10*mm
    
    for prop in properties:
        for loc in prop['locations']:
            for door in loc['doors']:
                if y < 100*mm:
                    c.showPage()
                    y = height - 30*mm
                
                # Door section
                c.setFillColor(navy)
                c.setFont("Helvetica-Bold", 11)
                c.drawString(35*mm, y, f"{loc['location_name']} - {door['door_reference']}")
                
                y -= 7*mm
                
                status_color = colors.green if door['pass_fail'] == 'Pass' else colors.red
                c.setFillColor(status_color)
                c.setFont("Helvetica-Bold", 10)
                status_text = "✓ PASS" if door['pass_fail'] == 'Pass' else "FAIL - Requires Immediate Attention"
                c.drawString(40*mm, y, f"Status: {status_text}")
                
                y -= 10*mm
                
                # Issues and repairs
                c.setFillColor(colors.black)
                c.setFont("Helvetica", 9)
                
                if door['work_items']:
                    c.setFont("Helvetica-Bold", 9)
                    c.drawString(40*mm, y, "Recommended Repairs:")
                    y -= 5*mm
                    
                    door_total = sum(float(item['mat_cost'] or 0) + float(item['lab_cost'] or 0) for item in door['work_items'])
                    
                    for item in door['work_items']:
                        if y < 40*mm:
                            c.showPage()
                            y = height - 30*mm
                        
                        # Use client_description if available, otherwise use work_item
                        description = item.get('client_description') or item['work_item']
                        
                        c.setFont("Helvetica", 9)
                        c.drawString(45*mm, y, f"• {description}")
                        y -= 5*mm
                    
                    c.setFont("Helvetica-Bold", 9)
                    c.drawString(40*mm, y, f"Estimated Cost: £{door_total:,.2f}")
                    y -= 10*mm
                
                y -= 5*mm
    
    y -= 20*mm
    
    # Footer / Compliance note
    if y < 60*mm:
        c.showPage()
        y = height - 30*mm
    
    c.setFillColor(colors.HexColor('#ef4444'))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(30*mm, y, "COMPLIANCE NOTE")
    
    y -= 8*mm
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(30*mm, y, "Current fire door deficiencies pose a safety risk.")
    y -= 5*mm
    c.drawString(30*mm, y, "We recommend repairs within 30 days to maintain building")
    y -= 5*mm
    c.drawString(30*mm, y, "insurance and fire safety compliance.")
    
    c.save()
    buffer.seek(0)
    return buffer

# ===== MAIN =====

import json
import secrets
from datetime import datetime, timedelta

# ===== ENGINE CORE API =====
# Facilities Management - Case & Evidence Artefacts

# Create evidence directory
os.makedirs('evidence', exist_ok=True)

# ===== PAYLOAD UTILITIES =====

def create_base_payload(artefact):
    """
    Create canonical base payload for new artefact.
    This ensures all artefacts start with a valid, minimal payload structure.
    
    Args:
        artefact: Dict with artefact fields (id, artefact_type, title, status, created_at)
    
    Returns:
        Dict: Canonical payload structure
    """
    return {
        'meta': {
            'schema_version': '1.0',
            'type': artefact['artefact_type'],
            'title': artefact['title'],
            'status': artefact['status'],
            'date': artefact.get('created_at', datetime.now().isoformat())[:10],  # YYYY-MM-DD
            'analyst': artefact.get('created_by', 'system'),
            'context': {
                'journey': '',
                'incident_type': '',
                'audience': '',
                'analysis_framework': {
                    'key': '',
                    'version': ''
                }
            }
        },
        'modules_enabled': [],
        'targets': []
    }


def resolve_audience(request_args, payload):
    """
    Centralized audience resolution for multi-audience rendering (Batch 4.1)
    
    Priority:
    1. Query parameter ?audience=... (if valid)
    2. payload.meta.context.audience (canonical key or old label)
    3. Default to 'technical'
    
    Args:
        request_args: Flask request.args object
        payload: Artefact payload dict
    
    Returns:
        str: One of 'executive', 'technical', 'compliance'
    """
    # Query parameter takes priority
    audience_param = request_args.get('audience', '').lower().strip()
    if audience_param in ['executive', 'technical', 'compliance']:
        return audience_param
    
    # Fallback to payload context
    context_audience = payload.get('meta', {}).get('context', {}).get('audience', '').lower().strip()
    
    # If already canonical, return it
    if context_audience in ['executive', 'technical', 'compliance']:
        return context_audience
    
    # Map old labels to canonical keys (backward compatibility)
    # Matches partial text for robustness
    audience_mapping = {
        'executive': 'executive',
        'c-suite': 'executive',
        'board': 'executive',
        'technical': 'technical',
        'engineer': 'technical',
        'devops': 'technical',
        'soc': 'technical',
        'incident response': 'technical',
        'risk management': 'technical',
        'legal': 'compliance',
        'compliance': 'compliance',
        'audit': 'compliance',
    }
    
    # Check for keyword matches in context audience
    for keyword, canonical in audience_mapping.items():
        if keyword in context_audience:
            return canonical
    
    # Default to technical (full view)
    return 'technical'


def resolve_audience(request_args, payload):
    """
    Centralized audience resolution for multi-audience rendering (Batch 4.1)
    
    Priority:
    1. Query parameter ?audience=... (if valid)
    2. payload.meta.context.audience (canonical key or old label)
    3. Default to 'technical'
    
    Args:
        request_args: Flask request.args object
        payload: Artefact payload dict
    
    Returns:
        str: One of 'executive', 'technical', 'compliance'
    """
    # Query parameter takes priority
    audience_param = request_args.get('audience', '').lower().strip()
    if audience_param in ['executive', 'technical', 'compliance']:
        return audience_param
    
    # Fallback to payload context
    context_audience = payload.get('meta', {}).get('context', {}).get('audience', '').lower().strip()
    
    # If already canonical, return it
    if context_audience in ['executive', 'technical', 'compliance']:
        return context_audience
    
    # Map old labels to canonical keys (backward compatibility)
    # Matches partial text for robustness
    audience_mapping = {
        'executive': 'executive',
        'c-suite': 'executive',
        'board': 'executive',
        'technical': 'technical',
        'engineer': 'technical',
        'devops': 'technical',
        'soc': 'technical',
        'incident response': 'technical',
        'risk management': 'technical',
        'legal': 'compliance',
        'compliance': 'compliance',
        'audit': 'compliance',
    }
    
    # Check for keyword matches in context audience
    for keyword, canonical in audience_mapping.items():
        if keyword in context_audience:
            return canonical
    
    # Default to technical (full view)
    return 'technical'


def map_audience_to_canonical(audience):
    """
    Map audience label to canonical key (Batch 5)
    Used for validation and normalization before saving.
    
    Args:
        audience: Audience string (canonical or old label)
    
    Returns:
        str: Canonical audience key ('executive', 'technical', 'compliance')
    """
    if not audience:
        return ''  # Allow empty (will default to technical on render)
    
    # Already canonical
    if audience in ['executive', 'technical', 'compliance']:
        return audience
    
    audience_lower = audience.lower().strip()
    
    # Already canonical (case-insensitive)
    if audience_lower in ['executive', 'technical', 'compliance']:
        return audience_lower
    
    # Map old labels to canonical
    if any(keyword in audience_lower for keyword in ['executive', 'c-suite', 'board']):
        return 'executive'
    if any(keyword in audience_lower for keyword in ['technical', 'engineer', 'devops', 'soc', 'incident response']):
        return 'technical'
    if any(keyword in audience_lower for keyword in ['legal', 'compliance', 'audit']):
        return 'compliance'
    
    # Unknown label - default to technical
    return 'technical'


def validate_payload(payload):
    """
    Validate payload structure before saving (Batch 5: Enhanced)
    Enforces:
    - Required keys and types
    - Context shape and canonical audience
    - Module consistency
    - Target shape
    - Backward compatibility (maps old labels)
    
    Args:
        payload: Dict to validate (modified in-place for audience mapping)
    
    Returns:
        tuple: (is_valid, error_message, field_path or None)
    """
    # Required top-level keys
    required_keys = ['meta', 'modules_enabled', 'targets']
    
    for key in required_keys:
        if key not in payload:
            return False, f"Missing required key: '{key}'", key
    
    # Validate meta is dict
    if not isinstance(payload['meta'], dict):
        return False, "'meta' must be an object", "meta"
    
    # Validate meta has required fields
    required_meta = ['schema_version', 'type', 'title', 'status']
    for field in required_meta:
        if field not in payload['meta']:
            return False, f"Missing required meta field: '{field}'", f"meta.{field}"
    
    # === BATCH 5: CONTEXT VALIDATION ===
    
    # Validate context exists and is object
    if 'context' not in payload['meta']:
        return False, "Missing required meta field: 'context'", "meta.context"
    
    if not isinstance(payload['meta']['context'], dict):
        return False, "'meta.context' must be an object", "meta.context"
    
    context = payload['meta']['context']
    
    # Validate audience (with backward-compatible mapping)
    if 'audience' in context:
        audience = context.get('audience', '')
        
        # Validate type
        if not isinstance(audience, str):
            return False, "'audience' must be a string", "meta.context.audience"
        
        # Map to canonical (in-place for backward compatibility)
        canonical_audience = map_audience_to_canonical(audience)
        payload['meta']['context']['audience'] = canonical_audience
    
    # Validate analysis_framework exists and is object
    if 'analysis_framework' not in context:
        return False, "Missing required context field: 'analysis_framework'", "meta.context.analysis_framework"
    
    if not isinstance(context['analysis_framework'], dict):
        return False, "'analysis_framework' must be an object", "meta.context.analysis_framework"
    
    framework = context['analysis_framework']
    
    # Validate analysis_framework has key and version (can be empty strings)
    if 'key' not in framework:
        return False, "Missing 'analysis_framework.key'", "meta.context.analysis_framework.key"
    
    if 'version' not in framework:
        return False, "Missing 'analysis_framework.version'", "meta.context.analysis_framework.version"
    
    if not isinstance(framework['key'], str):
        return False, "'analysis_framework.key' must be a string", "meta.context.analysis_framework.key"
    
    if not isinstance(framework['version'], str):
        return False, "'analysis_framework.version' must be a string", "meta.context.analysis_framework.version"
    
    # === BATCH 5: MODULE CONSISTENCY ===
    
    # Validate modules_enabled is array
    if not isinstance(payload['modules_enabled'], list):
        return False, "'modules_enabled' must be an array", "modules_enabled"
    
    # Known modules (for reference, but allow unknown for extensibility)
    known_modules = ['risk_assessment', 'site_inspection', 'contractor_review', 'incident_case']
    
    # For each enabled module, ensure payload contains corresponding object
    for module_name in payload['modules_enabled']:
        if not isinstance(module_name, str):
            return False, f"Module name must be string, got {type(module_name).__name__}", "modules_enabled"
        
        # Module must have corresponding payload data
        if module_name not in payload:
            return False, f"Module '{module_name}' enabled but no data provided", module_name
        
        # Module data must be an object
        if not isinstance(payload[module_name], dict):
            return False, f"Module '{module_name}' data must be an object, got {type(payload[module_name]).__name__}", module_name
    
    # === BATCH 5: TARGET SHAPE VALIDATION ===
    
    # Validate targets is array
    if not isinstance(payload['targets'], list):
        return False, "'targets' must be an array", "targets"
    
    # Validate each target structure
    for i, target in enumerate(payload['targets']):
        if not isinstance(target, dict):
            return False, f"Target {i} must be an object, got {type(target).__name__}", f"targets[{i}]"
        
        # Target must have 'name' field
        if 'name' not in target:
            return False, f"Target {i} missing required field: 'name'", f"targets[{i}].name"
        
        if not isinstance(target['name'], str):
            return False, f"Target {i} 'name' must be a string", f"targets[{i}].name"
        
        # Target must have 'assets' field
        if 'assets' not in target:
            return False, f"Target {i} missing required field: 'assets'", f"targets[{i}].assets"
        
        if not isinstance(target['assets'], list):
            return False, f"Target {i} 'assets' must be an array", f"targets[{i}].assets"
    
    return True, None, None


# ===== API: ARTEFACTS =====


@app.route('/api/context-options', methods=['GET'])
def api_context_options():
    """Return Facilities Management context options for the report editor."""
    try:
        with open('context_options.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to load context options: {str(e)}'}), 500

@app.route('/api/artefacts', methods=['POST'])
def create_artefact():
    """Create new artefact with initial version"""
    data = request.get_json()
    
    artefact_type = data.get('artefact_type')
    title = data.get('title')
    client_id = data.get('client_id')
    status = data.get('status', 'Draft')
    created_by = data.get('created_by', 'system')
    
    # Validate artefact_type
    valid_types = ['engagement', 'incident', 'assessment', 'investigation']
    if artefact_type not in valid_types:
        return jsonify({'error': f'Invalid artefact_type. Must be one of: {valid_types}'}), 400
    
    conn = get_db()
    
    # Create artefact record
    cursor = conn.execute(
        '''INSERT INTO artefacts (artefact_type, title, client_id, status, created_by)
           VALUES (?, ?, ?, ?, ?)''',
        (artefact_type, title, client_id, status, created_by)
    )
    artefact_id = cursor.lastrowid
    
    # Get the created artefact to build payload
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = dict(cursor.fetchone())
    
    # Create base payload
    base_payload = create_base_payload(artefact)
    payload_json = json.dumps(base_payload)
    
    # Create version 1 automatically
    cursor = conn.execute(
        '''INSERT INTO artefact_versions 
           (artefact_id, version_no, payload_json, rendered_html, version_notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (artefact_id, 1, payload_json, '', 'Initial version', created_by)
    )
    version_id = cursor.lastrowid
    
    conn.commit()
    conn.close()
    
    return jsonify({
        'id': artefact_id,
        'artefact_type': artefact_type,
        'title': title,
        'status': status,
        'version_no': 1,
        'created_at': datetime.now().isoformat()
    }), 201

@app.route('/api/artefacts', methods=['GET'])
def list_artefacts():
    """List artefacts with optional filtering"""
    artefact_type = request.args.get('type')
    module_key = request.args.get('module')
    status = request.args.get('status')
    
    conn = get_db()
    
    query = '''
        SELECT a.*, 
               MAX(av.version_no) as latest_version,
               COUNT(DISTINCT av.id) as version_count
        FROM artefacts a
        LEFT JOIN artefact_versions av ON a.id = av.artefact_id
    '''
    
    conditions = []
    params = []
    
    if artefact_type:
        conditions.append('a.artefact_type = ?')
        params.append(artefact_type)
    
    if status:
        conditions.append('a.status = ?')
        params.append(status)
    
    if module_key:
        query += ' LEFT JOIN artefact_modules am ON a.id = am.artefact_id'
        conditions.append('am.module_key = ? AND am.enabled = 1')
        params.append(module_key)
    
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    
    query += ' GROUP BY a.id ORDER BY a.updated_at DESC'
    
    cursor = conn.execute(query, params)
    artefacts = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(artefacts)

@app.route('/api/artefacts/<int:artefact_id>', methods=['GET'])
def get_artefact(artefact_id):
    """Get artefact metadata"""
    conn = get_db()
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    conn.close()
    
    if not artefact:
        return jsonify({'error': 'Artefact not found'}), 404
    
    return jsonify(dict(artefact))

@app.route('/api/artefacts/<int:artefact_id>', methods=['PUT'])
def update_artefact(artefact_id):
    """Update artefact metadata"""
    data = request.get_json()
    
    conn = get_db()
    
    # Check exists
    cursor = conn.execute('SELECT id FROM artefacts WHERE id = ?', (artefact_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Artefact not found'}), 404
    
    # Update
    updates = []
    params = []
    
    for field in ['title', 'status', 'client_id']:
        if field in data:
            updates.append(f'{field} = ?')
            params.append(data[field])
    
    if updates:
        params.append(artefact_id)
        conn.execute(
            f"UPDATE artefacts SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
    
    conn.close()
    return jsonify({'success': True})

@app.route('/api/artefacts/<int:artefact_id>', methods=['DELETE'])
def delete_artefact(artefact_id):
    """Delete artefact (cascade deletes versions, links, evidence)"""
    conn = get_db()
    cursor = conn.execute('SELECT id FROM artefacts WHERE id = ?', (artefact_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Artefact not found'}), 404
    
    # Delete evidence files from filesystem
    cursor = conn.execute('SELECT file_path FROM evidence_files WHERE artefact_id = ?', (artefact_id,))
    for row in cursor.fetchall():
        file_path = os.path.join('evidence', row[0])
        if os.path.exists(file_path):
            os.remove(file_path)
    
    # Delete artefact (cascade handles related records)
    conn.execute('DELETE FROM artefacts WHERE id = ?', (artefact_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/artefacts/<int:artefact_id>/versions', methods=['POST'])
def save_version(artefact_id):
    """Save new version of artefact with payload validation"""
    data = request.get_json()
    
    payload = data.get('payload')  # OBJECT, not string
    rendered_html = data.get('rendered_html')
    version_notes = data.get('version_notes', '')
    created_by = data.get('created_by', 'system')
    
    if not payload:
        return jsonify({'error': 'payload is required'}), 400
    
    # Validate payload structure (Batch 5: Enhanced validation)
    is_valid, error_message, field_path = validate_payload(payload)
    if not is_valid:
        error_response = {'error': f'Invalid payload: {error_message}'}
        if field_path:
            error_response['field'] = field_path
        return jsonify(error_response), 400
    
    # Server serializes payload to JSON
    payload_json = json.dumps(payload)
    
    conn = get_db()
    
    # Check artefact exists
    cursor = conn.execute('SELECT id FROM artefacts WHERE id = ?', (artefact_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Artefact not found'}), 404
    
    # Get next version number
    cursor = conn.execute(
        'SELECT COALESCE(MAX(version_no), 0) + 1 as next_version FROM artefact_versions WHERE artefact_id = ?',
        (artefact_id,)
    )
    version_no = cursor.fetchone()[0]
    
    # Insert version
    cursor = conn.execute(
        '''INSERT INTO artefact_versions 
           (artefact_id, version_no, payload_json, rendered_html, version_notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (artefact_id, version_no, payload_json, rendered_html, version_notes, created_by)
    )
    version_id = cursor.lastrowid
    
    # Update artefact updated_at
    conn.execute('UPDATE artefacts SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (artefact_id,))
    
    conn.commit()
    conn.close()
    
    return jsonify({
        'version_id': version_id,
        'version_no': version_no,
        'artefact_id': artefact_id,
        'created_at': datetime.now().isoformat(),
        'link': f'/artefacts/{artefact_id}/v/{version_no}'
    }), 201

@app.route('/api/artefacts/<int:artefact_id>/versions', methods=['GET'])
def list_versions(artefact_id):
    """List all versions of an artefact"""
    conn = get_db()
    cursor = conn.execute(
        '''SELECT id as version_id, version_no, version_notes, created_at, created_by
           FROM artefact_versions
           WHERE artefact_id = ?
           ORDER BY version_no DESC''',
        (artefact_id,)
    )
    versions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(versions)

@app.route('/api/artefacts/<int:artefact_id>/versions/<int:version_no>', methods=['GET'])
def get_version(artefact_id, version_no):
    """Get specific version (returns payload as object)"""
    conn = get_db()
    cursor = conn.execute(
        '''SELECT id as version_id, version_no, payload_json, rendered_html, version_notes, created_at, created_by
           FROM artefact_versions
           WHERE artefact_id = ? AND version_no = ?''',
        (artefact_id, version_no)
    )
    version = cursor.fetchone()
    conn.close()
    
    if not version:
        return jsonify({'error': 'Version not found'}), 404
    
    result = dict(version)
    # Deserialize payload_json to object
    result['payload'] = json.loads(result['payload_json'])
    del result['payload_json']  # Remove the string version
    
    return jsonify(result)

@app.route('/api/artefacts/<int:artefact_id>/share', methods=['POST'])
def create_share_link(artefact_id):
    """Create share link for artefact version"""
    data = request.get_json() or {}
    version_no = data.get('version_no')
    expires_in_days = data.get('expires_in_days')
    
    conn = get_db()
    
    # Check artefact exists
    cursor = conn.execute('SELECT id FROM artefacts WHERE id = ?', (artefact_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Artefact not found'}), 404
    
    # Get version
    if version_no:
        cursor = conn.execute(
            'SELECT id FROM artefact_versions WHERE artefact_id = ? AND version_no = ?',
            (artefact_id, version_no)
        )
    else:
        # Use latest version
        cursor = conn.execute(
            'SELECT id FROM artefact_versions WHERE artefact_id = ? ORDER BY version_no DESC LIMIT 1',
            (artefact_id,)
        )
    
    version = cursor.fetchone()
    if not version:
        conn.close()
        return jsonify({'error': 'Version not found'}), 404
    
    version_id = version[0]
    
    # Generate unique slug
    slug = secrets.token_urlsafe(16)
    
    # Calculate expiry
    expires_at = None
    if expires_in_days:
        expires_at = datetime.now() + timedelta(days=expires_in_days)
    
    # Create link
    conn.execute(
        'INSERT INTO artefact_links (artefact_id, version_id, slug, expires_at) VALUES (?, ?, ?, ?)',
        (artefact_id, version_id, slug, expires_at)
    )
    conn.commit()
    conn.close()
    
    return jsonify({
        'slug': slug,
        'url': f'/share/{slug}',
        'expires_at': expires_at.isoformat() if expires_at else None
    }), 201

@app.route('/api/artefacts/<int:artefact_id>/modules', methods=['GET'])
def get_artefact_modules(artefact_id):
    """Get enabled modules for artefact"""
    conn = get_db()
    cursor = conn.execute(
        '''SELECT am.module_key, m.module_name, m.icon, am.enabled
           FROM artefact_modules am
           JOIN modules m ON am.module_key = m.module_key
           WHERE am.artefact_id = ?''',
        (artefact_id,)
    )
    modules = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(modules)

@app.route('/api/artefacts/<int:artefact_id>/modules', methods=['POST'])
def update_artefact_modules(artefact_id):
    """Enable/disable modules for artefact"""
    data = request.get_json()
    module_key = data.get('module_key')
    enabled = data.get('enabled', True)
    
    conn = get_db()
    
    # Check artefact exists
    cursor = conn.execute('SELECT id FROM artefacts WHERE id = ?', (artefact_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Artefact not found'}), 404
    
    # Check module exists
    cursor = conn.execute('SELECT module_key FROM modules WHERE module_key = ?', (module_key,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Module not found'}), 404
    
    # Insert or update
    conn.execute(
        '''INSERT INTO artefact_modules (artefact_id, module_key, enabled)
           VALUES (?, ?, ?)
           ON CONFLICT(artefact_id, module_key) DO UPDATE SET enabled = ?''',
        (artefact_id, module_key, enabled, enabled)
    )
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/evidence', methods=['POST'])
def upload_evidence():
    """Upload evidence file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    artefact_id = request.form.get('artefact_id')
    file_type = request.form.get('file_type', 'screenshot')
    notes = request.form.get('notes', '')
    
    if not artefact_id:
        return jsonify({'error': 'artefact_id is required'}), 400
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    ext = os.path.splitext(original_filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    file_path = filename
    
    # Save file
    full_path = os.path.join('evidence', filename)
    file.save(full_path)
    file_size = os.path.getsize(full_path)
    
    # Save metadata
    conn = get_db()
    cursor = conn.execute(
        '''INSERT INTO evidence_files 
           (artefact_id, filename, original_filename, file_type, file_path, file_size, mime_type, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (artefact_id, filename, original_filename, file_type, file_path, file_size, file.content_type, notes)
    )
    file_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({
        'file_id': file_id,
        'filename': filename,
        'original_filename': original_filename,
        'file_size': file_size,
        'url': f'/api/evidence/{file_id}'
    }), 201

@app.route('/api/evidence/<int:file_id>', methods=['GET'])
def download_evidence(file_id):
    """Download evidence file"""
    conn = get_db()
    cursor = conn.execute(
        'SELECT filename, original_filename, mime_type FROM evidence_files WHERE id = ?',
        (file_id,)
    )
    evidence = cursor.fetchone()
    conn.close()
    
    if not evidence:
        return jsonify({'error': 'Evidence not found'}), 404
    
    file_path = os.path.join('evidence', evidence[0])
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found on disk'}), 404
    
    return send_file(file_path, 
                     mimetype=evidence[2],
                     as_attachment=True,
                     download_name=evidence[1])

@app.route('/api/evidence/<int:file_id>', methods=['DELETE'])
def delete_evidence(file_id):
    """Delete evidence file"""
    conn = get_db()
    cursor = conn.execute('SELECT file_path FROM evidence_files WHERE id = ?', (file_id,))
    evidence = cursor.fetchone()
    
    if not evidence:
        conn.close()
        return jsonify({'error': 'Evidence not found'}), 404
    
    # Delete file from filesystem
    file_path = os.path.join('evidence', evidence[0])
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Delete metadata
    conn.execute('DELETE FROM evidence_files WHERE id = ?', (file_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

# ===== ENGINE UI ROUTES =====

# =========================================================================
# PUBLIC LANDING PAGE
# =========================================================================

ISSUE_CATEGORIES = [
    ('plumbing',    'Plumbing / Water'),
    ('electrical',  'Electrical'),
    ('hvac',        'HVAC / Air Conditioning'),
    ('security',    'Security / Access'),
    ('carpentry',   'Carpentry / Fixtures'),
    ('cleaning',    'Cleaning / Waste'),
    ('pest_control','Pest Control'),
    ('painting',    'Painting / Decoration'),
    ('lift',        'Lift / Elevator'),
    ('general',     'General / Other'),
]

def _verify_turnstile(token):
    """Verify Cloudflare Turnstile token. Returns True if valid or not configured."""
    if not TURNSTILE_SECRET:
        return True
    if not token:
        return False
    try:
        import urllib.request as _ur, json as _json
        data = urllib.parse.urlencode({
            'secret': TURNSTILE_SECRET,
            'response': token,
        }).encode()
        req = _ur.Request(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data=data, method='POST'
        )
        with _ur.urlopen(req, timeout=5) as r:
            result = _json.loads(r.read())
        return result.get('success', False)
    except Exception:
        return True  # fail open on network error


@app.route('/')
def dx_public_landing():
    """Devex AI Studios public landing page — Phase 6."""
    return render_template('public/devex_landing.html')


@app.route('/report-issue')
def landing():
    """Legacy: FM tenant issue-reporting form — kept at /report-issue for backward compat."""
    whatsapp_number = (TWILIO_WA_FROM or '').replace('whatsapp:', '').replace('+', '')
    wa_link = f"https://wa.me/{whatsapp_number}" if whatsapp_number else None
    return render_template(
        'public/landing.html',
        categories=ISSUE_CATEGORIES,
        wa_link=wa_link,
        turnstile_sitekey=TURNSTILE_SITEKEY
    )


@app.route('/services')
def dx_public_services():
    """Devex AI Studios services page."""
    return render_template('public/services.html')


@app.route('/contact', methods=['GET'])
def dx_public_contact():
    """Devex AI Studios contact page."""
    return render_template('public/contact.html', success=False, error=None)


@app.route('/contact', methods=['POST'])
@limiter.limit("5 per minute")
def dx_contact_submit():
    """Handle contact form submission — logs to FM tickets as an enquiry."""
    name    = request.form.get('name', '').strip()
    email   = request.form.get('email', '').strip()
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()

    if not name or not email or not message:
        return render_template('public/contact.html',
                               success=False,
                               error='Please fill in your name, email, and message.',
                               form=request.form)

    # Log as a ticket for tracking
    try:
        conn = fm_get_db()
        ref = fm_generate_ref()
        summary = f"Contact form: {subject or '(no subject)'} — {message[:150]}"
        conn.execute(
            """INSERT INTO fm_tickets
               (ref, estate, unit, customer, source, priority, category, status, summary)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ref, 'Website', '', f"{name} <{email}>",
             'webchat', 'normal', 'general', 'NEW', summary)
        )
        conn.execute(
            """INSERT INTO fm_conversations (ticket_ref, sender, body, source)
               VALUES (?,?,?,?)""",
            (ref, 'customer', f"From: {name}\nEmail: {email}\nSubject: {subject}\n\n{message}", 'webchat')
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[CONTACT] Failed to log enquiry: {e}")

    return render_template('public/contact.html', success=True, error=None)


@app.route('/report', methods=['POST'])
def report_submit():
    """
    Handle tenant issue form submission.
    Creates FM ticket directly, then returns confirmation + WhatsApp fallback.
    """
    name        = request.form.get('name', '').strip()
    phone       = request.form.get('phone', '').strip()
    location    = request.form.get('location', '').strip()
    category    = request.form.get('category', 'general').strip()
    urgency     = request.form.get('urgency', 'normal').strip()
    description = request.form.get('description', '').strip()

    # Server-side validation — client HTML can be bypassed
    _errors = []
    if not name:        _errors.append('Full name is required.')
    if not location:    _errors.append('Location / site / flat is required.')
    if not description: _errors.append('Please describe the issue.')
    valid_categories = {
        'plumbing','electrical','hvac','security','carpentry',
        'cleaning','pest_control','painting','lift','general'
    }
    valid_urgencies = {'urgent', 'normal', 'low'}
    if category not in valid_categories:
        category = 'general'
    if urgency not in valid_urgencies:
        urgency = 'normal'
    if _errors:
        return render_template(
            'public/landing.html',
            categories=ISSUE_CATEGORIES,
            error=' '.join(_errors),
            form=request.form,
            turnstile_sitekey=TURNSTILE_SITEKEY
        )

    # Bot protection
    if not _verify_turnstile(request.form.get('cf-turnstile-response', '')):
        return render_template(
            'public/landing.html',
            categories=ISSUE_CATEGORIES,
            error='Security check failed. Please try again.',
            form=request.form,
            turnstile_sitekey=TURNSTILE_SITEKEY
        )

    # Map urgency label → FM priority
    priority_map = {'urgent': 'urgent', 'normal': 'normal', 'low': 'low'}
    priority = priority_map.get(urgency, 'normal')

    summary = description[:200]
    customer = name or phone or 'Web submission'

    conn = fm_get_db()
    ref = fm_generate_ref()
    conn.execute(
        """INSERT INTO fm_tickets
           (ref, estate, unit, customer, source, priority, category, status, summary)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ref, location, '', customer, 'webchat', priority, category, 'NEW', summary)
    )
    # Store the full description as a conversation message
    conn.execute(
        """INSERT INTO fm_conversations (ticket_ref, sender, body, source)
           VALUES (?,?,?,?)""",
        (ref, 'customer', description, 'webchat')
    )
    conn.commit()
    conn.close()

    print(f"[WEB] New ticket {ref} from {customer!r} category={category} priority={priority}")

    # Build prefilled WhatsApp message
    wa_text = (
        f"Hello, I want to report an issue.\n"
        f"Name: {name}\n"
        f"Phone: {phone}\n"
        f"Location: {location}\n"
        f"Category: {category}\n"
        f"Urgency: {urgency}\n"
        f"Description: {description}"
    )
    wa_number = (TWILIO_WA_FROM or '').replace('whatsapp:', '').replace('+', '')
    wa_link = f"https://wa.me/{wa_number}?text={urlquote(wa_text)}" if wa_number else None

    return render_template(
        'public/confirmation.html',
        ref=ref,
        name=name,
        wa_link=wa_link,
        wa_text=wa_text
    )


@app.route('/ops')
@app.route('/ops/dashboard')
def dashboard():
    """Internal ops artefacts dashboard"""
    conn = get_db()
    
    # Get all artefacts
    cursor = conn.execute('''
        SELECT * FROM artefacts
        ORDER BY updated_at DESC
    ''')
    all_artefacts = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    # Group by type
    artefacts_by_type = {
        'engagement': [],
        'incident': [],
        'investigation': [],
        'assessment': []
    }
    
    stats = {
        'total': len(all_artefacts),
        'draft': 0,
        'in_review': 0,
        'final': 0
    }
    
    for artefact in all_artefacts:
        artefacts_by_type[artefact['artefact_type']].append(artefact)
        
        if artefact['status'] == 'Draft':
            stats['draft'] += 1
        elif artefact['status'] == 'In Review':
            stats['in_review'] += 1
        elif artefact['status'] == 'Final':
            stats['final'] += 1
    
    return render_template('dashboard.html',
                         artefacts_by_type=artefacts_by_type,
                         stats=stats,
                         active_nav='dashboard')

@app.route('/artefacts')
def list_artefacts_view():
    """List artefacts filtered by type and status"""
    artefact_type = request.args.get('type')
    status = request.args.get('status')
    
    conn = get_db()
    
    query = 'SELECT * FROM artefacts WHERE 1=1'
    params = []
    
    if artefact_type:
        query += ' AND artefact_type = ?'
        params.append(artefact_type)
    
    if status:
        query += ' AND status = ?'
        params.append(status)
    
    query += ' ORDER BY updated_at DESC'
    
    cursor = conn.execute(query, params)
    artefacts = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Determine active nav
    active_nav_map = {
        'engagement': 'engagements',
        'incident': 'incidents',
        'investigation': 'investigations',
        'assessment': 'assessments'
    }
    active_nav = active_nav_map.get(artefact_type, 'artefacts')
    
    # Page title
    title_map = {
        'engagement': 'Inspection Reports',
        'incident': 'Incident Reports',
        'investigation': 'Investigations',
        'assessment': 'Assessments'
    }
    page_title = title_map.get(artefact_type, 'All Reports')
    
    return render_template('artefacts/list.html',
                         artefacts=artefacts,
                         artefact_type=artefact_type,
                         current_status=status,
                         page_title=page_title,
                         active_nav=active_nav)

@app.route('/artefacts/new')
def new_artefact():
    """Create new artefact wizard"""
    return render_template('engine/new_artefact.html', active_nav='new_artefact')

@app.route('/artefacts/<int:artefact_id>/edit')
def edit_artefact(artefact_id):
    """Edit artefact in engine editor"""
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get enabled modules
    cursor = conn.execute('''
        SELECT m.module_key, m.module_name, m.icon, m.description
        FROM artefact_modules am
        JOIN modules m ON am.module_key = m.module_key
        WHERE am.artefact_id = ? AND am.enabled = 1
        ORDER BY m.display_order
    ''', (artefact_id,))
    enabled_modules = [dict(row) for row in cursor.fetchall()]
    
    # Get latest version payload (if exists)
    cursor = conn.execute('''
        SELECT payload_json FROM artefact_versions
        WHERE artefact_id = ?
        ORDER BY version_no DESC
        LIMIT 1
    ''', (artefact_id,))
    latest_version = cursor.fetchone()
    payload = json.loads(latest_version[0]) if latest_version else None
    
    conn.close()
    
    return render_template('engine/editor.html',
                         artefact=dict(artefact),
                         enabled_modules=enabled_modules,
                         payload=payload,
                         active_nav='artefacts')

@app.route('/artefacts/<int:artefact_id>')
def view_artefact(artefact_id):
    """View latest version of artefact with audience filtering (Batch 4, 4.1)"""
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get latest version
    cursor = conn.execute('''
        SELECT * FROM artefact_versions
        WHERE artefact_id = ?
        ORDER BY version_no DESC
        LIMIT 1
    ''', (artefact_id,))
    version = cursor.fetchone()
    
    conn.close()
    
    if not version:
        return f"No versions saved yet. <a href='/artefacts/{artefact_id}/edit'>Edit artefact</a>", 404
    
    payload = json.loads(version['payload_json'])
    
    # Centralized audience resolution (Batch 4.1)
    audience = resolve_audience(request.args, payload)
    
    return render_template('artefacts/view.html',
                         artefact=dict(artefact),
                         version=dict(version),
                         version_no=version['version_no'],
                         payload=payload,
                         audience=audience,
                         active_nav='artefacts')

@app.route('/artefacts/<int:artefact_id>/v/<int:version_no>')
def view_artefact_version(artefact_id, version_no):
    """View specific version of artefact with audience filtering (Batch 4, 4.1)"""
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get specific version
    cursor = conn.execute('''
        SELECT * FROM artefact_versions
        WHERE artefact_id = ? AND version_no = ?
    ''', (artefact_id, version_no))
    version = cursor.fetchone()
    
    conn.close()
    
    if not version:
        return "Version not found", 404
    
    payload = json.loads(version['payload_json'])
    
    # Centralized audience resolution (Batch 4.1)
    audience = resolve_audience(request.args, payload)
    
    return render_template('artefacts/view.html',
                         artefact=dict(artefact),
                         version=dict(version),
                         version_no=version_no,
                         payload=payload,
                         audience=audience,
                         active_nav='artefacts')

@app.route('/artefacts/<int:artefact_id>/pdf')
def export_artefact_pdf(artefact_id):
    """Export latest version as PDF with audience filtering (Batch 4, 4.1)"""
    try:
        from weasyprint import HTML
    except ImportError:
        return render_template('errors/weasyprint_missing.html', artefact_id=artefact_id), 500
    
    from io import BytesIO
    
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get latest version
    cursor = conn.execute('''
        SELECT * FROM artefact_versions
        WHERE artefact_id = ?
        ORDER BY version_no DESC
        LIMIT 1
    ''', (artefact_id,))
    version = cursor.fetchone()
    
    conn.close()
    
    if not version:
        return "No versions to export", 404
    
    payload = json.loads(version['payload_json'])
    
    # Centralized audience resolution (Batch 4.1)
    audience = resolve_audience(request.args, payload)
    
    # Render HTML with audience filtering
    html_content = render_template('artefacts/view.html',
                                  artefact=dict(artefact),
                                  version=dict(version),
                                  version_no=version['version_no'],
                                  payload=payload,
                                  audience=audience,
                                  is_pdf_export=True)
    
    try:
        # Generate PDF
        pdf_file = BytesIO()
        HTML(string=html_content, base_url=request.url_root).write_pdf(pdf_file)
        pdf_file.seek(0)
        
        # Send file with audience in filename
        audience_suffix = f"_{audience}" if audience != 'technical' else ""
        filename = f"{artefact['title'].replace(' ', '_')}_v{version['version_no']}{audience_suffix}.pdf"
        return send_file(pdf_file, 
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=filename)
    except Exception as e:
        return f"PDF generation failed: {str(e)}", 500

@app.route('/artefacts/<int:artefact_id>/v/<int:version_no>/pdf')
def export_artefact_version_pdf(artefact_id, version_no):
    """Export specific version as PDF with audience filtering (Batch 4, 4.1)"""
    try:
        from weasyprint import HTML
    except ImportError:
        return render_template('errors/weasyprint_missing.html', 
                             artefact_id=artefact_id, 
                             version_no=version_no), 500
    
    from io import BytesIO
    
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get specific version
    cursor = conn.execute('''
        SELECT * FROM artefact_versions
        WHERE artefact_id = ? AND version_no = ?
    ''', (artefact_id, version_no))
    version = cursor.fetchone()
    
    conn.close()
    
    if not version:
        return "Version not found", 404
    
    payload = json.loads(version['payload_json'])
    
    # Centralized audience resolution (Batch 4.1)
    audience = resolve_audience(request.args, payload)
    
    # Render HTML with audience filtering
    html_content = render_template('artefacts/view.html',
                                  artefact=dict(artefact),
                                  version=dict(version),
                                  version_no=version_no,
                                  payload=payload,
                                  audience=audience,
                                  is_pdf_export=True)
    
    try:
        # Generate PDF
        pdf_file = BytesIO()
        HTML(string=html_content, base_url=request.url_root).write_pdf(pdf_file)
        pdf_file.seek(0)
        
        # Send file with audience in filename
        audience_suffix = f"_{audience}" if audience != 'technical' else ""
        filename = f"{artefact['title'].replace(' ', '_')}_v{version_no}{audience_suffix}.pdf"
        return send_file(pdf_file,
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name=filename)
    except Exception as e:
        return f"PDF generation failed: {str(e)}", 500

@app.route('/artefacts/<int:artefact_id>/versions')
def artefact_versions(artefact_id):
    """View version history"""
    conn = get_db()
    
    # Get artefact
    cursor = conn.execute('SELECT * FROM artefacts WHERE id = ?', (artefact_id,))
    artefact = cursor.fetchone()
    
    if not artefact:
        conn.close()
        return "Artefact not found", 404
    
    # Get all versions
    cursor = conn.execute('''
        SELECT * FROM artefact_versions
        WHERE artefact_id = ?
        ORDER BY version_no DESC
    ''', (artefact_id,))
    versions = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return render_template('artefacts/versions.html',
                         artefact=dict(artefact),
                         versions=versions,
                         active_nav='artefacts')

@app.route('/share/<slug>')
def view_share(slug):
    """View shared artefact via slug with audience filtering (Batch 4.1)"""
    conn = get_db()
    
    # Get link and check expiry
    cursor = conn.execute('''
        SELECT al.*, a.*, av.payload_json, av.version_no
        FROM artefact_links al
        JOIN artefacts a ON al.artefact_id = a.id
        JOIN artefact_versions av ON al.version_id = av.id
        WHERE al.slug = ?
    ''', (slug,))
    result = cursor.fetchone()
    
    conn.close()
    
    if not result:
        return "Share link not found or expired", 404
    
    # Check expiry
    if result['expires_at']:
        from datetime import datetime
        expires = datetime.fromisoformat(result['expires_at'])
        if datetime.now() > expires:
            return "Share link has expired", 410
    
    payload = json.loads(result['payload_json'])
    
    # Centralized audience resolution (Batch 4.1)
    audience = resolve_audience(request.args, payload)
    
    return render_template('artefacts/view.html',
                         artefact=dict(result),
                         version={'created_at': result['created_at']},
                         version_no=result['version_no'],
                         payload=payload,
                         audience=audience,
                         active_nav=None,
                         is_share=True)

@app.route('/commercial/quote')
def commercial_quote():
    """Facilities management quote generator (to be implemented)"""
    return render_template('commercial/quote_stub.html', 
                         active_nav='quote',
                         page_title='Service Quote Generator')

@app.route('/commercial/invoice')
def commercial_invoice():
    """Invoice generator (to be implemented)"""
    return render_template('commercial/invoice_stub.html',
                         active_nav='invoice', 
                         page_title='Invoice Generator')


# ===== DATABASE ROUTES (CLIENTS + SERVICES) =====

@app.route('/databases/clients')
def database_clients():
    """Client database list"""
    conn = get_db()
    cursor = conn.execute('SELECT * FROM clients ORDER BY created_at DESC')
    clients = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('databases/clients_list.html',
                         clients=clients,
                         active_nav='clients',
                         page_title='Client Database')

@app.route('/databases/clients/new', methods=['GET', 'POST'])
def database_clients_new():
    """Add new client"""
    if request.method == 'POST':
        conn = get_db()
        conn.execute('''
            INSERT INTO clients (name, email, phone, organisation, address, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            request.form.get('name'),
            request.form.get('email'),
            request.form.get('phone'),
            request.form.get('organisation'),
            request.form.get('address'),
            request.form.get('notes')
        ))
        conn.commit()
        conn.close()
        return redirect('/databases/clients')
    
    return render_template('databases/clients_new.html',
                         active_nav='clients',
                         page_title='Add Client')

@app.route('/databases/services')
def database_services():
    """Service catalogue list"""
    conn = get_db()
    cursor = conn.execute('SELECT * FROM services ORDER BY created_at DESC')
    services = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('databases/services_list.html',
                         services=services,
                         active_nav='services',
                         page_title='Service Catalogue')

@app.route('/databases/services/new', methods=['GET', 'POST'])
def database_services_new():
    """Add new service"""
    if request.method == 'POST':
        conn = get_db()
        conn.execute('''
            INSERT INTO services (name, description, unit_price, unit_type, active)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            request.form.get('name'),
            request.form.get('description'),
            request.form.get('unit_price'),
            request.form.get('unit_type', 'hour'),
            1 if request.form.get('active') else 0
        ))
        conn.commit()
        conn.close()
        return redirect('/databases/services')
    
    return render_template('databases/services_new.html',
                         active_nav='services',
                         page_title='Add Service')


# ===== FINANCIAL ROUTES (STUBS) =====

@app.route('/financials/quote')
def financial_quote():
    """Job quote generator (stub)"""
    return render_template('financials/quote_stub.html',
                         active_nav='quote',
                         page_title='Quote Generator')

@app.route('/financials/invoice')
def financial_invoice():
    """Invoice generator (stub)"""
    return render_template('financials/invoice_stub.html',
                         active_nav='invoice',
                         page_title='Invoice Generator')





# ===== FM OPERATIONS MODULE =====

# ===== FM: HELPERS =====

def fm_generate_ref():
    """Generate ticket ref: FM-DDMMYY-XXXXX (5 uppercase alphanumeric)."""
    from datetime import datetime
    date_part = datetime.utcnow().strftime('%d%m%y')
    rand_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f'FM-{date_part}-{rand_part}'


def fm_dedup_key(payload_str):
    """SHA-256 dedup key from raw payload string."""
    return hashlib.sha256(payload_str.encode()).hexdigest()[:32]


def fm_infer_priority(text):
    """Keyword-based priority inference."""
    text_lower = text.lower()
    urgent_keywords = [
        'urgent', 'emergency', 'flooding', 'no power', 'no heat',
        'fire', 'gas smell', 'sparks', 'no water', 'burst pipe', 'broken'
    ]
    for kw in urgent_keywords:
        if kw in text_lower:
            return 'urgent'
    return 'normal'


def fm_infer_category(text):
    """Keyword-based category inference."""
    text_lower = text.lower()
    rules = [
        ('plumbing',    ['leak', 'flood', 'water', 'pipe', 'tap', 'drain', 'boiler', 'burst']),
        ('electrical',  ['power', 'socket', 'switch', 'circuit', 'fuse', 'spark', 'electric', 'light']),
        ('security',    ['cctv', 'camera', 'lock', 'alarm', 'gate', 'access', 'key']),
        ('hvac',        ['ac', 'air con', 'cooling', 'ventilation', 'noise', 'thermostat', 'heating']),
        ('carpentry',   ['door', 'hinge', 'window', 'wardrobe', 'frame', 'cabinet', 'shelf']),
        ('cleaning',    ['clean', 'dirt', 'stain', 'rubbish', 'waste']),
        ('painting',    ['paint', 'crack', 'plaster', 'wall', 'ceiling']),
        ('pest_control',['pest', 'rat', 'mouse', 'cockroach', 'insect', 'bug']),
    ]
    for category, keywords in rules:
        for kw in keywords:
            if kw in text_lower:
                return category
    return 'general'


def fm_get_db():
    """FM uses engine.db — same connection as artefacts."""
    conn = sqlite3.connect(ENGINE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ===== FM: UI ROUTES =====


@app.route('/fm')
@app.route('/fm/dashboard')
def fm_dashboard():
    """FM Operations Console — main dashboard."""
    conn = fm_get_db()

    # KPI counts
    kpis = {}
    kpis['total'] = conn.execute("SELECT COUNT(*) FROM fm_tickets").fetchone()[0]
    kpis['open']  = conn.execute(
        "SELECT COUNT(*) FROM fm_tickets WHERE status NOT IN ('DONE','CANCELLED')"
    ).fetchone()[0]
    kpis['urgent'] = conn.execute(
        "SELECT COUNT(*) FROM fm_tickets WHERE priority='urgent' AND status NOT IN ('DONE','CANCELLED')"
    ).fetchone()[0]
    kpis['pending_triage'] = conn.execute(
        "SELECT COUNT(*) FROM fm_tickets WHERE status='NEW'"
    ).fetchone()[0]

    # Ticket queue — most recent 50
    tickets = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_tickets ORDER BY updated_at DESC LIMIT 50"
    ).fetchall()]

    # Estates list for filter dropdown
    estates = [r[0] for r in conn.execute(
        "SELECT DISTINCT estate FROM fm_tickets WHERE estate != '' ORDER BY estate"
    ).fetchall()]

    # Recent events (last 30)
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_inbound_events ORDER BY received_at DESC LIMIT 30"
    ).fetchall()]

    conn.close()

    return render_template(
        'fm/dashboard.html',
        kpis=kpis,
        tickets=tickets,
        estates=estates,
        events=events,
        active_nav='fm_dashboard'
    )


@app.route('/fm/ticket/<ref>')
def fm_ticket_detail(ref):
    """FM ticket detail — conversation + fields."""
    conn = fm_get_db()

    ticket = conn.execute(
        "SELECT * FROM fm_tickets WHERE ref = ?", (ref,)
    ).fetchone()
    if not ticket:
        conn.close()
        return "Ticket not found", 404
    ticket = dict(ticket)

    messages = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_conversations WHERE ticket_ref = ? ORDER BY created_at ASC",
        (ref,)
    ).fetchall()]

    evidence = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_evidence WHERE ticket_ref = ? ORDER BY uploaded_at ASC",
        (ref,)
    ).fetchall()]

    # Recent events for this ticket
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_inbound_events WHERE ticket_ref = ? ORDER BY received_at DESC LIMIT 20",
        (ref,)
    ).fetchall()]

    conn.close()

    return render_template(
        'fm/ticket.html',
        ticket=ticket,
        messages=messages,
        evidence=evidence,
        events=events,
        active_nav='fm_dashboard'
    )


# ===== FM: API ROUTES =====

@app.route('/fm/api/tickets', methods=['GET'])
def fm_api_tickets():
    """List tickets with optional filtering."""
    status   = request.args.get('status')
    priority = request.args.get('priority')
    estate   = request.args.get('estate')
    q        = request.args.get('q', '').strip()

    conn = fm_get_db()
    sql = "SELECT * FROM fm_tickets WHERE 1=1"
    params = []

    if status:
        sql += " AND status = ?"
        params.append(status)
    if priority:
        sql += " AND priority = ?"
        params.append(priority)
    if estate:
        sql += " AND estate = ?"
        params.append(estate)
    if q:
        sql += " AND (ref LIKE ? OR summary LIKE ? OR customer LIKE ? OR unit LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like, like]

    sql += " ORDER BY updated_at DESC LIMIT 100"
    tickets = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify(tickets)


@app.route('/fm/api/tickets', methods=['POST'])
def create_project_ticket(data):
    """
    Create a new Project / Quote request from AI assistant
    """

    conn = get_engine_db()

    ref = "PRJ-" + str(uuid.uuid4())[:8].upper()

    conn.execute(
    """
    INSERT INTO quote_requests
    (
        ref,
        contact_name,
        email,
        company,
        phone,
        project_name,
        project_type,
        project_description,
        features,
        tech_stack,
        integrations,
        timeline,
        budget,
        status,
        source,
        created_at
    )
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
    """,
    (
        ref,
        data.get("name"),
        data.get("email"),
        data.get("company"),
        data.get("phone"),
        data.get("project_name"),
        data.get("project_type"),
        data.get("project_description"),
        data.get("features"),
        data.get("tech_stack"),
        data.get("integrations"),
        data.get("timeline"),
        data.get("budget"),
        "NEW",
        "ai_assistant"
    )
    )

    conn.commit()
    conn.close()

    return ref
@app.route('/fm/api/tickets/<ref>', methods=['GET'])
def fm_api_get_ticket(ref):
    conn = fm_get_db()
    ticket = conn.execute("SELECT * FROM fm_tickets WHERE ref = ?", (ref,)).fetchone()
    if not ticket:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    result = dict(ticket)
    result['messages'] = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_conversations WHERE ticket_ref = ? ORDER BY created_at ASC", (ref,)
    ).fetchall()]
    result['evidence'] = [dict(r) for r in conn.execute(
        "SELECT id, original_filename, caption, uploaded_at FROM fm_evidence WHERE ticket_ref = ?", (ref,)
    ).fetchall()]
    conn.close()
    return jsonify(result)


@app.route('/fm/api/tickets/<ref>', methods=['PATCH'])
def fm_api_update_ticket(ref):
    """Update editable fields on a ticket."""
    data = request.get_json()
    conn = fm_get_db()

    ticket = conn.execute("SELECT id FROM fm_tickets WHERE ref = ?", (ref,)).fetchone()
    if not ticket:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    allowed = ['status', 'priority', 'category', 'assignee', 'summary',
               'materials', 'estate', 'unit', 'customer', 'location_note']
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        conn.close()
        return jsonify({'error': 'No valid fields to update'}), 400

    set_clause = ', '.join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [ref]
    conn.execute(f"UPDATE fm_tickets SET {set_clause} WHERE ref = ?", values)

    # Log the update event
    event_id = fm_dedup_key(f"{ref}-updated-{datetime.utcnow().isoformat()}")
    conn.execute(
        """INSERT INTO fm_inbound_events
           (event_id, source, event_type, ticket_ref, payload_json, status, processed_at)
           VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (event_id, 'manual', 'ticket.updated', ref, json.dumps(updates), 'processed')
    )

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'ref': ref})


@app.route('/fm/api/tickets/<ref>/messages', methods=['GET'])
def fm_api_get_messages(ref):
    conn = fm_get_db()
    msgs = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_conversations WHERE ticket_ref = ? ORDER BY created_at ASC", (ref,)
    ).fetchall()]
    conn.close()
    return jsonify(msgs)


@app.route('/fm/api/tickets/<ref>/messages', methods=['POST'])
def fm_api_add_message(ref):
    """Add a message to the conversation thread."""
    data    = request.get_json()
    body    = data.get('body', '').strip()
    sender  = data.get('sender', 'staff')
    _src    = data.get('source', 'manual')
    _valid_sources = {'webchat','whatsapp','whatsapp_json','ai','gateway','system','manual'}
    source  = _src if _src in _valid_sources else 'manual'
    internal = data.get('is_internal', False)

    if not body:
        return jsonify({'error': 'body is required'}), 400

    conn = fm_get_db()
    ticket = conn.execute("SELECT id FROM fm_tickets WHERE ref = ?", (ref,)).fetchone()
    if not ticket:
        conn.close()
        return jsonify({'error': 'Ticket not found'}), 404

    conn.execute(
        "INSERT INTO fm_conversations (ticket_ref, sender, body, source, is_internal) VALUES (?,?,?,?,?)",
        (ref, sender, body, source, 1 if internal else 0)
    )

    # Log message event
    event_id = fm_dedup_key(f"{ref}-msg-{body[:20]}-{datetime.utcnow().isoformat()}")
    conn.execute(
        """INSERT INTO fm_inbound_events
           (event_id, source, event_type, ticket_ref, payload_json, status, processed_at)
           VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (event_id, source, 'message.outbound', ref, json.dumps({'body': body, 'sender': sender}), 'processed')
    )

    conn.commit()
    conn.close()
    return jsonify({'success': True}), 201


@app.route('/push/events', methods=['POST'])
def fm_push_events():
    """
    Inbound event gateway for webchat, WhatsApp, AI, and system sources.
    Deduplicates by eventId (SHA-256 of payload if not provided).
    Responds within target of <300ms — processing is synchronous but lightweight.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    raw = json.dumps(data)
    event_id = data.get('eventId') or fm_dedup_key(raw)
    source   = data.get('source', 'webchat')
    event_type = data.get('type', 'message.inbound')
    ticket_ref_hint = data.get('ref')  # optional existing ref

    conn = fm_get_db()

    # Deduplication check
    existing = conn.execute(
        "SELECT id, status FROM fm_inbound_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'eventId': event_id, 'status': 'duplicate', 'ref': ticket_ref_hint}), 200

    # Store raw event immediately
    conn.execute(
        """INSERT INTO fm_inbound_events
           (event_id, source, event_type, ticket_ref, payload_json, status)
           VALUES (?,?,?,?,?,?)""",
        (event_id, source, event_type, ticket_ref_hint, raw, 'queued')
    )
    conn.commit()

    # Process event
    result_ref = ticket_ref_hint
    try:
        if event_type in ('message.inbound', 'whatsapp.message'):
            result_ref = _fm_handle_inbound_message(conn, data, event_id, source)
        elif event_type == 'ticket.created':
            result_ref = _fm_handle_ticket_create(conn, data, source)
        elif event_type == 'whatsapp_json.import':
            result_ref = _fm_handle_whatsapp_json(conn, data)

        # Mark processed
        conn.execute(
            "UPDATE fm_inbound_events SET status='processed', ticket_ref=?, processed_at=CURRENT_TIMESTAMP WHERE event_id=?",
            (result_ref, event_id)
        )
        conn.commit()

    except Exception as e:
        conn.execute(
            "UPDATE fm_inbound_events SET status='error' WHERE event_id=?", (event_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'error': str(e), 'eventId': event_id}), 500

    conn.close()
    return jsonify({'eventId': event_id, 'status': 'processed', 'ref': result_ref}), 200


def _fm_handle_inbound_message(conn, data, event_id, source):
    """Handle an inbound message — find or create ticket, append to thread."""
    ref       = data.get('ref')
    message   = data.get('message', data.get('body', '')).strip()
    customer  = data.get('customer', data.get('from', '')).strip()
    estate    = data.get('estate', '').strip()
    unit      = data.get('unit', '').strip()

    if ref:
        # Append to existing ticket
        existing = conn.execute("SELECT id FROM fm_tickets WHERE ref=?", (ref,)).fetchone()
        if existing:
            conn.execute(
                "INSERT INTO fm_conversations (ticket_ref, sender, body, source) VALUES (?,?,?,?)",
                (ref, 'customer', message, source)
            )
            conn.commit()
            return ref

    # No ref — create new ticket from message
    ref = fm_generate_ref()
    priority = fm_infer_priority(message)
    category = fm_infer_category(message)
    conn.execute(
        """INSERT INTO fm_tickets
           (ref, estate, unit, customer, source, priority, category, status, summary)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ref, estate, unit, customer, source, priority, category, 'NEW', message[:200])
    )
    conn.execute(
        "INSERT INTO fm_conversations (ticket_ref, sender, body, source) VALUES (?,?,?,?)",
        (ref, 'customer', message, source)
    )
    conn.commit()
    return ref


def _fm_handle_ticket_create(conn, data, source):
    """Handle explicit ticket.created event."""
    ref = fm_generate_ref()
    summary  = data.get('summary', '').strip() or 'No summary provided'
    priority = data.get('priority') or fm_infer_priority(summary)
    category = data.get('category') or fm_infer_category(summary)
    conn.execute(
        """INSERT INTO fm_tickets
           (ref, estate, unit, customer, source, priority, category, status, summary, materials, assignee)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ref,
         data.get('estate',''), data.get('unit',''), data.get('customer',''),
         source, priority, category, 'NEW', summary,
         data.get('materials',''), data.get('assignee',''))
    )
    first_msg = data.get('first_message','').strip()
    if first_msg:
        conn.execute(
            "INSERT INTO fm_conversations (ticket_ref, sender, body, source) VALUES (?,?,?,?)",
            (ref, 'customer', first_msg, source)
        )
    conn.commit()
    return ref


def _fm_handle_whatsapp_json(conn, data):
    """
    Handle a WhatsApp JSON import payload (LLM-generated).
    Expected shape (flexible — mapper tries common field names):
    {
      "source": "whatsapp_json",
      "customer": "Jane Azubuike",
      "phone": "+234...",
      "estate": "Ikoyi Phase 2",
      "unit": "Flat 12",
      "summary": "AC making loud noise",
      "priority": "normal",          # optional — inferred if absent
      "category": "hvac",            # optional — inferred if absent
      "messages": [
        {"from": "customer", "text": "...", "ts": "2026-02-26 09:42"},
        ...
      ]
    }
    """
    customer = (data.get('customer') or data.get('name') or data.get('from') or '').strip()
    estate   = (data.get('estate') or data.get('property') or data.get('building') or '').strip()
    unit     = (data.get('unit') or data.get('flat') or data.get('apartment') or '').strip()
    summary  = (data.get('summary') or data.get('issue') or data.get('description') or '').strip()
    priority = data.get('priority') or fm_infer_priority(summary)
    category = data.get('category') or fm_infer_category(summary)
    materials = (data.get('materials') or data.get('notes') or '').strip()

    if not summary and data.get('messages'):
        # Pull summary from first customer message
        for m in data['messages']:
            if m.get('from', 'customer') == 'customer':
                summary = m.get('text', '')[:200]
                break

    ref = fm_generate_ref()
    conn.execute(
        """INSERT INTO fm_tickets
           (ref, estate, unit, customer, source, priority, category, status, summary, materials)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ref, estate, unit, customer, 'whatsapp_json', priority, category, 'NEW', summary, materials)
    )

    # Import message thread
    messages = data.get('messages', [])
    for m in messages:
        sender_raw = str(m.get('from', 'customer')).lower()
        sender = 'customer' if sender_raw in ('customer', 'user', 'client') else \
                 'ai' if sender_raw in ('ai', 'bot', 'llm', 'assistant') else 'staff'
        body = (m.get('text') or m.get('body') or m.get('message') or '').strip()
        ts   = m.get('ts') or m.get('timestamp') or m.get('time', '')
        if body:
            conn.execute(
                """INSERT INTO fm_conversations (ticket_ref, sender, body, source, created_at)
                   VALUES (?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))""",
                (ref, sender, body, 'whatsapp', ts if ts else None)
            )

    conn.commit()
    return ref


@app.route('/fm/api/events', methods=['GET'])
def fm_api_events():
    """Return recent inbound events for audit log."""
    limit = min(int(request.args.get('limit', 50)), 200)
    conn = fm_get_db()
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_inbound_events ORDER BY received_at DESC LIMIT ?", (limit,)
    ).fetchall()]
    conn.close()
    return jsonify(events)


@app.route('/fm/api/import/whatsapp', methods=['POST'])
def fm_api_import_whatsapp():
    """
    Dedicated WhatsApp JSON import endpoint.
    Validates the payload, creates ticket + conversation, returns preview.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    # Wrap as a push event
    data['source'] = 'whatsapp_json'
    data['type'] = 'whatsapp_json.import'
    raw = json.dumps(data)
    event_id = data.get('eventId') or fm_dedup_key(raw)

    conn = fm_get_db()

    # Dedup check
    existing = conn.execute(
        "SELECT ticket_ref FROM fm_inbound_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'status': 'duplicate', 'ref': existing[0]}), 200

    # Log event
    conn.execute(
        """INSERT INTO fm_inbound_events
           (event_id, source, event_type, payload_json, status)
           VALUES (?,?,?,?,?)""",
        (event_id, 'whatsapp_json', 'whatsapp_json.import', raw, 'queued')
    )
    conn.commit()

    ref = _fm_handle_whatsapp_json(conn, data)

    conn.execute(
        "UPDATE fm_inbound_events SET status='processed', ticket_ref=?, processed_at=CURRENT_TIMESTAMP WHERE event_id=?",
        (ref, event_id)
    )
    conn.commit()

    # Return ticket for preview
    ticket = dict(conn.execute("SELECT * FROM fm_tickets WHERE ref=?", (ref,)).fetchone())
    msgs = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_conversations WHERE ticket_ref=? ORDER BY created_at ASC", (ref,)
    ).fetchall()]
    conn.close()

    return jsonify({
        'status': 'created',
        'ref': ref,
        'ticket': ticket,
        'messages': msgs,
        'event_id': event_id
    }), 201


@app.route('/fm/api/classify', methods=['POST'])
def fm_api_classify():
    """
    Classify a free-text message into FM fields.
    Deterministic keyword rules by default.
    Swap the body for an Anthropic API call when ready:

    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system=(
            "You are an FM triage engine. "
            "From the user message extract JSON with keys: "
            "summary (max 80 chars), priority (urgent/normal/low), "
            "category (general/electrical/plumbing/hvac/security/carpentry/"
            "cleaning/painting/pest_control), draft_reply (string). "
            "Return JSON only."
        ),
        messages=[{"role": "user", "content": text}]
    )
    import json
    result = json.loads(resp.content[0].text)
    """
    data = request.get_json(silent=True)
    text = (data or {}).get('text', '').strip()
    if not text:
        return jsonify({'error': 'text is required'}), 400

    priority = fm_infer_priority(text)
    category = fm_infer_category(text)
    summary  = text[:80] + ('…' if len(text) > 80 else '')

    return jsonify({
        'summary': summary,
        'priority': priority,
        'category': category,
        'draft_reply': (
            f"Thank you for reporting this. We have logged a {priority} priority "
            f"{category} issue and will be in touch shortly with next steps."
        )
    })


# ===== FM EVIDENCE + ARTISAN ROUTES =====

import os
import uuid
from werkzeug.utils import secure_filename

# Evidence upload directory — created at startup if missing
FM_EVIDENCE_DIR = os.path.join('evidence', 'fm')
os.makedirs(FM_EVIDENCE_DIR, exist_ok=True)

FM_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'heic'}
FM_MAX_UPLOAD_MB = 10


def _fm_allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in FM_ALLOWED_EXTENSIONS


# ===== FM: EVIDENCE ROUTES =====

@app.route('/fm/api/evidence', methods=['POST'])
def fm_api_upload_evidence():
    """Upload photo / document for a ticket."""
    ref     = request.form.get('ref', '').strip()
    caption = request.form.get('caption', '').strip()

    if not ref:
        return jsonify({'error': 'ref is required'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file in request'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    if not _fm_allowed_file(file.filename):
        return jsonify({'error': f'File type not allowed. Allowed: {FM_ALLOWED_EXTENSIONS}'}), 400

    # Check ticket exists
    conn = fm_get_db()
    ticket = conn.execute("SELECT id FROM fm_tickets WHERE ref = ?", (ref,)).fetchone()
    if not ticket:
        conn.close()
        return jsonify({'error': 'Ticket not found'}), 404

    # Save file with UUID name
    ext       = secure_filename(file.filename).rsplit('.', 1)[-1].lower()
    stored_fn = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(FM_EVIDENCE_DIR, stored_fn)

    # Check file size via read
    file_data = file.read()
    if len(file_data) > FM_MAX_UPLOAD_MB * 1024 * 1024:
        conn.close()
        return jsonify({'error': f'File exceeds {FM_MAX_UPLOAD_MB}MB limit'}), 413

    with open(file_path, 'wb') as f:
        f.write(file_data)

    file_size = len(file_data)
    mime_type = file.content_type or ''

    # Store record
    conn.execute(
        """INSERT INTO fm_evidence
           (ticket_ref, filename, original_filename, file_path, file_size, mime_type, caption)
           VALUES (?,?,?,?,?,?,?)""",
        (ref, stored_fn, secure_filename(file.filename), file_path, file_size, mime_type, caption)
    )

    # Log event
    event_id = fm_dedup_key(f"{ref}-evidence-{stored_fn}")
    conn.execute(
        """INSERT INTO fm_inbound_events
           (event_id, source, event_type, ticket_ref, payload_json, status, processed_at)
           VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (event_id, 'manual', 'evidence.uploaded', ref,
         json.dumps({'filename': file.filename, 'size': file_size}), 'processed')
    )

    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'ref': ref,
        'filename': stored_fn,
        'original_filename': file.filename,
        'file_size': file_size,
        'url': f'/fm/evidence/{stored_fn}'
    }), 201


@app.route('/fm/evidence/<filename>')
def fm_serve_evidence(filename):
    """Serve evidence file."""
    safe = secure_filename(filename)
    path = os.path.join(FM_EVIDENCE_DIR, safe)
    if not os.path.exists(path):
        return 'Not found', 404
    directory = os.path.abspath(FM_EVIDENCE_DIR)
    return send_file(os.path.join(directory, safe))


@app.route('/fm/api/evidence/<ref>', methods=['GET'])
def fm_api_get_evidence(ref):
    """List evidence for a ticket."""
    conn = fm_get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_evidence WHERE ticket_ref = ? ORDER BY uploaded_at ASC", (ref,)
    ).fetchall()]
    conn.close()
    # Inject serve URL
    for row in rows:
        row['url'] = f"/fm/evidence/{row['filename']}"
    return jsonify(rows)


# ===== FM: EVENTS PAGE =====

@app.route('/fm/events')
def fm_events_page():
    """FM inbound events audit log page."""
    conn = fm_get_db()
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_inbound_events ORDER BY received_at DESC LIMIT 200"
    ).fetchall()]
    conn.close()
    return render_template('fm/events.html', events=events, active_nav='fm_dashboard')


# ===== FM: ARTISAN MOBILE STUB =====

@app.route('/fm/artisan/<ref>')
def fm_artisan_view(ref):
    """
    Artisan / technician mobile job card.
    Lightweight single-ticket view optimised for mobile — no sidebar, no complex layout.
    """
    conn = fm_get_db()

    ticket = conn.execute("SELECT * FROM fm_tickets WHERE ref = ?", (ref,)).fetchone()
    if not ticket:
        conn.close()
        return render_template('fm/artisan_404.html', ref=ref), 404
    ticket = dict(ticket)

    messages = [dict(r) for r in conn.execute(
        """SELECT * FROM fm_conversations
           WHERE ticket_ref = ? AND is_internal = 0
           ORDER BY created_at ASC""",
        (ref,)
    ).fetchall()]

    evidence = [dict(r) for r in conn.execute(
        "SELECT * FROM fm_evidence WHERE ticket_ref = ? ORDER BY uploaded_at ASC",
        (ref,)
    ).fetchall()]
    for ev in evidence:
        ev['url'] = f"/fm/evidence/{ev['filename']}"

    conn.close()

    return render_template(
        'fm/artisan.html',
        ticket=ticket,
        messages=messages,
        evidence=evidence
    )



# ===== WHATSAPP ↔ DEEPSEEK ↔ FM BRIDGE =====


import os
import re
import requests
import threading
import time

# =========================================================================
# CONFIGURATION — override via environment variables
# =========================================================================

DEEPSEEK_API_KEY   = os.environ.get('DEEPSEEK_API_KEY', '')
GEMINI_API_KEY     = os.environ.get('GEMINI_API_KEY', '')   # for WhatsApp image analysis
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN  = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_WA_FROM     = os.environ.get('TWILIO_WA_FROM', '')   # whatsapp:+14155238886

# ── Cloudflare Turnstile (bot protection) ───────────────────────────────────
TURNSTILE_SECRET  = os.environ.get('TURNSTILE_SECRET_KEY', '')
TURNSTILE_SITEKEY = os.environ.get('TURNSTILE_SITE_KEY', '')

# Flush triggers — tune for your demo
WA_FLUSH_ON_COUNT   = 4      # flush after this many messages
WA_FLUSH_TIMEOUT_S  = 300    # flush if no message for 5 minutes
WA_FLUSH_KEYWORDS   = {      # flush immediately if any of these in message
    'done', 'finished', 'that\'s all', 'thats all', 'ready', 'send',
}
WA_CONFIRMATION_MSG = (
    "Thanks, we've logged your issue as ticket *{ref}*. "
    "Our team will be in touch shortly. "
    "Reply with your ticket ref to add more details."
)
WA_ACK_MSG = "Got it. Tell us more about your issue — estate, flat number, and what's happened."

# =========================================================================
# DATABASE HELPER (reuse fm_get_db)
# =========================================================================

def wa_get_db():
    """Return engine.db connection (same db as FM)."""
    return fm_get_db()


# =========================================================================
# TWILIO REPLY
# =========================================================================

def wa_send_reply(to_number: str, body: str) -> bool:
    """
    Send a WhatsApp reply via Twilio REST API.
    Returns True on success, False on failure.
    to_number: E.164 with whatsapp: prefix, e.g. 'whatsapp:+2348012345678'
    """
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WA_FROM]):
        print(f"[WA] Twilio not configured — reply not sent to {to_number}: {body[:60]}")
        return False

    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        resp = requests.post(
            url,
            data={'From': TWILIO_WA_FROM, 'To': to_number, 'Body': body},
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10
        )
        if resp.status_code == 201:
            sid = resp.json().get('sid', '')
            print(f"[WA] Sent reply to {to_number} ({sid})")
            return True
        else:
            print(f"[WA] Reply failed {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[WA] Reply error: {e}")
        return False


# =========================================================================
# GEMINI VISION — image description for WhatsApp photo messages
# =========================================================================

def wa_describe_image_gemini(media_url: str, media_type: str = 'image/jpeg') -> str:
    """
    Fetch a Twilio media URL and send it to Gemini 2.0 Flash for FM-focused
    image description. Returns a plain-text description, or empty string on failure.

    Gemini REST endpoint (no SDK required):
      POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=KEY
    """
    if not GEMINI_API_KEY:
        print('[WA] GEMINI_API_KEY not set — skipping image analysis')
        return ''

    if not media_url:
        return ''

    # Step 1: Fetch the image bytes from Twilio (authenticated)
    try:
        img_resp = requests.get(
            media_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=15
        )
        if img_resp.status_code != 200:
            print(f'[WA] Failed to fetch media from Twilio: {img_resp.status_code}')
            return ''
        image_bytes = img_resp.content
    except Exception as e:
        print(f'[WA] Media fetch error: {e}')
        return ''

    # Step 2: Base64-encode for Gemini inline data
    import base64
    b64_image = base64.b64encode(image_bytes).decode('utf-8')

    # Step 3: Call Gemini Vision REST API
    gemini_url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}'
    )
    payload = {
        'contents': [{
            'parts': [
                {
                    'inline_data': {
                        'mime_type': media_type or 'image/jpeg',
                        'data': b64_image
                    }
                },
                {
                    'text': (
                        'You are a facilities management assistant. Describe what you see in this image '
                        'from a property maintenance perspective. Focus on: visible damage or defects, '
                        'affected systems (plumbing, electrical, HVAC, structure, etc.), '
                        'severity and urgency indicators, and location context if visible. '
                        'Keep the description concise — 2 to 4 sentences maximum.'
                    )
                }
            ]
        }],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 256
        }
    }

    try:
        resp = requests.post(gemini_url, json=payload, timeout=20)
        if resp.status_code != 200:
            print(f'[WA] Gemini error {resp.status_code}: {resp.text[:200]}')
            return ''
        data = resp.json()
        description = (
            data.get('candidates', [{}])[0]
                .get('content', {})
                .get('parts', [{}])[0]
                .get('text', '')
                .strip()
        )
        print(f'[WA] Gemini image description: {description[:100]}')
        return description
    except Exception as e:
        print(f'[WA] Gemini call error: {e}')
        return ''


# =========================================================================
# DEEPSEEK CLASSIFICATION
# =========================================================================

DEEPSEEK_SYSTEM_PROMPT = """You are a facilities management triage assistant.
Given a WhatsApp conversation transcript between a tenant and a property management company,
extract the structured data and return ONLY valid JSON with no markdown, no explanation.

JSON shape required:
{
  "customer": "tenant full name or phone number if name unknown",
  "estate": "estate/development name if mentioned, else empty string",
  "unit": "flat/unit/room number if mentioned, else empty string",
  "summary": "one sentence describing the issue (max 120 chars)",
  "priority": "urgent | normal | low",
  "category": "general | electrical | plumbing | hvac | security | carpentry | cleaning | painting | pest_control",
  "materials": "any materials or parts mentioned, else empty string",
  "messages": [
    {"from": "customer | staff | ai", "text": "message text", "ts": "timestamp or empty string"}
  ]
}

Priority rules:
- urgent: water leak, flood, burst pipe, fire, gas, sparking, no power, break-in, security breach
- low: cosmetic, minor inconvenience, non-urgent request
- normal: everything else

Return ONLY the JSON object. No markdown code fences. No preamble. No explanation."""


def wa_call_deepseek(transcript: str, session_meta: dict) -> dict | None:
    """
    Send transcript to DeepSeek and return parsed FM JSON.
    Returns dict on success, None on failure.
    """
    if not DEEPSEEK_API_KEY:
        print("[WA] DeepSeek API key not set — using keyword fallback")
        return _wa_keyword_fallback(transcript, session_meta)

    user_content = f"""Tenant WhatsApp number: {session_meta.get('wa_from', 'unknown')}
Display name: {session_meta.get('display_name', 'unknown')}

Conversation transcript:
{transcript}

Extract and return the FM ticket JSON."""

    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={
                'Authorization': f"Bearer {DEEPSEEK_API_KEY}",
                'Content-Type': 'application/json'
            },
            json={
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': DEEPSEEK_SYSTEM_PROMPT},
                    {'role': 'user',   'content': user_content}
                ],
                'temperature': 0.1,
                'max_tokens': 800,
            },
            timeout=30
        )

        if resp.status_code != 200:
            print(f"[WA] DeepSeek error {resp.status_code}: {resp.text[:300]}")
            return _wa_keyword_fallback(transcript, session_meta)

        raw_content = resp.json()['choices'][0]['message']['content'].strip()

        # Strip markdown fences if model added them despite instructions
        raw_content = re.sub(r'^```(?:json)?\s*', '', raw_content, flags=re.MULTILINE)
        raw_content = re.sub(r'\s*```$', '', raw_content, flags=re.MULTILINE)

        parsed = json.loads(raw_content)
        print(f"[WA] DeepSeek classified: priority={parsed.get('priority')} category={parsed.get('category')}")
        return parsed

    except json.JSONDecodeError as e:
        print(f"[WA] DeepSeek JSON parse error: {e} — raw: {raw_content[:200]}")
        return _wa_keyword_fallback(transcript, session_meta)
    except Exception as e:
        print(f"[WA] DeepSeek call error: {e}")
        return _wa_keyword_fallback(transcript, session_meta)


def _wa_keyword_fallback(transcript: str, session_meta: dict) -> dict:
    """
    Fallback when DeepSeek unavailable: use keyword inference (same as FM module).
    Returns a best-effort FM JSON dict.
    """
    lines = [l.strip() for l in transcript.split('\n') if l.strip()]
    messages = []
    for line in lines:
        if line.startswith('[customer]'):
            messages.append({'from': 'customer', 'text': line[10:].strip(), 'ts': ''})
        elif line.startswith('[staff]'):
            messages.append({'from': 'staff', 'text': line[7:].strip(), 'ts': ''})

    customer_text = ' '.join(m['text'] for m in messages if m['from'] == 'customer')
    summary = customer_text[:120] if customer_text else 'Issue reported via WhatsApp'

    return {
        'customer': session_meta.get('display_name') or session_meta.get('wa_from', 'WhatsApp User'),
        'estate': '',
        'unit': '',
        'summary': summary,
        'priority': fm_infer_priority(summary),
        'category': fm_infer_category(summary),
        'materials': '',
        'messages': messages
    }


# =========================================================================
# FLUSH SESSION → DeepSeek → FM ticket
# =========================================================================

def wa_flush_session(session_id: int, trigger: str = 'manual'):
    """
    Flush a buffered session: build transcript, call DeepSeek, create FM ticket,
    send WhatsApp confirmation. Runs in a background thread.
    """
    conn = wa_get_db()

    session = conn.execute(
        "SELECT * FROM wa_sessions WHERE id = ?", (session_id,)
    ).fetchone()

    if not session or session['status'] != 'active':
        conn.close()
        return

    session = dict(session)

    # Mark as processing to prevent double-flush
    conn.execute(
        "UPDATE wa_sessions SET status='processing', flushed_at=CURRENT_TIMESTAMP, flush_trigger=? WHERE id=?",
        (trigger, session_id)
    )
    conn.commit()

    # Build transcript
    msgs = [dict(r) for r in conn.execute(
        "SELECT * FROM wa_messages WHERE session_id=? AND direction='inbound' ORDER BY received_at ASC",
        (session_id,)
    ).fetchall()]

    if not msgs:
        conn.execute("UPDATE wa_sessions SET status='done' WHERE id=?", (session_id,))
        conn.commit()
        conn.close()
        return

    transcript_lines = []
    message_objects  = []
    for m in msgs:
        ts = m['received_at'][:16] if m['received_at'] else ''
        transcript_lines.append(f"[customer] {m['body']}")
        message_objects.append({'from': 'customer', 'text': m['body'], 'ts': ts})

        # If this message had an image, describe it via Gemini and inject into transcript
        if m.get('media_url') and m.get('media_type', '').startswith('image/'):
            image_desc = wa_describe_image_gemini(m['media_url'], m['media_type'])
            if image_desc:
                transcript_lines.append(f"[image sent by customer] {image_desc}")
                message_objects.append({
                    'from': 'customer',
                    'text': f'[Photo] {image_desc}',
                    'ts': ts
                })

    transcript = '\n'.join(transcript_lines)

    # Call DeepSeek
    fm_json = wa_call_deepseek(transcript, session)
    deepseek_raw = json.dumps(fm_json)

    if not fm_json:
        conn.execute(
            "UPDATE wa_sessions SET status='error', error_detail='DeepSeek returned None' WHERE id=?",
            (session_id,)
        )
        conn.commit()
        conn.close()
        return

    # Ensure messages from DeepSeek are merged with raw messages
    if not fm_json.get('messages'):
        fm_json['messages'] = message_objects

    # Ensure customer name falls back to WhatsApp number
    if not fm_json.get('customer'):
        fm_json['customer'] = session.get('display_name') or session['wa_from']

    # Add source marker
    fm_json['_source'] = 'whatsapp'

    # POST to FM import endpoint
    try:
        with app.test_client() as client:
            r = client.post(
                '/fm/api/import/whatsapp',
                json=fm_json,
                content_type='application/json'
            )
            result = r.get_json()
            ticket_ref = result.get('ref')
    except Exception as e:
        print(f"[WA] FM import error: {e}")
        conn.execute(
            "UPDATE wa_sessions SET status='error', error_detail=?, deepseek_response=? WHERE id=?",
            (str(e), deepseek_raw, session_id)
        )
        conn.commit()
        conn.close()
        return

    # Update session
    conn.execute(
        "UPDATE wa_sessions SET status='done', ticket_ref=?, deepseek_response=? WHERE id=?",
        (ticket_ref, deepseek_raw, session_id)
    )
    conn.commit()

    # Log to fm_inbound_events
    event_id = fm_dedup_key(f"wa-session-{session_id}-{ticket_ref}")
    try:
        conn.execute(
            """INSERT INTO fm_inbound_events
               (event_id, source, event_type, ticket_ref, payload_json, status, processed_at)
               VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (event_id, 'whatsapp', 'whatsapp.session.flushed', ticket_ref,
             json.dumps({'session_id': session_id, 'trigger': trigger,
                         'message_count': len(msgs)}), 'processed')
        )
        conn.commit()
    except Exception:
        pass  # Event logging is non-critical

    conn.close()

    # Send Twilio confirmation reply
    if ticket_ref:
        wa_send_reply(
            session['wa_from'],
            WA_CONFIRMATION_MSG.format(ref=ticket_ref)
        )
        print(f"[WA] Session {session_id} → ticket {ticket_ref} (trigger={trigger})")
    else:
        print(f"[WA] Session {session_id} flushed but no ticket ref returned")


# =========================================================================
# TIMEOUT WATCHER — background thread
# =========================================================================

def _wa_timeout_watcher():
    """
    Background thread: every 60 seconds, find sessions idle > WA_FLUSH_TIMEOUT_S
    and flush them.
    """
    while True:
        try:
            time.sleep(60)
            conn = wa_get_db()
            idle_sessions = conn.execute(
                """SELECT id FROM wa_sessions
                   WHERE status = 'active'
                   AND (strftime('%s','now') - strftime('%s', last_message_at)) > ?""",
                (WA_FLUSH_TIMEOUT_S,)
            ).fetchall()
            conn.close()

            for row in idle_sessions:
                print(f"[WA] Timeout flush: session {row['id']}")
                threading.Thread(
                    target=wa_flush_session,
                    args=(row['id'], 'timeout'),
                    daemon=True
                ).start()

        except Exception as e:
            print(f"[WA] Timeout watcher error: {e}")


# Start timeout watcher on module load
_wa_watcher_thread = threading.Thread(target=_wa_timeout_watcher, daemon=True)
_wa_watcher_thread.start()


# =========================================================================
# TWILIO INBOUND WEBHOOK
# =========================================================================

# =========================================================================
# SHARED INBOUND PROCESSOR — used by /wa/inbound AND /wa/simulate
# =========================================================================

def process_inbound_wa(wa_from, wa_to, body, message_sid,
                        profile_name='', media_url='', media_type='',
                        simulated=False):
    """
    Single entry point for all inbound WhatsApp messages, real or simulated.

    Normalises input, deduplicates, creates/updates a session, stores the
    message, triggers flush if threshold reached, and returns a structured
    result dict.

    Returns:
        {
          'ok': bool,
          'session_id': int,
          'message_count': int,
          'flushing': bool,
          'flush_trigger': str | None,
          'is_new_session': bool,
          'error': str   # only if ok=False
        }
    """
    label = '[WA/sim]' if simulated else '[WA/inbound]'
    body_display = body or ('[media]' if media_url else '[empty]')
    print(f"{label} from={wa_from} name={profile_name!r} body={body_display[:80]!r}")

    try:
        conn = wa_get_db()

        # ── Dedup by MessageSid (skip for empty or already-seen sids) ──
        if message_sid:
            existing = conn.execute(
                "SELECT id FROM wa_messages WHERE twilio_sid = ?", (message_sid,)
            ).fetchone()
            if existing:
                conn.close()
                print(f"{label} duplicate sid={message_sid} — skipped")
                return {
                    'ok': True, 'session_id': None, 'message_count': 0,
                    'flushing': False, 'flush_trigger': None,
                    'is_new_session': False, 'duplicate': True
                }

        # ── Find or create active session ──
        session = conn.execute(
            "SELECT * FROM wa_sessions WHERE wa_from=? AND status='active' "
            "ORDER BY id DESC LIMIT 1",
            (wa_from,)
        ).fetchone()

        is_new_session = session is None

        if session:
            session_id = session['id']
            if profile_name:
                conn.execute(
                    "UPDATE wa_sessions SET last_message_at=CURRENT_TIMESTAMP, "
                    "message_count=message_count+1, display_name=? WHERE id=?",
                    (profile_name, session_id)
                )
            else:
                conn.execute(
                    "UPDATE wa_sessions SET last_message_at=CURRENT_TIMESTAMP, "
                    "message_count=message_count+1 WHERE id=?",
                    (session_id,)
                )
        else:
            wa_to_val = wa_to or TWILIO_WA_FROM or 'whatsapp:+14155238886'
            cursor = conn.execute(
                "INSERT INTO wa_sessions (wa_from, wa_to, display_name, message_count) "
                "VALUES (?,?,?,1)",
                (wa_from, wa_to_val, profile_name or '')
            )
            session_id = cursor.lastrowid
            print(f"{label} new session id={session_id}")

            # ACK only for real messages (not simulator)
            if not simulated:
                threading.Thread(
                    target=wa_send_reply,
                    args=(wa_from, WA_ACK_MSG),
                    daemon=True
                ).start()

        # ── Store message ──
        # Generate a stable SID for simulated messages if none provided
        final_sid = message_sid or f"SIM{fm_dedup_key(wa_from + body + str(time.time()))[:20]}"
        conn.execute(
            "INSERT OR IGNORE INTO wa_messages "
            "(session_id, direction, body, media_url, media_type, twilio_sid) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, 'inbound', body or '', media_url or '', media_type or '', final_sid)
        )
        conn.commit()

        count = conn.execute(
            "SELECT message_count FROM wa_sessions WHERE id=?", (session_id,)
        ).fetchone()['message_count']

        conn.close()

        # ── Flush triggers ──
        should_flush  = False
        flush_trigger = None
        body_lower    = (body or '').lower()

        if any(kw in body_lower for kw in WA_FLUSH_KEYWORDS):
            should_flush  = True
            flush_trigger = 'keyword'
        elif count >= WA_FLUSH_ON_COUNT:
            should_flush  = True
            flush_trigger = 'count'

        if should_flush:
            print(f"{label} triggering flush session={session_id} trigger={flush_trigger}")
            threading.Thread(
                target=wa_flush_session,
                args=(session_id, flush_trigger),
                daemon=True
            ).start()

        return {
            'ok':             True,
            'session_id':     session_id,
            'message_count':  count,
            'flushing':       should_flush,
            'flush_trigger':  flush_trigger,
            'is_new_session': is_new_session,
            'duplicate':      False
        }

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {'ok': False, 'error': str(exc)}


# =========================================================================
# TWILIO SIGNATURE VALIDATION — Phase 3
# =========================================================================

def _validate_twilio_signature(request_obj) -> bool:
    """
    Validate incoming Twilio webhook signature to prevent spoofing.
    Returns True if signature is valid (or if auth token is not configured).
    Skips validation in development (FLASK_ENV=development).
    """
    if not TWILIO_AUTH_TOKEN:
        return True  # Auth token not set — skip validation (dev mode)

    flask_env = os.environ.get('FLASK_ENV', 'production')
    if flask_env == 'development':
        return True  # Skip in development

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        url  = request_obj.url
        params = request_obj.form.to_dict()
        signature = request_obj.headers.get('X-Twilio-Signature', '')
        return validator.validate(url, params, signature)
    except ImportError:
        print("[WA] twilio.request_validator not available — skipping signature check")
        return True
    except Exception as e:
        print(f"[WA] Signature validation error: {e}")
        return False


@app.route('/wa/inbound', methods=['POST'])
@app.route('/studio/comms/inbound', methods=['POST'])
def wa_inbound():
    """
    Twilio WhatsApp inbound webhook.
    Accepts application/x-www-form-urlencoded from Twilio.
    Validates Twilio signature, then passes to process_inbound_wa().
    Also registered at /studio/comms/inbound for Devex routing.
    """
    # Validate Twilio signature before processing
    if not _validate_twilio_signature(request):
        print("[WA/inbound] Invalid Twilio signature — request rejected")
        return _wa_twiml_response(''), 403

    wa_from      = request.form.get('From', '').strip()
    wa_to        = request.form.get('To', '').strip()
    body         = request.form.get('Body', '').strip()
    message_sid  = request.form.get('MessageSid', '').strip()
    profile_name = request.form.get('ProfileName', '').strip()
    media_url    = request.form.get('MediaUrl0', '').strip()
    media_type   = request.form.get('MediaContentType0', '').strip()

    print(f"[WA/inbound] From={wa_from} Sid={message_sid} Body={body[:60]!r}")

    if not wa_from:
        print("[WA/inbound] Missing From field — ignoring")
        return _wa_twiml_response(''), 200

    process_inbound_wa(
        wa_from      = wa_from,
        wa_to        = wa_to,
        body         = body,
        message_sid  = message_sid,
        profile_name = profile_name,
        media_url    = media_url,
        media_type   = media_type,
        simulated    = False
    )

    return _wa_twiml_response(''), 200


def _wa_twiml_response(message: str) -> str:
    """Return minimal TwiML response. Empty message = no auto-reply."""
    if message:
        return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


# =========================================================================
# MANUAL FLUSH (staff can trigger from dashboard)
# =========================================================================

@app.route('/wa/api/sessions', methods=['GET'])
def wa_api_sessions():
    """List WA sessions — for the monitor dashboard."""
    limit = int(request.args.get('limit', 50))
    conn  = wa_get_db()
    rows  = [dict(r) for r in conn.execute(
        """SELECT s.*, COUNT(m.id) as msg_count
           FROM wa_sessions s
           LEFT JOIN wa_messages m ON m.session_id = s.id AND m.direction = 'inbound'
           GROUP BY s.id
           ORDER BY s.last_message_at DESC
           LIMIT ?""",
        (limit,)
    ).fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/wa/api/sessions/<int:session_id>', methods=['GET'])
def wa_api_session_detail(session_id):
    """Get session + messages."""
    conn = wa_get_db()
    session = conn.execute("SELECT * FROM wa_sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    msgs = [dict(r) for r in conn.execute(
        "SELECT * FROM wa_messages WHERE session_id=? ORDER BY received_at ASC",
        (session_id,)
    ).fetchall()]
    conn.close()
    return jsonify({'session': dict(session), 'messages': msgs})


@app.route('/wa/api/sessions/<int:session_id>/flush', methods=['POST'])
def wa_api_flush_session(session_id):
    """Manually flush a session to DeepSeek → FM ticket."""
    threading.Thread(
        target=wa_flush_session,
        args=(session_id, 'manual'),
        daemon=True
    ).start()
    return jsonify({'status': 'flushing', 'session_id': session_id})


@app.route('/wa/api/sessions/<int:session_id>/reply', methods=['POST'])
def wa_api_send_reply(session_id):
    """Send a manual WhatsApp reply to a session's sender."""
    data = request.get_json()
    body = (data or {}).get('body', '').strip()
    if not body:
        return jsonify({'error': 'body required'}), 400

    conn = wa_get_db()
    session = conn.execute("SELECT wa_from FROM wa_sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404

    wa_from = session['wa_from']
    conn.execute(
        "INSERT INTO wa_messages (session_id, direction, body) VALUES (?,?,?)",
        (session_id, 'outbound', body)
    )
    conn.commit()
    conn.close()

    ok = wa_send_reply(wa_from, body)
    return jsonify({'sent': ok, 'to': wa_from})


@app.route('/wa/api/simulate', methods=['POST'])
@app.route('/wa/simulate', methods=['POST'])
def wa_api_simulate():
    """
    Simulate a WhatsApp inbound message without Twilio.
    Accepts JSON:  {"from": "+2348012345678", "body": "...", "name": "..."}
    Routes through the same process_inbound_wa() as a real Twilio webhook.
    """
    data = request.get_json(silent=True) or {}
    raw_from = data.get('from', '+0000000000').strip()
    wa_from  = 'whatsapp:' + raw_from.lstrip('whatsapp:')
    body     = data.get('body', '').strip()
    name     = data.get('name', '').strip()

    if not body:
        return jsonify({'ok': False, 'error': 'body required'}), 400

    sim_sid = f"SIM{fm_dedup_key(wa_from + body + str(time.time()))[:20]}"

    result = process_inbound_wa(
        wa_from     = wa_from,
        wa_to       = TWILIO_WA_FROM or 'whatsapp:+14155238886',
        body        = body,
        message_sid = sim_sid,
        profile_name= name,
        media_url   = '',
        media_type  = '',
        simulated   = True
    )

    if not result['ok']:
        return jsonify(result), 500

    return jsonify(result)


# =========================================================================
# MONITOR DASHBOARD PAGE
# =========================================================================

@app.route('/wa/monitor')
def wa_monitor():
    """WhatsApp bridge live monitor page."""
    return render_template('wa/monitor.html', active_nav='fm_dashboard')


@app.route('/wa/api/config', methods=['GET'])
@app.route('/studio/comms/api/config', methods=['GET'])
def wa_api_config():
    """Return sanitised config status for the monitor dashboard."""
    return jsonify({
        # All values masked — never expose full credentials to the browser
        'deepseek_key':  ('set:' + DEEPSEEK_API_KEY[-4:])   if DEEPSEEK_API_KEY   else '',
        'gemini_key':    ('set:' + GEMINI_API_KEY[-4:])     if GEMINI_API_KEY     else '',
        'twilio_sid':    ('set:' + TWILIO_ACCOUNT_SID[-4:]) if TWILIO_ACCOUNT_SID else '',
        'twilio_token':  'configured'                        if TWILIO_AUTH_TOKEN  else '',
        'twilio_from':   ('set:' + TWILIO_WA_FROM[-4:])     if TWILIO_WA_FROM     else '',
        'webhook_url':   request.host_url.rstrip('/') + '/wa/inbound',
        'flush_count':   WA_FLUSH_ON_COUNT,
        'flush_timeout': WA_FLUSH_TIMEOUT_S,
        'flush_keywords': list(WA_FLUSH_KEYWORDS),
    })


# =========================================================================
# STUDIO COMMS API ALIASES — Phase 3
# /studio/comms/* mirrors /wa/* for new Devex routing
# All actual logic stays in the /wa/* handlers above
# =========================================================================

@app.route('/studio/comms/api/sessions', methods=['GET'])
def studio_comms_sessions():
    """Alias: studio comms sessions list."""
    return wa_api_sessions()

@app.route('/studio/comms/api/sessions/<int:session_id>', methods=['GET'])
def studio_comms_session_detail(session_id):
    """Alias: studio comms session detail."""
    return wa_api_session_detail(session_id)

@app.route('/studio/comms/api/sessions/<int:session_id>/flush', methods=['POST'])
def studio_comms_flush(session_id):
    """Alias: studio comms manual flush."""
    return wa_api_flush_session(session_id)

@app.route('/studio/comms/api/sessions/<int:session_id>/reply', methods=['POST'])
def studio_comms_reply(session_id):
    """Alias: studio comms send reply."""
    return wa_api_send_reply(session_id)

@app.route('/studio/comms/api/simulate', methods=['POST'])
def studio_comms_simulate():
    """Alias: studio comms simulate inbound message."""
    return wa_api_simulate()


if __name__ == '__main__':
    print('\n' + '='*60)
    print('DEVEX AI STUDIOS PLATFORM - VERSION 1.0.0')
    print('='*60)
    
    init_db()
    
    print('\nServer starting on http://0.0.0.0:5000 ...')
    print('Press Ctrl+C to stop\n')
    
    app.run(debug=True, host='0.0.0.0', port=5000)


# ===== COMMERCIAL ROUTES (STUBS FOR REPURPOSING) =====

