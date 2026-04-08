-- =========================================================================
-- WHATSAPP BRIDGE SCHEMA
-- =========================================================================
-- Buffers inbound Twilio WhatsApp messages per sender until ready to flush
-- to DeepSeek for classification and FM ticket creation.
-- Stored in engine.db alongside FM tables.
-- Version: WA1.0
-- =========================================================================

-- WA SESSIONS: One row per active conversation (sender phone number)
-- A session accumulates messages until flushed to DeepSeek.
-- After flush, status moves to 'processing' then 'done'.
CREATE TABLE IF NOT EXISTS wa_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_from         TEXT NOT NULL,          -- E.164 e.g. whatsapp:+2348012345678
    wa_to           TEXT NOT NULL,          -- Your Twilio WhatsApp number
    display_name    TEXT DEFAULT '',        -- Twilio ProfileName if available
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','processing','done','error')),
    ticket_ref      TEXT,                   -- FM ticket ref once created
    message_count   INTEGER DEFAULT 0,
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    flushed_at      TIMESTAMP,
    flush_trigger   TEXT DEFAULT '',        -- 'timeout','keyword','count','manual'
    deepseek_response TEXT,                 -- Raw DeepSeek JSON response for audit
    error_detail    TEXT DEFAULT '',
    FOREIGN KEY (ticket_ref) REFERENCES fm_tickets(ref)
);

-- WA MESSAGES: Individual messages within a session
CREATE TABLE IF NOT EXISTS wa_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    direction   TEXT NOT NULL DEFAULT 'inbound'
                    CHECK(direction IN ('inbound','outbound')),
    body        TEXT NOT NULL,
    media_url   TEXT DEFAULT '',           -- Twilio media URL if photo sent
    media_type  TEXT DEFAULT '',
    twilio_sid  TEXT UNIQUE,               -- Twilio MessageSid for dedup
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES wa_sessions(id) ON DELETE CASCADE
);

-- =========================================================================
-- INDEXES
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_wa_sessions_from    ON wa_sessions(wa_from);
CREATE INDEX IF NOT EXISTS idx_wa_sessions_status  ON wa_sessions(status);
CREATE INDEX IF NOT EXISTS idx_wa_sessions_last    ON wa_sessions(last_message_at);
CREATE INDEX IF NOT EXISTS idx_wa_messages_session ON wa_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_wa_messages_sid     ON wa_messages(twilio_sid);
