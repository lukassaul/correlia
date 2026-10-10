-- Correlia: required extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy text matching for place names
CREATE EXTENSION IF NOT EXISTS btree_gin;    -- composite indexes
