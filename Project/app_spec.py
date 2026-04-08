#!/usr/bin/env python3
"""
Devex AI Studios — Spec Scope Builder
Project/app_spec.py

Routes: /studio/specs/*
Tables: spec_scopes, spec_scope_sections, spec_scope_questions,
        spec_scope_answers, spec_scope_ai_runs  (all in engine.db)

Integration:
    In app.py add:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Project'))
        from app_spec import register_spec_routes, run_spec_migrations
    In init_db():
        conn_spec = sqlite3.connect(ENGINE_DB_PATH)
        run_spec_migrations(conn_spec)
        conn_spec.commit()
        conn_spec.close()
    After register_quote_routes(app):
        register_spec_routes(app)
"""

import json
import os
import re
import time
import sqlite3
import requests
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, send_file, session
)

ENGINE_DB_PATH   = 'engine.db'
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
GEMINI_API_KEY   = os.environ.get('GEMINI_API_KEY', '')


# =========================================================================
# DATABASE
# =========================================================================

def get_spec_db():
    conn = sqlite3.connect(ENGINE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_spec_migrations(conn):
    """Idempotent. Call from init_db() in app.py."""
    print("  Running Spec Scope Builder migrations...")
    needs = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='spec_scopes'"
    ).fetchone() is None

    if needs:
        schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema_spec.sql')
        try:
            with open(schema_path, 'r') as f:
                conn.executescript(f.read())
            conn.commit()
            print("  ✓ Spec Scope tables created")
        except FileNotFoundError:
            print(f"  ! schema_spec.sql not found at {schema_path}")
    else:
        print("  ✓ Spec Scope tables already exist")


# =========================================================================
# DEFAULT SECTIONS & QUESTIONS
# 15 sections covering a full software project scope
# =========================================================================

DEFAULT_SECTIONS = [
    {
        "title": "Business Context",
        "description": "Understand the organisation, the problem being solved, and what success looks like.",
        "questions": [
            {"label": "What does your company / organisation do?", "field_type": "textarea",
             "placeholder": "Brief description of the business and its market.", "required": True},
            {"label": "What problem is this project solving?", "field_type": "textarea",
             "placeholder": "Describe the pain point or opportunity that this software addresses.", "required": True},
            {"label": "What does success look like in 6 months?", "field_type": "textarea",
             "placeholder": "Measurable outcomes, KPIs, or qualitative goals.", "required": True},
            {"label": "What is driving the timeline?", "field_type": "radio",
             "options_json": json.dumps(["Hard business deadline", "Funding / investor milestone",
                                          "Strategic window", "No hard deadline — timeline is flexible"]),
             "required": False},
            {"label": "Who are the main competitors or comparable tools?", "field_type": "textarea",
             "placeholder": "Names, URLs, or brief descriptions.", "required": False},
        ]
    },
    {
        "title": "Project Goals",
        "description": "What the project must achieve — primary goals, secondary goals, and the MVP definition.",
        "questions": [
            {"label": "Primary goals for this project", "field_type": "chips",
             "options_json": json.dumps(["Lead generation", "Online sales", "Process automation",
                                          "Customer retention", "Internal efficiency", "Brand presence",
                                          "Reporting & analytics", "Compliance", "Cost reduction"]),
             "required": True},
            {"label": "What should users be able to do that they cannot do today?", "field_type": "textarea",
             "placeholder": "New capabilities this project unlocks.", "required": True},
            {"label": "What manual processes should this replace or streamline?", "field_type": "textarea",
             "placeholder": "e.g. email-based approvals, spreadsheet tracking, phone orders.", "required": False},
            {"label": "What does a minimal viable product (MVP) look like?", "field_type": "textarea",
             "placeholder": "The smallest useful version — what must it do on day one?",
             "help_text": "Be specific. A tightly scoped MVP reduces risk and delivers value faster.", "required": True},
            {"label": "What is explicitly out of scope for this project?", "field_type": "textarea",
             "placeholder": "Features, integrations, or capabilities that should NOT be included.", "required": False},
        ]
    },
    {
        "title": "Users & Roles",
        "description": "Who uses the system, what roles exist, and what access levels are needed.",
        "questions": [
            {"label": "Who are the end users of this system?", "field_type": "textarea",
             "placeholder": "Describe each user type — internal staff, external clients, public visitors, etc.",
             "required": True},
            {"label": "What roles will exist in the system?", "field_type": "chips",
             "options_json": json.dumps(["Super Admin", "Admin", "Manager", "Staff / Team member",
                                          "Client / Customer", "Partner", "Public (unauthenticated)", "API / Integration"]),
             "required": True},
            {"label": "Approximate number of registered users at launch", "field_type": "radio",
             "options_json": json.dumps(["Under 100", "100 – 1,000", "1,000 – 10,000",
                                          "10,000 – 100,000", "100,000+"]),
             "required": False},
            {"label": "Do different user roles see different data or features?", "field_type": "yes_no",
             "help_text": "Role-based access control (RBAC) — affects auth complexity.", "required": True},
            {"label": "Does unauthenticated (public) users need access to any part of the system?", "field_type": "yes_no",
             "required": False},
            {"label": "Are there any regulatory or accessibility requirements for users?", "field_type": "textarea",
             "placeholder": "e.g. WCAG 2.1 AA, GDPR consent, age verification.", "required": False},
        ]
    },
    {
        "title": "Core Workflows",
        "description": "The most critical user journeys and how data flows through the system.",
        "questions": [
            {"label": "Describe the most important workflow step by step", "field_type": "textarea",
             "placeholder": "Walk through the primary user journey from start to finish.",
             "help_text": "This is the single most important thing to get right. Be specific.", "required": True},
            {"label": "What are the top 3 user journeys?", "field_type": "textarea",
             "placeholder": "Journey 1: ...\nJourney 2: ...\nJourney 3: ...", "required": True},
            {"label": "What data enters the system and what data exits?", "field_type": "textarea",
             "placeholder": "Inputs: form submissions, file uploads, API data...\nOutputs: reports, exports, notifications...",
             "required": False},
            {"label": "Are there approval, review, or sign-off stages in any workflow?", "field_type": "yes_no",
             "help_text": "Multi-step approvals add significant complexity.", "required": True},
            {"label": "Are there time-sensitive or automated triggers in the workflow?", "field_type": "textarea",
             "placeholder": "e.g. reminders sent 24 hours before, status changes automatically after 7 days.",
             "required": False},
        ]
    },
    {
        "title": "Features & Functionality",
        "description": "What the system needs to do — must-haves, nice-to-haves, and priority ranking.",
        "questions": [
            {"label": "Core features required in the system", "field_type": "chips",
             "options_json": json.dumps(["User authentication", "Dashboard / Overview", "Form builder",
                                          "Search & filter", "Email notifications", "Push notifications",
                                          "Payment processing", "Reporting & charts", "File upload",
                                          "Document generation", "In-app messaging", "Booking / scheduling",
                                          "Calendar", "Map / location", "Offline mode", "Multi-language"]),
             "required": True},
            {"label": "Any other must-have features not listed above?", "field_type": "textarea",
             "placeholder": "Describe any custom or business-specific features.", "required": False},
            {"label": "Nice-to-have features (can be deferred to a later phase)", "field_type": "textarea",
             "placeholder": "Features that would add value but are not blockers for launch.", "required": False},
            {"label": "Priority ranking — list your top 5 features in order of importance", "field_type": "textarea",
             "placeholder": "1. ...\n2. ...\n3. ...\n4. ...\n5. ...",
             "help_text": "This helps scope the MVP and set sprint priorities.", "required": False},
        ]
    },
    {
        "title": "Admin & Back-Office",
        "description": "What the internal team needs to manage and operate the system.",
        "questions": [
            {"label": "Does the system need an admin panel or back-office interface?", "field_type": "yes_no",
             "required": True},
            {"label": "What should admins be able to do?", "field_type": "chips",
             "options_json": json.dumps(["Manage users & roles", "View analytics & reports",
                                          "Configure system settings", "Moderate content",
                                          "Manage billing & subscriptions", "Export data",
                                          "Trigger automations manually", "Review audit logs",
                                          "Impersonate / support users"]),
             "required": False},
            {"label": "Are there operational dashboards the internal team needs?", "field_type": "textarea",
             "placeholder": "e.g. live orders dashboard, support ticket queue, pipeline overview.", "required": False},
            {"label": "What manual overrides or interventions does the operations team need?", "field_type": "textarea",
             "placeholder": "e.g. manually mark an order as fulfilled, override a user's tier.",
             "help_text": "Admin overrides add build time but reduce support burden.", "required": False},
        ]
    },
    {
        "title": "Integrations & APIs",
        "description": "External services, existing systems, and data connections.",
        "questions": [
            {"label": "External services / tools to integrate", "field_type": "chips",
             "options_json": json.dumps(["Stripe / payments", "Twilio / SMS", "SendGrid / email",
                                          "Slack", "Google Workspace", "Microsoft 365", "HubSpot",
                                          "Salesforce", "Xero / accounting", "Zapier / Make",
                                          "Shopify", "Mailchimp", "Intercom", "Zendesk",
                                          "AWS S3", "Cloudflare"]),
             "required": False},
            {"label": "Does this need to connect to an existing internal system?", "field_type": "yes_no",
             "required": True},
            {"label": "If yes, describe the existing system and what data needs to flow", "field_type": "textarea",
             "placeholder": "System name, data format (REST/SOAP/DB), direction of sync, frequency.",
             "required": False},
            {"label": "Will this project expose its own API for other systems or partners to use?", "field_type": "yes_no",
             "help_text": "Public APIs require versioning, auth, documentation, and rate limiting.", "required": True},
            {"label": "Any other data sources or third-party data feeds?", "field_type": "textarea",
             "placeholder": "e.g. postcode lookup, companies house, mapping APIs, weather data.",
             "required": False},
        ]
    },
    {
        "title": "Data & Reporting",
        "description": "What data the system stores, how it is reported, and compliance requirements.",
        "questions": [
            {"label": "What are the primary data entities in this system?", "field_type": "textarea",
             "placeholder": "e.g. Users, Orders, Products, Invoices, Reports, Documents...",
             "help_text": "Data entities become database tables — this drives the data model.", "required": True},
            {"label": "What reports or analytics does the business need?", "field_type": "textarea",
             "placeholder": "e.g. monthly revenue by client, conversion rates, active user trends.",
             "required": False},
            {"label": "Data export formats required", "field_type": "chips",
             "options_json": json.dumps(["CSV", "Excel (XLSX)", "PDF", "JSON / API", "None required"]),
             "required": False},
            {"label": "Regulatory / compliance requirements that apply to this data", "field_type": "chips",
             "options_json": json.dumps(["GDPR (UK/EU)", "HIPAA (healthcare)", "PCI-DSS (payments)",
                                          "ISO 27001", "SOC 2", "FCA (financial)", "None"]),
             "required": True},
            {"label": "Data retention policy", "field_type": "textarea",
             "placeholder": "How long is data kept? Who can delete it? Any archival requirements?",
             "required": False},
        ]
    },
    {
        "title": "AI & Automation",
        "description": "AI-powered features and workflow automation requirements.",
        "questions": [
            {"label": "Is AI a core, differentiating part of this product?", "field_type": "yes_no",
             "required": True},
            {"label": "AI capabilities needed", "field_type": "chips",
             "options_json": json.dumps(["Text generation / summarisation", "Classification / tagging",
                                          "Semantic search", "Personalised recommendations",
                                          "Document processing / extraction",
                                          "Conversational chatbot", "Image / vision analysis",
                                          "Predictive analytics", "Code generation", "None"]),
             "required": False},
            {"label": "What processes or decisions should be automated?", "field_type": "textarea",
             "placeholder": "e.g. auto-classify support tickets, auto-send invoices, auto-assign tasks.",
             "required": False},
            {"label": "Any specific AI models, providers, or existing AI services to use?", "field_type": "textarea",
             "placeholder": "e.g. OpenAI GPT-4, Anthropic Claude, Google Gemini, custom fine-tuned model.",
             "required": False},
            {"label": "What happens when the AI is wrong or unavailable?", "field_type": "textarea",
             "placeholder": "Fallback behaviour — manual override, graceful degradation, human-in-the-loop.",
             "help_text": "AI fallback design is often overlooked and causes production issues.", "required": False},
        ]
    },
    {
        "title": "Design, Content & Brand",
        "description": "Visual design requirements, branding assets, and content ownership.",
        "questions": [
            {"label": "Is there existing branding (logo, colours, typography, guidelines)?", "field_type": "yes_no",
             "required": True},
            {"label": "Design style direction", "field_type": "radio",
             "options_json": json.dumps(["Modern & minimal", "Professional & corporate",
                                          "Bold & creative", "Technical / data-focused",
                                          "Warm & approachable", "Match an existing site"]),
             "required": False},
            {"label": "Who provides the content (copy, images, data)?", "field_type": "radio",
             "options_json": json.dumps(["Client provides everything", "Agency writes everything",
                                          "Mixed — client provides brief, agency writes",
                                          "CMS — content managed after launch by client"]),
             "required": True},
            {"label": "Does the system need to support multiple languages?", "field_type": "yes_no",
             "help_text": "i18n / l10n adds 15–30% to frontend build time.", "required": True},
            {"label": "Reference sites or design inspiration", "field_type": "textarea",
             "placeholder": "URLs or descriptions of sites with a look/feel you like or want to avoid.",
             "required": False},
        ]
    },
    {
        "title": "Security, Auth & Permissions",
        "description": "Authentication methods, access control, and security requirements.",
        "questions": [
            {"label": "Authentication methods required", "field_type": "chips",
             "options_json": json.dumps(["Email & password", "Google SSO", "Microsoft SSO",
                                          "GitHub OAuth", "Magic link (passwordless)", "Phone OTP",
                                          "Enterprise SAML / LDAP"]),
             "required": True},
            {"label": "Is two-factor authentication (2FA) required?", "field_type": "yes_no",
             "required": True},
            {"label": "Data sensitivity level", "field_type": "risk_level",
             "help_text": "Low = public data. Medium = personal info. High = financial/health. Critical = regulated/classified.",
             "required": True},
            {"label": "Is role-based access control (RBAC) required?", "field_type": "yes_no",
             "required": True},
            {"label": "Any penetration testing, security audit, or accreditation requirements?", "field_type": "textarea",
             "placeholder": "e.g. Cyber Essentials, OWASP audit, third-party pen test before launch.",
             "required": False},
        ]
    },
    {
        "title": "Performance & Scale",
        "description": "Expected load, uptime requirements, and real-time feature needs.",
        "questions": [
            {"label": "Expected peak concurrent users", "field_type": "radio",
             "options_json": json.dumps(["Under 100", "100 – 1,000", "1,000 – 10,000",
                                          "10,000 – 100,000", "Enterprise scale (100k+)"]),
             "required": True},
            {"label": "Uptime / availability requirement", "field_type": "radio",
             "options_json": json.dumps(["No formal SLA required", "99% (3.7 days/year downtime OK)",
                                          "99.9% (8.7 hours/year — standard production)",
                                          "99.99% (52 mins/year — business critical)"]),
             "required": True},
            {"label": "Real-time features required", "field_type": "chips",
             "options_json": json.dumps(["Live notifications", "Real-time dashboards",
                                          "Collaborative editing", "Live chat / messaging",
                                          "WebSocket data streams", "None"]),
             "required": False},
            {"label": "Are there large file uploads, video processing, or heavy compute needs?", "field_type": "textarea",
             "placeholder": "e.g. video transcoding, PDF generation at scale, bulk data imports.",
             "required": False},
            {"label": "Geographic distribution — where are the users?", "field_type": "radio",
             "options_json": json.dumps(["UK only", "UK + Europe", "UK + US", "Global"]),
             "required": False},
        ]
    },
    {
        "title": "Delivery Phases",
        "description": "How the project should be delivered and what the timeline milestones are.",
        "questions": [
            {"label": "Preferred delivery model", "field_type": "radio",
             "options_json": json.dumps(["MVP first — launch fast, iterate based on feedback",
                                          "Phased — planned releases over several months",
                                          "Big bang — full feature set before launch",
                                          "Continuous delivery — rolling weekly/fortnightly releases"]),
             "required": True},
            {"label": "What must Phase 1 / MVP include?", "field_type": "textarea",
             "placeholder": "The minimum that makes this usable and valuable at launch.",
             "help_text": "Less is more. Tight MVPs ship faster and generate earlier feedback.", "required": True},
            {"label": "What can be deferred to Phase 2 or later?", "field_type": "textarea",
             "placeholder": "Features or improvements that add value but are not launch-blockers.",
             "required": False},
            {"label": "Are there any hard deadlines or immovable dates?", "field_type": "textarea",
             "placeholder": "e.g. trade show, contract start date, board presentation.", "required": False},
            {"label": "Budget range for this project", "field_type": "select",
             "options_json": json.dumps(["Not yet set", "Under £5,000", "£5,000 – £15,000",
                                          "£15,000 – £30,000", "£30,000 – £60,000",
                                          "£60,000 – £100,000", "£100,000+"]),
             "required": True},
        ]
    },
    {
        "title": "Assumptions & Exclusions",
        "description": "What we are assuming the client provides, and what is out of scope.",
        "questions": [
            {"label": "What are we assuming the client will provide?", "field_type": "textarea",
             "placeholder": "e.g. brand assets, copy/content, test accounts for third-party services, domain & hosting.",
             "help_text": "Unwritten assumptions are the primary cause of scope disputes.", "required": True},
            {"label": "What is explicitly excluded from this project?", "field_type": "textarea",
             "placeholder": "Features, services, or platforms that are NOT part of this engagement.",
             "required": True},
            {"label": "What third-party dependencies could delay delivery?", "field_type": "textarea",
             "placeholder": "e.g. waiting for API credentials, legal approval, content sign-off.",
             "required": False},
            {"label": "What decisions need to be made before build can start?", "field_type": "textarea",
             "placeholder": "Architecture decisions, technology choices, or business decisions.",
             "help_text": "Pre-build blockers should be listed here and tracked.", "required": False},
        ]
    },
    {
        "title": "Risks & Open Questions",
        "description": "Known risks, unknowns, and questions that need answers before final scoping.",
        "questions": [
            {"label": "What could go wrong with this project?", "field_type": "textarea",
             "placeholder": "Technical risks, business risks, team availability risks, dependency risks.",
             "required": True},
            {"label": "What is still unclear or requires investigation?", "field_type": "textarea",
             "placeholder": "Items that need a spike, discovery call, or third-party input before scoping.",
             "required": False},
            {"label": "Overall project risk level", "field_type": "risk_level",
             "help_text": "Based on complexity, unknowns, timeline pressure, and stakeholder alignment.",
             "required": True},
            {"label": "Any other notes, context, or open questions?", "field_type": "textarea",
             "placeholder": "Anything else the development team should know.", "required": False},
        ]
    },
]


def create_default_sections(conn, scope_id: int):
    """Seed the 15 default sections + questions for a new spec."""
    for idx, sec_def in enumerate(DEFAULT_SECTIONS):
        cursor = conn.execute(
            "INSERT INTO spec_scope_sections (scope_id, title, description, order_idx) VALUES (?,?,?,?)",
            (scope_id, sec_def['title'], sec_def.get('description', ''), idx)
        )
        section_id = cursor.lastrowid

        for q_idx, q in enumerate(sec_def.get('questions', [])):
            conn.execute(
                """INSERT INTO spec_scope_questions
                   (scope_id, section_id, label, field_type, placeholder,
                    help_text, internal_note, required, order_idx, options_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    scope_id,
                    section_id,
                    q['label'],
                    q.get('field_type', 'textarea'),
                    q.get('placeholder', ''),
                    q.get('help_text', ''),
                    q.get('internal_note', ''),
                    1 if q.get('required') else 0,
                    q_idx,
                    q.get('options_json', '[]'),
                )
            )
    conn.commit()


# =========================================================================
# HELPERS
# =========================================================================

def _load_spec(conn, spec_id):
    """Return spec row as dict, or None."""
    row = conn.execute("SELECT * FROM spec_scopes WHERE id=?", (spec_id,)).fetchone()
    return dict(row) if row else None


def _load_sections(conn, scope_id):
    """Return ordered list of section dicts."""
    rows = conn.execute(
        "SELECT * FROM spec_scope_sections WHERE scope_id=? ORDER BY order_idx, id",
        (scope_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _load_questions(conn, scope_id):
    """Return all questions for scope as list of dicts."""
    rows = conn.execute(
        "SELECT * FROM spec_scope_questions WHERE scope_id=? ORDER BY section_id, order_idx, id",
        (scope_id,)
    ).fetchall()
    qs = []
    for r in rows:
        q = dict(r)
        try:
            q['options'] = json.loads(q.get('options_json') or '[]')
        except Exception:
            q['options'] = []
        qs.append(q)
    return qs


def _load_answers(conn, scope_id):
    """Return {question_id: answer_dict}."""
    rows = conn.execute(
        "SELECT * FROM spec_scope_answers WHERE scope_id=?", (scope_id,)
    ).fetchall()
    out = {}
    for r in rows:
        a = dict(r)
        try:
            a['answer_json_parsed'] = json.loads(a.get('answer_json') or '[]')
        except Exception:
            a['answer_json_parsed'] = []
        out[a['question_id']] = a
    return out


def _count_completion(sections, questions, answers):
    """Return {section_id: {total, answered, status}} per section."""
    q_by_sec = {}
    for q in questions:
        q_by_sec.setdefault(q['section_id'], []).append(q)

    result = {}
    total_all = answered_all = 0

    for sec in sections:
        qs = q_by_sec.get(sec['id'], [])
        total = len(qs)
        answered = 0
        for q in qs:
            ans = answers.get(q['id'])
            if ans:
                val = (ans.get('answer_text') or '').strip()
                multi = ans.get('answer_json_parsed') or []
                if val or multi:
                    answered += 1
        status = 'empty'
        if total and answered == total:
            status = 'complete'
        elif answered > 0:
            status = 'partial'
        result[sec['id']] = {'total': total, 'answered': answered, 'status': status}
        total_all += total
        answered_all += answered

    return result, total_all, answered_all


# =========================================================================
# AI ANALYSIS — DeepSeek primary, Gemini fallback
# =========================================================================

SPEC_AI_SYSTEM_PROMPT = """You are a senior software architect and delivery lead at Devex AI Studios,
a UK-based digital agency specialising in custom software, web applications, and AI integration.

You have been given a complete software project scope document filled in by a client or project manager.
Your job is to produce a comprehensive, honest, actionable analysis of this scope.

Return ONLY valid JSON with no markdown, no explanation, no preamble.

Required JSON shape:
{
  "executive_summary": "3-4 sentence plain English summary of what this project is and what it needs to achieve",
  "complexity_score": "Low | Medium | High | Enterprise",
  "estimated_weeks": <integer — realistic total delivery including discovery, build, and launch>,
  "confidence": "Low | Medium | High",
  "stack_recommendation": "Specific technology recommendation with brief rationale",
  "phase_breakdown": [
    {"phase": "Discovery & Design", "weeks": <int>, "notes": "..."},
    {"phase": "Core Development", "weeks": <int>, "notes": "..."},
    {"phase": "Testing & QA", "weeks": <int>, "notes": "..."},
    {"phase": "Launch & Handover", "weeks": <int>, "notes": "..."}
  ],
  "gaps_found": [
    "A specific missing or underspecified requirement — be precise",
    "..."
  ],
  "risks": [
    {"risk": "Risk description", "severity": "Low | Medium | High | Critical", "mitigation": "Suggested mitigation"},
    "..."
  ],
  "contradictions": [
    "A specific contradiction or conflict between requirements — be precise"
  ],
  "must_have_features": ["feature1", "feature2", "..."],
  "recommended_additions": ["Feature or consideration not mentioned but likely needed"],
  "suggested_next_questions": [
    "Specific clarifying question that would help finalise the scope"
  ],
  "ai_integration_notes": "If AI features are involved, specific observations and recommendations",
  "scope_risks_summary": "One sentence characterising the overall risk profile of this scope"
}

Complexity guidelines:
- Low: brochure site or simple CRUD app, minimal integrations (1–6 weeks)
- Medium: standard web app with auth, integrations, moderate data model (6–16 weeks)
- High: complex platform, custom workflows, multiple integrations, real-time features (16–30 weeks)
- Enterprise: multi-tenant, compliance-heavy, bespoke platform, AI-core (30+ weeks)

Be direct. Flag real problems. Do not pad gaps_found with generic advice.
Return ONLY the JSON object."""


def _build_spec_content(scope: dict, sections: list, questions: list, answers: dict) -> str:
    """Build the text content describing the scope for AI analysis."""
    q_by_id = {q['id']: q for q in questions}
    lines = [
        f"PROJECT SCOPE DOCUMENT: {scope.get('title', 'Untitled')}",
        f"Client: {scope.get('client_name', 'Not specified')}",
        f"Project Type: {scope.get('project_type', 'Not specified')}",
        f"Status: {scope.get('status', 'draft')}",
        "",
    ]

    sec_q_map = {}
    for q in questions:
        sec_q_map.setdefault(q['section_id'], []).append(q)

    for sec in sections:
        lines.append(f"=== {sec['title']} ===")
        if sec.get('description'):
            lines.append(f"({sec['description']})")

        for q in sec_q_map.get(sec['id'], []):
            ans = answers.get(q['id'])
            ans_text = ''
            if ans:
                text_val = (ans.get('answer_text') or '').strip()
                multi_val = ans.get('answer_json_parsed') or []
                if multi_val:
                    ans_text = ', '.join(str(v) for v in multi_val)
                elif text_val:
                    ans_text = text_val

            req_marker = '[REQUIRED]' if q.get('required') else ''
            lines.append(f"Q: {q['label']} {req_marker}")
            lines.append(f"A: {ans_text or '(no answer provided)'}")
            lines.append("")

    return '\n'.join(lines)


def _spec_deepseek_analyse(content: str) -> tuple:
    """Call DeepSeek for spec analysis. Returns (result_dict, latency_ms)."""
    if not DEEPSEEK_API_KEY:
        return None, 0
    t0 = time.time()
    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': SPEC_AI_SYSTEM_PROMPT},
                    {'role': 'user', 'content': f"Analyse this software project scope:\n\n{content}"}
                ],
                'temperature': 0.2,
                'max_tokens': 2000,
            },
            timeout=60
        )
        ms = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            print(f"[SPEC/DS] HTTP {resp.status_code}: {resp.text[:200]}")
            return None, ms
        raw = resp.json()['choices'][0]['message']['content'].strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        result = json.loads(raw)
        print(f"[SPEC/DS] Analysis OK: {result.get('complexity_score')} ({ms}ms)")
        return result, ms
    except Exception as e:
        print(f"[SPEC/DS] Error: {e}")
        return None, int((time.time() - t0) * 1000)


def _spec_gemini_analyse(content: str) -> tuple:
    """Gemini fallback for spec analysis. Returns (result_dict, latency_ms)."""
    if not GEMINI_API_KEY:
        return None, 0
    prompt = (
        f"{SPEC_AI_SYSTEM_PROMPT}\n\n"
        f"Analyse this software project scope and return ONLY the JSON:\n\n{content}"
    )
    t0 = time.time()
    try:
        resp = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/'
            f'gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}',
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 2000}
            },
            timeout=45
        )
        ms = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            return None, ms
        raw = (resp.json()
               .get('candidates', [{}])[0]
               .get('content', {})
               .get('parts', [{}])[0]
               .get('text', '').strip())
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        result = json.loads(raw)
        print(f"[SPEC/GM] Fallback OK: {result.get('complexity_score')} ({ms}ms)")
        return result, ms
    except Exception as e:
        print(f"[SPEC/GM] Error: {e}")
        return None, int((time.time() - t0) * 1000)


def _spec_default_analysis(scope: dict, questions: list, answers: dict) -> dict:
    """Keyword-based fallback when all AI providers fail."""
    ans_count = sum(
        1 for q in questions
        if answers.get(q['id']) and (
            (answers[q['id']].get('answer_text') or '').strip() or
            (answers[q['id']].get('answer_json_parsed') or [])
        )
    )
    total_q = len(questions)
    completion_pct = (ans_count / total_q * 100) if total_q else 0

    if completion_pct < 30:
        complexity, weeks = 'Low', 4
    elif completion_pct < 60:
        complexity, weeks = 'Medium', 10
    else:
        complexity, weeks = 'High', 18

    return {
        'executive_summary': (
            f"Scope document for '{scope.get('title', 'this project')}' — "
            f"{ans_count}/{total_q} questions answered ({completion_pct:.0f}% complete). "
            f"AI analysis unavailable — manual review required."
        ),
        'complexity_score': complexity,
        'estimated_weeks': weeks,
        'confidence': 'Low',
        'stack_recommendation': 'To be determined after discovery call.',
        'phase_breakdown': [
            {'phase': 'Discovery & Design', 'weeks': max(1, weeks // 5), 'notes': 'TBC'},
            {'phase': 'Core Development',   'weeks': max(2, int(weeks * 0.6)), 'notes': 'TBC'},
            {'phase': 'Testing & QA',        'weeks': max(1, weeks // 5), 'notes': 'TBC'},
            {'phase': 'Launch & Handover',   'weeks': 1, 'notes': 'TBC'},
        ],
        'gaps_found': ['AI analysis unavailable — all gaps require manual review'],
        'risks': [{'risk': 'Incomplete scope', 'severity': 'Medium',
                   'mitigation': 'Schedule discovery call to complete outstanding questions'}],
        'contradictions': [],
        'must_have_features': [],
        'recommended_additions': [],
        'suggested_next_questions': ['Please book a discovery call to finalise scope.'],
        'ai_integration_notes': '',
        'scope_risks_summary': 'Risk profile cannot be assessed until scope is more complete.',
    }


def spec_analyse_with_fallback(scope_id: int, conn) -> dict:
    """
    Full analysis fallback chain: DeepSeek → Gemini → default.
    Saves result to spec_scope_ai_runs. Returns result dict.
    """
    spec = _load_spec(conn, scope_id)
    sections = _load_sections(conn, scope_id)
    questions = _load_questions(conn, scope_id)
    answers = _load_answers(conn, scope_id)

    content = _build_spec_content(spec, sections, questions, answers)

    result, ms, provider = None, 0, 'default'

    # DeepSeek
    result, ms = _spec_deepseek_analyse(content)
    if result and result.get('complexity_score'):
        provider = 'deepseek'
    else:
        # Gemini fallback
        result, ms = _spec_gemini_analyse(content)
        if result and result.get('complexity_score'):
            provider = 'gemini'
        else:
            result = _spec_default_analysis(spec, questions, answers)
            provider = 'default'
            ms = 0

    # Persist
    try:
        conn.execute(
            """INSERT INTO spec_scope_ai_runs (scope_id, provider, run_type, output_json, latency_ms)
               VALUES (?,?,?,?,?)""",
            (scope_id, provider, 'full_analysis', json.dumps(result), ms)
        )
        conn.commit()
    except Exception as e:
        print(f"[SPEC] Failed to save AI run: {e}")

    return result


# =========================================================================
# ROUTE REGISTRATION
# =========================================================================

def register_spec_routes(app: Flask):
    """Call from app.py after app is created."""

    # ── List all specs ─────────────────────────────────────────────────────

    @app.route('/studio/specs')
    def spec_list():
        conn = get_spec_db()
        specs = [dict(r) for r in conn.execute(
            "SELECT * FROM spec_scopes ORDER BY updated_at DESC"
        ).fetchall()]
        # Attach latest AI run and completion for each spec
        for spec in specs:
            ai = conn.execute(
                "SELECT * FROM spec_scope_ai_runs WHERE scope_id=? ORDER BY created_at DESC LIMIT 1",
                (spec['id'],)
            ).fetchone()
            if ai:
                ai_dict = dict(ai)
                try:
                    ai_dict['_output'] = json.loads(ai_dict.get('output_json') or '{}')
                except Exception:
                    ai_dict['_output'] = {}
                spec['latest_ai'] = ai_dict
            else:
                spec['latest_ai'] = None

            q_count = conn.execute(
                "SELECT COUNT(*) FROM spec_scope_questions WHERE scope_id=?", (spec['id'],)
            ).fetchone()[0]
            a_count = conn.execute(
                """SELECT COUNT(*) FROM spec_scope_answers sa
                   JOIN spec_scope_questions sq ON sa.question_id=sq.id
                   WHERE sa.scope_id=? AND (sa.answer_text!='' OR sa.answer_json!='[]')""",
                (spec['id'],)
            ).fetchone()[0]
            spec['total_questions'] = q_count
            spec['answered_questions'] = a_count

        conn.close()
        return render_template('studio/specs/list.html',
                               specs=specs, active_nav='specs')

    # ── New spec form ──────────────────────────────────────────────────────

    @app.route('/studio/specs/new', methods=['GET', 'POST'])
    def spec_new():
        conn = get_spec_db()

        if request.method == 'GET':
            quote_ref = request.args.get('quote_ref', '')
            quote = None
            if quote_ref:
                try:
                    quote = conn.execute(
                        "SELECT ref, project_name, contact_name, company_name, project_type "
                        "FROM quote_requests WHERE ref=?", (quote_ref,)
                    ).fetchone()
                    if quote:
                        quote = dict(quote)
                except Exception:
                    pass
            # List of quotes for dropdown
            try:
                quotes = [dict(r) for r in conn.execute(
                    "SELECT ref, project_name, contact_name, status FROM quote_requests "
                    "WHERE status NOT IN ('ARCHIVED') ORDER BY created_at DESC LIMIT 50"
                ).fetchall()]
            except Exception:
                quotes = []
            conn.close()
            return render_template('studio/specs/new.html',
                                   quote=quote, quotes=quotes,
                                   quote_ref=quote_ref,
                                   active_nav='specs')

        # POST — create spec
        title       = request.form.get('title', '').strip() or 'Untitled Spec'
        quote_ref   = request.form.get('quote_ref', '').strip()
        client_name = request.form.get('client_name', '').strip()
        project_type = request.form.get('project_type', '').strip()
        description = request.form.get('description', '').strip()

        cursor = conn.execute(
            """INSERT INTO spec_scopes (title, quote_ref, client_name, project_type, description)
               VALUES (?,?,?,?,?)""",
            (title, quote_ref, client_name, project_type, description)
        )
        scope_id = cursor.lastrowid
        conn.commit()

        # Seed default sections + questions
        create_default_sections(conn, scope_id)

        conn.close()
        return redirect(f'/studio/specs/{scope_id}')

    # ── Builder ────────────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>')
    def spec_builder(spec_id):
        conn = get_spec_db()
        spec = _load_spec(conn, spec_id)
        if not spec:
            conn.close()
            return 'Spec not found', 404

        sections  = _load_sections(conn, spec_id)
        questions = _load_questions(conn, spec_id)
        answers   = _load_answers(conn, spec_id)

        # Group questions by section
        q_by_section = {}
        for q in questions:
            q_by_section.setdefault(q['section_id'], []).append(q)

        # Completion state
        completion, total_q, answered_q = _count_completion(sections, questions, answers)

        # Latest AI run
        ai_run = conn.execute(
            "SELECT * FROM spec_scope_ai_runs WHERE scope_id=? ORDER BY created_at DESC LIMIT 1",
            (spec_id,)
        ).fetchone()
        latest_ai = None
        if ai_run:
            latest_ai = dict(ai_run)
            try:
                latest_ai['output'] = json.loads(latest_ai.get('output_json') or '{}')
            except Exception:
                latest_ai['output'] = {}

        # Link back to quote if set
        linked_quote = None
        if spec.get('quote_ref'):
            try:
                q = conn.execute(
                    "SELECT ref, project_name, status FROM quote_requests WHERE ref=?",
                    (spec['quote_ref'],)
                ).fetchone()
                if q:
                    linked_quote = dict(q)
            except Exception:
                pass

        conn.close()

        # Role detection — admin sees editing controls, client sees view-only
        is_admin = session.get('dx_role') in ('admin', None)  # None = no auth active = dev mode

        return render_template(
            'studio/specs/builder.html',
            spec=spec,
            sections=sections,
            questions=questions,
            q_by_section=q_by_section,
            answers=answers,
            completion=completion,
            total_q=total_q,
            answered_q=answered_q,
            latest_ai=latest_ai,
            linked_quote=linked_quote,
            active_nav='specs',
            is_admin=is_admin,
        )

    # ── Autosave answer ────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/autosave', methods=['POST'])
    def spec_autosave(spec_id):
        data = request.get_json(silent=True) or {}
        question_id = data.get('question_id')
        answer_text = data.get('answer_text', '')
        answer_json_val = data.get('answer_json', [])

        if not question_id:
            return jsonify({'ok': False, 'error': 'question_id required'}), 400

        conn = get_spec_db()
        try:
            conn.execute(
                """INSERT INTO spec_scope_answers (scope_id, question_id, answer_text, answer_json, answered_at)
                   VALUES (?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(scope_id, question_id) DO UPDATE SET
                     answer_text=excluded.answer_text,
                     answer_json=excluded.answer_json,
                     answered_at=CURRENT_TIMESTAMP""",
                (spec_id, question_id, str(answer_text), json.dumps(answer_json_val))
            )
            conn.execute(
                "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
            )
            conn.commit()

            # Re-count completion for the spec
            questions = _load_questions(conn, spec_id)
            answers   = _load_answers(conn, spec_id)
            sections  = _load_sections(conn, spec_id)
            completion, total_q, answered_q = _count_completion(sections, questions, answers)

            # Which section does this question belong to?
            q = conn.execute("SELECT section_id FROM spec_scope_questions WHERE id=?", (question_id,)).fetchone()
            sec_id = q['section_id'] if q else None
            sec_completion = completion.get(sec_id, {}) if sec_id else {}

        finally:
            conn.close()

        return jsonify({
            'ok': True,
            'total_q': total_q,
            'answered_q': answered_q,
            'section_status': sec_completion.get('status', 'empty'),
            'section_answered': sec_completion.get('answered', 0),
            'section_total': sec_completion.get('total', 0),
        })

    # ── AI Analysis ────────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/analyse', methods=['POST'])
    def spec_analyse(spec_id):
        conn = get_spec_db()
        spec = _load_spec(conn, spec_id)
        if not spec:
            conn.close()
            return jsonify({'ok': False, 'error': 'Spec not found'}), 404

        try:
            result = spec_analyse_with_fallback(spec_id, conn)
        except Exception as e:
            conn.close()
            return jsonify({'ok': False, 'error': str(e)}), 500

        conn.close()
        return jsonify({'ok': True, 'result': result})

    # ── Export HTML ────────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/export')
    def spec_export(spec_id):
        conn = get_spec_db()
        spec = _load_spec(conn, spec_id)
        if not spec:
            conn.close()
            return 'Spec not found', 404

        sections  = _load_sections(conn, spec_id)
        questions = _load_questions(conn, spec_id)
        answers   = _load_answers(conn, spec_id)

        q_by_section = {}
        for q in questions:
            q_by_section.setdefault(q['section_id'], []).append(q)

        completion, total_q, answered_q = _count_completion(sections, questions, answers)

        ai_run = conn.execute(
            "SELECT * FROM spec_scope_ai_runs WHERE scope_id=? ORDER BY created_at DESC LIMIT 1",
            (spec_id,)
        ).fetchone()
        latest_ai = None
        if ai_run:
            latest_ai = dict(ai_run)
            try:
                latest_ai['output'] = json.loads(latest_ai.get('output_json') or '{}')
            except Exception:
                latest_ai['output'] = {}

        conn.close()
        return render_template(
            'studio/specs/export.html',
            spec=spec,
            sections=sections,
            q_by_section=q_by_section,
            answers=answers,
            completion=completion,
            total_q=total_q,
            answered_q=answered_q,
            latest_ai=latest_ai,
            now=datetime.now,
        )

    # ── Update spec status ─────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/status', methods=['POST'])
    def spec_update_status(spec_id):
        data   = request.get_json(silent=True) or {}
        status = data.get('status', '').lower()
        valid  = ('draft', 'in_review', 'final', 'archived')
        if status not in valid:
            return jsonify({'ok': False, 'error': f'Invalid status. Must be one of: {valid}'}), 400
        conn = get_spec_db()
        conn.execute(
            "UPDATE spec_scopes SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, spec_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'status': status})

    # ── Update spec title / meta ───────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/update', methods=['POST'])
    def spec_update_meta(spec_id):
        data = request.get_json(silent=True) or {}
        conn = get_spec_db()
        conn.execute(
            """UPDATE spec_scopes SET title=?, client_name=?, project_type=?,
               description=?, quote_ref=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                data.get('title', '').strip() or 'Untitled Spec',
                data.get('client_name', '').strip(),
                data.get('project_type', '').strip(),
                data.get('description', '').strip(),
                data.get('quote_ref', '').strip(),
                spec_id,
            )
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True})

    # ── Sections CRUD ──────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/sections', methods=['POST'])
    def spec_add_section(spec_id):
        data = request.get_json(silent=True) or {}
        title = data.get('title', '').strip()
        if not title:
            return jsonify({'ok': False, 'error': 'Title required'}), 400

        conn = get_spec_db()
        max_order = conn.execute(
            "SELECT MAX(order_idx) FROM spec_scope_sections WHERE scope_id=?", (spec_id,)
        ).fetchone()[0]
        new_order = (max_order or 0) + 1

        cursor = conn.execute(
            "INSERT INTO spec_scope_sections (scope_id, title, description, order_idx) VALUES (?,?,?,?)",
            (spec_id, title, data.get('description', ''), new_order)
        )
        section_id = cursor.lastrowid
        conn.execute(
            "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'section_id': section_id, 'order_idx': new_order})

    @app.route('/studio/specs/<int:spec_id>/sections/<int:section_id>', methods=['POST'])
    def spec_update_section(spec_id, section_id):
        data = request.get_json(silent=True) or {}
        action = data.get('action', 'update')

        conn = get_spec_db()
        if action == 'delete':
            conn.execute(
                "DELETE FROM spec_scope_sections WHERE id=? AND scope_id=?",
                (section_id, spec_id)
            )
            conn.execute(
                "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
            )
            conn.commit()
            conn.close()
            return jsonify({'ok': True, 'deleted': True})

        if action == 'reorder':
            new_order = data.get('order_idx')
            if new_order is not None:
                conn.execute(
                    "UPDATE spec_scope_sections SET order_idx=? WHERE id=? AND scope_id=?",
                    (new_order, section_id, spec_id)
                )
                conn.commit()
            conn.close()
            return jsonify({'ok': True})

        # Default: update title/description
        title = data.get('title', '').strip()
        if title:
            conn.execute(
                "UPDATE spec_scope_sections SET title=?, description=? WHERE id=? AND scope_id=?",
                (title, data.get('description', ''), section_id, spec_id)
            )
            conn.execute(
                "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
            )
            conn.commit()
        conn.close()
        return jsonify({'ok': True})

    # ── Questions CRUD ─────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/questions', methods=['POST'])
    def spec_add_question(spec_id):
        data       = request.get_json(silent=True) or {}
        section_id = data.get('section_id')
        label      = data.get('label', '').strip()
        if not section_id or not label:
            return jsonify({'ok': False, 'error': 'section_id and label required'}), 400

        conn = get_spec_db()
        max_order = conn.execute(
            "SELECT MAX(order_idx) FROM spec_scope_questions WHERE section_id=? AND scope_id=?",
            (section_id, spec_id)
        ).fetchone()[0]
        new_order = (max_order or 0) + 1

        valid_types = ('text', 'textarea', 'radio', 'checkbox', 'select',
                       'chips', 'date', 'yes_no', 'priority', 'risk_level')
        field_type = data.get('field_type', 'textarea')
        if field_type not in valid_types:
            field_type = 'textarea'

        options = data.get('options', [])
        cursor = conn.execute(
            """INSERT INTO spec_scope_questions
               (scope_id, section_id, label, field_type, placeholder, help_text,
                internal_note, required, order_idx, options_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                spec_id, section_id, label, field_type,
                data.get('placeholder', ''), data.get('help_text', ''),
                data.get('internal_note', ''),
                1 if data.get('required') else 0,
                new_order,
                json.dumps(options if isinstance(options, list) else []),
            )
        )
        question_id = cursor.lastrowid
        conn.execute(
            "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'question_id': question_id})

    @app.route('/studio/specs/<int:spec_id>/questions/<int:question_id>', methods=['POST'])
    def spec_update_question(spec_id, question_id):
        data   = request.get_json(silent=True) or {}
        action = data.get('action', 'update')

        conn = get_spec_db()
        if action == 'delete':
            conn.execute(
                "DELETE FROM spec_scope_questions WHERE id=? AND scope_id=?",
                (question_id, spec_id)
            )
            conn.execute(
                "DELETE FROM spec_scope_answers WHERE question_id=? AND scope_id=?",
                (question_id, spec_id)
            )
            conn.execute(
                "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
            )
            conn.commit()
            conn.close()
            return jsonify({'ok': True, 'deleted': True})

        # Update
        valid_types = ('text', 'textarea', 'radio', 'checkbox', 'select',
                       'chips', 'date', 'yes_no', 'priority', 'risk_level')
        field_type = data.get('field_type', 'textarea')
        if field_type not in valid_types:
            field_type = 'textarea'

        label = data.get('label', '').strip()
        if not label:
            conn.close()
            return jsonify({'ok': False, 'error': 'label required'}), 400

        options = data.get('options', [])
        conn.execute(
            """UPDATE spec_scope_questions SET
               label=?, field_type=?, placeholder=?, help_text=?,
               internal_note=?, required=?, options_json=?
               WHERE id=? AND scope_id=?""",
            (
                label, field_type,
                data.get('placeholder', ''), data.get('help_text', ''),
                data.get('internal_note', ''),
                1 if data.get('required') else 0,
                json.dumps(options if isinstance(options, list) else []),
                question_id, spec_id,
            )
        )
        conn.execute(
            "UPDATE spec_scopes SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (spec_id,)
        )
        conn.commit()
        conn.close()
        return jsonify({'ok': True})

    # ── Delete spec ────────────────────────────────────────────────────────

    @app.route('/studio/specs/<int:spec_id>/delete', methods=['POST'])
    def spec_delete(spec_id):
        conn = get_spec_db()
        conn.execute("DELETE FROM spec_scopes WHERE id=?", (spec_id,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'redirect': '/studio/specs'})

    print("  ✓ Spec Scope Builder routes registered")
