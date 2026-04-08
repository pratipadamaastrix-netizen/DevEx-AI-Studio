-- =========================================================================
-- DEVEX AI STUDIOS — SPEC SCOPE BUILDER
-- Project/schema_spec.sql
-- =========================================================================
-- All tables stored in engine.db alongside quote_requests, artefacts, etc.
-- Run via run_spec_migrations() called from init_db() in app.py.
-- =========================================================================

-- ── Parent record: one per software project scope document ────────────────
CREATE TABLE IF NOT EXISTS spec_scopes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT 'Untitled Spec',
    quote_ref   TEXT    DEFAULT '',      -- soft-link to quote_requests.ref
    client_name TEXT    DEFAULT '',
    project_type TEXT   DEFAULT '',
    description TEXT    DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','in_review','final','archived')),
    created_by  TEXT    DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Ordered sections within a spec ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS spec_scope_sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id    INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    order_idx   INTEGER DEFAULT 0,
    visible     INTEGER DEFAULT 1,      -- 1=visible, 0=hidden
    FOREIGN KEY (scope_id) REFERENCES spec_scopes(id) ON DELETE CASCADE
);

-- ── Questions within sections ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spec_scope_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id        INTEGER NOT NULL,
    section_id      INTEGER NOT NULL,
    label           TEXT    NOT NULL,
    field_type      TEXT    NOT NULL DEFAULT 'textarea'
                    CHECK(field_type IN (
                        'text','textarea','radio','checkbox',
                        'select','chips','date','yes_no',
                        'priority','risk_level'
                    )),
    placeholder     TEXT    DEFAULT '',
    help_text       TEXT    DEFAULT '',
    internal_note   TEXT    DEFAULT '',
    required        INTEGER DEFAULT 0,
    order_idx       INTEGER DEFAULT 0,
    options_json    TEXT    DEFAULT '[]',  -- options for radio/checkbox/select/chips
    FOREIGN KEY (scope_id)   REFERENCES spec_scopes(id)          ON DELETE CASCADE,
    FOREIGN KEY (section_id) REFERENCES spec_scope_sections(id)  ON DELETE CASCADE
);

-- ── Saved answers per question ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spec_scope_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id    INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    answer_text TEXT    DEFAULT '',     -- primary answer value
    answer_json TEXT    DEFAULT '[]',   -- multi-value answers (chips/checkbox)
    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope_id, question_id),
    FOREIGN KEY (scope_id)    REFERENCES spec_scopes(id)           ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES spec_scope_questions(id)  ON DELETE CASCADE
);

-- ── AI analysis runs ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spec_scope_ai_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id    INTEGER NOT NULL,
    provider    TEXT    NOT NULL,       -- 'deepseek' | 'gemini' | 'default'
    run_type    TEXT    NOT NULL DEFAULT 'full_analysis',
    output_json TEXT    DEFAULT '{}',   -- full structured AI output
    error_detail TEXT   DEFAULT '',
    latency_ms  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scope_id) REFERENCES spec_scopes(id) ON DELETE CASCADE
);

-- ── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_spec_scopes_status     ON spec_scopes(status);
CREATE INDEX IF NOT EXISTS idx_spec_scopes_quote_ref  ON spec_scopes(quote_ref);
CREATE INDEX IF NOT EXISTS idx_spec_sections_scope    ON spec_scope_sections(scope_id);
CREATE INDEX IF NOT EXISTS idx_spec_questions_scope   ON spec_scope_questions(scope_id);
CREATE INDEX IF NOT EXISTS idx_spec_questions_section ON spec_scope_questions(section_id);
CREATE INDEX IF NOT EXISTS idx_spec_answers_scope     ON spec_scope_answers(scope_id);
CREATE INDEX IF NOT EXISTS idx_spec_ai_runs_scope     ON spec_scope_ai_runs(scope_id);
