-- =========================================================================
-- CONTRACTOR QUOTATION FRAMEWORK - ENGINE SCHEMA
-- =========================================================================
-- Engine-centric architecture for contractor quotation work
-- - artefacts = single source of truth for all quotation/assessment work
-- - modules = pluggable UI panels that populate sections of the payload
-- - One save/version/export/share pipeline for everything
-- =========================================================================
-- Database: engine.db
-- Version: 0.2.1 (CF1.1)
-- =========================================================================

-- CORE ENGINE: Artefacts (Container for all quotation/assessment work)
CREATE TABLE IF NOT EXISTS artefacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_type TEXT NOT NULL CHECK(artefact_type IN (
        'engagement',      -- Site inspection, risk assessment, compliance
        'incident',        -- Post mortem, incident response
        'assessment',      -- Security audit, compliance review
        'investigation'    -- Incident case, root cause review
    )),
    title TEXT NOT NULL,
    client_id INTEGER,
    status TEXT DEFAULT 'Draft' CHECK(status IN (
        'Draft', 'In Review', 'Final'
    )),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

-- CORE ENGINE: Version History (Immutable snapshots)
CREATE TABLE IF NOT EXISTS artefact_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    payload_json TEXT NOT NULL,        -- Complete structured data
    rendered_html TEXT,                -- Cached report HTML (server-generated canonical)
    version_notes TEXT,
    schema_version TEXT DEFAULT '1.0', -- For future payload evolution
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE,
    UNIQUE(artefact_id, version_no)
);

-- CORE ENGINE: Share Links (Public access to versions)
CREATE TABLE IF NOT EXISTS artefact_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_id INTEGER NOT NULL,
    version_id INTEGER NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES artefact_versions(id) ON DELETE CASCADE
);

-- CORE ENGINE: Evidence Files (Screenshots, logs, documents, etc.)
CREATE TABLE IF NOT EXISTS evidence_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_id INTEGER NOT NULL,
    filename TEXT NOT NULL,            -- Stored filename (UUID-based)
    original_filename TEXT NOT NULL,   -- User's original filename
    file_type TEXT,                    -- 'screenshot', 'log', 'pcap', 'document', 'network_trace'
    file_path TEXT NOT NULL,           -- Relative path in evidence/ directory
    file_size INTEGER,
    mime_type TEXT,
    notes TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    uploaded_by TEXT,
    FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE
);

-- MODULE REGISTRY: Available Modules
CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_key TEXT NOT NULL UNIQUE,   -- internal keys kept for compatibility
    module_name TEXT NOT NULL,         -- 'Risk Assessment', 'Site Inspection'
    description TEXT,
    icon TEXT,                         -- Icon class or identifier
    enabled_by_default BOOLEAN DEFAULT 0,
    display_order INTEGER DEFAULT 0
);

-- MODULE REGISTRY: Artefact-Module Associations
CREATE TABLE IF NOT EXISTS artefact_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artefact_id INTEGER NOT NULL,
    module_key TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    config_json TEXT,                  -- Module-specific configuration
    FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE,
    FOREIGN KEY (module_key) REFERENCES modules(module_key),
    UNIQUE(artefact_id, module_key)
);

-- =========================================================================
-- SHARED RESOURCES - Clients and Services
-- =========================================================================
-- These are shared resources used across the contractor framework
-- Placed in engine.db as they are general business entities
-- =========================================================================

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    organisation TEXT,
    address TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    unit_price DECIMAL,
    unit_type TEXT DEFAULT 'hour',  -- hour, item, day
    active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- INDEXES: Performance optimization
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_artefacts_type ON artefacts(artefact_type);
CREATE INDEX IF NOT EXISTS idx_artefacts_client ON artefacts(client_id);
CREATE INDEX IF NOT EXISTS idx_artefacts_status ON artefacts(status);
CREATE INDEX IF NOT EXISTS idx_artefact_versions_artefact ON artefact_versions(artefact_id);

-- =========================================================================
-- SEED DATA: Core Modules
-- =========================================================================

INSERT OR IGNORE INTO modules (module_key, module_name, description, icon, display_order) VALUES
('risk_assessment', 'Risk Assessment', 'SLA + Safety triage, hazards, mitigations', 'clipboard', 1),
('site_inspection', 'Site Inspection', 'Findings, photos, defects, recommended actions', 'search', 2),
('contractor_review', 'Contractor Review', 'Timeline, root cause, corrective actions, rework prevention', 'clipboard', 3),
('incident_case', 'Incident Case', 'Case notes, updates, actions, tenant comms', 'alert', 4);
