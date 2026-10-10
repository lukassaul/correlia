-- Correlia: roles and access control

-- Roles
CREATE ROLE correlia_viewer   NOLOGIN;
CREATE ROLE correlia_uploader NOLOGIN;
CREATE ROLE correlia_admin    NOLOGIN BYPASSRLS;

-- Read access for viewers
GRANT USAGE ON SCHEMA analysis, catalog TO correlia_viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA analysis TO correlia_viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA catalog  TO correlia_viewer;

-- Uploaders inherit viewer privileges
GRANT correlia_viewer TO correlia_uploader;
GRANT INSERT ON analysis.observations, analysis.sources, analysis.indicators TO correlia_uploader;

-- Track which uploader owns which source
CREATE TABLE analysis.source_owners (
    source_id   BIGINT NOT NULL REFERENCES analysis.sources(source_id),
    owner_role  NAME NOT NULL,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, owner_role)
);

-- Enable row-level security on observations
ALTER TABLE analysis.observations ENABLE ROW LEVEL SECURITY;

-- Everyone with SELECT can read all rows
CREATE POLICY obs_public_read ON analysis.observations
    FOR SELECT USING (true);

-- Uploaders may only insert rows for sources they own
CREATE POLICY obs_uploader_insert ON analysis.observations
    FOR INSERT
    WITH CHECK (
        source_id IN (
            SELECT source_id FROM analysis.source_owners
            WHERE owner_role = current_user
        )
    );

-- Admin bypasses RLS
ALTER TABLE analysis.observations FORCE ROW LEVEL SECURITY;
GRANT ALL ON ALL TABLES IN SCHEMA analysis, catalog TO correlia_admin;
GRANT ALL ON ALL SEQUENCES IN SCHEMA analysis TO correlia_admin;
