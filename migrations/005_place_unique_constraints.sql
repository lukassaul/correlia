-- Correlia: unique constraints on places

-- A country is uniquely identified by its ISO 3166-1 alpha-3 code.
CREATE UNIQUE INDEX places_iso_alpha3_uniq
    ON analysis.places (iso_alpha3)
    WHERE iso_alpha3 IS NOT NULL;

-- An admin-1 region is uniquely identified by its ISO 3166-2 code.
CREATE UNIQUE INDEX places_iso_3166_2_uniq
    ON analysis.places (iso_3166_2)
    WHERE iso_3166_2 IS NOT NULL;

-- An admin-2 region is uniquely identified by its GDL code.
CREATE UNIQUE INDEX places_gdl_code_uniq
    ON analysis.places (gdl_code)
    WHERE gdl_code IS NOT NULL;

-- A populated place is uniquely identified by its GeoNames ID.
CREATE UNIQUE INDEX places_geonames_id_uniq
    ON analysis.places (geonames_id)
    WHERE geonames_id IS NOT NULL;
