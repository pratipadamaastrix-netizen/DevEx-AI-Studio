-- =========================================================================
-- FIRE DOOR LEGACY SYSTEM SCHEMA
-- =========================================================================
-- Fire Door Report Builder - Legacy system for fire door compliance reporting
-- Photos stored in filesystem with metadata
-- =========================================================================
-- Database: fire_door_reports.db
-- Version: 0.2.1 (CF1.1)
-- =========================================================================

-- FIRE DOOR CORE: Reports
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

-- FIRE DOOR: Properties (Sites/Buildings)
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

-- FIRE DOOR: Internal Locations (Floors/Areas within properties)
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

-- FIRE DOOR: Fire Doors
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

-- FIRE DOOR: Work Items (Remediation tasks)
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

-- FIRE DOOR: Photos (Universal photo table with filesystem storage)
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
    photo_role TEXT DEFAULT 'door_evidence',  -- 'door_evidence', 'property_plan', 'location_access'
    caption TEXT,
    display_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (internal_location_id) REFERENCES internal_locations(id) ON DELETE CASCADE,
    FOREIGN KEY (fire_door_id) REFERENCES fire_doors(id) ON DELETE CASCADE
);

-- =========================================================================
-- FUTURE EXTENSIONS: Gardening and Decoration
-- =========================================================================
-- These tables are reserved for future use when expanding beyond fire doors
-- =========================================================================

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

-- =========================================================================
-- INDEXES: Performance optimization
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_properties_report ON properties(report_id);
CREATE INDEX IF NOT EXISTS idx_locations_property ON internal_locations(property_id);
CREATE INDEX IF NOT EXISTS idx_doors_location ON fire_doors(internal_location_id);
CREATE INDEX IF NOT EXISTS idx_work_items_door ON work_items(fire_door_id);
CREATE INDEX IF NOT EXISTS idx_photos_report ON photos(report_id);
CREATE INDEX IF NOT EXISTS idx_photos_door ON photos(fire_door_id);
CREATE INDEX IF NOT EXISTS idx_photos_location ON photos(internal_location_id);

-- =========================================================================
-- TRIGGERS: Automatic timestamp updates
-- =========================================================================

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
