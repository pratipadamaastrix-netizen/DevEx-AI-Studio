-- =========================================================================
-- DEVEX AI STUDIOS — MEETING ROOMS
-- Project/schema_rooms.sql
-- =========================================================================
-- Stored in engine.db. Separate from spec_scopes.
-- A Meeting Room is a collaboration workspace for a project.
-- =========================================================================

CREATE TABLE IF NOT EXISTS meeting_rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT 'Untitled Room',
    subtitle    TEXT    DEFAULT 'Client Meet Room',
    quote_ref   TEXT    DEFAULT '',
    client_name TEXT    DEFAULT '',
    project_type TEXT   DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','in_clarification','scoped','in_build','delivered')),
    created_by  TEXT    DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meeting_room_sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL,
    group_name  TEXT    NOT NULL DEFAULT '',    -- e.g. Architecture, Products, Commercial
    title       TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    order_idx   INTEGER DEFAULT 0,
    FOREIGN KEY (room_id) REFERENCES meeting_rooms(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_room_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         INTEGER NOT NULL,
    section_id      INTEGER NOT NULL,
    code            TEXT    DEFAULT '',          -- e.g. A1, B2, C3
    label           TEXT    NOT NULL,
    context         TEXT    DEFAULT '',          -- explanatory context shown under label
    field_type      TEXT    NOT NULL DEFAULT 'options'
                    CHECK(field_type IN ('options','checklist','textarea','text','date')),
    tag_text        TEXT    DEFAULT '',          -- e.g. Blocker, Confirm, TBC
    tag_class       TEXT    DEFAULT '',          -- e.g. tag-blocker, tag-required, tag-tbc
    callout_text    TEXT    DEFAULT '',          -- callout box text
    callout_type    TEXT    DEFAULT '',          -- amber, red, blue, green
    options_json    TEXT    DEFAULT '[]',        -- for options/checklist items
    required        INTEGER DEFAULT 0,
    order_idx       INTEGER DEFAULT 0,
    FOREIGN KEY (room_id)    REFERENCES meeting_rooms(id)          ON DELETE CASCADE,
    FOREIGN KEY (section_id) REFERENCES meeting_room_sections(id)  ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_room_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    selected_option TEXT DEFAULT '',    -- selected radio option text
    checked_json    TEXT DEFAULT '[]',  -- checked checklist items
    notes_json      TEXT DEFAULT '{}',  -- {field_key: value} for text/textarea fields
    status          TEXT DEFAULT 'empty' CHECK(status IN ('empty','partial','answered')),
    answered_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(room_id, question_id),
    FOREIGN KEY (room_id)     REFERENCES meeting_rooms(id)            ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES meeting_room_questions(id)   ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mr_sections_room ON meeting_room_sections(room_id);
CREATE INDEX IF NOT EXISTS idx_mr_questions_room ON meeting_room_questions(room_id);
CREATE INDEX IF NOT EXISTS idx_mr_questions_section ON meeting_room_questions(section_id);
CREATE INDEX IF NOT EXISTS idx_mr_answers_room ON meeting_room_answers(room_id);
