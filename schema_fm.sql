-- =========================================================================
-- FM OPERATIONS MODULE SCHEMA
-- =========================================================================
-- Facilities Management ticket system integrated into the contractor framework
-- Stored in engine.db alongside artefacts/modules
-- Version: FM1.0
-- Batch: FM1.1
-- =========================================================================

-- FM TICKETS: One row per reported issue
CREATE TABLE IF NOT EXISTS fm_tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT NOT NULL UNIQUE,          -- FM-DDMMYY-XXXXX
    estate      TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL DEFAULT '',
    customer    TEXT NOT NULL DEFAULT '',
    phone       TEXT DEFAULT '',              
    address     TEXT DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'webchat'
                    CHECK(source IN ('webchat','whatsapp','whatsapp_json','system','manual')),
    priority    TEXT NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('urgent','normal','low')),
    category    TEXT NOT NULL DEFAULT 'general'
                    CHECK(category IN ('general','electrical','plumbing','hvac',
                                       'security','carpentry','cleaning','painting','pest_control')),
    status      TEXT NOT NULL DEFAULT 'NEW'
                    CHECK(status IN ('NEW','TRIAGED','ASSIGNED','IN_PROGRESS',
                                     'WAITING_CUSTOMER','BLOCKED','DONE','CANCELLED')),
    assignee    TEXT DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    materials   TEXT DEFAULT '',
    location_note TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FM CONVERSATIONS: Per-ticket message thread
CREATE TABLE IF NOT EXISTS fm_conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_ref  TEXT NOT NULL,
    sender      TEXT NOT NULL DEFAULT 'customer'
                    CHECK(sender IN ('customer','staff','ai','system')),
    body        TEXT NOT NULL,
    source      TEXT DEFAULT 'webchat',        -- webchat, whatsapp, staff, ai
    is_internal BOOLEAN DEFAULT 0,             -- 1 = internal note, not visible to customer
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_ref) REFERENCES fm_tickets(ref) ON DELETE CASCADE
);

-- FM INBOUND EVENTS: Webhook / push audit log
CREATE TABLE IF NOT EXISTS fm_inbound_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT UNIQUE NOT NULL,          -- SHA256 dedup key or provided eventId
    source      TEXT NOT NULL DEFAULT 'webchat'
                    CHECK(source IN ('webchat','whatsapp','whatsapp_json','ai','gateway','system','manual')),
    event_type  TEXT NOT NULL,                 -- message.inbound, draft.generated, ticket.created, etc.
    ticket_ref  TEXT,                          -- NULL if not yet resolved to a ticket
    payload_json TEXT,                         -- Raw inbound payload for audit
    status      TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','processed','duplicate','error','retry')),
    retry_count INTEGER DEFAULT 0,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    FOREIGN KEY (ticket_ref) REFERENCES fm_tickets(ref)
);

-- FM EVIDENCE: Photo references per ticket
CREATE TABLE IF NOT EXISTS fm_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_ref  TEXT NOT NULL,
    filename    TEXT NOT NULL,                 -- UUID-based stored filename
    original_filename TEXT,
    file_path   TEXT NOT NULL,                 -- Relative path in evidence/fm/
    file_size   INTEGER,
    mime_type   TEXT,
    caption     TEXT DEFAULT '',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_ref) REFERENCES fm_tickets(ref) ON DELETE CASCADE
);

-- =========================================================================
-- INDEXES
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_fm_tickets_status   ON fm_tickets(status);
CREATE INDEX IF NOT EXISTS idx_fm_tickets_priority ON fm_tickets(priority);
CREATE INDEX IF NOT EXISTS idx_fm_tickets_estate   ON fm_tickets(estate);
CREATE INDEX IF NOT EXISTS idx_fm_tickets_updated  ON fm_tickets(updated_at);
CREATE INDEX IF NOT EXISTS idx_fm_conv_ref         ON fm_conversations(ticket_ref);
CREATE INDEX IF NOT EXISTS idx_fm_events_ref       ON fm_inbound_events(ticket_ref);
CREATE INDEX IF NOT EXISTS idx_fm_events_received  ON fm_inbound_events(received_at);
CREATE INDEX IF NOT EXISTS idx_fm_evidence_ref     ON fm_evidence(ticket_ref);

-- =========================================================================
-- TRIGGERS: keep updated_at fresh on fm_tickets
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS trg_fm_tickets_updated
AFTER UPDATE ON fm_tickets
BEGIN
    UPDATE fm_tickets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
