-- =========================================================================
-- DEVEX STUDIOS — PROJECT REQUIREMENTS PORTAL
-- schema_quote.sql
-- =========================================================================
-- Stored in engine.db alongside fm_tickets, artefacts, clients.
-- Version: QP1.0
-- =========================================================================

-- =========================================================================
-- QUOTE REQUESTS
-- One row per submitted project requirement intake form.
-- Status pipeline: NEW → REVIEWING → SCOPED → SENT → WON | LOST | ARCHIVED
-- =========================================================================

CREATE TABLE IF NOT EXISTS quote_requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ref                 TEXT NOT NULL UNIQUE,           -- REQ-DDMMYY-XXXXX

    -- ── Pipeline status ──────────────────────────────────────────────────
    status              TEXT NOT NULL DEFAULT 'NEW'
                            CHECK(status IN (
                                'NEW',          -- Just submitted
                                'REVIEWING',    -- Being looked at by team
                                'SCOPED',       -- AI + manual scope done
                                'SENT',         -- Proposal sent to client
                                'WON',          -- Client accepted
                                'LOST',         -- Client declined
                                'ARCHIVED'      -- No longer active
                            )),

    -- ── Contact details ──────────────────────────────────────────────────
    contact_name        TEXT NOT NULL DEFAULT '',
    contact_email       TEXT NOT NULL DEFAULT '',
    contact_phone       TEXT DEFAULT '',
    company_name        TEXT DEFAULT '',

    -- ── Project overview ─────────────────────────────────────────────────
    project_name        TEXT DEFAULT '',
    project_type        TEXT DEFAULT 'website'
                            CHECK(project_type IN (
                                'website', 'mobile_app', 'web_app',
                                'both', 'ecommerce', 'portal', 'other'
                            )),
    brief_description   TEXT DEFAULT '',

    -- ── Goals (JSON array of strings) ────────────────────────────────────
    -- e.g. ["lead_gen","branding","booking"]
    goals_json          TEXT DEFAULT '[]',

    -- ── Business context ─────────────────────────────────────────────────
    business_overview   TEXT DEFAULT '',
    target_audience     TEXT DEFAULT '',
    competitors         TEXT DEFAULT '',
    usp                 TEXT DEFAULT '',

    -- ── Features (JSON object) ───────────────────────────────────────────
    -- { "must_have": ["login","search","payments"], "optional": ["chat","analytics"] }
    features_json       TEXT DEFAULT '{"must_have":[],"optional":[]}',

    -- ── Website requirements (JSON) ──────────────────────────────────────
    -- { "type": "brochure", "pages": 8, "cms": true, "seo": "advanced" }
    website_json        TEXT DEFAULT '{}',

    -- ── Mobile app requirements (JSON) ───────────────────────────────────
    -- { "platform": "both", "dev_type": "hybrid", "offline": false, "push": true }
    mobile_json         TEXT DEFAULT '{}',

    -- ── Design & content (JSON) ──────────────────────────────────────────
    -- { "has_branding": true, "style": "modern", "content_provided": false }
    design_json         TEXT DEFAULT '{}',

    -- ── Technical requirements (JSON) ────────────────────────────────────
    -- { "preferred_tech": "React + Node", "hosting": "AWS", "integrations": ["Stripe","HubSpot"] }
    tech_json           TEXT DEFAULT '{}',

    -- ── Timeline & budget ────────────────────────────────────────────────
    budget_range        TEXT DEFAULT '',
    start_date          TEXT DEFAULT '',
    launch_date         TEXT DEFAULT '',

    -- ── Maintenance & support ─────────────────────────────────────────────
    maintenance_json    TEXT DEFAULT '{}',
    -- { "required": true, "type": "retainer", "sla": "24h", "scalability": "high" }

    -- ── Priority matrix (JSON array) ─────────────────────────────────────
    -- [{"feature": "Login", "priority": "Critical", "notes": "..."}]
    priority_matrix_json TEXT DEFAULT '[]',

    -- ── Discovery Q&A (JSON array) ───────────────────────────────────────
    -- [{"question": "...", "answer": "..."}]
    discovery_qa_json   TEXT DEFAULT '[]',

    -- ── AI outputs ───────────────────────────────────────────────────────
    ai_summary          TEXT DEFAULT '',        -- Gemini: human-readable scope summary
    ai_flags_json       TEXT DEFAULT '[]',      -- Gemini: ["No content provider","Budget mismatch risk"]
    ai_questions_json   TEXT DEFAULT '[]',      -- Gemini: follow-up questions generated mid-form

    deepseek_scope_json TEXT DEFAULT '{}',      -- DeepSeek: full structured scope response
    complexity_score    TEXT DEFAULT ''
                            CHECK(complexity_score IN (
                                '', 'Low', 'Medium', 'High', 'Enterprise'
                            )),
    estimated_weeks     INTEGER,                -- DeepSeek timeline estimate
    stack_recommendation TEXT DEFAULT '',       -- DeepSeek: recommended tech stack

    -- ── Internal ops ─────────────────────────────────────────────────────
    assignee            TEXT DEFAULT '',
    internal_notes      TEXT DEFAULT '',
    source              TEXT NOT NULL DEFAULT 'web'
                            CHECK(source IN ('web','email','manual','referral','api')),

    -- ── Timestamps ───────────────────────────────────────────────────────
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- QUOTE EVENTS
-- Immutable audit trail for every change/action on a quote request.
-- Mirrors fm_inbound_events pattern.
-- =========================================================================

CREATE TABLE IF NOT EXISTS quote_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,       -- SHA-256 dedup key
    quote_ref       TEXT,                       -- NULL if not yet linked to a request
    event_type      TEXT NOT NULL,
    -- quote.created | quote.updated | quote.status_changed
    -- ai.gemini.analyse | ai.deepseek.scope
    -- pdf.exported | email.sent
    source          TEXT NOT NULL DEFAULT 'web',
    payload_json    TEXT DEFAULT '{}',          -- Full event payload for audit
    status          TEXT NOT NULL DEFAULT 'processed'
                        CHECK(status IN ('processed','error','skipped')),
    error_detail    TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_ref) REFERENCES quote_requests(ref)
);

-- =========================================================================
-- QUOTE AI LOG
-- Stores every AI call input/output for debugging, replay, and cost tracking.
-- Separate from events so we can prune it independently.
-- =========================================================================

CREATE TABLE IF NOT EXISTS quote_ai_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_ref       TEXT,
    ai_provider     TEXT NOT NULL
                        CHECK(ai_provider IN ('gemini', 'deepseek')),
    call_type       TEXT NOT NULL,
    -- gemini: 'mid_form_suggestions' | 'scope_summary' | 'gap_analysis'
    -- deepseek: 'scope_analysis' | 'stack_recommendation' | 'complexity_score'
    prompt_json     TEXT DEFAULT '{}',          -- What we sent
    response_json   TEXT DEFAULT '{}',          -- What we got back
    tokens_used     INTEGER DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    success         BOOLEAN DEFAULT 1,
    error_detail    TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quote_ref) REFERENCES quote_requests(ref)
);

-- =========================================================================
-- INDEXES
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_quote_requests_status   ON quote_requests(status);
CREATE INDEX IF NOT EXISTS idx_quote_requests_ref      ON quote_requests(ref);
CREATE INDEX IF NOT EXISTS idx_quote_requests_email    ON quote_requests(contact_email);
CREATE INDEX IF NOT EXISTS idx_quote_requests_created  ON quote_requests(created_at);
CREATE INDEX IF NOT EXISTS idx_quote_events_ref        ON quote_events(quote_ref);
CREATE INDEX IF NOT EXISTS idx_quote_events_type       ON quote_events(event_type);
CREATE INDEX IF NOT EXISTS idx_quote_ai_log_ref        ON quote_ai_log(quote_ref);
CREATE INDEX IF NOT EXISTS idx_quote_ai_log_provider   ON quote_ai_log(ai_provider);
