-- Enhanced Fire Door Report Builder Schema
-- Photos stored in filesystem with metadata

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_title TEXT NOT NULL,
    quote_reference TEXT,
    site_name TEXT NOT NULL,
    site_address TEXT,
    client_name TEXT,
    inspector_name TEXT,
    inspection_date DATE,
    project_type TEXT DEFAULT 'fire_door',  -- fire_door, gardening, decoration, fence, other
    status TEXT DEFAULT 'draft',
    workflow_status TEXT DEFAULT 'Draft',  -- Draft, Sent to Contractor, Priced, Sent to Client, Approved
    profit_margin DECIMAL DEFAULT 20.0,
    contractor_prices_received BOOLEAN DEFAULT 0,
    client_total DECIMAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    property_name TEXT NOT NULL,
    property_address TEXT,
    project_type TEXT DEFAULT 'fire_door',  -- fire_door, gardening, decoration, fence, other
    site_plan_photo TEXT,  -- Filename in photos/
    site_plan_notes TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS internal_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL,
    location_name TEXT NOT NULL,  -- "Ground Floor Entrance", "Hallway (Upstairs)"
    location_photo TEXT,  -- Filename
    access_instructions TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE
);

-- Fire Door specific tables
CREATE TABLE IF NOT EXISTS fire_doors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_location_id INTEGER NOT NULL,
    door_reference TEXT NOT NULL,
    fd_rating TEXT NOT NULL,  -- FD30, FD60, FD90, FD120
    frame_condition TEXT,
    door_condition TEXT,
    seal_condition TEXT,
    closer_condition TEXT,
    gaps_ok BOOLEAN DEFAULT 0,
    intumescent_ok BOOLEAN DEFAULT 0,
    pass_fail TEXT DEFAULT 'Pass',
    notes TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (internal_location_id) REFERENCES internal_locations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fire_door_id INTEGER NOT NULL,
    work_item TEXT NOT NULL,  -- "Replace Door Seals"
    specification_scope TEXT,  -- Technical detail for contractor
    client_description TEXT,  -- Simplified for client
    mat_cost DECIMAL DEFAULT 0,
    lab_cost DECIMAL DEFAULT 0,
    contractor_notes TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (fire_door_id) REFERENCES fire_doors(id) ON DELETE CASCADE
);

-- Universal photo table (filesystem storage with metadata)
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,  -- Stored in photos/ directory
    original_filename TEXT,
    
    -- Metadata for organization
    site_name TEXT,
    internal_location TEXT,
    specs TEXT,  -- FD rating or other specs
    notes TEXT,
    
    -- Relationships (nullable - can belong to different entities)
    report_id INTEGER,
    property_id INTEGER,
    internal_location_id INTEGER,
    fire_door_id INTEGER,
    
    photo_type TEXT,  -- Legacy: 'site_plan', 'location', 'condition', 'work_item'
    photo_role TEXT DEFAULT 'door_evidence',  -- Phase 1: 'door_evidence', 'property_plan', 'location_access'
    caption TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (internal_location_id) REFERENCES internal_locations(id) ON DELETE CASCADE,
    FOREIGN KEY (fire_door_id) REFERENCES fire_doors(id) ON DELETE CASCADE
);

-- Gardening tables (for future)
CREATE TABLE IF NOT EXISTS garden_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_location_id INTEGER NOT NULL,
    area_name TEXT NOT NULL,
    area_type TEXT,  -- front, back, side
    work_required TEXT,
    notes TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (internal_location_id) REFERENCES internal_locations(id) ON DELETE CASCADE
);

-- Decoration tables (for future)
CREATE TABLE IF NOT EXISTS decoration_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    internal_location_id INTEGER NOT NULL,
    room_name TEXT NOT NULL,
    room_type TEXT,  -- bedroom, kitchen, living_room
    work_required TEXT,
    notes TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (internal_location_id) REFERENCES internal_locations(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_properties_report ON properties(report_id);
CREATE INDEX IF NOT EXISTS idx_locations_property ON internal_locations(property_id);
CREATE INDEX IF NOT EXISTS idx_doors_location ON fire_doors(internal_location_id);
CREATE INDEX IF NOT EXISTS idx_work_items_door ON work_items(fire_door_id);
CREATE INDEX IF NOT EXISTS idx_photos_report ON photos(report_id);
CREATE INDEX IF NOT EXISTS idx_photos_door ON photos(fire_door_id);
CREATE INDEX IF NOT EXISTS idx_photos_location ON photos(internal_location_id);

-- Triggers for updated_at
CREATE TRIGGER IF NOT EXISTS update_reports_timestamp 
AFTER UPDATE ON reports
BEGIN
    UPDATE reports SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_fire_doors_timestamp 
AFTER UPDATE ON fire_doors
BEGIN
    UPDATE fire_doors SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- =========================================================================
-- FACILITIES MANAGEMENT PLATFORM - ENGINE CORE TABLES
-- =========================================================================
-- These tables implement the engine-centric architecture where:
-- - artefacts = single source of truth for all facilities management work
-- - modules = pluggable UI panels that populate sections of the payload
-- - One save/version/export/share pipeline for everything
-- =========================================================================

-- CORE ENGINE: Artefacts (Container for all facilities management work)
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

-- CORE ENGINE: Evidence Files (Screenshots, logs, PCAPs, etc.)
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
    icon TEXT,                         -- Icon emoji or class
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

-- SEED DATA: Core Modules
INSERT OR IGNORE INTO modules (module_key, module_name, description, icon, display_order) VALUES
('risk_assessment', 'Risk Assessment', 'SLA + Safety triage, hazards, mitigations', '🛡️', 1),
('site_inspection', 'Site Inspection', 'Findings, photos, defects, recommended actions', '🔍', 2),
('contractor_review', 'Contractor Review', 'Timeline, root cause, corrective actions, rework prevention', '📋', 3),
('incident_case', 'Incident Case', 'Alerts, IOCs, investigation notes, response actions', '🚨', 4);

-- INDEXES: Performance optimization
CREATE INDEX IF NOT EXISTS idx_artefacts_type ON artefacts(artefact_type);
CREATE INDEX IF NOT EXISTS idx_artefacts_client ON artefacts(client_id);
CREATE INDEX IF NOT EXISTS idx_artefacts_status ON artefacts(status);
CREATE INDEX IF NOT EXISTS idx_artefact_versions_artefact ON artefact_versions(artefact_id);

-- ===== FACILITIES MANAGEMENT TABLES =====
-- Clients and Services databases (NOT case artefacts - separate from engine)

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

