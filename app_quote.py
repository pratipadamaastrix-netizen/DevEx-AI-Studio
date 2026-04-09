#!/usr/bin/env python3
"""
DevEx Studios — Project Requirements Portal
app_quote.py  (drop-in extension for devex_fm app.py)
Version: QP1.0

INTEGRATION:
  In app.py, add at the bottom of the imports block:
      from app_quote import register_quote_routes
  Then after `app = Flask(__name__)` is set up and before `if __name__ == '__main__'`:
      register_quote_routes(app)
  And inside init_db(), add:
      run_quote_migrations(conn_engine)

  In .env, ensure these are set:
      DEEPSEEK_API_KEY=...
      GEMINI_API_KEY=...
"""

import json
import hashlib
import random
import string
import time
import re
import os
import requests
from datetime import datetime
from io import BytesIO

import sqlite3
from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle

# =========================================================================
# CONFIGURATION — inherits from app.py environment
# =========================================================================

ENGINE_DB_PATH  = 'engine.db'          # Same DB as artefacts / fm_tickets
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
GEMINI_API_KEY   = os.environ.get('GEMINI_API_KEY', '')

# =========================================================================
# DATABASE
# =========================================================================

def get_quote_db():
    """Quote portal uses engine.db — same as artefacts and FM."""
    conn = sqlite3.connect(ENGINE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_quote_migrations(conn):
    """
    Idempotent migration runner. Call from init_db() in app.py.
    Reads schema_quote.sql and creates tables if not present.
    """
    print("  Running Quote Portal migrations...")

    needs_creation = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='quote_requests'"
    ).fetchone() is None

    if needs_creation:
        print("  Creating Quote Portal tables from schema_quote.sql...")
        try:
            with open('schema_quote.sql', 'r') as f:
                conn.executescript(f.read())
            conn.commit()
            print("  ✓ Quote Portal tables created")
        except FileNotFoundError:
            print("  ! schema_quote.sql not found — skipping quote migrations")
    else:
        print("  ✓ Quote Portal tables already exist")

    # Devex Phase 1: add access_code column for client portal login
    _qr_col = conn.execute(
        "PRAGMA table_info(quote_requests)"
    ).fetchall()
    existing_cols = [row[1] for row in _qr_col]
    if 'access_code' not in existing_cols:
        conn.execute("ALTER TABLE quote_requests ADD COLUMN access_code TEXT DEFAULT ''")
        conn.commit()
        print("  + Added quote_requests.access_code")

    # Devex Phase 2: dx_failed_jobs table for AI fallback audit trail
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dx_failed_jobs'"
    ).fetchone() is None:
        conn.execute("""
            CREATE TABLE dx_failed_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type     TEXT NOT NULL,
                ref          TEXT,
                provider     TEXT,
                attempt      INTEGER DEFAULT 1,
                error_detail TEXT,
                payload_json TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("  + Created dx_failed_jobs table")


# =========================================================================
# UTILITIES
# =========================================================================

def _sanitise_complexity(raw_val: str) -> str:
    """Normalise complexity_score to match DB CHECK constraint values."""
    valid = ('Low', 'Medium', 'High', 'Enterprise')
    raw = str(raw_val or '').strip()
    for v in valid:
        if v.lower() == raw.lower():
            return v
    return ''


def quote_generate_ref():
    """Generate ticket ref: REQ-DDMMYY-XXXXX (5 uppercase alphanumeric)."""
    date_part = datetime.utcnow().strftime('%d%m%y')
    rand_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f'REQ-{date_part}-{rand_part}'


def quote_dedup_key(payload_str: str) -> str:
    """SHA-256 dedup key — mirrors fm_dedup_key."""
    return hashlib.sha256(payload_str.encode()).hexdigest()[:32]


def quote_log_event(conn, quote_ref: str, event_type: str,
                    payload: dict, source: str = 'web', status: str = 'processed'):
    """Insert a quote event row. Silently ignores duplicate event_ids."""
    event_id = quote_dedup_key(f"{quote_ref}-{event_type}-{datetime.utcnow().isoformat()}")
    try:
        conn.execute(
            """INSERT OR IGNORE INTO quote_events
               (event_id, quote_ref, event_type, source, payload_json, status)
               VALUES (?,?,?,?,?,?)""",
            (event_id, quote_ref, event_type, source,
             json.dumps(payload), status)
        )
    except Exception as e:
        print(f"[QUOTE] Event log error: {e}")


def quote_log_ai(conn, quote_ref: str, provider: str, call_type: str,
                  prompt: dict, response: dict, latency_ms: int,
                  success: bool = True, error: str = ''):
    """Log an AI call for audit and debugging."""
    try:
        conn.execute(
            """INSERT INTO quote_ai_log
               (quote_ref, ai_provider, call_type, prompt_json, response_json,
                latency_ms, success, error_detail)
               VALUES (?,?,?,?,?,?,?,?)""",
            (quote_ref, provider, call_type,
             json.dumps(prompt), json.dumps(response),
             latency_ms, 1 if success else 0, error)
        )
    except Exception as e:
        print(f"[QUOTE] AI log error: {e}")


# =========================================================================
# DEEPSEEK — Full scope analysis on form submission
# =========================================================================

DEEPSEEK_SCOPE_SYSTEM_PROMPT = """You are a senior software development estimator at Devex Studios,
a UK-based digital agency specialising in websites, web apps, and mobile applications.

Given a structured project requirements intake, analyse the project and return ONLY valid JSON
with no markdown, no explanation, no preamble.

JSON shape required:
{
  "complexity_score": "Low | Medium | High | Enterprise",
  "estimated_weeks": <integer, realistic delivery weeks>,
  "confidence": "Low | Medium | High",
  "stack_recommendation": "e.g. React + Node.js + PostgreSQL on AWS",
  "scope_summary": "2-3 sentence plain English summary of the project scope",
  "flags": [
    "Plain English risk or gap — e.g. No content provider specified",
    "Budget vs scope mismatch risk",
    "No branding assets available"
  ],
  "phase_breakdown": [
    {"phase": "Discovery & Design", "weeks": 2, "notes": "..."},
    {"phase": "Core Development", "weeks": 6, "notes": "..."},
    {"phase": "Testing & QA", "weeks": 2, "notes": "..."},
    {"phase": "Launch & Handover", "weeks": 1, "notes": "..."}
  ],
  "must_have_features": ["feature1", "feature2"],
  "recommended_additions": ["feature or service that would improve success"],
  "questions_for_client": ["Specific question to clarify scope", "..."]
}

Complexity rules:
- Low: brochure site, <6 pages, no backend, minimal features (1-4 weeks)
- Medium: standard website/app, auth, some integrations (5-12 weeks)
- High: complex app, custom workflows, multiple integrations, API (12-20 weeks)
- Enterprise: multi-tenant, bespoke platform, compliance requirements (20+ weeks)

Budget alignment:
- Flag if budget_range seems misaligned with complexity
- Be specific and actionable with flags — not generic

Return ONLY the JSON object. No markdown. No explanation."""


def deepseek_analyse_scope(quote_data: dict) -> tuple[dict | None, int]:
    """
    Send structured quote data to DeepSeek for scope analysis.
    Returns (result_dict, latency_ms). result_dict is None on failure.
    """
    if not DEEPSEEK_API_KEY:
        print("[QUOTE] DeepSeek API key not set — using mock scope")
        return _mock_scope_response(quote_data), 0

    user_content = f"""Project Requirements Intake — {quote_data.get('project_name', 'Unnamed')}

Contact: {quote_data.get('contact_name')} | {quote_data.get('company_name')}
Project Type: {quote_data.get('project_type')}
Budget Range: {quote_data.get('budget_range', 'Not specified')}
Start Date: {quote_data.get('start_date', 'Not specified')}
Launch Date: {quote_data.get('launch_date', 'Not specified')}

Brief Description:
{quote_data.get('brief_description', 'Not provided')}

Goals: {', '.join(json.loads(quote_data.get('goals_json', '[]')))}

Business Context:
- Target Audience: {quote_data.get('target_audience', 'Not specified')}
- Competitors: {quote_data.get('competitors', 'Not specified')}
- USP: {quote_data.get('usp', 'Not specified')}

Features:
{json.dumps(json.loads(quote_data.get('features_json', '{}')), indent=2)}

Technical Requirements:
{json.dumps(json.loads(quote_data.get('tech_json', '{}')), indent=2)}

Website Requirements:
{json.dumps(json.loads(quote_data.get('website_json', '{}')), indent=2)}

Mobile Requirements:
{json.dumps(json.loads(quote_data.get('mobile_json', '{}')), indent=2)}

Design & Content:
{json.dumps(json.loads(quote_data.get('design_json', '{}')), indent=2)}

Maintenance & Support:
{json.dumps(json.loads(quote_data.get('maintenance_json', '{}')), indent=2)}

Discovery Q&A:
{json.dumps(json.loads(quote_data.get('discovery_qa_json', '[]')), indent=2)}

Analyse this project and return the structured scope JSON."""

    t0 = time.time()
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
                    {'role': 'system', 'content': DEEPSEEK_SCOPE_SYSTEM_PROMPT},
                    {'role': 'user',   'content': user_content}
                ],
                'temperature': 0.2,
                'max_tokens': 1200,
            },
            timeout=45
        )
        latency_ms = int((time.time() - t0) * 1000)

        if resp.status_code != 200:
            print(f"[QUOTE/DS] Error {resp.status_code}: {resp.text[:300]}")
            return _mock_scope_response(quote_data), latency_ms

        raw = resp.json()['choices'][0]['message']['content'].strip()

        # Strip markdown fences if model added them
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

        parsed = json.loads(raw)
        print(f"[QUOTE/DS] Scope: complexity={parsed.get('complexity_score')} "
              f"weeks={parsed.get('estimated_weeks')} ({latency_ms}ms)")
        return parsed, latency_ms

    except json.JSONDecodeError as e:
        print(f"[QUOTE/DS] JSON parse error: {e}")
        return _mock_scope_response(quote_data), int((time.time() - t0) * 1000)
    except Exception as e:
        print(f"[QUOTE/DS] Call error: {e}")
        return _mock_scope_response(quote_data), int((time.time() - t0) * 1000)


def _mock_scope_response(quote_data: dict) -> dict:
    """Fallback when DeepSeek unavailable — keyword-based estimates."""
    features = json.loads(quote_data.get('features_json', '{"must_have":[]}'))
    feature_count = len(features.get('must_have', []))
    project_type  = quote_data.get('project_type', 'website')

    if feature_count > 6 or project_type in ('both', 'portal'):
        complexity, weeks = 'High', 16
    elif feature_count > 3 or project_type in ('web_app', 'ecommerce', 'mobile_app'):
        complexity, weeks = 'Medium', 8
    else:
        complexity, weeks = 'Low', 4

    return {
        'complexity_score':     complexity,
        'estimated_weeks':      weeks,
        'confidence':           'Low',
        'stack_recommendation': 'To be determined during discovery',
        'scope_summary':        (
            f"A {project_type.replace('_', ' ')} project with "
            f"{feature_count} core features. Full scope requires discovery call."
        ),
        'flags':                ['AI analysis unavailable — manual review required'],
        'phase_breakdown':      [
            {'phase': 'Discovery & Design', 'weeks': max(1, weeks // 5), 'notes': 'TBC'},
            {'phase': 'Development',        'weeks': max(1, int(weeks * 0.6)), 'notes': 'TBC'},
            {'phase': 'Testing & Launch',   'weeks': max(1, weeks // 5), 'notes': 'TBC'},
        ],
        'must_have_features':      features.get('must_have', []),
        'recommended_additions':   [],
        'questions_for_client':    ['Please book a discovery call to finalise scope.']
    }


# =========================================================================
# DX AI FALLBACK CHAIN — Phase 2
# Chain: DeepSeek (primary) → Gemini (secondary) → default classification
# Retry with exponential backoff. Log all failures to dx_failed_jobs.
# =========================================================================

def _dx_log_failed_job(conn, job_type, ref, provider, attempt, error, payload):
    """Log a failed AI job to dx_failed_jobs for audit."""
    try:
        conn.execute(
            """INSERT INTO dx_failed_jobs
               (job_type, ref, provider, attempt, error_detail, payload_json)
               VALUES (?,?,?,?,?,?)""",
            (job_type, ref, provider, attempt, str(error)[:500],
             json.dumps(payload)[:2000])
        )
        conn.commit()
    except Exception as e:
        print(f"[DX] Failed to log failed job: {e}")


def _gemini_scope_analysis(quote_data: dict) -> tuple[dict | None, int]:
    """
    Secondary: Gemini scope analysis. Same output shape as DeepSeek.
    Used as fallback when DeepSeek is unavailable.
    """
    if not GEMINI_API_KEY:
        return None, 0

    project_type = quote_data.get('project_type', 'website')
    features     = json.loads(quote_data.get('features_json', '{"must_have":[]}'))
    brief        = quote_data.get('brief_description', '')

    prompt = (
        f"Analyse this software project and return ONLY valid JSON (no markdown):\n"
        f"Project Type: {project_type}\n"
        f"Description: {brief}\n"
        f"Must-Have Features: {', '.join(features.get('must_have', []))}\n"
        f"Budget: {quote_data.get('budget_range', 'unspecified')}\n\n"
        f"Return JSON with keys: complexity_score (Low/Medium/High/Enterprise), "
        f"estimated_weeks (int), stack_recommendation (string), "
        f"scope_summary (string), flags (list of strings), "
        f"phase_breakdown (list of objects with phase/weeks/notes), "
        f"must_have_features (list), recommended_additions (list), "
        f"questions_for_client (list). No markdown, no explanation."
    )

    t0 = time.time()
    try:
        resp = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/'
            f'gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}',
            json={'contents': [{'parts': [{'text': prompt}]}]},
            timeout=30
        )
        latency_ms = int((time.time() - t0) * 1000)

        if resp.status_code != 200:
            print(f"[DX/Gemini] HTTP {resp.status_code}")
            return None, latency_ms

        raw = (resp.json()
               .get('candidates', [{}])[0]
               .get('content', {})
               .get('parts', [{}])[0]
               .get('text', ''))
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        parsed = json.loads(raw.strip())
        print(f"[DX/Gemini] Fallback scope: complexity={parsed.get('complexity_score')} ({latency_ms}ms)")
        return parsed, latency_ms

    except Exception as e:
        print(f"[DX/Gemini] Fallback error: {e}")
        return None, int((time.time() - t0) * 1000)


def dx_analyse_scope_with_fallback(quote_data: dict, quote_ref: str = '',
                                    conn=None) -> dict:
    """
    AI fallback chain for scope analysis:
      1. DeepSeek with up to 2 retries (exponential backoff)
      2. Gemini (single attempt)
      3. Default keyword-based classification

    Logs failures to dx_failed_jobs.
    Returns scope dict (always succeeds).
    """
    MAX_DS_ATTEMPTS = 2
    result = None
    latency = 0

    # ── Attempt 1: DeepSeek with retries ──
    for attempt in range(1, MAX_DS_ATTEMPTS + 1):
        try:
            result, latency = deepseek_analyse_scope(quote_data)
            if result and result.get('complexity_score'):
                print(f"[DX] DeepSeek scope OK (attempt {attempt})")
                return result
        except Exception as e:
            print(f"[DX] DeepSeek attempt {attempt} exception: {e}")
            if conn:
                _dx_log_failed_job(conn, 'scope_analysis', quote_ref,
                                   'deepseek', attempt, e, {})

        if attempt < MAX_DS_ATTEMPTS:
            backoff = 2 ** attempt
            print(f"[DX] DeepSeek retry in {backoff}s...")
            time.sleep(backoff)

    if conn:
        _dx_log_failed_job(conn, 'scope_analysis', quote_ref,
                           'deepseek', MAX_DS_ATTEMPTS,
                           'All DeepSeek attempts failed', {})

    # ── Attempt 2: Gemini fallback ──
    print("[DX] Falling back to Gemini for scope analysis")
    try:
        result, latency = _gemini_scope_analysis(quote_data)
        if result and result.get('complexity_score'):
            print("[DX] Gemini fallback scope OK")
            return result
    except Exception as e:
        print(f"[DX] Gemini fallback error: {e}")
        if conn:
            _dx_log_failed_job(conn, 'scope_analysis', quote_ref,
                               'gemini', 1, e, {})

    # ── Attempt 3: Default keyword-based classification ──
    print("[DX] Using default keyword-based classification")
    if conn:
        _dx_log_failed_job(conn, 'scope_analysis', quote_ref,
                           'default', 1, 'All AI providers failed', {})
    return _mock_scope_response(quote_data)


# =========================================================================
# GEMINI — Mid-form AI suggestions (called from wizard via AJAX)
# =========================================================================

def gemini_mid_form_suggestions(partial_data: dict) -> dict:
    """
    Called mid-wizard with partial form data.
    Returns suggested features, follow-up questions, and gap warnings.
    """
    if not GEMINI_API_KEY:
        return {
            'suggested_features': [],
            'questions':          [],
            'warnings':           ['AI suggestions unavailable — API key not configured.']
        }

    project_type = partial_data.get('project_type', 'website')
    goals        = partial_data.get('goals', [])
    features     = partial_data.get('features', [])
    description  = partial_data.get('brief_description', '')

    prompt_text = f"""You are a project scoping assistant at a digital agency.
A client is filling in a project requirements form. Based on their partial answers,
suggest additional features they might need, flag any missing information, and ask
clarifying questions.

Respond ONLY with valid JSON. No markdown. No explanation.

JSON shape:
{{
  "suggested_features": ["feature name", ...],
  "questions": ["Clarifying question?", ...],
  "warnings": ["Missing info or risk flag", ...]
}}

Client's partial answers:
- Project type: {project_type}
- Goals: {', '.join(goals) if goals else 'None yet'}
- Features selected: {', '.join(features) if features else 'None yet'}
- Description: {description or 'Not provided'}

Return ONLY the JSON object."""

    t0 = time.time()
    gemini_url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}'
    )
    payload = {
        'contents': [{'parts': [{'text': prompt_text}]}],
        'generationConfig': {
            'temperature': 0.3,
            'maxOutputTokens': 512
        }
    }

    try:
        resp = requests.post(gemini_url, json=payload, timeout=20)
        latency_ms = int((time.time() - t0) * 1000)

        if resp.status_code != 200:
            print(f"[QUOTE/GM] Error {resp.status_code}: {resp.text[:200]}")
            return {'suggested_features': [], 'questions': [], 'warnings': []}

        raw = (
            resp.json()
                .get('candidates', [{}])[0]
                .get('content', {})
                .get('parts', [{}])[0]
                .get('text', '')
                .strip()
        )
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

        result = json.loads(raw)
        print(f"[QUOTE/GM] Mid-form: {len(result.get('suggested_features',[]))} suggestions ({latency_ms}ms)")
        return result

    except Exception as e:
        print(f"[QUOTE/GM] Error: {e}")
        return {'suggested_features': [], 'questions': [], 'warnings': []}


def gemini_gap_analysis(quote_data: dict) -> tuple[str, list, list]:
    """
    Post-submission: Gemini reads complete form and returns:
    - ai_summary (string)
    - flags (list of risk/gap strings)
    - ai_questions (list of follow-up questions)
    """
    if not GEMINI_API_KEY:
        return (
            'AI summary unavailable — Gemini API key not configured.',
            ['Manual review required'],
            []
        )

    prompt_text = f"""You are a senior project manager at a digital agency reviewing a client's
project requirements form before scoping begins.

Review this submission and return ONLY valid JSON. No markdown. No explanation.

JSON shape:
{{
  "summary": "2-3 sentence plain English summary of what this client needs",
  "flags": ["Specific risk or missing info — be actionable, not generic"],
  "questions": ["Important question for the discovery call"]
}}

Project requirements:
{json.dumps(quote_data, indent=2)}

Return ONLY the JSON object."""

    t0 = time.time()
    gemini_url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}'
    )
    payload = {
        'contents': [{'parts': [{'text': prompt_text}]}],
        'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 600}
    }

    try:
        resp = requests.post(gemini_url, json=payload, timeout=25)
        latency_ms = int((time.time() - t0) * 1000)

        raw = (
            resp.json()
                .get('candidates', [{}])[0]
                .get('content', {})
                .get('parts', [{}])[0]
                .get('text', '')
                .strip()
        )
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

        result = json.loads(raw)
        print(f"[QUOTE/GM] Gap analysis complete ({latency_ms}ms): "
              f"{len(result.get('flags',[]))} flags")
        return (
            result.get('summary', ''),
            result.get('flags', []),
            result.get('questions', [])
        )

    except Exception as e:
        print(f"[QUOTE/GM] Gap analysis error: {e}")
        return ('AI summary generation failed.', ['Manual review required'], [])


# =========================================================================
# PDF EXPORT — Scope Summary Document
# =========================================================================

def generate_scope_pdf(quote: dict, scope: dict) -> BytesIO:
    """
    Generate a professional scope summary PDF for a quote request.
    Uses the same ReportLab approach as the existing contractor PDF.
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # ── Brand colours ──
    ink     = colors.HexColor('#0d1117')
    accent  = colors.HexColor('#2563eb')   # Devex blue
    silver  = colors.HexColor('#f1f5f9')
    mid     = colors.HexColor('#64748b')
    green   = colors.HexColor('#16a34a')
    amber   = colors.HexColor('#d97706')
    red_c   = colors.HexColor('#dc2626')

    complexity_colours = {
        'Low':        green,
        'Medium':     amber,
        'High':       red_c,
        'Enterprise': colors.HexColor('#7c3aed')
    }

    y = height - 30*mm

    # ── Cover header ──────────────────────────────────────────────────────
    c.setFillColor(ink)
    c.rect(0, height - 22*mm, width, 22*mm, fill=True, stroke=False)

    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(20*mm, height - 14*mm, 'DEVEX STUDIOS')
    c.setFont('Helvetica', 9)
    c.drawRightString(width - 20*mm, height - 14*mm,
                      f"REF: {quote.get('ref','—')}  |  {datetime.now().strftime('%d %b %Y')}")

    y = height - 38*mm

    # Project title
    c.setFillColor(ink)
    c.setFont('Helvetica-Bold', 22)
    c.drawString(20*mm, y, quote.get('project_name') or 'Project Scope Summary')
    y -= 9*mm

    c.setFillColor(accent)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20*mm, y, 'PROJECT SCOPE & REQUIREMENTS SUMMARY')
    y -= 6*mm

    c.setStrokeColor(accent)
    c.setLineWidth(2)
    c.line(20*mm, y, width - 20*mm, y)
    y -= 10*mm

    # ── Contact / project info ────────────────────────────────────────────
    def info_row(label, value, y_pos):
        c.setFillColor(mid)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(20*mm, y_pos, label.upper())
        c.setFillColor(ink)
        c.setFont('Helvetica', 9)
        c.drawString(60*mm, y_pos, str(value or '—'))
        return y_pos - 6*mm

    y = info_row('Client',       quote.get('contact_name') or quote.get('company_name'), y)
    y = info_row('Company',      quote.get('company_name'), y)
    y = info_row('Project Type', quote.get('project_type','').replace('_',' ').title(), y)
    y = info_row('Budget Range', quote.get('budget_range'), y)
    y = info_row('Target Launch', quote.get('launch_date'), y)
    y -= 6*mm

    # ── Complexity badge ──────────────────────────────────────────────────
    complexity = scope.get('complexity_score', '')
    badge_col  = complexity_colours.get(complexity, ink)
    c.setFillColor(badge_col)
    c.roundRect(20*mm, y - 8*mm, 50*mm, 12*mm, 3, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(23*mm, y - 2*mm, f'COMPLEXITY: {complexity or "TBC"}')

    est_weeks = scope.get('estimated_weeks')
    if est_weeks:
        c.setFillColor(ink)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(80*mm, y - 2*mm, f'EST. DELIVERY:  {est_weeks} weeks')

    y -= 18*mm

    # ── Scope summary ─────────────────────────────────────────────────────
    section_header = lambda title, yy: _pdf_section(c, title, yy, width, ink, accent)

    y = section_header('SCOPE SUMMARY', y)
    summary_text = scope.get('scope_summary') or quote.get('brief_description') or '—'
    y = _pdf_wrap_text(c, summary_text, 20*mm, y, width - 40*mm, ink, 9)
    y -= 8*mm

    # ── Stack recommendation ──────────────────────────────────────────────
    if scope.get('stack_recommendation'):
        y = section_header('RECOMMENDED TECHNOLOGY STACK', y)
        y = _pdf_wrap_text(c, scope['stack_recommendation'],
                           20*mm, y, width - 40*mm, ink, 9)
        y -= 8*mm

    # ── Phase breakdown ────────────────────────────────────────────────────
    phases = scope.get('phase_breakdown', [])
    if phases:
        if y < 80*mm:
            c.showPage(); y = height - 30*mm
        y = section_header('DELIVERY PHASES', y)
        for phase in phases:
            if y < 40*mm:
                c.showPage(); y = height - 30*mm
            c.setFillColor(silver)
            c.rect(20*mm, y - 8*mm, width - 40*mm, 9*mm, fill=True, stroke=False)
            c.setFillColor(ink)
            c.setFont('Helvetica-Bold', 9)
            c.drawString(23*mm, y - 3*mm, phase.get('phase', ''))
            c.setFillColor(accent)
            c.setFont('Helvetica-Bold', 9)
            c.drawRightString(width - 23*mm, y - 3*mm,
                              f"{phase.get('weeks', '?')} weeks")
            y -= 9*mm
            notes = phase.get('notes', '')
            if notes and notes != 'TBC':
                c.setFillColor(mid)
                c.setFont('Helvetica', 8)
                c.drawString(25*mm, y, notes)
                y -= 5*mm
            y -= 2*mm
        y -= 4*mm

    # ── Must-have features ────────────────────────────────────────────────
    must_have = scope.get('must_have_features', [])
    if not must_have:
        try:
            must_have = json.loads(quote.get('features_json', '{}')).get('must_have', [])
        except Exception:
            must_have = []

    if must_have:
        if y < 80*mm:
            c.showPage(); y = height - 30*mm
        y = section_header('CORE FEATURES', y)
        for feat in must_have:
            if y < 30*mm:
                c.showPage(); y = height - 30*mm
            c.setFillColor(accent)
            c.setFont('Helvetica', 8)
            c.drawString(20*mm, y, '▸')
            c.setFillColor(ink)
            c.drawString(25*mm, y, str(feat))
            y -= 5.5*mm
        y -= 6*mm

    # ── Risk flags ────────────────────────────────────────────────────────
    flags = scope.get('flags', [])
    try:
        flags += json.loads(quote.get('ai_flags_json', '[]'))
    except Exception:
        pass
    flags = list(dict.fromkeys(flags))  # dedupe preserving order

    if flags:
        if y < 80*mm:
            c.showPage(); y = height - 30*mm
        y = section_header('RISK FLAGS & OPEN QUESTIONS', y)
        for flag in flags:
            if y < 30*mm:
                c.showPage(); y = height - 30*mm
            c.setFillColor(amber)
            c.setFont('Helvetica-Bold', 8)
            c.drawString(20*mm, y, '⚑')
            c.setFillColor(ink)
            c.setFont('Helvetica', 8)
            y = _pdf_wrap_text(c, flag, 27*mm, y, width - 47*mm, ink, 8)
            y -= 3*mm
        y -= 6*mm

    # ── Discovery questions ───────────────────────────────────────────────
    questions = scope.get('questions_for_client', [])
    try:
        questions += json.loads(quote.get('ai_questions_json', '[]'))
    except Exception:
        pass
    questions = list(dict.fromkeys(questions))

    if questions:
        if y < 80*mm:
            c.showPage(); y = height - 30*mm
        y = section_header('DISCOVERY CALL AGENDA', y)
        for i, q in enumerate(questions, 1):
            if y < 30*mm:
                c.showPage(); y = height - 30*mm
            c.setFillColor(mid)
            c.setFont('Helvetica-Bold', 8)
            c.drawString(20*mm, y, f'{i}.')
            c.setFillColor(ink)
            y = _pdf_wrap_text(c, q, 27*mm, y, width - 47*mm, ink, 8)
            y -= 3*mm

    # ── Footer ────────────────────────────────────────────────────────────
    c.setFillColor(ink)
    c.rect(0, 0, width, 12*mm, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica', 7)
    c.drawString(20*mm, 4*mm,
                 'Devex Studios  |  devex.studios  |  This document is confidential and for recipient use only.')
    c.setFont('Helvetica-Bold', 7)
    c.drawRightString(width - 20*mm, 4*mm, f"Generated {datetime.now().strftime('%d %b %Y %H:%M')}")

    c.save()
    buffer.seek(0)
    return buffer


def _pdf_section(c, title, y, width, ink, accent):
    """Draw a section header bar. Returns new y position."""
    c.setFillColor(accent)
    c.rect(20*mm, y - 7*mm, width - 40*mm, 8*mm, fill=True, stroke=False)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(23*mm, y - 3.5*mm, title)
    return y - 14*mm


def _pdf_wrap_text(c, text, x, y, max_width, colour, font_size):
    """
    Simple word-wrap for ReportLab canvas text.
    Returns new y position after drawing.
    """
    c.setFillColor(colour)
    c.setFont('Helvetica', font_size)
    words = str(text).split()
    line  = ''
    line_h = font_size * 0.4 + 1.5

    for word in words:
        test = (line + ' ' + word).strip()
        if c.stringWidth(test, 'Helvetica', font_size) < max_width:
            line = test
        else:
            c.drawString(x, y, line)
            y -= line_h * mm
            line = word
    if line:
        c.drawString(x, y, line)
        y -= line_h * mm
    return y


# =========================================================================
# ROUTE REGISTRATION
# =========================================================================

def register_quote_routes(app: Flask):
    """
    Call this from app.py after Flask app is created:
        from app_quote import register_quote_routes
        register_quote_routes(app)
    """

    # ── Public: Multi-step wizard ─────────────────────────────────────────

    @app.route('/quote')
    def quote_wizard():
        """Public multi-step project requirements wizard."""
        return render_template('public/quote_wizard.html')

    @app.route('/api/quote/submit', methods=['POST'])
    def quote_submit():
        """
        Handle wizard form submission.
        1. Validate required fields
        2. Create quote_request row
        3. Fire Gemini gap analysis (sync — fast model)
        4. Fire DeepSeek scope analysis (sync — heavier)
        5. Update row with AI results
        6. Log events
        7. Redirect to confirmation
        """
        data = request.get_json(silent=True) or {}

        # ── Validate required fields ──────────────────────────────────────
        errors = []
        if not data.get('contact_name', '').strip():
            errors.append('Full name is required.')
        if not data.get('contact_email', '').strip():
            errors.append('Email address is required.')
        if not data.get('project_type'):
            errors.append('Project type is required.')
        if not data.get('brief_description', '').strip():
            errors.append('Project description is required.')

        if errors:
            return jsonify({'ok': False, 'errors': errors}), 400

        # ── Safe JSON field defaults ──────────────────────────────────────
        def safe_json(val, default):
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            try:
                json.loads(val)
                return val
            except Exception:
                return json.dumps(default)

        ref = quote_generate_ref()

        # Flatten everything into DB row
        row = {
            'ref':                  ref,
            'contact_name':         data.get('contact_name', '').strip(),
            'contact_email':        data.get('contact_email', '').strip(),
            'contact_phone':        data.get('contact_phone', '').strip(),
            'company_name':         data.get('company_name', '').strip(),
            'project_name':         data.get('project_name', '').strip(),
            'project_type':         data.get('project_type', 'website'),
            'brief_description':    data.get('brief_description', '').strip(),
            'goals_json':           safe_json(data.get('goals', []), []),
            'business_overview':    data.get('business_overview', '').strip(),
            'target_audience':      data.get('target_audience', '').strip(),
            'competitors':          data.get('competitors', '').strip(),
            'usp':                  data.get('usp', '').strip(),
            'features_json':        safe_json(data.get('features', {}),
                                              {'must_have': [], 'optional': []}),
            'website_json':         safe_json(data.get('website', {}), {}),
            'mobile_json':          safe_json(data.get('mobile', {}), {}),
            'design_json':          safe_json(data.get('design', {}), {}),
            'tech_json':            safe_json(data.get('tech', {}), {}),
            'maintenance_json':     safe_json(data.get('maintenance', {}), {}),
            'priority_matrix_json': safe_json(data.get('priority_matrix', []), []),
            'discovery_qa_json':    safe_json(data.get('discovery_qa', []), []),
            'budget_range':         data.get('budget_range', '').strip(),
            'start_date':           data.get('start_date', '').strip(),
            'launch_date':          data.get('launch_date', '').strip(),
            'source':               data.get('source', 'web'),
        }

        conn = get_quote_db()

        # ── Insert row ────────────────────────────────────────────────────
        conn.execute(
            """INSERT INTO quote_requests
               (ref, status, contact_name, contact_email, contact_phone,
                company_name, project_name, project_type, brief_description,
                goals_json, business_overview, target_audience, competitors, usp,
                features_json, website_json, mobile_json, design_json, tech_json,
                maintenance_json, priority_matrix_json, discovery_qa_json,
                budget_range, start_date, launch_date, source)
               VALUES
               (:ref,'NEW',:contact_name,:contact_email,:contact_phone,
                :company_name,:project_name,:project_type,:brief_description,
                :goals_json,:business_overview,:target_audience,:competitors,:usp,
                :features_json,:website_json,:mobile_json,:design_json,:tech_json,
                :maintenance_json,:priority_matrix_json,:discovery_qa_json,
                :budget_range,:start_date,:launch_date,:source)""",
            row
        )
        conn.commit()
        quote_log_event(conn, ref, 'quote.created', {'source': row['source']})
        conn.commit()

        # ── Gemini gap analysis ───────────────────────────────────────────
        t0 = time.time()
        ai_summary, ai_flags, ai_questions = gemini_gap_analysis(row)
        gemini_ms = int((time.time() - t0) * 1000)

        quote_log_ai(conn, ref, 'gemini', 'gap_analysis',
                     {'input_fields': list(row.keys())},
                     {'summary': ai_summary, 'flags': ai_flags, 'questions': ai_questions},
                     gemini_ms)

        # ── AI scope analysis (DeepSeek → Gemini → default fallback chain) ─
        t_scope = time.time()
        scope_result = dx_analyse_scope_with_fallback(row, ref, conn)
        ds_ms = int((time.time() - t_scope) * 1000)

        if scope_result:
            provider = 'deepseek' if DEEPSEEK_API_KEY else ('gemini' if GEMINI_API_KEY else 'default')
            quote_log_ai(conn, ref, provider, 'scope_analysis',
                         {'project_type': row['project_type']},
                         scope_result, ds_ms)

        # ── Update row with AI results ────────────────────────────────────
        conn.execute(
            """UPDATE quote_requests SET
               status              = 'REVIEWING',
               ai_summary          = ?,
               ai_flags_json       = ?,
               ai_questions_json   = ?,
               deepseek_scope_json = ?,
               complexity_score    = ?,
               estimated_weeks     = ?,
               stack_recommendation = ?,
               updated_at          = CURRENT_TIMESTAMP
               WHERE ref = ?""",
            (
                ai_summary,
                json.dumps(ai_flags),
                json.dumps(ai_questions),
                json.dumps(scope_result or {}),
                _sanitise_complexity((scope_result or {}).get('complexity_score', '')),
                (scope_result or {}).get('estimated_weeks'),
                (scope_result or {}).get('stack_recommendation', ''),
                ref
            )
        )
        conn.commit()

        quote_log_event(conn, ref, 'ai.analysis.complete', {
            'gemini_ms':    gemini_ms,
            'deepseek_ms':  ds_ms,
            'complexity':   (scope_result or {}).get('complexity_score', ''),
        })
        conn.commit()
        conn.close()

        print(f"[QUOTE] Submitted {ref} — complexity={( scope_result or {}).get('complexity_score')}")
        return jsonify({'ok': True, 'ref': ref})

    # ── Public: Confirmation page ─────────────────────────────────────────

    @app.route('/quote/confirmation/<ref>')
    def quote_confirmation(ref):
        conn = get_quote_db()
        quote = conn.execute(
            'SELECT * FROM quote_requests WHERE ref = ?', (ref,)
        ).fetchone()
        conn.close()
        if not quote:
            return 'Quote not found', 404
        return render_template('public/quote_confirmation.html', quote=dict(quote))

    # ── AJAX: Mid-form AI suggestions ─────────────────────────────────────

    @app.route('/api/quote/suggest', methods=['POST'])
    def api_quote_suggest():
        """
        Called mid-wizard (Step 2 → 3 transition).
        Returns Gemini suggestions for current partial data.
        Lightweight — fast model, no DB write.
        """
        data    = request.get_json(silent=True) or {}
        result  = gemini_mid_form_suggestions(data)
        return jsonify(result)

    # ── Ops: Quote dashboard ──────────────────────────────────────────────

    @app.route('/ops/quotes')
    def ops_quotes():
        """Internal quote pipeline dashboard."""
        status_filter  = request.args.get('status')
        search         = request.args.get('q', '').strip()

        conn  = get_quote_db()
        sql   = 'SELECT * FROM quote_requests WHERE 1=1'
        params = []

        if status_filter:
            sql += ' AND status = ?'
            params.append(status_filter)
        if search:
            sql += ' AND (ref LIKE ? OR contact_name LIKE ? OR company_name LIKE ? OR project_name LIKE ?)'
            like = f'%{search}%'
            params += [like, like, like, like]

        sql += ' ORDER BY created_at DESC LIMIT 100'
        quotes = [dict(r) for r in conn.execute(sql, params).fetchall()]

        # Pipeline KPIs
        kpis = {}
        for s in ('NEW', 'REVIEWING', 'SCOPED', 'SENT', 'WON', 'LOST'):
            kpis[s.lower()] = conn.execute(
                'SELECT COUNT(*) FROM quote_requests WHERE status = ?', (s,)
            ).fetchone()[0]
        kpis['total'] = conn.execute(
            'SELECT COUNT(*) FROM quote_requests'
        ).fetchone()[0]

        conn.close()
        return render_template(
            'ops/quotes_dashboard.html',
            quotes=quotes,
            kpis=kpis,
            status_filter=status_filter,
            search=search,
            active_nav='quotes'
        )

    @app.route('/ops/quotes/<ref>')
    def ops_quote_detail(ref):
        """Full quote detail view with AI summary and scope."""
        conn  = get_quote_db()
        quote = conn.execute(
            'SELECT * FROM quote_requests WHERE ref = ?', (ref,)
        ).fetchone()
        if not quote:
            conn.close()
            return 'Quote not found', 404

        quote   = dict(quote)
        events  = [dict(r) for r in conn.execute(
            'SELECT * FROM quote_events WHERE quote_ref = ? ORDER BY created_at DESC LIMIT 30',
            (ref,)
        ).fetchall()]
        ai_logs = [dict(r) for r in conn.execute(
            'SELECT * FROM quote_ai_log WHERE quote_ref = ? ORDER BY created_at DESC',
            (ref,)
        ).fetchall()]
        conn.close()

        # Parse JSON fields for template
        for field in ('goals_json', 'features_json', 'website_json', 'mobile_json',
                      'design_json', 'tech_json', 'maintenance_json',
                      'priority_matrix_json', 'discovery_qa_json',
                      'ai_flags_json', 'ai_questions_json', 'deepseek_scope_json'):
            try:
                quote[field.replace('_json', '')] = json.loads(quote.get(field) or '{}')
            except Exception:
                quote[field.replace('_json', '')] = {}

        return render_template(
            'ops/quote_detail.html',
            quote=quote,
            events=events,
            ai_logs=ai_logs,
            active_nav='quotes'
        )

    # ── Ops: Update quote status ──────────────────────────────────────────

    @app.route('/ops/quotes/<ref>/status', methods=['POST'])
    def ops_quote_update_status(ref):
        data       = request.get_json(silent=True) or {}
        new_status = data.get('status', '').upper()
        valid      = ('NEW', 'REVIEWING', 'SCOPED', 'SENT', 'WON', 'LOST', 'ARCHIVED')
        if new_status not in valid:
            return jsonify({'error': f'Invalid status. Must be one of: {valid}'}), 400

        conn = get_quote_db()
        quote = conn.execute(
            'SELECT ref FROM quote_requests WHERE ref = ?', (ref,)
        ).fetchone()
        if not quote:
            conn.close()
            return jsonify({'error': 'Quote not found'}), 404

        conn.execute(
            "UPDATE quote_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE ref = ?",
            (new_status, ref)
        )
        quote_log_event(conn, ref, 'quote.status_changed',
                        {'new_status': new_status}, source='ops')
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'ref': ref, 'status': new_status})

    # ── Ops: Update internal notes ────────────────────────────────────────

    @app.route('/ops/quotes/<ref>/notes', methods=['POST'])
    def ops_quote_update_notes(ref):
        data  = request.get_json(silent=True) or {}
        notes = data.get('notes', '').strip()

        conn = get_quote_db()
        conn.execute(
            "UPDATE quote_requests SET internal_notes = ?, assignee = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE ref = ?",
            (notes, data.get('assignee', ''), ref)
        )
        quote_log_event(conn, ref, 'quote.updated',
                        {'fields': ['internal_notes', 'assignee']}, source='ops')
        conn.commit()
        conn.close()
        return jsonify({'ok': True})

    # ── Ops: Re-run AI analysis ───────────────────────────────────────────

    @app.route('/ops/quotes/<ref>/reanalyse', methods=['POST'])
    def ops_quote_reanalyse(ref):
        """
        Re-run Gemini + DeepSeek on an existing quote.
        Useful after manual edits or when AI keys are newly configured.
        """
        conn = get_quote_db()
        try:
            quote = conn.execute(
                'SELECT * FROM quote_requests WHERE ref = ?', (ref,)
            ).fetchone()
            if not quote:
                conn.close()
                return jsonify({'ok': False, 'error': 'Quote not found'}), 404

            row = dict(quote)

            ai_summary, ai_flags, ai_questions = gemini_gap_analysis(row)
            t_scope = time.time()
            scope_result = dx_analyse_scope_with_fallback(row, ref, conn)
            ds_ms = int((time.time() - t_scope) * 1000)

            # Sanitise complexity_score to match DB CHECK constraint
            valid_cx = ('', 'Low', 'Medium', 'High', 'Enterprise')
            raw_cx = (scope_result or {}).get('complexity_score', '')
            cx_val = ''
            for v in valid_cx:
                if v and v.lower() == str(raw_cx).lower().strip():
                    cx_val = v
                    break

            conn.execute(
                """UPDATE quote_requests SET
                   ai_summary          = ?,
                   ai_flags_json       = ?,
                   ai_questions_json   = ?,
                   deepseek_scope_json = ?,
                   complexity_score    = ?,
                   estimated_weeks     = ?,
                   stack_recommendation = ?,
                   updated_at          = CURRENT_TIMESTAMP
                   WHERE ref = ?""",
                (
                    ai_summary,
                    json.dumps(ai_flags),
                    json.dumps(ai_questions),
                    json.dumps(scope_result or {}),
                    cx_val,
                    (scope_result or {}).get('estimated_weeks'),
                    (scope_result or {}).get('stack_recommendation', ''),
                    ref
                )
            )
            quote_log_event(conn, ref, 'ai.analysis.rerun', {}, source='ops')
            conn.commit()
            conn.close()

            return jsonify({
                'ok':            True,
                'complexity':    cx_val,
                'weeks':         (scope_result or {}).get('estimated_weeks'),
                'flags':         ai_flags,
            })

        except Exception as e:
            print(f"[QUOTE] Reanalyse error for {ref}: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return jsonify({'ok': False, 'error': f'Analysis failed: {str(e)[:200]}'}), 500

    # ── Ops: PDF export ───────────────────────────────────────────────────

    @app.route('/ops/quotes/<ref>/pdf')
    def ops_quote_pdf(ref):
        """Export scope summary as PDF."""
        conn = get_quote_db()
        quote = conn.execute(
            'SELECT * FROM quote_requests WHERE ref = ?', (ref,)
        ).fetchone()
        conn.close()
        if not quote:
            return 'Quote not found', 404

        quote = dict(quote)
        try:
            scope = json.loads(quote.get('deepseek_scope_json') or '{}')
        except Exception:
            scope = {}

        buffer   = generate_scope_pdf(quote, scope)
        filename = f"scope_{ref}_{datetime.now().strftime('%Y%m%d')}.pdf"
        quote_log_event(get_quote_db(), ref, 'pdf.exported',
                        {'filename': filename}, source='ops')

        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    # ── API: List quotes (JSON) ───────────────────────────────────────────

    @app.route('/api/quotes', methods=['GET'])
    def api_quotes_list():
        """JSON list endpoint for dashboard AJAX refresh."""
        status = request.args.get('status')
        conn   = get_quote_db()
        sql    = 'SELECT * FROM quote_requests WHERE 1=1'
        params = []
        if status:
            sql += ' AND status = ?'
            params.append(status)
        sql += ' ORDER BY created_at DESC LIMIT 50'
        quotes = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return jsonify(quotes)

    @app.route('/api/quotes/<ref>', methods=['GET'])
    def api_quote_get(ref):
        """JSON detail endpoint."""
        conn  = get_quote_db()
        quote = conn.execute(
            'SELECT * FROM quote_requests WHERE ref = ?', (ref,)
        ).fetchone()
        conn.close()
        if not quote:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(dict(quote))

    print("  ✓ Quote Portal routes registered")
